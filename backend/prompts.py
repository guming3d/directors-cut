"""Script parsing and prompt refinement helpers."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from functools import lru_cache
from string import Template
from typing import Literal

from .config import (
    DEFAULT_VIDEO_PROVIDER,
    DIRECTOR_REFINEMENT_DEPLOYMENT,
    GPT5_REFINEMENT_DEPLOYMENT,
    HEADER_PATTERN,
    ROOT,
    SORA_PROMPT_GUIDE_URL,
    azure_text_client,
    extract_response_text,
    logger,
    normalize_video_provider,
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


PROMPT_TEMPLATE_DIR = ROOT / "prompts"
WORKFLOW_TEMPLATE_FILES = {
    "sora2": PROMPT_TEMPLATE_DIR / "sora_prompt_generation.txt",
    "kling": PROMPT_TEMPLATE_DIR / "kling_prompt_generation.txt",
}

STATIC_PROMPT_FILES = {
    "refine_scene_system": PROMPT_TEMPLATE_DIR / "refine_scene_system.txt",
    "refine_scene_user": PROMPT_TEMPLATE_DIR / "refine_scene_user.txt",
    "refine_single_system": PROMPT_TEMPLATE_DIR / "refine_single_system.txt",
    "refine_single_user": PROMPT_TEMPLATE_DIR / "refine_single_user.txt",
    "first_frame_system": PROMPT_TEMPLATE_DIR / "first_frame_system.txt",
    "first_frame_user": PROMPT_TEMPLATE_DIR / "first_frame_user.txt",
    "first_frame_fallback": PROMPT_TEMPLATE_DIR / "first_frame_fallback.txt",
    "reproduce_segment": PROMPT_TEMPLATE_DIR / "reproduce_segment_prompt.txt",
    "reproduce_stylize": PROMPT_TEMPLATE_DIR / "reproduce_stylize_prompt.txt",
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


async def refine_scene_description(title: str, description: str, duration: int) -> str:
    normalized = description.strip()
    if not normalized:
        return description

    client = azure_text_client()
    if client is None:
        return description

    system_prompt = render_prompt_template(
        "refine_scene_system",
        guide_url=SORA_PROMPT_GUIDE_URL,
        duration=duration,
    )
    user_prompt = render_prompt_template(
        "refine_scene_user",
        title=title,
        duration=duration,
        description=normalized,
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
    script_text: str, use_gpt5: bool, video_provider: str | None = None
) -> tuple[str, bool, bool]:
    """Normalize scene headers and optionally refine with GPT-5 before parsing."""

    normalized = _normalize_scene_headers(normalize_script_text(script_text))
    has_headers = _contains_scene_headers(normalized)
    used_refinement = False
    if has_headers or not use_gpt5:
        return normalized, has_headers, used_refinement

    used_refinement = True
    refined = await refine_text_with_gpt5(
        normalized, mode="workflow", video_provider=video_provider
    )
    refined = refined.strip() or normalized
    refined_normalized = _normalize_scene_headers(normalize_script_text(refined))
    has_headers = _contains_scene_headers(refined_normalized)
    return refined_normalized, has_headers, used_refinement


async def refine_text_with_gpt5(
    content: str, mode: Literal["workflow", "single"], video_provider: str | None = None
) -> str:
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
        system_prompt = _workflow_template_for_provider(
            video_provider or DEFAULT_VIDEO_PROVIDER
        )
        user_prompt = normalized
        max_tokens = 8000
    else:
        system_prompt = render_prompt_template("refine_single_system")
        user_prompt = render_prompt_template("refine_single_user", prompt=normalized)
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
    "get_workflow_template_text",
    "update_workflow_template_text",
    "workflow_template_path",
    "slugify",
    "render_prompt_template",
]
