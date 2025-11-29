"""General utilities for the Sora workflow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .config import OUTPUT_DIR, SCENE_IMAGE_SIZE, STATIC_URL_PREFIX, logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required script file: {path}")
    return path.read_text(encoding="utf-8").strip()


def slugify(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "scene"


def normalize_script_text(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Script text cannot be empty.")
    if not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def resolution_to_size(resolution: str) -> tuple[int, int]:
    """Convert a WIDTHxHEIGHT string into an integer tuple with validation."""

    try:
        width_str, height_str = resolution.lower().split("x", 1)
        width = int(width_str)
        height = int(height_str)
    except (AttributeError, ValueError):
        logger.warning(
            "Invalid resolution '%s'; falling back to default scene image size %sx%s.",
            resolution,
            SCENE_IMAGE_SIZE[0],
            SCENE_IMAGE_SIZE[1],
        )
        return SCENE_IMAGE_SIZE

    if width <= 0 or height <= 0:
        logger.warning(
            "Non-positive resolution '%s'; falling back to default scene image size %sx%s.",
            resolution,
            SCENE_IMAGE_SIZE[0],
            SCENE_IMAGE_SIZE[1],
        )
        return SCENE_IMAGE_SIZE

    return width, height


def escape_for_concat(path: Path) -> str:
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


async def run_subprocess(
    cmd: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode(errors="ignore")
    stderr = stderr_bytes.decode(errors="ignore")
    if process.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)} failed ({process.returncode}): {stderr or stdout}"
        )
    return stdout, stderr


async def extract_last_frame(video_path: Path, dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "select=eq(n\\,N-1)",
        "-vframes",
        "1",
        str(dest_path),
    ]
    await run_subprocess(cmd)
    return dest_path


def build_static_asset_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    file_path = Path(path)
    try:
        rel = file_path.relative_to(OUTPUT_DIR)
    except ValueError:
        return None
    return f"{STATIC_URL_PREFIX}/{rel.as_posix()}"


__all__ = [
    "build_static_asset_url",
    "escape_for_concat",
    "extract_last_frame",
    "normalize_script_text",
    "read_text",
    "resolution_to_size",
    "run_subprocess",
    "slugify",
    "utc_now",
]
