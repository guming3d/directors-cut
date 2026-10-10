"""Script parsing and prompt refinement helpers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from functools import lru_cache
from dataclasses import dataclass
from string import Template
from typing import Literal, Sequence

from .config import (
    DEFAULT_VIDEO_PROVIDER,
    HEADER_PATTERN,
    REASONING_DEPLOYMENT,
    ROOT,
    SEEDANCE_GENERATE_AUDIO,
    azure_text_client,
    extract_response_text,
    logger,
    normalize_video_provider,
    reasoning_request_options,
)
from .utils import nearest_aspect_ratio, normalize_script_text, resolution_to_size, slugify

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


PROMPT_TEMPLATE_DIR = ROOT / "prompts"
WORKFLOW_TEMPLATE_FILES = {
    "sora2": PROMPT_TEMPLATE_DIR / "sora_prompt_generation.txt",
    "kling": PROMPT_TEMPLATE_DIR / "kling_prompt_generation.txt",
    "jimeng": PROMPT_TEMPLATE_DIR / "jimeng_prompt_generation.txt",
    "seedance": PROMPT_TEMPLATE_DIR / "seedance_prompt_generation.txt",
}

VISUAL_STYLE_DESCRIPTIONS: dict[str, str] = {
    "2d_animation": (
        "Rendered in vivid 2D cel-shaded animation style with bold outlines, flat color fills, "
        "and hand-drawn textures. Use bright, saturated palettes with clean vector-like shapes. "
        "Characters have expressive anime-influenced proportions."
    ),
    "3d_animation": (
        "Rendered in high-quality 3D animation with soft, realistic lighting and depth of field. "
        "Character design uses a blend of anime-style facial proportions with realistic textures "
        "and lighting. Smooth subsurface scattering on skin, detailed hair strands, and subtle "
        "ambient occlusion."
    ),
    "realistic": (
        "Photorealistic live-action cinematography. Shot on digital cinema cameras with natural "
        "lighting, accurate skin tones, real fabric textures, and physically correct depth of field. "
        "No stylization — aim for documentary or feature-film realism."
    ),
}

DEFAULT_VISUAL_STYLE = "3d_animation"


def get_visual_style_description(style: str) -> str:
    """Return the descriptive text for a visual style, falling back to the default."""
    return VISUAL_STYLE_DESCRIPTIONS.get(style, VISUAL_STYLE_DESCRIPTIONS[DEFAULT_VISUAL_STYLE])


STATIC_PROMPT_FILES = {
    "refine_single_sora2": PROMPT_TEMPLATE_DIR / "refine_single_sora2.txt",
    "refine_single_kling": PROMPT_TEMPLATE_DIR / "refine_single_kling.txt",
    "refine_single_jimeng": PROMPT_TEMPLATE_DIR / "refine_single_jimeng.txt",
    "refine_single_seedance": PROMPT_TEMPLATE_DIR / "refine_single_seedance.txt",
    "refine_single_user": PROMPT_TEMPLATE_DIR / "refine_single_user.txt",
    "scene_split_system": PROMPT_TEMPLATE_DIR / "scene_split_system.txt",
    "first_frame_system": PROMPT_TEMPLATE_DIR / "first_frame_system.txt",
    "first_frame_user": PROMPT_TEMPLATE_DIR / "first_frame_user.txt",
    "first_frame_fallback": PROMPT_TEMPLATE_DIR / "first_frame_fallback.txt",
    "reproduce_segment": PROMPT_TEMPLATE_DIR / "reproduce_segment_prompt.txt",
    "reproduce_stylize": PROMPT_TEMPLATE_DIR / "reproduce_stylize_prompt.txt",
    "character_extract_system": PROMPT_TEMPLATE_DIR / "character_extract_system.txt",
    "character_ref_image": PROMPT_TEMPLATE_DIR / "character_ref_image.txt",
}


@lru_cache(maxsize=None)
def _read_prompt_file(path_str: str) -> str:
    path = Path(path_str)
    try:
        template = path.read_text(encoding="utf-8").strip()
    except OSError as exc:  # pragma: no cover - filesystem failures are rare
        raise RuntimeError(f"Prompt template missing: {path}") from exc
    if not template:
        raise RuntimeError(f"Prompt template is empty: {path}")
    return template


@lru_cache(maxsize=None)
def _prompt_template(name: str) -> Template:
    path = STATIC_PROMPT_FILES.get(name)
    if path is None:
        raise RuntimeError(f"Unknown prompt template key: {name}")
    return Template(_read_prompt_file(str(path)))


def render_prompt_template(name: str, **values: object) -> str:
    template = _prompt_template(name)
    serialized = {key: str(value) for key, value in values.items()}
    return template.safe_substitute(**serialized)


@lru_cache(maxsize=None)
def _workflow_template_for_provider(provider: str | None) -> str:
    normalized = normalize_video_provider(provider)
    path = WORKFLOW_TEMPLATE_FILES.get(normalized)
    if path is None:
        normalized = DEFAULT_VIDEO_PROVIDER
        path = WORKFLOW_TEMPLATE_FILES.get(normalized)
    if path is None:
        raise RuntimeError(
            "Workflow refinement template files are not configured for any provider."
        )
    template = _read_prompt_file(str(path))
    return template


def workflow_template_path(provider: str | None = None) -> Path:
    normalized = normalize_video_provider(provider)
    path = WORKFLOW_TEMPLATE_FILES.get(normalized)
    if path is None:
        raise RuntimeError(f"No workflow prompt template configured for provider: {normalized}")
    return path


def get_workflow_template_text(provider: str | None = None) -> str:
    return _workflow_template_for_provider(provider)


def update_workflow_template_text(provider: str | None, template: str) -> str:
    normalized_text = (template or "").strip()
    if not normalized_text:
        raise ValueError("Workflow prompt template content cannot be empty.")

    path = workflow_template_path(provider)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = f"{normalized_text}\n"
    path.write_text(serialized, encoding="utf-8")

    _read_prompt_file.cache_clear()
    _workflow_template_for_provider.cache_clear()
    return normalized_text


def build_prompt_text(
    title: str, description: str, duration: int, resolution: str
) -> str:
    """Return a scene prompt without duplicating duration/resolution metadata.

    Sora already receives duration and resolution as explicit API parameters, so
    we keep the prompt text focused on the refined description to avoid drift.
    """
    prompt_body = description.strip()
    if not prompt_body:
        return description

    title_line = title.strip()
    if title_line:
        return f"{title_line}\n\n{prompt_body}"
    return prompt_body


@dataclass(frozen=True)
class ProviderProfile:
    label: str
    audio: str
    # Hard limit enforced by the provider API; longer prompts are rejected.
    max_prompt_chars: int | None = None


PROVIDER_PROFILES: dict[str, ProviderProfile] = {
    "sora2": ProviderProfile(
        "Sora 2",
        "Generates synchronized dialogue, sound effects, and ambience from the prompt.",
    ),
    "kling": ProviderProfile(
        "Kling 3.0",
        "Video only: no sound or speech is generated, so spoken lines must become visible action.",
        max_prompt_chars=2500,
    ),
    "jimeng": ProviderProfile(
        "Jimeng 3.0 Pro",
        "Video only: no sound or speech is generated, so spoken lines must become visible action.",
        max_prompt_chars=800,
    ),
    "seedance": ProviderProfile(
        "Seedance 2.5",
        (
            "Generates dialogue, sound effects, and ambience from the prompt."
            if SEEDANCE_GENERATE_AUDIO
            else "Video only: no sound or speech is generated, so spoken lines must become visible action."
        ),
    ),
}


def provider_profile(provider: str | None) -> ProviderProfile:
    return PROVIDER_PROFILES[normalize_video_provider(provider)]


@dataclass(frozen=True)
class ProductionSettings:
    """Facts about the render that the prompt writer cannot infer from the script."""

    video_provider: str | None = None
    clip_seconds: int | None = None
    resolution: str | None = None
    visual_style: str | None = None
    uses_start_image: bool = False

    def describe(self) -> str:
        provider = normalize_video_provider(self.video_provider)
        profile = PROVIDER_PROFILES[provider]
        lines = [
            "Production settings (fixed by the app; follow them exactly):",
            f"- Video model: {profile.label}.",
        ]
        if self.clip_seconds:
            lines.append(
                f"- Every scene is rendered as exactly one {self.clip_seconds}-second clip, "
                "and every scene is a separately billed generation."
            )
        if self.resolution:
            width, height = resolution_to_size(self.resolution)
            orientation = "portrait" if height > width else "landscape" if width > height else "square"
            ratio = nearest_aspect_ratio(self.resolution, ("9:16", "16:9", "1:1", "3:4", "4:3", "21:9"))
            lines.append(f"- Frame: {ratio} {orientation} ({width}x{height}); compose every shot for this frame.")
        if self.visual_style:
            lines.append(f"- Visual style for every scene: {get_visual_style_description(self.visual_style)}")
        lines.append(f"- Audio: {profile.audio}")
        lines.append(
            "- Start image: each scene starts from a generated first-frame image, so describe what happens "
            "from that frame onward."
            if self.uses_start_image
            else "- Start image: none; the prompt alone defines the opening frame."
        )
        if profile.max_prompt_chars:
            lines.append(
                f"- Hard limit: each scene prompt must stay under {profile.max_prompt_chars} characters."
            )
        return "\n".join(lines)


def check_prompt_lengths(prompts: Sequence[tuple[str, str]], provider: str | None) -> None:
    """Fail before any paid render when a prompt exceeds the provider's hard limit."""

    limit = provider_profile(provider).max_prompt_chars
    if not limit:
        return
    too_long = [f"{label} ({len(text)} chars)" for label, text in prompts if len(text) > limit]
    if too_long:
        raise RuntimeError(
            f"{provider_profile(provider).label} accepts prompts up to {limit} characters; shorten "
            + ", ".join(too_long)
            + " before rendering."
        )


