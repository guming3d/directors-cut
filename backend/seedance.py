"""Async client for Doubao Seedance 2.5 video generation on Volcengine Ark."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import (
    ARK_API_KEY,
    SEEDANCE_ALLOWED_DURATIONS,
    SEEDANCE_CREATE_MAX_RETRIES,
    SEEDANCE_API_URL,
    SEEDANCE_GENERATE_AUDIO,
    SEEDANCE_IMAGE_ROLE,
    SEEDANCE_MODEL,
    SEEDANCE_POLL_INTERVAL_SECONDS,
    SEEDANCE_POLL_TIMEOUT_SECONDS,
    SEEDANCE_RESOLUTION,
    SEEDANCE_SUPPORTED_ASPECT_RATIOS,
    SEEDANCE_WATERMARK,
    logger,
)
from .provider_limits import provider_slot
from .r2_storage import upload_public_file_async
from .utils import nearest_aspect_ratio

DOWNLOAD_CHUNK_SIZE = 1048576
ACTIVE_STATUSES = {"queued", "running"}
FAILED_STATUSES = {"failed", "expired", "cancelled", "canceled"}
MAX_CONSECUTIVE_POLL_ERRORS = 5


class SeedanceAPIError(RuntimeError):
    """Raised when Ark rejects or fails a Seedance generation task."""


@dataclass
class SeedanceRenderResult:
    task_id: str
    endpoint: str
    request_payload: dict[str, Any]
    final_response: dict[str, Any]
    video_url: str
    status: str | None = None


def build_seedance_payload(
    *,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    image_url: str | None = None,
) -> dict[str, Any]:
    prompt = prompt_text.strip()
    if image_url and SEEDANCE_IMAGE_ROLE == "reference_image":
        # A reference image alone does not pin the opening frame; say so in the prompt.
        prompt = f"The video opens on the composition of Image 1.\n\n{prompt}"
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if image_url:
        content.append(
            {"type": "image_url", "image_url": {"url": image_url}, "role": SEEDANCE_IMAGE_ROLE}
        )
    payload: dict[str, Any] = {
        "model": SEEDANCE_MODEL,
        "content": content,
        "generate_audio": SEEDANCE_GENERATE_AUDIO,
        "duration": int(duration_seconds),
        "watermark": SEEDANCE_WATERMARK,
    }
    # With a first-frame image Ark derives the ratio from the image and rejects
    # an explicit one (InvalidParameter.TaskTypeConstraint).
    if not image_url or SEEDANCE_IMAGE_ROLE != "first_frame":
        payload["ratio"] = nearest_aspect_ratio(resolution, SEEDANCE_SUPPORTED_ASPECT_RATIOS)
    if SEEDANCE_RESOLUTION:
        payload["resolution"] = SEEDANCE_RESOLUTION
    return payload


def _headers() -> dict[str, str]:
    if not ARK_API_KEY:
        raise SeedanceAPIError("Configure ARK_API_KEY before using the Seedance provider.")
    return {"Authorization": f"Bearer {ARK_API_KEY}", "Content-Type": "application/json"}


def _redact(text: str) -> str:
    return text.replace(ARK_API_KEY, "[REDACTED]") if ARK_API_KEY else text


async def _create_task(client: httpx.AsyncClient, payload: dict[str, Any]) -> str:
    # Never retried automatically: every accepted create is billed, and after a
    # timeout or 5xx the task may already exist on Ark.
    uncertain = " The task may already exist; check the Ark console before resubmitting."
    for attempt in range(SEEDANCE_CREATE_MAX_RETRIES + 1):
        try:
            response = await client.post(SEEDANCE_API_URL, json=payload, headers=_headers())
        except httpx.RequestError as exc:
            raise SeedanceAPIError(f"Seedance create request failed: {exc}.{uncertain}") from exc
        # 429 (rate limit) means Ark created no task, so only this is retried.
        if response.status_code != 429 or attempt == SEEDANCE_CREATE_MAX_RETRIES:
            break
        delay = min(60.0, 5.0 * (2**attempt))
        logger.warning(
            "Seedance create rate-limited (HTTP 429); retrying in %.0fs (%d/%d).",
            delay,
            attempt + 1,
            SEEDANCE_CREATE_MAX_RETRIES,
        )
        await asyncio.sleep(delay)
    if response.status_code >= 400:
        hint = uncertain if response.status_code >= 500 else ""
        raise SeedanceAPIError(
            f"Seedance create returned HTTP {response.status_code}: {_redact(response.text[:1000])}{hint}"
        )
    try:
        task_id = response.json().get("id")
    except ValueError:
        task_id = None
    if not task_id:
        raise SeedanceAPIError(f"Seedance create response has no task id.{uncertain}")
    return str(task_id)


def _failure_message(task_id: str, record: dict[str, Any]) -> str:
    error = record.get("error") or {}
    detail = error.get("message") or error.get("code") or "no error detail returned"
    return f"Seedance task {task_id} {record.get('status')}: {detail}"


async def _poll_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + SEEDANCE_POLL_TIMEOUT_SECONDS
    consecutive_errors = 0
    while True:
        try:
            response = await client.get(f"{SEEDANCE_API_URL}/{task_id}", headers=_headers())
            response.raise_for_status()
            record = response.json()
            consecutive_errors = 0
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            # Polling is free, so tolerate brief network or gateway hiccups.
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                raise SeedanceAPIError(
                    f"Seedance task {task_id} status checks failed {consecutive_errors} times: {_redact(str(exc))}. "
                    "The task may still be running on Ark."
                ) from exc
            record = {"status": "running"}

        status = (record.get("status") or "").lower()
        if status == "succeeded":
            return record
        if status in FAILED_STATUSES:
            raise SeedanceAPIError(_failure_message(task_id, record))
        if status not in ACTIVE_STATUSES:
            raise SeedanceAPIError(f"Seedance task {task_id} returned unrecognized status {status!r}.")
        if time.monotonic() >= deadline:
            raise SeedanceAPIError(
                f"Seedance task {task_id} did not finish within {SEEDANCE_POLL_TIMEOUT_SECONDS:.0f}s; "
                "it may still complete on Ark."
            )
        await asyncio.sleep(SEEDANCE_POLL_INTERVAL_SECONDS)


async def _download_video(url: str, dest: Path) -> None:
    # Separate unauthenticated client: the Ark key must never reach the media host.
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    timeout = httpx.Timeout(300.0, connect=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with partial.open("wb") as fh:
                    async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                        fh.write(chunk)
        if partial.stat().st_size == 0:
            raise SeedanceAPIError(f"Seedance video download from {url} was empty.")
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


async def render_seedance_clip(
    *,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    output_path: Path,
    primary_image_path: str | None = None,
) -> SeedanceRenderResult:
    if duration_seconds not in SEEDANCE_ALLOWED_DURATIONS:
        allowed_text = ", ".join(str(value) for value in SEEDANCE_ALLOWED_DURATIONS)
        raise SeedanceAPIError(f"Seedance supports durations of {allowed_text} seconds.")
    _headers()  # fail fast before uploading references when the key is missing

    image_url = await upload_public_file_async(primary_image_path) if primary_image_path else None
    payload = build_seedance_payload(
        prompt_text=prompt_text,
        duration_seconds=duration_seconds,
        resolution=resolution,
        image_url=image_url,
    )
    logger.debug(
        "Submitting Seedance request duration=%ss ratio=%s has_reference=%s",
        payload["duration"],
        payload.get("ratio", "from first frame"),
        bool(image_url),
    )

    timeout = httpx.Timeout(60.0, connect=30.0)
    async with provider_slot("seedance"), httpx.AsyncClient(timeout=timeout) as client:
        task_id = await _create_task(client, payload)
        logger.info("Seedance task %s submitted for %s", task_id, output_path.name)
        final_response = await _poll_task(client, task_id)

    video_url = (final_response.get("content") or {}).get("video_url")
    if not video_url:
        raise SeedanceAPIError(f"Seedance task {task_id} succeeded without content.video_url.")
    await _download_video(video_url, Path(output_path))

    return SeedanceRenderResult(
        task_id=task_id,
        endpoint=SEEDANCE_API_URL,
        request_payload=payload,
        final_response=final_response,
        video_url=video_url,
        status=final_response.get("status"),
    )


def write_seedance_logs(
    result: SeedanceRenderResult, video_path: Path, client_log_suffix: str, action: str = "task"
) -> tuple[Path, Path]:
    """Persist the request/response JSON and a short client log next to the clip."""

    log_path = video_path.with_suffix(".json")
    client_log_path = video_path.with_suffix(client_log_suffix)
    log_data = {
        "provider": "seedance",
        "task_id": result.task_id,
        "endpoint": result.endpoint,
        "request": result.request_payload,
        "response": result.final_response,
        "video_url": result.video_url,
        "status": result.status,
    }
    log_path.write_text(json.dumps(log_data, indent=2, ensure_ascii=False), encoding="utf-8")
    client_log_path.write_text(
        f"Seedance {action} {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path


__all__ = [
    "SeedanceAPIError",
    "SeedanceRenderResult",
    "build_seedance_payload",
    "render_seedance_clip",
    "write_seedance_logs",
]
