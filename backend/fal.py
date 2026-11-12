"""Async client for Fal video models deployed in Microsoft Foundry.

Requests go to <FOUNDRY_FAL_ENDPOINT>/providers/falai/v1/<route>: a submit
returns a queue request id, ``requests/{id}/status`` reports progress, and
``requests/{id}`` returns ``{"video": {"url": ...}}`` once complete. The video
URL is an expiring asset on the same Foundry resource, so it is downloaded
straight away with the same credentials.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .config import (
    FAL_CREATE_MAX_RETRIES,
    FAL_GENERATE_AUDIO,
    FAL_MINIMAX_PROMPT_EXPANSION,
    FAL_MODELS,
    FAL_POLL_INTERVAL_SECONDS,
    FAL_POLL_TIMEOUT_SECONDS,
    FOUNDRY_FAL_API_KEY,
    FOUNDRY_FAL_AUTH,
    FOUNDRY_FAL_ENDPOINT,
    FalModelSpec,
    logger,
)
from .provider_limits import provider_slot
from .utils import nearest_aspect_ratio, resolution_to_size

DOWNLOAD_CHUNK_SIZE = 1048576
ACTIVE_STATUSES = {"IN_QUEUE", "IN_PROGRESS"}
MAX_CONSECUTIVE_POLL_ERRORS = 5
ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"

# Aspect ratios each family accepts on text-to-video (image-to-video takes the
# frame from the start image).
_TEXT_ASPECT_RATIOS = {
    "kling": ("16:9", "9:16", "1:1"),
    "ltx": ("16:9", "9:16"),
    "minimax": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
}


class FalAPIError(RuntimeError):
    """Raised when Foundry rejects or fails a Fal video generation request."""


@dataclass
class FalRenderResult:
    provider: str
    request_id: str
    endpoint: str
    request_payload: dict[str, Any]
    final_response: dict[str, Any]
    video_url: str
    status: str | None = None


def fal_model(provider: str) -> FalModelSpec:
    try:
        return FAL_MODELS[provider]
    except KeyError as exc:
        raise FalAPIError(f"Unknown Fal video provider {provider!r}.") from exc


def _is_1080(resolution: str) -> bool:
    width, height = resolution_to_size(resolution)
    return max(width, height) >= 1920


def build_fal_payload(
    spec: FalModelSpec,
    *,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Request body for the spec's text-to-video or image-to-video route."""

    payload: dict[str, Any] = {"model": spec.deployment, "prompt": prompt_text.strip()}
    if image_url:
        payload[spec.image_field] = image_url
    aspect_ratio = nearest_aspect_ratio(resolution, _TEXT_ASPECT_RATIOS[spec.family])

    if spec.family == "kling":
        # Kling takes the duration as a string enum, "3" to "15".
        payload["duration"] = str(int(duration_seconds))
        payload["generate_audio"] = FAL_GENERATE_AUDIO
        if not image_url:
            payload["aspect_ratio"] = aspect_ratio
    elif spec.family == "ltx":
        payload["duration"] = int(duration_seconds)
        payload["resolution"] = "1080p" if _is_1080(resolution) else "720p"
        payload["aspect_ratio"] = aspect_ratio
        payload["generate_audio"] = FAL_GENERATE_AUDIO
    elif spec.family == "minimax":
        payload["duration"] = int(duration_seconds)
        payload["resolution"] = "1080P" if _is_1080(resolution) else "768P"
        payload["prompt_expansion_mode"] = FAL_MINIMAX_PROMPT_EXPANSION
        if not image_url:
            payload["aspect_ratio"] = aspect_ratio
    else:  # pragma: no cover - specs are defined in config
        raise FalAPIError(f"Unsupported Fal model family {spec.family!r}.")
    return payload


def image_data_uri(path: str | Path) -> str:
    """Encode a start image as a JPEG data URI (Foundry accepts these inline)."""

    with Image.open(path) as img:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "JPEG", quality=92)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


_credential = None


