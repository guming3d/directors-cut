"""Image helpers for start screens and uploaded assets."""

from __future__ import annotations

import base64
import io
import shutil
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image

from .config import (
    DATA_URL_PATTERN,
    SCENE_IMAGE_DIR,
    SCENE_IMAGE_PATTERN,
    SCENE_IMAGE_RESAMPLE,
    SCENE_IMAGE_SIZE,
    SCENE_TAIL_PATTERN,
    logger,
)
from .models import EncodedImagePayload
from .utils import resolution_to_size


def decode_image_payload(upload: EncodedImagePayload) -> tuple[Optional[str], bytes]:
    data_url = upload.data_url.strip()
    match = DATA_URL_PATTERN.match(data_url)
    if not match:
        raise ValueError("Unsupported image encoding; expected data URL with base64 content.")
    encoded = match.group("data")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (base64.binascii.Error, ValueError) as exc:  # type: ignore[attr-defined]
        raise ValueError("Failed to decode base64 image content.") from exc
    mime = match.group("mime") or upload.content_type
    return mime, data


def save_payload_as_png(upload: EncodedImagePayload, dest: Path) -> Path:
    mime, data = decode_image_payload(upload)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as img:
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            converted = img.convert(mode)
            converted.save(dest, format="PNG")
    except Exception:
        dest.write_bytes(data)
    logger.debug("Saved image upload %s (%s) -> %s", upload.filename, mime, dest)
    return dest


def _persist_scene_image_uploads(dest_dir: Path, uploads: Sequence[EncodedImagePayload]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx, upload in enumerate(uploads, start=1):
        filename = dest_dir / f"scene{idx}.png"
        save_payload_as_png(upload, filename)


def resolve_scene_image_dir(
    job_dir: Path,
    uploads: Sequence[EncodedImagePayload] | None,
    override_dir: Optional[str],
) -> Path:
    if uploads:
        dest_dir = job_dir / "scene_images"
        dest_dir.mkdir(parents=True, exist_ok=True)
        base_dir = (
            Path(override_dir).expanduser().resolve()
            if override_dir
            else SCENE_IMAGE_DIR
        )
        if base_dir.is_dir():
            for item in base_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_dir / item.name)
        elif override_dir:
            logger.warning(
                "Scene image override directory %s not found; using uploads only.",
                override_dir,
            )
        _persist_scene_image_uploads(dest_dir, uploads)
        return dest_dir

    if override_dir:
        resolved = Path(override_dir).expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(
                f"Scene image directory override not found: {resolved}"
            )
        return resolved

    return SCENE_IMAGE_DIR


def resize_scene_image(
    source: Path, dest: Path, target_size: tuple[int, int] | None = None
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        has_alpha = "A" in img.getbands()
        mode = "RGBA" if has_alpha else "RGB"
        converted = img.convert(mode)

        target_w, target_h = target_size or SCENE_IMAGE_SIZE
        src_w, src_h = converted.size
        if src_w == 0 or src_h == 0:
            raise ValueError(f"Invalid source image dimensions for {source}")

        scale = max(target_w / src_w, target_h / src_h)
        resized_w = max(1, int(round(src_w * scale)))
        resized_h = max(1, int(round(src_h * scale)))
        resized = converted.resize((resized_w, resized_h), SCENE_IMAGE_RESAMPLE)

        left = (resized_w - target_w) // 2
        top = (resized_h - target_h) // 2
        right = left + target_w
        bottom = top + target_h

        cropped = resized.crop((left, top, right, bottom))
        cropped.save(dest, format="PNG")


def _find_scene_image(scene_dir: Path, index: int) -> Optional[Path]:
    if not scene_dir.is_dir():
        return None
    for entry in scene_dir.iterdir():
        if not entry.is_file():
            continue
        match = SCENE_IMAGE_PATTERN.fullmatch(entry.name)
        if match and int(match.group(1)) == index:
            return entry
    return None


def prepare_sequence_initial_image(
    scene_dir: Path, tmp_dir: Path, resolution: str
) -> Optional[str]:
    source = _find_scene_image(scene_dir, 1)
    if source is None:
        return None
    dest = tmp_dir / "sequence_initial.png"
    resize_scene_image(source, dest, target_size=resolution_to_size(resolution))
    return str(dest)


def prepare_scene_start_images(
    scene_dir: Path, tmp_dir: Path, scene_count: int, resolution: str
) -> list[str]:
    if scene_count <= 0:
        return []
    if not scene_dir.is_dir():
        raise FileNotFoundError(
            f"Scene start screen directory not found: {scene_dir}. Provide one with scene1.png, scene2.png, ..."
        )

    matches: dict[int, Path] = {}
    for entry in scene_dir.iterdir():
        if not entry.is_file():
            continue
        match = SCENE_IMAGE_PATTERN.fullmatch(entry.name)
        if match:
            index = int(match.group(1))
            matches[index] = entry

    if not matches:
        raise RuntimeError(
            f"No scene start screens found in {scene_dir}. Expected files named scene1.png, scene2.png, ..."
        )

    expected = set(range(1, scene_count + 1))
    available = set(matches)
    missing = sorted(expected - available)
    if missing:
        raise RuntimeError(
            "Scene/image count mismatch: missing "
            + ", ".join(f"scene{idx}.png" for idx in missing)
            + f". Ensure there is at least one start image per scene in {scene_dir}."
        )

    prepared_dir = tmp_dir / "scene_start_images"
    target_size = resolution_to_size(resolution)
    target_w, target_h = target_size
    prepared_paths: list[str] = []
    for idx in range(1, scene_count + 1):
        source = matches[idx]
        dest = prepared_dir / f"scene{idx}_{target_w}x{target_h}.png"
        resize_scene_image(source, dest, target_size=target_size)
        prepared_paths.append(str(dest))
    return prepared_paths


def prepare_scene_tail_image(
    scene_dir: Path, tmp_dir: Path, scene_index: int, resolution: str
) -> Optional[str]:
    if not scene_dir.is_dir():
        return None
    candidate: Optional[Path] = None
    for entry in scene_dir.iterdir():
        if not entry.is_file():
            continue
        match = SCENE_TAIL_PATTERN.fullmatch(entry.name)
        if match and int(match.group(1)) == scene_index:
            candidate = entry
            break
    if candidate is None:
        return None
    target_size = resolution_to_size(resolution)
    prepared_dir = tmp_dir / "scene_tail_images"
    dest = prepared_dir / f"scene{scene_index}_tail_{target_size[0]}x{target_size[1]}.png"
    resize_scene_image(candidate, dest, target_size=target_size)
    return str(dest)


__all__ = [
    "decode_image_payload",
    "prepare_scene_start_images",
    "prepare_sequence_initial_image",
    "prepare_scene_tail_image",
    "resolve_scene_image_dir",
    "resize_scene_image",
    "save_payload_as_png",
]
