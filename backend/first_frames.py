"""AI-assisted first frame generation using the reasoning model and GPT Image 2.5."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from .config import (
    AZURE_IMAGE_DEPLOYMENT,
    REASONING_DEPLOYMENT,
    azure_image_client,
    azure_text_client,
    extract_response_text,
    logger,
    normalize_video_provider,
    reasoning_request_options,
)
from .image_utils import select_supported_image_size, trim_image_bytes
from .models import CharacterReference, ScenePlan, VideoWorkflowRequest
from .prompts import get_visual_style_description, provider_profile, render_prompt_template

MAX_CHARACTER_REF_IMAGES = 4

# Sora 2 rejects start images that contain human faces, which wastes the render.
SORA_FACE_RULE = (
    "Show no human face: people appear from behind, in silhouette, or partly out of frame."
)


def _face_rule(video_provider: str | None) -> str:
    return SORA_FACE_RULE if normalize_video_provider(video_provider) == "sora2" else ""


def _fallback_image_prompt(
    plan: ScenePlan, resolution: str, visual_style: str = "3d_animation", video_provider: str | None = None
) -> str:
    style_desc = get_visual_style_description(visual_style)
    return render_prompt_template(
        "first_frame_fallback",
        resolution=resolution,
        prompt_text=plan.prompt_text,
        visual_style=style_desc,
        face_rule=_face_rule(video_provider),
    )


async def build_first_frame_prompt(
    plan: ScenePlan,
    resolution: str,
    visual_style: str = "3d_animation",
    video_provider: str | None = None,
) -> str:
    """Use the reasoning model to craft an image prompt tailored to the scene opening."""

    client = azure_text_client()
    if client is None:
        return _fallback_image_prompt(plan, resolution, visual_style, video_provider)

    style_desc = get_visual_style_description(visual_style)
    system_prompt = render_prompt_template("first_frame_system")
    user_prompt = render_prompt_template(
        "first_frame_user",
        scene_label=plan.label,
        video_model=provider_profile(video_provider).label,
        resolution=resolution,
        prompt_text=plan.prompt_text,
        visual_style=style_desc,
        face_rule=_face_rule(video_provider),
    )

    def _call() -> str:
        response = client.responses.create(
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
            **reasoning_request_options(900),
        )
        return extract_response_text(response)

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive log path
        logger.warning("%s failed to draft first-frame prompt for %s: %s", REASONING_DEPLOYMENT, plan.label, exc)
        return _fallback_image_prompt(plan, resolution, visual_style, video_provider)

    cleaned = refined.strip()
    return cleaned or _fallback_image_prompt(plan, resolution, visual_style, video_provider)


async def _generate_image_bytes(
    image_prompt: str,
    resolution: str,
    character_ref_paths: list[str] | None = None,
) -> bytes:
    client = azure_image_client()
    if client is None:
        raise RuntimeError("FOUNDRY_GPT_IMAGE_API_KEY is not configured for image generation.")

    azure_size = select_supported_image_size(resolution)
    valid_refs = [
        p for p in (character_ref_paths or [])
        if Path(p).is_file() and Path(p).stat().st_size > 0
    ][:MAX_CHARACTER_REF_IMAGES]

    def _call_with_refs() -> bytes:
        ref_files = [open(p, "rb") for p in valid_refs]
        try:
            response = client.images.edit(
                model=AZURE_IMAGE_DEPLOYMENT,
                image=ref_files,
                prompt=image_prompt,
                n=1,
                size=azure_size,
            )
        finally:
            for fh in ref_files:
                fh.close()
        if not response.data:
            raise RuntimeError("Image edit returned no data payload.")
        return base64.b64decode(response.data[0].b64_json)

    def _call_generate() -> bytes:
        response = client.images.generate(
            model=AZURE_IMAGE_DEPLOYMENT,
            prompt=image_prompt,
            n=1,
            size=azure_size,
        )
        if not response.data:
            raise RuntimeError("Image generation returned no data payload.")
        return base64.b64decode(response.data[0].b64_json)

    if valid_refs:
        try:
            return await asyncio.to_thread(_call_with_refs)
        except Exception as exc:
            logger.warning(
                "images.edit with %d character refs failed, falling back to images.generate: %s",
                len(valid_refs),
                exc,
            )
            return await asyncio.to_thread(_call_generate)

    return await asyncio.to_thread(_call_generate)


async def generate_first_frame_image(
    plan: ScenePlan,
    request: VideoWorkflowRequest,
    character_refs: list[CharacterReference] | None = None,
) -> str:
    """Create and persist an opening frame for the scene, returning the file path."""

    base_dir = Path(request.first_frame_dir or Path(request.output_dir) / "first_frames")
    base_dir.mkdir(parents=True, exist_ok=True)
    target_path = base_dir / f"scene_{plan.order:02d}_first_frame.png"

    if target_path.is_file() and target_path.stat().st_size > 0:
        return str(target_path)

    image_prompt = await build_first_frame_prompt(
        plan, request.resolution, request.visual_style, request.video_provider
    )

    ref_paths: list[str] = []
    for ref in (character_refs or []):
        if ref.image_path and Path(ref.image_path).is_file():
            ref_paths.append(ref.image_path)

    raw_image = await _generate_image_bytes(image_prompt, request.resolution, ref_paths or None)
    trimmed_image = trim_image_bytes(raw_image, request.resolution)
    target_path.write_bytes(trimmed_image)

    return str(target_path)


__all__ = ["build_first_frame_prompt", "generate_first_frame_image"]
