"""Async client helpers for invoking the Kling video generation API."""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Sequence, Tuple

import httpx

try:  # pragma: no cover - optional when using legacy tokens
    import jwt
except ImportError:  # pragma: no cover
    jwt = None

from .config import (
    KLING_ACCESS_KEY,
    KLING_API_BASE_URL,
    KLING_API_KEY,
    KLING_DEFAULT_CFG_SCALE,
    KLING_DEFAULT_MODE,
    KLING_DEFAULT_MODEL_NAME,
    KLING_DOWNLOAD_CHUNK_SIZE,
    KLING_POLL_INTERVAL_SECONDS,
    KLING_POLL_TIMEOUT_SECONDS,
    KLING_SECRET_KEY,
    KLING_TOKEN_TTL_SECONDS,
    logger,
)
from .utils import resolution_to_size


class KlingAPIError(RuntimeError):
    """Raised when the Kling API returns an error response."""


@dataclass
class KlingRenderResult:
    task_id: str
    endpoint: str
    request_payload: dict[str, Any]
    final_response: dict[str, Any]
    video_url: str
    duration: str | None = None


def encode_image_to_base64(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def resolution_to_aspect_ratio_code(resolution: str) -> str:
    width, height = resolution_to_size(resolution)
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"


_token_lock = Lock()
_cached_token: str | None = None
_cached_token_expires_at: int | None = None
_cached_token_source: str | None = None


def _issue_kling_token(now: int) -> Tuple[str, str, int | None]:
    if KLING_ACCESS_KEY and KLING_SECRET_KEY:
        if jwt is None:
            raise KlingAPIError(
                "PyJWT is required to derive Kling tokens from KLING_ACCESS_KEY/KLING_SECRET_KEY."
            )
        headers = {"alg": "HS256", "typ": "JWT"}
        expires_at = now + KLING_TOKEN_TTL_SECONDS
        payload = {
            "iss": KLING_ACCESS_KEY,
            "exp": expires_at,
            "nbf": max(0, now - 5),
        }
        try:
            token = jwt.encode(payload, KLING_SECRET_KEY, algorithm="HS256", headers=headers)
        except Exception as exc:  # pragma: no cover - PyJWT surfaces details
            raise KlingAPIError(f"Failed to encode Kling auth token: {exc}") from exc
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token, "aksk", expires_at
    if KLING_API_KEY:
        return KLING_API_KEY, "api_key", None
    raise KlingAPIError(
        "Configure KLING_ACCESS_KEY and KLING_SECRET_KEY (preferred) or set KLING_API_KEY for legacy auth."
    )


def generate_kling_auth_token(*, force_refresh: bool = False) -> tuple[str, str, int | None]:
    """Return the cached Kling auth token (and its source/expiry)."""

    global _cached_token, _cached_token_expires_at, _cached_token_source
    now = int(time.time())
    with _token_lock:
        if (
            not force_refresh
            and _cached_token
            and (
                _cached_token_expires_at is None
                or now < max(0, _cached_token_expires_at - 30)
            )
        ):
            return _cached_token, _cached_token_source or "unknown", _cached_token_expires_at

        token, source, expires_at = _issue_kling_token(now)
        _cached_token = token
        _cached_token_source = source
        _cached_token_expires_at = expires_at
        return token, source, expires_at


def _build_headers() -> dict[str, str]:
    token, _, _ = generate_kling_auth_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _supports_cfg_scale(model_name: str) -> bool:
    return "v2" not in model_name.lower()


async def _download_video(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with dest.open("wb") as fh:
            async for chunk in response.aiter_bytes(KLING_DOWNLOAD_CHUNK_SIZE):
                fh.write(chunk)


async def render_kling_clip(
    *,
    prompt_text: str,
    duration_seconds: int,
    resolution: str,
    output_path: Path,
    primary_image_path: str | None = None,
    tail_image_path: str | None = None,
    extra_image_paths: Sequence[str] | None = None,
    external_task_id: str | None = None,
    mode: str | None = None,
    model_name: str | None = None,
    cfg_scale: float | None = None,
) -> KlingRenderResult:
    base_url = KLING_API_BASE_URL or ""
    if not base_url:
        raise KlingAPIError("KLING_API_BASE_URL is not configured; cannot call Kling APIs.")

    headers = _build_headers()
    aspect_ratio = resolution_to_aspect_ratio_code(resolution)
    duration_str = str(duration_seconds)
    request_payload: dict[str, Any] = {
        "prompt": prompt_text,
        "duration": duration_str,
        "aspect_ratio": aspect_ratio,
        "model_name": (model_name or KLING_DEFAULT_MODEL_NAME),
        "mode": (mode or KLING_DEFAULT_MODE),
    }
    if external_task_id:
        request_payload["external_task_id"] = external_task_id

    cfg_value = cfg_scale if cfg_scale is not None else KLING_DEFAULT_CFG_SCALE
    if cfg_value is not None and _supports_cfg_scale(request_payload["model_name"]):
        request_payload["cfg_scale"] = max(0.0, min(1.0, cfg_value))

    image_paths = [primary_image_path] if primary_image_path else []
    if extra_image_paths:
        image_paths.extend(path for path in extra_image_paths if path)

    endpoint = "text2video"
    if len(image_paths) > 1:
        endpoint = "multi-image2video"
        request_payload["image_list"] = [
            {"image": encode_image_to_base64(path)} for path in image_paths[:4]
        ]
    elif image_paths:
        endpoint = "image2video"
        request_payload["image"] = encode_image_to_base64(image_paths[0])
        if tail_image_path:
            request_payload["image_tail"] = encode_image_to_base64(tail_image_path)

    timeout = httpx.Timeout(KLING_POLL_TIMEOUT_SECONDS, connect=30.0)
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout,
    ) as client:
        create_path = f"/v1/videos/{endpoint}"
        response = await client.post(create_path, json=request_payload)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise KlingAPIError(f"Kling API returned error: {payload}")

        data = payload.get("data") or {}
        task_id = data.get("task_id")
        if not task_id:
            raise KlingAPIError("Kling API did not return a task_id.")

        poll_path = f"/v1/videos/{endpoint}/{task_id}"
        deadline = asyncio.get_event_loop().time() + KLING_POLL_TIMEOUT_SECONDS
        poll_payload: dict[str, Any] | None = None
        poll_data: dict[str, Any] = {}
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise KlingAPIError(f"Kling task {task_id} timed out while processing.")
            await asyncio.sleep(KLING_POLL_INTERVAL_SECONDS)
            poll_response = await client.get(poll_path)
            poll_response.raise_for_status()
            poll_payload = poll_response.json()
            poll_data = poll_payload.get("data") or {}
            status = (poll_data.get("task_status") or "").lower()
            if status in {"succeed", "failed"}:
                if status == "failed":
                    message = poll_data.get("task_status_msg") or "Unknown Kling failure."
                    raise KlingAPIError(f"Kling task {task_id} failed: {message}")
                break

        assert poll_payload is not None
        task_result = (poll_data.get("task_result") or {}).get("videos") or []
        if not task_result:
            raise KlingAPIError(f"Kling task {task_id} completed without any video URLs.")
        video_url = task_result[0].get("url")
        if not video_url:
            raise KlingAPIError(f"Kling task {task_id} returned an empty video URL.")

        await _download_video(client, video_url, output_path)

    logger.debug("Kling task %s completed via %s", task_id, endpoint)
    return KlingRenderResult(
        task_id=task_id,
        endpoint=endpoint,
        request_payload=request_payload,
        final_response=poll_payload or payload,
        video_url=video_url,
        duration=poll_data.get("task_result", {}).get("videos", [{}])[0].get("duration"),
    )


__all__ = [
    "KlingAPIError",
    "KlingRenderResult",
    "encode_image_to_base64",
    "generate_kling_auth_token",
    "render_kling_clip",
    "resolution_to_aspect_ratio_code",
]
