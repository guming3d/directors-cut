"""Gemini NanoBanana helpers for concept art and first-frame generation."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Iterable, Optional, Sequence

from PIL import Image, ImageDraw

from .config import (
    GEMINI_API_KEY,
    GEMINI_NANOBANANA_ASPECT_RATIO,
    GEMINI_NANOBANANA_DRY_RUN,
    GEMINI_NANOBANANA_MODEL,
    GEMINI_NANOBANANA_RESOLUTION,
    logger,
)
from .utils import resolution_to_size


def aspect_ratio_from_resolution(resolution: str, fallback: str = "9:16") -> str:
    """Return a reduced aspect ratio string from a WIDTHxHEIGHT resolution."""

    try:
        width_text, height_text = resolution.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            raise ValueError("Non-positive dimensions")
        gcd = math.gcd(width, height)
        return f"{width // gcd}:{height // gcd}"
    except Exception:
        return fallback


def _write_placeholder_image(prompt: str, output_path: Path, resolution_hint: Optional[str]) -> Path:
    """Persist a simple placeholder image when Gemini is unavailable."""

    width, height = resolution_to_size(resolution_hint or "720x1280")
    image = Image.new("RGB", (width, height), color=(242, 244, 247))
    draw = ImageDraw.Draw(image)
    text = (prompt or "placeholder").strip() or "placeholder"
    snippet = text[:240] + ("..." if len(text) > 240 else "")

    # naive wrapping to avoid overflowing the canvas
    lines: list[str] = []
    words = snippet.split()
    current: list[str] = []
    max_chars = 38
    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) <= max_chars:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))

    y = 20
    for line in lines[:14]:
        draw.text((20, y), line, fill=(67, 79, 95))
        y += 24

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return output_path


async def generate_nanobanana_image(
    prompt: str,
    image_paths: Iterable[str | Path] | None,
    output_path: Path,
    aspect_ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    resolution_hint: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> Path:
    """Call the Gemini NanoBanana model to render an image.

    Falls back to writing a placeholder PNG when the Gemini client or API key
    is unavailable. The function runs the blocking Gemini call in a worker
    thread to avoid stalling the event loop.
    """

    output_path = output_path.expanduser().resolve()
    selected_aspect = (
        aspect_ratio
        or GEMINI_NANOBANANA_ASPECT_RATIO
        or aspect_ratio_from_resolution(resolution_hint or "720x1280")
    )
    selected_size = image_size or GEMINI_NANOBANANA_RESOLUTION
    use_dry_run = GEMINI_NANOBANANA_DRY_RUN or bool(dry_run) or not GEMINI_API_KEY

    if use_dry_run:
        logger.info(
            "Gemini NanoBanana dry run enabled; writing placeholder image to %s.",
            output_path,
        )
        return _write_placeholder_image(prompt, output_path, resolution_hint)

    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover - import guarding
        logger.warning("google-genai import failed (%s); falling back to placeholder image.", exc)
        return _write_placeholder_image(prompt, output_path, resolution_hint)

    contents: list[object] = [prompt]
    for path in image_paths or []:
        try:
            with Image.open(path) as img:
                contents.append(img.copy())
        except Exception as exc:  # pragma: no cover - defensive log
            logger.warning("Failed to load reference image %s: %s", path, exc)

    def _call() -> Path:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_NANOBANANA_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=selected_aspect,
                    image_size=selected_size,
                ),
            ),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        for part in response.parts:
            if part.text:
                continue
            image = part.as_image()
            if image is None:
                continue
            image.save(output_path)
            return output_path
        raise RuntimeError("Gemini response did not include an image.")

    try:
        return await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("Gemini NanoBanana call failed: %s", exc)
        return _write_placeholder_image(prompt, output_path, resolution_hint)


__all__: Sequence[str] = [
    "aspect_ratio_from_resolution",
    "generate_nanobanana_image",
]
