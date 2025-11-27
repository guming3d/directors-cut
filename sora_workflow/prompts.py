"""Script parsing and prompt refinement helpers."""

from __future__ import annotations

import asyncio
import os
import re
import textwrap
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
from .utils import normalize_script_text, slugify

HEADER_LINE_PATTERN = re.compile(
    r"^\**\s*Scene\s+(\d+)(?:\s*[(:\-]\s*(.*?))?\s*\**$",
    re.IGNORECASE,
)


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


def _normalize_scene_headers(script_text: str) -> str:
    """Convert loose scene headers into the bold format expected by parse_script."""

    normalized: list[str] = []
    for line in script_text.splitlines():
        stripped = line.strip()
        if HEADER_PATTERN.match(stripped):
            normalized.append(stripped)
            continue

        match = HEADER_LINE_PATTERN.match(stripped)
        if not match:
            normalized.append(line)
            continue

        number = match.group(1)
        raw_title = match.group(2) or ""
        title = raw_title.strip(" )-") or f"Scene {number}"
        normalized.append(f"**Scene {number} ({title})**")

    return "\n".join(normalized)


def _contains_scene_headers(script_text: str) -> bool:
    return any(
        HEADER_PATTERN.match(line.strip()) for line in script_text.splitlines()
    )


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
                }
            ],
            max_output_tokens=max_tokens,
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


async def ensure_scene_headers(
    script_text: str, use_gpt5: bool
) -> tuple[str, bool, bool]:
    """Normalize scene headers and optionally refine with GPT-5 before parsing."""

    normalized = _normalize_scene_headers(normalize_script_text(script_text))
    has_headers = _contains_scene_headers(normalized)
    used_refinement = False
    if has_headers or not use_gpt5:
        return normalized, has_headers, used_refinement

    used_refinement = True
    refined = await refine_text_with_gpt5(normalized, mode="workflow")
    print("---------------------------------")
    print(refined)
    refined = refined.strip() or normalized
    refined_normalized = _normalize_scene_headers(normalize_script_text(refined))
    has_headers = _contains_scene_headers(refined_normalized)
    return refined_normalized, has_headers, used_refinement