async def ensure_scene_headers(
    script_text: str,
    use_refinement: bool,
    settings: ProductionSettings | None = None,
) -> tuple[str, bool, bool]:
    """Normalize scene headers, calling the reasoning model only when needed.

    Returns ``(script, has_headers, used_llm)``. When the script has no scene
    separators the reasoning model always inserts them, so header-less input no
    longer stops the pipeline. Full refinement still runs only when requested.
    """

    normalized = _normalize_scene_headers(normalize_script_text(script_text))
    if _contains_scene_headers(normalized):
        return normalized, True, False

    if use_refinement:
        refined = await refine_text_with_llm(normalized, mode="workflow", settings=settings)
        refined_normalized = _normalize_scene_headers(
            normalize_script_text(refined.strip() or normalized)
        )
        if _contains_scene_headers(refined_normalized):
            return refined_normalized, True, True
        normalized = refined_normalized

    split = await split_script_into_scenes(normalized, settings.clip_seconds if settings else None)
    split_normalized = _normalize_scene_headers(normalize_script_text(split))
    return split_normalized, _contains_scene_headers(split_normalized), True


async def split_script_into_scenes(script_text: str, scene_duration: int | None = None) -> str:
    """Insert ``**Scene N (Title)**`` separators without rewriting the content."""

    normalized = script_text.strip()
    if not normalized:
        return script_text

    client = azure_text_client()
    if client is None:
        raise RuntimeError(
            "Script has no scene headers and the Azure OpenAI client is not configured to add them."
        )

    duration_hint = (
        f"Each scene is rendered as one {scene_duration}-second video clip."
        if scene_duration
        else "Each scene is rendered as one short video clip."
    )
    system_prompt = render_prompt_template("scene_split_system", duration_hint=duration_hint)
    # The answer repeats the whole script plus headers, so budget for that.
    max_tokens = max(2000, len(normalized) * 2)

    def _call() -> str:
        response = client.responses.create(
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": normalized}]},
            ],
            **reasoning_request_options(max_tokens),
        )
        return extract_response_text(response)

    logger.info(
        "Script has no scene headers; asking %s to insert separators (chars=%d).",
        REASONING_DEPLOYMENT,
        len(normalized),
    )
    try:
        split = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.error("Scene separation failed: %s", exc)
        raise RuntimeError(f"Scene separation with {REASONING_DEPLOYMENT} failed: {exc}") from exc
    return split.strip() or script_text


