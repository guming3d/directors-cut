"""Character extraction and reference image generation for visual consistency."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Optional

from .config import (
    AZURE_IMAGE_DEPLOYMENT,
    DIRECTOR_REFINEMENT_DEPLOYMENT,
    azure_image_client,
    azure_text_client,
    extract_response_text,
    logger,
)
from .image_utils import select_supported_image_size, trim_image_bytes
from .models import CharacterReference
from .prompts import get_visual_style_description, render_prompt_template

MAX_CHARACTERS = 10


async def extract_characters(script_text: str) -> list[CharacterReference]:
    """Use GPT to extract characters with visual descriptions from a storyboard script.

    Returns an empty list on failure so the workflow can continue without character refs.
    """
    client = azure_text_client()
    if client is None:
        logger.info("Text client not configured; skipping character extraction.")
        return []

    if not DIRECTOR_REFINEMENT_DEPLOYMENT:
        logger.info("No text deployment configured; skipping character extraction.")
        return []

    system_prompt = render_prompt_template("character_extract_system")

    def _call() -> str:
        response = client.responses.create(
            model=DIRECTOR_REFINEMENT_DEPLOYMENT,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": script_text}]},
            ],
            max_output_tokens=2000,
        )
        return extract_response_text(response)

    try:
        raw = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.warning("Character extraction LLM call failed: %s", exc)
        return []

    logger.info("Character extraction raw response (%d chars): %.200s", len(raw), raw)

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse character extraction response: %s — raw: %.300s", exc, raw)
        return []

    if not isinstance(parsed, list):
        logger.warning("Character extraction returned non-list: %s", type(parsed).__name__)
        return []

    characters: list[CharacterReference] = []
    for item in parsed[:MAX_CHARACTERS]:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        description = (item.get("description") or "").strip()
        if name and description:
            characters.append(CharacterReference(name=name, description=description))

    return characters


async def generate_character_ref_image(
    character: CharacterReference,
    resolution: str,
    output_dir: Path,
    visual_style: str = "3d_animation",
) -> Optional[str]:
    """Generate a reference portrait for one character. Idempotent: skips if file exists.

    Returns the image path on success, None on failure.
    """
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "" for c in character.name)
    safe_name = safe_name.strip().replace(" ", "_")[:50] or "character"
    target_path = output_dir / f"{safe_name}_ref.png"

    if target_path.is_file() and target_path.stat().st_size > 0:
        character.image_path = str(target_path)
        return str(target_path)

    client = azure_image_client()
    if client is None:
        logger.warning("Image client not configured; skipping ref image for %s.", character.name)
        return None

    style_desc = get_visual_style_description(visual_style)
    image_prompt = render_prompt_template(
        "character_ref_image",
        character_name=character.name,
        character_description=character.description,
        resolution=resolution,
        visual_style=style_desc,
    )
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

    logger.info("Generating reference image for character '%s' -> %s", character.name, target_path)

    try:
        raw_bytes = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.warning("Reference image generation failed for %s: %s", character.name, exc)
        return None

    try:
        trimmed = trim_image_bytes(raw_bytes, resolution)
    except Exception as exc:
        logger.warning("Image trimming failed for %s: %s", character.name, exc)
        trimmed = raw_bytes

    output_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(trimmed)
    logger.info("Character reference image saved: %s (%d bytes)", target_path, len(trimmed))
    character.image_path = str(target_path)
    return str(target_path)


async def prepare_character_refs(
    script_text: str,
    resolution: str,
    output_dir: Path,
    visual_style: str = "3d_animation",
) -> list[CharacterReference]:
    """Orchestrate character extraction and reference image generation.

    Best-effort: returns whatever characters were successfully processed.
    Individual failures do not stop the workflow.
    """
    try:
        characters = await extract_characters(script_text)
    except Exception as exc:
        logger.warning("Character extraction failed entirely: %s", exc)
        return []

    if not characters:
        return []

    logger.info("Extracted %d character(s) from script; generating reference images.", len(characters))
    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        generate_character_ref_image(character, resolution, output_dir, visual_style)
        for character in characters
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = []
    for character, result in zip(characters, results):
        if isinstance(result, Exception):
            logger.warning("Reference image failed for %s: %s", character.name, result)
        elif result is not None:
            successful.append(character)
        else:
            successful.append(character)

    return successful


__all__ = [
    "extract_characters",
    "generate_character_ref_image",
    "prepare_character_refs",
]
