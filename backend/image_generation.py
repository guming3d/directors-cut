"""Pluggable helpers for image generation backends used in reproduce workflows."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .config import (
    AZURE_IMAGE_DEPLOYMENT,
    DEFAULT_IMAGE_GENERATOR,
    DEFAULT_SORA_RESOLUTION,
    KLING_POLL_INTERVAL_SECONDS,
    KLING_POLL_TIMEOUT_SECONDS,
    SUPPORTED_IMAGE_GENERATORS,
    azure_image_client,
    logger,
)
from .image_utils import select_supported_image_size, trim_image_bytes
from .kling import (
    KLING_API_BASE_URL,
    encode_image_to_base64,
    generate_kling_auth_token,
    resolution_to_aspect_ratio_code,
)


class ImageGenerationError(RuntimeError):
    """Raised when an image generation backend fails permanently."""


class ImageGenerator(Protocol):
    name: str

    async def generate(
        self,
        *,
        prompt: str,
        resolution: str,
        image_path: Path | None = None,
    ) -> bytes:
        """Produce an image given a prompt and optional reference frame."""


@dataclass(slots=True)
class AzureImageGenerator:
    name: str = "gpt-image-1"

    async def generate(
        self,
        *,
        prompt: str,
        resolution: str,
        image_path: Path | None = None,
    ) -> bytes:
        if image_path is None:
            raise ImageGenerationError("Azure image edits require a source frame.")
        client = azure_image_client()
        if client is None:
            raise ImageGenerationError("Azure image client is not configured.")
        azure_size = select_supported_image_size(resolution or DEFAULT_SORA_RESOLUTION)

        def _call() -> bytes:
            with image_path.open("rb") as fh:
                response = client.images.edit(  # type: ignore[attr-defined]
                    model=AZURE_IMAGE_DEPLOYMENT,
                    prompt=prompt,
                    image=fh,
                    size=azure_size,
                    input_fidelity="high",
                    background="auto",
                )
            data = getattr(response, "data", None) or []
            if not data:
                raise ImageGenerationError("Azure image edit returned no payload.")
            payload = data[0].b64_json
            if not payload:
                raise ImageGenerationError("Azure image edit response missing base64 payload.")
            return trim_image_bytes(
                base64.b64decode(payload),
                resolution or DEFAULT_SORA_RESOLUTION,
            )

        raw_bytes = await asyncio.to_thread(_call)
        return raw_bytes


def _kling_headers() -> dict[str, str]:
    token, _, _ = generate_kling_auth_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _kling_resolution_hint(resolution: str) -> str:
    return "1k"


async def _download_image(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    return response.content


async def _lookup_kling_image_task(client: httpx.AsyncClient, task_id: str) -> dict | None:
    page = 1
    page_size = 100
    while page <= 10:
        response = await client.get(
            "/v1/images/generations",
            params={"pageNum": page, "pageSize": page_size},
        )
        response.raise_for_status()
        payload = response.json()
        for entry in payload.get("data") or []:
            if entry.get("task_id") == task_id:
                return entry
        if not payload.get("data") or len(payload.get("data") or []) < page_size:
            return None
        page += 1
    return None


async def _fetch_kling_image_status(client: httpx.AsyncClient, task_id: str) -> dict | None:
    path = f"/v1/images/generations/{task_id}"
    try:
        response = await client.get(path)
        if response.status_code == 404:
            return await _lookup_kling_image_task(client, task_id)
        response.raise_for_status()
        payload = response.json()
        return payload.get("data")
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return await _lookup_kling_image_task(client, task_id)
        raise


@dataclass(slots=True)
class KlingImageGenerator:
    name: str = "kling"

    async def generate(
        self,
        *,
        prompt: str,
        resolution: str,
        image_path: Path | None = None,
    ) -> bytes:
        if not KLING_API_BASE_URL:
            raise ImageGenerationError("KLING_API_BASE_URL is not configured.")
        headers = _kling_headers()
        aspect_ratio = resolution_to_aspect_ratio_code(resolution or DEFAULT_SORA_RESOLUTION)
        payload: dict[str, object] = {
            "prompt": prompt,
            "model_name": "kling-v2",
            "aspect_ratio": aspect_ratio,
            "resolution": _kling_resolution_hint(resolution or DEFAULT_SORA_RESOLUTION),
        }
        if image_path is not None:
            payload["image"] = encode_image_to_base64(image_path)

        timeout = httpx.Timeout(KLING_POLL_TIMEOUT_SECONDS, connect=30.0)
        async with httpx.AsyncClient(
            base_url=KLING_API_BASE_URL,
            headers=headers,
            timeout=timeout,
        ) as client:
            try:
                create_response = await client.post("/v1/images/generations", json=payload)
                create_response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # pragma: no cover - network failure
                body = exc.response.text[:500] if exc.response is not None else str(exc)
                raise ImageGenerationError(
                    f"Kling image create request failed: {body}"
                ) from exc
            except httpx.RequestError as exc:  # pragma: no cover - network failure
                raise ImageGenerationError(f"Kling image create network error: {exc}") from exc
            create_payload = create_response.json()
            if create_payload.get("code") != 0:
                raise ImageGenerationError(f"Kling image create failed: {create_payload}")
            data = create_payload.get("data") or {}
            task_id = data.get("task_id")
            if not task_id:
                raise ImageGenerationError("Kling image generation did not return a task_id.")

            deadline = asyncio.get_event_loop().time() + KLING_POLL_TIMEOUT_SECONDS
            task_record: dict | None = data
            while True:
                status = (task_record.get("task_status") or "").lower() if task_record else ""
                if status in {"succeed", "failed"}:
                    if status == "failed":
                        raise ImageGenerationError(
                            f"Kling image task {task_id} failed: {task_record.get('task_status_msg')}"
                        )
                    break
                if asyncio.get_event_loop().time() > deadline:
                    raise ImageGenerationError(f"Kling image task {task_id} timed out.")
                await asyncio.sleep(KLING_POLL_INTERVAL_SECONDS)
                task_record = await _fetch_kling_image_status(client, task_id)
                if task_record is None:
                    logger.debug("Kling image task %s not yet visible in listings.", task_id)

            task_result = ((task_record or {}).get("task_result") or {}).get("images") or []
            if not task_result:
                raise ImageGenerationError(f"Kling image task {task_id} returned no images.")
            image_url = task_result[0].get("url")
            if not image_url:
                raise ImageGenerationError(f"Kling image task {task_id} returned an empty image URL.")
            image_bytes = await _download_image(client, image_url)
        return trim_image_bytes(image_bytes, resolution or DEFAULT_SORA_RESOLUTION)


_GENERATOR_CACHE: dict[str, ImageGenerator] = {}


def _normalize_backend(name: str | None) -> str:
    if not name:
        return DEFAULT_IMAGE_GENERATOR
    value = name.strip().lower()
    if value not in SUPPORTED_IMAGE_GENERATORS:
        return DEFAULT_IMAGE_GENERATOR
    return value


def get_image_generator(name: str | None) -> ImageGenerator:
    backend = _normalize_backend(name)
    if backend not in _GENERATOR_CACHE:
        if backend == "gpt-image-1":
            _GENERATOR_CACHE[backend] = AzureImageGenerator()
        elif backend == "kling":
            _GENERATOR_CACHE[backend] = KlingImageGenerator()
        else:  # pragma: no cover - defensive default
            raise ImageGenerationError(f"Unsupported image generator: {backend}")
    return _GENERATOR_CACHE[backend]


__all__ = [
    "AzureImageGenerator",
    "ImageGenerationError",
    "ImageGenerator",
    "KlingImageGenerator",
    "get_image_generator",
]