async def refine_text_with_llm(
    content: str,
    mode: Literal["workflow", "single"],
    settings: ProductionSettings | None = None,
) -> str:
    normalized = content.strip()
    if not normalized:
        return content

    client = azure_text_client()
    if client is None:
        raise RuntimeError("Azure OpenAI client not configured.")

    logger.info("Using reasoning deployment for refinement: %s", REASONING_DEPLOYMENT)

    settings = settings or ProductionSettings()
    provider = normalize_video_provider(settings.video_provider)
    if mode == "workflow":
        system_prompt = _workflow_template_for_provider(provider)
        user_prompt = f"{settings.describe()}\n\nScript:\n{normalized}"
        max_tokens = 8000
    else:
        system_prompt = render_prompt_template(f"refine_single_{provider}")
        user_prompt = render_prompt_template(
            "refine_single_user", settings=settings.describe(), prompt=normalized
        )
        max_tokens = 2000

    request_text = f"{system_prompt}\n\n---\n{user_prompt}\n---"

    def _call() -> str:
        logger.debug(
            "Submitting refinement request (mode=%s, model=%s, chars=%d).",
            mode,
            REASONING_DEPLOYMENT,
            len(request_text),
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": request_text,
                    }
                ],
            }
        ]
        response = client.responses.create(
            input=messages,
            **reasoning_request_options(max_tokens),
        )
        output_text = extract_response_text(response)
        logger.debug(
            "Received refinement response (mode=%s, chars=%d).",
            mode,
            len(output_text),
        )
        return output_text

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("%s refinement failed (%s mode): %s", REASONING_DEPLOYMENT, mode, exc)
        raise RuntimeError(f"{REASONING_DEPLOYMENT} refinement failed ({mode} mode).") from exc

    cleaned = refined.strip()
    return cleaned or content


__all__ = [
    "build_prompt_text",
    "parse_script",
    "PROVIDER_PROFILES",
    "ProductionSettings",
    "check_prompt_lengths",
    "provider_profile",
    "ensure_scene_headers",
    "refine_text_with_llm",
    "split_script_into_scenes",
    "get_workflow_template_text",
    "update_workflow_template_text",
    "workflow_template_path",
    "slugify",
    "render_prompt_template",
]
