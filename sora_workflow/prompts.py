"""Script parsing and prompt refinement helpers."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Literal

from .config import (
    DIRECTOR_REFINEMENT_DEPLOYMENT,
    GPT5_REFINEMENT_DEPLOYMENT,
    HEADER_PATTERN,
    RESOLUTION_PATTERN,
    SORA_PROMPT_GUIDE_URL,
    logger,
    azure_text_client,
    extract_response_text,
)
from .utils import slugify


def parse_script(script_text: str):
    from .models import SceneDraft

    drafts: list[SceneDraft] = []
    current: SceneDraft | None = None
    order = 0

    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        header_match = HEADER_PATTERN.match(line)
        if header_match:
            if current:
                drafts.append(current)
            order += 1
            header_text = header_match.group(1)
            number_text = header_match.group(2)
            title_match = re.search(r"\((.*?)\)", header_text)
            title = title_match.group(1) if title_match else header_text
            current = SceneDraft(
                order=order,
                scene_number=int(number_text or order),
                title=title.strip(),
                description_lines=[],
            )
            continue
        if current and line:
            line = line.lstrip("*").strip()
            if line:
                current.description_lines.append(line)

    if current:
        drafts.append(current)
    return drafts


def _orientation_suffix(resolution: str) -> str:
    try:
        width_str, height_str = resolution.lower().split("x", 1)
        width = int(width_str)
        height = int(height_str)
    except (ValueError, AttributeError):
        return ""
    if width > height:
        return " (landscape orientation)"
    if height > width:
        return " (portrait orientation)"
    return " (square aspect ratio)"


def build_prompt_text(
    title: str, description: str, duration: int, resolution: str
) -> str:
    bullet_lines = "\n".join(
        f"- {line.strip()}" for line in description.splitlines() if line.strip()
    )
    return (
        f"Scene title: {title}\n"
        f"Clip duration: {duration} seconds (fixed length).\n"
        f"Output resolution: {resolution}{_orientation_suffix(resolution)}.\n"
        "Character continuity: keep the same heroine (Brown hair, casual flight outfit) and hero (casual outfit) across clips.\n"
        "Visual direction:\n"
        f"{bullet_lines}\n"
        "Cinematic tone: energetic, emotional, with tasteful motion graphics.\n"
        "Audio: ambient sound design only; no background music."
    )


async def refine_scene_description(title: str, description: str, duration: int) -> str:
    normalized = description.strip()
    if not normalized:
        return description

    client = azure_text_client()
    if client is None:
        return description

    system_prompt = (
        "You are an expert cinematic prompt engineer for Azure OpenAI Sora2. "
        f"Incorporate the best practices from {SORA_PROMPT_GUIDE_URL} to polish scene descriptions. "
        f"Produce concise bullet-point guidance that covers camera direction, subject detail, motion, and audio cues suitable for a {duration}-second clip."
    )
    user_prompt = (
        f"Scene title: {title}\n"
        f"Clip length: {duration} seconds.\n"
        "Original storyboard notes:\n"
        f"{normalized}\n\n"
        f"Rewrite the notes into 4-6 focused bullet points optimized for Sora2 at {duration} seconds."
    )

    max_tokens_str = os.getenv("DIRECTOR_REFINEMENT_MAX_TOKENS", "900")
    try:
        max_tokens = int(max_tokens_str)
    except ValueError:
        max_tokens = 900

    def _call() -> str:
        response = client.responses.create(
            model=DIRECTOR_REFINEMENT_DEPLOYMENT,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_prompt}],
                },
            ],
            max_output_tokens=max_tokens,
            temperature=0.4,
        )
        return extract_response_text(response)

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("Prompt refinement failed for %s: %s", title, exc)
        return description

    lines = [line.strip() for line in refined.splitlines() if line.strip()]
    if not lines:
        return description

    cleaned: list[str] = []
    for line in lines:
        stripped = line.lstrip("-•0123456789. ").strip()
        cleaned.append(stripped or line)

    return "\n".join(cleaned)


async def refine_text_with_gpt5(content: str, mode: Literal["workflow", "single"]) -> str:
    normalized = content.strip()
    if not normalized:
        return content

    deployment = GPT5_REFINEMENT_DEPLOYMENT
    if not deployment:
        return content

    client = azure_text_client()
    if client is None:
        return content

    if mode == "workflow":
        system_prompt = (
            "You are GPT-5 helping polish multi-scene storyboards for Azure OpenAI Sora2. "
            "Keep every scene heading in the exact format **Scene X (Title)** while rewriting the narrative "
            "under each heading into vivid, production-ready copy. Preserve the number and order of scenes."
        )
        user_tone = (
            "Rewrite the storyboard below. Keep headings identical, but upgrade the scene descriptions into 2-4 polished sentences "
            "with strong cinematic cues (camera moves, subject, motion, lighting). Separate each scene block with a blank line."
        )
    else:
        system_prompt = (
            "You are GPT-5 optimizing single-scene prompts for Azure OpenAI Sora2 video generation."
        )
        user_tone = (
            "Refine the following prompt into one paragraph that clearly describes subject, motion, camera, and mood."
        )

    user_prompt = f"{user_tone}\n\n---\n{normalized}\n---"

    max_tokens = 1400 if mode == "workflow" else 600

    def _call() -> str:
        response = client.responses.create(
            model=deployment,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "text", "text": system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_prompt}],
                },
            ],
            max_output_tokens=max_tokens,
            temperature=0.35,
        )
        return extract_response_text(response)

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("GPT-5 refinement failed (%s mode): %s", mode, exc)
        return content

    cleaned = refined.strip()
    return cleaned or content


__all__ = [
    "build_prompt_text",
    "parse_script",
    "refine_scene_description",
    "refine_text_with_gpt5",
    "slugify",
]
