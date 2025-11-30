"""AI-assisted first frame generation using GPT-5 and gpt-image-1."""

from __future__ import annotations

import asyncio
import base64
import textwrap
from io import BytesIO
from pathlib import Path

from .config import (
    AZURE_IMAGE_DEPLOYMENT,
    GPT5_REFINEMENT_DEPLOYMENT,
    azure_image_client,
    azure_text_client,
    extract_response_text,
    logger,
)
from .image_utils import select_supported_image_size, trim_image_bytes
from .models import ScenePlan, VideoWorkflowRequest
from .utils import resolution_to_size

def _fallback_image_prompt(plan: ScenePlan, resolution: str) -> str:
    return (
        "Create a single still frame that opens the scene below. "
        f"Match the {resolution} resolution, keep characters consistent, and avoid motion blur. "
        "Lean into strong lighting and framing that sets the tone for the first second of the clip.\n\n"
        f"{plan.prompt_text}"
    )


async def build_first_frame_prompt(plan: ScenePlan, resolution: str) -> str:
    """Use GPT-5 to craft a gpt-image-1 prompt tailored to the scene opening."""

    if not GPT5_REFINEMENT_DEPLOYMENT:
        return _fallback_image_prompt(plan, resolution)

    client = azure_text_client()
    if client is None:
        return _fallback_image_prompt(plan, resolution)

    system_prompt = textwrap.dedent(
        """
        You turn Azure Sora2 video prompts into a single gpt-image-1 prompt that captures the very first frame of the scene.
        Preserve the subjects, environment, and mood; make the composition stable for the opening second before motion begins.
        Keep the output concise (one paragraph) and avoid quoting or adding meta instructions.
        """
    ).strip()
    user_prompt = (
        f"Scene label: {plan.label}\n"
        f"Target resolution: {resolution}\n"
        "Sora2 prompt:\n"
        f"{plan.prompt_text}\n\n"
        "Return one gpt-image-1 prompt suitable for the first frame."
    )

    def _call() -> str:
        response = client.responses.create(
            model=GPT5_REFINEMENT_DEPLOYMENT,
            input=[
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
            ],
            max_output_tokens=900,
        )
        return extract_response_text(response)

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive log path
        logger.warning("GPT-5 failed to draft first-frame prompt for %s: %s", plan.label, exc)
        return _fallback_image_prompt(plan, resolution)

    cleaned = refined.strip()
    return cleaned or _fallback_image_prompt(plan, resolution)


async def _generate_image_bytes(image_prompt: str, resolution: str) -> bytes:
    client = azure_image_client()
    if client is None:
        raise RuntimeError("Azure OpenAI client not configured for image generation.")

    azure_size = select_supported_image_size(resolution)

    def _call() -> bytes:
        response = client.images.generate(
            model=AZURE_IMAGE_DEPLOYMENT,
            prompt=image_prompt,
            n=1,
            size=azure_size,
        )
        if not response.data:
            raise RuntimeError("Image generation returned no data payload.")
        return base64.b64decode(response.data[0].b64_json)

    return await asyncio.to_thread(_call)


async def generate_first_frame_image(plan: ScenePlan, request: VideoWorkflowRequest) -> str:
    """Create and persist an opening frame for the scene, returning the file path."""

    base_dir = Path(request.first_frame_dir or Path(request.output_dir) / "first_frames")
    base_dir.mkdir(parents=True, exist_ok=True)
    target_path = base_dir / f"scene_{plan.order:02d}_first_frame.png"

    if target_path.is_file() and target_path.stat().st_size > 0:
        return str(target_path)

    image_prompt = await build_first_frame_prompt(plan, request.resolution)
    raw_image = await _generate_image_bytes(image_prompt, request.resolution)
    trimmed_image = trim_image_bytes(raw_image, request.resolution)
    target_path.write_bytes(trimmed_image)

    return str(target_path)


__all__ = ["build_first_frame_prompt", "generate_first_frame_image"]
