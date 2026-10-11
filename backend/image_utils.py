"""Shared helpers for Azure image generation and post-processing."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from .config import SCENE_IMAGE_RESAMPLE, logger
from .utils import resolution_to_size

PORTRAIT_IMAGE_SIZE = "1024x1536"
LANDSCAPE_IMAGE_SIZE = "1536x1024"
AZURE_IMAGE_SIZE_MAP = {
    "720x1280": PORTRAIT_IMAGE_SIZE,
    "1280x720": LANDSCAPE_IMAGE_SIZE,
}
# Share of a vertical crop taken from the top. Heads and ears sit near the top
# edge far more often than anything important sits at the very bottom.
CROP_TOP_SHARE = 0.25


def select_supported_image_size(resolution: str) -> str:
    """Map an arbitrary resolution to a size accepted by gpt-image-1."""

    normalized = (resolution or "").strip().lower()
    mapped = AZURE_IMAGE_SIZE_MAP.get(normalized)
    if mapped:
        return mapped

    try:
        width_str, height_str = normalized.split("x", 1)
        width = int(width_str)
        height = int(height_str)
    except (ValueError, AttributeError):
        logger.info(
            "Unrecognized resolution '%s'; defaulting to %s for Azure image generation.",
            resolution,
            PORTRAIT_IMAGE_SIZE,
        )
        return PORTRAIT_IMAGE_SIZE

    if width <= height:
        chosen = PORTRAIT_IMAGE_SIZE
    else:
        chosen = LANDSCAPE_IMAGE_SIZE

    logger.info(
        "Azure image API does not accept %s; falling back to %s for generation.",
        resolution,
        chosen,
    )
    return chosen


def framing_note(resolution: str) -> str:
    """Tell the image model which edges ``trim_image_bytes`` will cut off."""

    target_w, target_h = resolution_to_size(resolution)
    gen_w, gen_h = (int(value) for value in select_supported_image_size(resolution).split("x"))
    target_ratio = target_w / target_h
    gen_ratio = gen_w / gen_h
    if abs(gen_ratio - target_ratio) < 0.01:
        return f"The image is used uncropped at {target_w}x{target_h}."
    if gen_ratio < target_ratio:
        cut = 1 - (gen_w / target_ratio) / gen_h
        top = round(cut * CROP_TOP_SHARE * 100)
        bottom = round(cut * (1 - CROP_TOP_SHARE) * 100)
        strips = f"the top {top}% and bottom {bottom}% of the image"
        band = "ear tips, hair, tails, and feet"
    else:
        side = round((1 - (gen_h * target_ratio) / gen_w) / 2 * 100)
        strips = f"the left {side}% and right {side}% of the image"
        band = "hands, tails, and key objects"
    return (
        f"The image is cropped to {target_w}x{target_h}: {strips} are cut off. Fill those strips "
        f"with background only (wall, sky, floor) and place the whole subject, including {band}, "
        "inside the remaining area with a little space to spare."
    )


def trim_image_bytes(image_bytes: bytes, resolution: str) -> bytes:
    """Crop and resize Azure output to the exact workflow resolution.

    Side crops are centered; vertical crops take ``CROP_TOP_SHARE`` from the top.
    """

    target_w, target_h = resolution_to_size(resolution)
    with Image.open(BytesIO(image_bytes)) as img:
        has_alpha = "A" in img.getbands()
        mode = "RGBA" if has_alpha else "RGB"
        converted = img.convert(mode)
        src_w, src_h = converted.size
        if src_w <= 0 or src_h <= 0:
            raise ValueError("Invalid generated image dimensions.")

        if src_w < target_w or src_h < target_h:
            logger.warning(
                "Generated image %sx%s is smaller than requested %s; scaling may reduce quality.",
                src_w,
                src_h,
                resolution,
            )
            resized = converted.resize((target_w, target_h), SCENE_IMAGE_RESAMPLE)
        else:
            target_ratio = target_w / target_h
            src_ratio = src_w / src_h
            if abs(src_ratio - target_ratio) < 1e-4:
                cropped = converted
            elif src_ratio > target_ratio:
                new_width = int(src_h * target_ratio)
                left = max(0, (src_w - new_width) // 2)
                right = left + new_width
                cropped = converted.crop((left, 0, right, src_h))
            else:
                new_height = int(src_w / target_ratio)
                top = max(0, int((src_h - new_height) * CROP_TOP_SHARE))
                bottom = top + new_height
                cropped = converted.crop((0, top, src_w, bottom))
            resized = cropped.resize((target_w, target_h), SCENE_IMAGE_RESAMPLE)

        buffer = BytesIO()
        resized.save(buffer, format="PNG")
        return buffer.getvalue()


__all__ = ["framing_note", "select_supported_image_size", "trim_image_bytes"]
