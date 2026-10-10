"""Async client helpers for invoking the Jimeng text/image-to-video API."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

import httpx

from .config import (
    JIMENG_ACCESS_KEY_ID,
    JIMENG_API_BASE_URL,
    JIMENG_ALLOWED_DURATIONS,
    JIMENG_MAX_CONCURRENT_TASKS,
    JIMENG_SUBMIT_MAX_RETRIES,
    JIMENG_SUBMIT_RETRY_BASE_DELAY_SECONDS,
    JIMENG_POLL_INTERVAL_SECONDS,
    JIMENG_POLL_TIMEOUT_SECONDS,
    JIMENG_REGION,
    JIMENG_REQ_KEY,
    JIMENG_SECRET_ACCESS_KEY,
    JIMENG_SECURITY_TOKEN,
    JIMENG_SERVICE,
    JIMENG_SUPPORTED_ASPECT_RATIOS,
    logger,
)
from .utils import nearest_aspect_ratio

DOWNLOAD_CHUNK_SIZE = 1048576
API_VERSION = "2022-08-31"


class JimengAPIError(RuntimeError):
    """Raised when the Jimeng API returns an error response."""


@dataclass
class JimengRenderResult:
    task_id: str
    endpoint: str
    request_payload: dict[str, Any]
    final_response: dict[str, Any]
    video_url: str
    status: str | None = None


def encode_image_to_base64(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _derive_signing_key(short_date: str) -> bytes:
    """Derive the Jimeng signing key following the official VOLC HMAC chain.

    Note: Do NOT prefix the secret with 'VOLC' (the official guide omits it).
    """

    if not JIMENG_SECRET_ACCESS_KEY:
        raise JimengAPIError("Configure JIMENG_SECRET_ACCESS_KEY before calling Jimeng APIs.")
    secret = JIMENG_SECRET_ACCESS_KEY.encode("utf-8")
    key_date = _sign(secret, short_date)
    key_region = _sign(key_date, JIMENG_REGION)
    key_service = _sign(key_region, JIMENG_SERVICE)
    return _sign(key_service, "request")


def _canonical_query(params: Mapping[str, str]) -> str:
    items: list[str] = []
    for key in sorted(params):
        value = params[key]
        items.append(f"{quote(str(key), safe='-_.~')}={quote(str(value), safe='-_.~')}")
    # Align with official behavior: replace '+' with '%20' to encode spaces consistently.
    return "&".join(items).replace("+", "%20")


def _prepare_signed_request(action: str, body: dict[str, Any]) -> tuple[str, dict[str, str], bytes]:
    if not JIMENG_ACCESS_KEY_ID or not JIMENG_SECRET_ACCESS_KEY:
        raise JimengAPIError("Configure JIMENG_ACCESS_KEY_ID and JIMENG_SECRET_ACCESS_KEY before using Jimeng.")

    body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    body_bytes = body_text.encode("utf-8")
    payload_hash = _sha256_hex(body_bytes)
    now = _utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    parsed = urlparse(JIMENG_API_BASE_URL)
    host = parsed.netloc or parsed.path
    if not host:
        raise JimengAPIError(f"Invalid JIMENG_API_BASE_URL: {JIMENG_API_BASE_URL}")

    canonical_headers = {
        "content-type": "application/json",
        "host": host,
        "x-content-sha256": payload_hash,
        "x-date": x_date,
    }
    if JIMENG_SECURITY_TOKEN:
        canonical_headers["x-security-token"] = JIMENG_SECURITY_TOKEN

    signed_header_names = ";".join(sorted(canonical_headers))
    canonical_header_lines = "".join(
        f"{name}:{canonical_headers[name]}\n" for name in sorted(canonical_headers)
    )
    canonical_query = _canonical_query({"Action": action, "Version": API_VERSION})

    canonical_request = "\n".join(
        [
            "POST",
            "/",
            canonical_query,
            canonical_header_lines,
            signed_header_names,
            payload_hash,
        ]
    )
    hashed_request = _sha256_hex(canonical_request.encode("utf-8"))
    scope = f"{short_date}/{JIMENG_REGION}/{JIMENG_SERVICE}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            scope,
            hashed_request,
        ]
    )
    signing_key = _derive_signing_key(short_date)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={JIMENG_ACCESS_KEY_ID}/{scope}, "
        f"SignedHeaders={signed_header_names}, Signature={signature}"
    )

    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": host,
        "X-Content-Sha256": payload_hash,
        "X-Date": x_date,
    }
    if JIMENG_SECURITY_TOKEN:
        headers["X-Security-Token"] = JIMENG_SECURITY_TOKEN

    url = f"{JIMENG_API_BASE_URL}?{canonical_query}"
    return url, headers, body_bytes


async def _invoke_action(
    client: httpx.AsyncClient, action: str, body: dict[str, Any]
) -> dict[str, Any]:
    url, headers, body_bytes = _prepare_signed_request(action, body)
    response = await client.post(url, content=body_bytes, headers=headers)
    response.raise_for_status()
    try:
        return response.json()
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise JimengAPIError(f"Jimeng {action} returned non-JSON response.") from exc


def _resolution_to_aspect_ratio(resolution: str) -> str:
    return nearest_aspect_ratio(resolution, JIMENG_SUPPORTED_ASPECT_RATIOS)


_SLOTS_BY_LOOP: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _task_slots() -> asyncio.Semaphore:
    """Limit concurrent Jimeng tasks; the account rejects extras with HTTP 429.

    A slot is held from submission until the result arrives, so parallel
    scenes queue here instead of failing.
    """

    loop = asyncio.get_running_loop()
    slots = _SLOTS_BY_LOOP.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(JIMENG_MAX_CONCURRENT_TASKS)
        _SLOTS_BY_LOOP[loop] = slots
    return slots


async def _submit_with_retry(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a task, retrying HTTP 429.

    A 429 means Jimeng created no task, so retrying cannot bill twice.
    """

    for attempt in range(JIMENG_SUBMIT_MAX_RETRIES + 1):
        try:
            return await _invoke_action(client, "CVSync2AsyncSubmitTask", payload)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or attempt == JIMENG_SUBMIT_MAX_RETRIES:
                if exc.response.status_code == 429:
                    raise JimengAPIError(
                        f"Jimeng is still at its concurrent task limit after {attempt + 1} attempts (HTTP 429). "
                        "Lower parallel renders or JIMENG_MAX_CONCURRENT_TASKS, or retry later."
                    ) from exc
                raise
            delay = min(60.0, JIMENG_SUBMIT_RETRY_BASE_DELAY_SECONDS * (2**attempt))
            logger.warning(
                "Jimeng submit hit its concurrency limit (HTTP 429); retrying in %.0fs (%d/%d).",
                delay,
                attempt + 1,
                JIMENG_SUBMIT_MAX_RETRIES,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def _download_video(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_SIZE):
                fh.write(chunk)


async def _poll_task(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    payload = {"req_key": JIMENG_REQ_KEY, "task_id": task_id}
    deadline = time.time() + JIMENG_POLL_TIMEOUT_SECONDS
    last_response: dict[str, Any] | None = None
    while time.time() < deadline:
        last_response = await _invoke_action(client, "CVSync2AsyncGetResult", payload)
        code = last_response.get("code")
        if code != 10000:
            message = last_response.get("message") or "Unknown error"
            raise JimengAPIError(f"Jimeng task {task_id} failed (code={code}): {message}")
        data = last_response.get("data") or {}
        status = (data.get("status") or "").lower()
        if status == "done":
            return last_response
        if status in {"expired", "not_found"}:
            raise JimengAPIError(f"Jimeng task {task_id} status: {status}.")
        await asyncio.sleep(JIMENG_POLL_INTERVAL_SECONDS)
    raise JimengAPIError(
        f"Jimeng task {task_id} did not complete within {JIMENG_POLL_TIMEOUT_SECONDS}s."
    )


async def render_jimeng_clip(
    *,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    output_path: Path,
    primary_image_path: str | None = None,
    seed: int = -1,
) -> JimengRenderResult:
    if duration_seconds not in JIMENG_ALLOWED_DURATIONS:
        allowed_text = ", ".join(str(value) for value in JIMENG_ALLOWED_DURATIONS)
        raise JimengAPIError(f"Jimeng supports durations of {allowed_text} seconds.")
    aspect_ratio = _resolution_to_aspect_ratio(resolution)
    frames = max(1, int(duration_seconds * 24) + 1)
    request_payload: dict[str, Any] = {
        "req_key": JIMENG_REQ_KEY,
        "prompt": prompt_text,
        "seed": seed,
        "frames": frames,
    }
    if not primary_image_path:
        request_payload["aspect_ratio"] = aspect_ratio
    if primary_image_path:
        try:
            request_payload["binary_data_base64"] = [encode_image_to_base64(primary_image_path)]
        except OSError as exc:
            raise JimengAPIError(f"Failed to load reference image {primary_image_path}: {exc}") from exc

    logger.debug(
        "Submitting Jimeng request duration=%ss frames=%s aspect_ratio=%s has_reference=%s",
        duration_seconds,
        frames,
        request_payload.get("aspect_ratio"),
        "binary_data_base64" in request_payload,
    )

    timeout = httpx.Timeout(JIMENG_POLL_TIMEOUT_SECONDS, connect=30.0)
    async with _task_slots(), httpx.AsyncClient(timeout=timeout) as client:
        submit_response = await _submit_with_retry(client, request_payload)
        code = submit_response.get("code")
        if code != 10000:
            message = submit_response.get("message") or "Unknown error"
            raise JimengAPIError(f"Jimeng submit failed (code={code}): {message}")
        task_id = (submit_response.get("data") or {}).get("task_id")
        if not task_id:
            raise JimengAPIError("Jimeng submit response missing task_id.")
        # Logged so a result can still be fetched if this process is interrupted.
        logger.info("Jimeng task %s submitted for %s", task_id, Path(output_path).name)

        final_response = await _poll_task(client, task_id)
        data = final_response.get("data") or {}
        video_url = data.get("video_url")
        status = (data.get("status") or "").lower()
        if not video_url:
            raise JimengAPIError(f"Jimeng task {task_id} completed without a video_url (status={status}).")

        await _download_video(client, video_url, Path(output_path))
        if not Path(output_path).is_file():
            raise JimengAPIError(f"Jimeng reported success but no video was saved to {output_path}.")

    return JimengRenderResult(
        task_id=task_id,
        endpoint=JIMENG_API_BASE_URL,
        request_payload=request_payload,
        final_response=final_response,
        video_url=video_url,
        status=status,
    )


__all__ = [
    "JimengAPIError",
    "JimengRenderResult",
    "encode_image_to_base64",
    "render_jimeng_clip",
]