WORKFLOW_REFINEMENT_TEMPLATE = textwrap.dedent(
    """
    I need you to help me transfer the long video script to a multiple scenes sora2 system prompts, the total length of the output video is 90 seconds or 120 seconds. Each scene can be 8 seconds or 12 seconds long, so you need to based on the original script to decide how many dialogs is suitable for each scene. You must make sure all the scene is using the same output style: Rendered in high-quality 3D animation with soft, realistic lighting and depth of field, the character design uses a blend of anime-style facial proportions (large eyes, sharp jawline) with realistic textures and lighting, make sure each scene's Format & Look part is same!!!.

    You can refer to following example sora2 prompt to know how to control the video style in details:
    ```
    Format & Look
    Duration 4s; 180-degree shutter; digital capture emulating 65 mm photochemical contrast; fine grain; subtle halation on speculars; no gate weave.

    Lenses & Filtration
    32 mm / 50 mm spherical primes; Black Pro-Mist 1/4; slight CPL rotation to manage glass reflections on train windows.

    Grade / Palette
    Highlights: clean morning sunlight with amber lift.
    Mids: balanced neutrals with slight teal cast in shadows.
    Blacks: soft, neutral with mild lift for haze retention.

    Lighting & Atmosphere
    Natural sunlight from camera left, low angle (07:30 AM).
    Bounce: 4x4 ultrabounce silver from trackside.
    Negative fill from opposite wall.
    Practical: sodium platform lights on dim fade.
    Atmos: gentle mist; train exhaust drift through light beam.

    Location & Framing
    Urban commuter platform, dawn.
    Foreground: yellow safety line, coffee cup on bench.
    Midground: waiting passengers silhouetted in haze.
    Background: arriving train braking to a stop.
    Avoid signage or corporate branding.

    Wardrobe / Props / Extras
    Main subject: mid-30s traveler, navy coat, backpack slung on one shoulder, holding phone loosely at side.
    Extras: commuters in muted tones; one cyclist pushing bike.
    Props: paper coffee cup, rolling luggage, LED departure board (generic destinations).

    Sound
    Diegetic only: faint rail screech, train brakes hiss, distant announcement muffled (-20 LUFS), low ambient hum.
    Footsteps and paper rustle; no score or added foley.

    Optimized Shot List (2 shots / 4 s total)

    0.00-2.40 - "Arrival Drift" (32 mm, shoulder-mounted slow dolly left)
    Camera slides past platform signage edge; shallow focus reveals traveler mid-frame looking down tracks. Morning light blooms across lens; train headlights flare softly through mist. Purpose: establish setting and tone, hint anticipation.

    2.40-4.00 - "Turn and Pause" (50 mm, slow arc in)
    Cut to tighter over-shoulder arc as train halts; traveler turns slightly toward camera, catching sunlight rim across cheek and phone screen reflection. Eyes flick up toward something unseen. Purpose: create human focal moment with minimal motion.

    Camera Notes (Why It Reads)
    Keep eyeline low and close to lens axis for intimacy.
    Allow micro flares from train glass as aesthetic texture.
    Preserve subtle handheld imperfection for realism.
    Do not break silhouette clarity with overexposed flare; retain skin highlight roll-off.

    Finishing
    Fine-grain overlay with mild chroma noise for realism; restrained halation on practicals; warm-cool LUT for morning split tone.
    Mix: prioritize train and ambient detail over footstep transients.
    Poster frame: traveler mid-turn, golden rim light, arriving train soft-focus in background haze.
    ```

    The final output should be comply with following template between three backticks:
    ```
    **Video title**

    **Scene 1 (Scene 1 title)**
    Scene 1 sora2 prompt content

    **Scene 2 (Scene 2 title)**
    Scene 2 sora2 prompt content

    **Scene 3 (Scene 3 title) **
    Scene 3 sora2 prompt content
    ```

    Following is the original long video script written in Chinese, the output prompt should be in English but all the dialogs should be in Chinese since this video is for China audience.

    - Input Script
    - Video background and Video script
    """
).strip()


async def refine_text_with_gpt5(content: str, mode: Literal["workflow", "single"]) -> str:
    normalized = content.strip()
    if not normalized:
        return content

    deployment = GPT5_REFINEMENT_DEPLOYMENT
    if not deployment:
        raise RuntimeError("GPT-5 refinement deployment not configured.")

    client = azure_text_client()
    if client is None:
        raise RuntimeError("Azure OpenAI client not configured.")
    
    logger.info("Using GPT-5 refinement deployment: %s", deployment)

    if mode == "workflow":
        system_prompt = (
            WORKFLOW_REFINEMENT_TEMPLATE
                    )
        user_prompt =  normalized
        max_tokens = 8000
    else:
        system_prompt = "You are GPT-5 optimizing single-scene prompts for Azure OpenAI Sora2 video generation."
        user_prompt = (
            "Refine the following prompt into one paragraph that clearly describes subject, motion, camera, and mood.\n\n---\n"
            f"{normalized}\n---"
        )
        max_tokens = 2000

    question = f"{system_prompt}\n\n---\n{user_prompt}\n---"

    def _call() -> str:
        logger.info(
            "GPT-5 refinement request (mode=%s, model=%s): %s",
            mode,
            deployment,
            question,
        )
        response = client.responses.create(
            model=deployment,
            input=question,
            max_output_tokens=max_tokens,
        )
        output_text = extract_response_text(response)
        print(response)
        logger.info("GPT-5 refinement response (mode=%s): %s", mode, output_text)
        return output_text

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("GPT-5 refinement failed (%s mode): %s", mode, exc)
        return "failure calling to GPT-5"

    cleaned = refined.strip()
    return cleaned or content


__all__ = [
    "build_prompt_text",
    "parse_script",
    "refine_scene_description",
    "ensure_scene_headers",
    "refine_text_with_gpt5",
    "slugify",
]