async def _auth_headers() -> dict[str, str]:
    if FOUNDRY_FAL_AUTH == "key":
        if not FOUNDRY_FAL_API_KEY:
            raise FalAPIError("FOUNDRY_FAL_AUTH=key needs FOUNDRY_FAL_API_KEY; set it or use FOUNDRY_FAL_AUTH=entra.")
        return {"api-key": FOUNDRY_FAL_API_KEY}
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    token = await asyncio.to_thread(_credential.get_token, ENTRA_SCOPE)
    return {"Authorization": f"Bearer {token.token}"}


def _base_url() -> str:
    if not FOUNDRY_FAL_ENDPOINT:
        raise FalAPIError(
            "Set FOUNDRY_FAL_ENDPOINT (e.g. https://<resource>.services.ai.azure.com) to use the Fal video models."
        )
    return f"{FOUNDRY_FAL_ENDPOINT}/providers/falai/v1"


def _redact(text: str) -> str:
    return text.replace(FOUNDRY_FAL_API_KEY, "[REDACTED]") if FOUNDRY_FAL_API_KEY else text


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("retry-after", ""))
    except ValueError:
        return None


async def _submit(client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> str:
    # Only HTTP 429 is retried: an accepted submit is billed, and after a
    # timeout or 5xx the request may already be running.
    uncertain = " The request may already be running; check the deployment before resubmitting."
    for attempt in range(FAL_CREATE_MAX_RETRIES + 1):
        try:
            response = await client.post(url, json=payload, headers=await _auth_headers())
        except httpx.RequestError as exc:
            raise FalAPIError(f"Fal submit request failed: {exc}.{uncertain}") from exc
        if response.status_code != 429 or attempt == FAL_CREATE_MAX_RETRIES:
            break
        # Deployments allow a few submissions per minute, so wait in tens of seconds.
        delay = max(_retry_after(response) or 0.0, min(120.0, 15.0 * (2**attempt)))
        logger.warning(
            "Fal submit for %s rate-limited (HTTP 429); retrying in %.0fs (%d/%d).",
            payload.get("model"),
            delay,
            attempt + 1,
            FAL_CREATE_MAX_RETRIES,
        )
        await asyncio.sleep(delay)
    if response.status_code >= 400:
        hint = uncertain if response.status_code >= 500 else ""
        raise FalAPIError(
            f"{payload.get('model')} submit returned HTTP {response.status_code}: "
            f"{_redact(response.text[:1000])}{hint}"
        )
    try:
        request_id = response.json().get("request_id")
    except ValueError:
        request_id = None
    if not request_id:
        raise FalAPIError(f"{payload.get('model')} submit response has no request_id.{uncertain}")
    return str(request_id)


async def _wait_until_complete(client: httpx.AsyncClient, queue_url: str, request_id: str) -> None:
    deadline = time.monotonic() + FAL_POLL_TIMEOUT_SECONDS
    consecutive_errors = 0
    while True:
        await asyncio.sleep(FAL_POLL_INTERVAL_SECONDS)
        try:
            response = await client.get(f"{queue_url}/requests/{request_id}/status", headers=await _auth_headers())
            response.raise_for_status()
            status = str(response.json().get("status") or "").upper()
            consecutive_errors = 0
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
            # Status checks are free, so ride out brief network or gateway errors.
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
                raise FalAPIError(
                    f"Fal request {request_id} status checks failed {consecutive_errors} times: "
                    f"{_redact(str(exc))}. The request may still be running."
                ) from exc
            continue

        if status == "COMPLETED":
            return
        if status not in ACTIVE_STATUSES:
            raise FalAPIError(f"Fal request {request_id} returned unrecognized status {status!r}.")
        if time.monotonic() >= deadline:
            raise FalAPIError(
                f"Fal request {request_id} did not finish within {FAL_POLL_TIMEOUT_SECONDS:.0f}s; "
                "it may still complete."
            )


async def _fetch_result(client: httpx.AsyncClient, queue_url: str, request_id: str) -> dict[str, Any]:
    response = await client.get(f"{queue_url}/requests/{request_id}", headers=await _auth_headers())
    if response.status_code >= 400:
        # A failed generation surfaces here, with the model's error as the body.
        raise FalAPIError(
            f"Fal request {request_id} failed with HTTP {response.status_code}: {_redact(response.text[:1000])}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise FalAPIError(f"Fal request {request_id} returned a non-JSON result.") from exc


def _same_host(url: str, base: str) -> bool:
    return urlsplit(url).netloc.lower() == urlsplit(base).netloc.lower()


async def _download_video(url: str, dest: Path) -> None:
    # The asset is served by the Foundry resource and needs its credentials;
    # they are never sent to any other host.
    headers = await _auth_headers() if _same_host(url, FOUNDRY_FAL_ENDPOINT) else {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    timeout = httpx.Timeout(300.0, connect=30.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                with partial.open("wb") as fh:
                    async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                        fh.write(chunk)
        if partial.stat().st_size == 0:
            raise FalAPIError(f"Fal video download from {url} was empty.")
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)


def _loggable_payload(payload: dict[str, Any], spec: FalModelSpec) -> dict[str, Any]:
    logged = dict(payload)
    image = logged.get(spec.image_field)
    if isinstance(image, str) and image.startswith("data:"):
        logged[spec.image_field] = f"<data URI omitted, {len(image)} chars>"
    return logged


async def render_fal_clip(
    *,
    provider: str,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    output_path: Path,
    primary_image_path: str | None = None,
) -> FalRenderResult:
    spec = fal_model(provider)
    if duration_seconds not in spec.durations:
        allowed_text = ", ".join(str(value) for value in spec.durations)
        raise FalAPIError(f"{spec.label} supports durations of {allowed_text} seconds.")
    base = _base_url()
    await _auth_headers()  # fail fast on missing credentials before encoding images

    image_url = await asyncio.to_thread(image_data_uri, primary_image_path) if primary_image_path else None
    payload = build_fal_payload(
        spec,
        prompt_text=prompt_text,
        duration_seconds=duration_seconds,
        resolution=resolution,
        image_url=image_url,
    )
    route = spec.image_to_video_path if image_url else spec.text_to_video_path
    submit_url = f"{base}/{route}"
    queue_url = f"{base}/{spec.queue_route}"

    timeout = httpx.Timeout(120.0, connect=30.0)
    async with provider_slot(provider), httpx.AsyncClient(timeout=timeout) as client:
        request_id = await _submit(client, submit_url, payload)
        logger.info("%s request %s submitted for %s", spec.deployment, request_id, output_path.name)
        await _wait_until_complete(client, queue_url, request_id)
        final_response = await _fetch_result(client, queue_url, request_id)

    video_url = (final_response.get("video") or {}).get("url")
    if not video_url:
        raise FalAPIError(f"Fal request {request_id} completed without video.url: {json.dumps(final_response)[:500]}")
    await _download_video(video_url, Path(output_path))

    return FalRenderResult(
        provider=provider,
        request_id=request_id,
        endpoint=submit_url,
        request_payload=_loggable_payload(payload, spec),
        final_response=final_response,
        video_url=video_url,
        status="COMPLETED",
    )


def write_fal_logs(
    result: FalRenderResult, video_path: Path, client_log_suffix: str, action: str = "request"
) -> tuple[Path, Path]:
    """Persist the request/response JSON and a short client log next to the clip."""

    log_path = video_path.with_suffix(".json")
    client_log_path = video_path.with_suffix(client_log_suffix)
    log_data = {
        "provider": result.provider,
        "task_id": result.request_id,
        "endpoint": result.endpoint,
        "request": result.request_payload,
        "response": result.final_response,
        "video_url": result.video_url,
        "status": result.status,
    }
    log_path.write_text(json.dumps(log_data, indent=2, ensure_ascii=False), encoding="utf-8")
    client_log_path.write_text(
        f"{fal_model(result.provider).label} {action} {result.request_id} completed via {result.endpoint}.\n"
        f"Video URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path


__all__ = [
    "FalAPIError",
    "FalRenderResult",
    "build_fal_payload",
    "fal_model",
    "image_data_uri",
    "render_fal_clip",
    "write_fal_logs",
]
