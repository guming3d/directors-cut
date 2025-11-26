"""Shared configuration, constants, and client helpers for the Sora workflow."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, AzureOpenAI
from PIL import Image

# Base paths
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(override=True)
SCRIPT_PATH = ROOT / "Chapters" / "video_script.txt"
OUTPUT_DIR = ROOT / "output"
PROMPTS_DIR = OUTPUT_DIR / "prompts"
SCENES_DIR = OUTPUT_DIR / "scenes"
FINAL_VIDEO_PATH = OUTPUT_DIR / "sora_story_compilation.mp4"
SORA_CLIENT = ROOT / "sora-openai-client.py"
TMP_DIR = OUTPUT_DIR / "tmp"
SINGLE_VIDEO_JOBS_DIR = OUTPUT_DIR / "single_videos"
WORKFLOW_DB_PATH = OUTPUT_DIR / "db" / "workflow_jobs.json"
SINGLE_VIDEO_DB_PATH = OUTPUT_DIR / "db" / "single_video_jobs.json"
SCENE_IMAGE_DIR = ROOT / "scene_start_screen"

# Static assets & patterns
DEFAULT_SORA_RESOLUTION = "720x1280"
SORA_CLIP_DURATION_SECONDS = 8
SORA_PROMPT_GUIDE_URL = "https://cookbook.openai.com/examples/sora/sora2_prompting_guide"
STATIC_URL_PREFIX = "/static"
SCENE_IMAGE_PATTERN = re.compile(r"scene0*(\d+)\.(?:png|jpe?g)$", re.IGNORECASE)
DATA_URL_PATTERN = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.IGNORECASE)
RESOLUTION_PATTERN = re.compile(r"^\d+x\d+$")
HEADER_PATTERN = re.compile(r"^\*\*\s*(Scene\s+(\d+)[^*]*)\*\*$", re.IGNORECASE)
SCENE_IMAGE_SIZE = (720, 1280)
try:
    SCENE_IMAGE_RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - Pillow < 10 fallback
    SCENE_IMAGE_RESAMPLE = Image.LANCZOS

try:
    SORA_BLOCK_RETRIES = max(1, int(os.getenv("SORA_BLOCK_RETRIES", "3")))
except ValueError:  # pragma: no cover - defensive fallback
    SORA_BLOCK_RETRIES = 3

# Azure configuration
DIRECTOR_REFINEMENT_DEPLOYMENT = (
    os.getenv("DIRECTOR_REFINEMENT_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT41_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT")
)
GPT5_REFINEMENT_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_GPT5_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT")
)
AZURE_TEXT_ENDPOINT = (os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("ENDPOINT_URL") or "").rstrip("/")
AZURE_TEXT_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_TEXT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
_TEXT_CLIENT: AzureOpenAI | None = None
_VIDEO_CLIENT: AsyncOpenAI | None = None


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    log_path = OUTPUT_DIR / "sora_workflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("sora_workflow")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    logger.debug("Logging configured at %s", level_name)
    return logger


logger = _configure_logging()


def azure_text_client() -> AzureOpenAI | None:
    """Create or reuse a shared Azure OpenAI client for GPT refinement."""

    if not (DIRECTOR_REFINEMENT_DEPLOYMENT or GPT5_REFINEMENT_DEPLOYMENT):
        return None
    if not AZURE_TEXT_ENDPOINT or not AZURE_TEXT_API_KEY:
        logger.debug("Azure OpenAI credentials not configured; skipping prompt refinement client.")
        return None

    global _TEXT_CLIENT  # type: ignore[assignment]
    try:
        client = _TEXT_CLIENT
    except NameError:
        client = None

    if client is None:
        client = AzureOpenAI(
            api_key=AZURE_TEXT_API_KEY,
            azure_endpoint=AZURE_TEXT_ENDPOINT,
            api_version=AZURE_TEXT_API_VERSION,
        )
        _TEXT_CLIENT = client  # type: ignore[assignment]
    return client


def video_client() -> AsyncOpenAI:
    global _VIDEO_CLIENT  # type: ignore[assignment]
    if _VIDEO_CLIENT is not None:
        return _VIDEO_CLIENT
    endpoint = (os.getenv("ENDPOINT_URL") or os.getenv("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not endpoint:
        raise RuntimeError("Configure ENDPOINT_URL or AZURE_OPENAI_ENDPOINT before using remix features.")
    if not api_key:
        raise RuntimeError("Configure AZURE_OPENAI_API_KEY or OPENAI_API_KEY before using remix features.")
    _VIDEO_CLIENT = AsyncOpenAI(api_key=api_key, base_url=f"{endpoint}/openai/v1/", timeout=900.0)
    return _VIDEO_CLIENT


def extract_response_text(response: Any) -> str:
    """Normalize Azure Responses API output into plain text."""

    output_text: Optional[str] = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    chunks: list[str] = []
    output = getattr(response, "output", None)
    if output:
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for piece in content:
                text = getattr(piece, "text", None)
                if isinstance(text, str):
                    chunks.append(text)

    if not chunks:
        data = getattr(response, "data", None)
        if data:
            for datum in data:
                content = getattr(datum, "content", None)
                if not content:
                    continue
                for piece in content:
                    text = getattr(piece, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)

    return "".join(chunks)


def allowed_origins() -> list[str]:
    configured = os.getenv("SORA_WORKFLOW_ALLOWED_ORIGINS")
    if not configured:
        return ["*"]
    values = [origin.strip() for origin in configured.split(",")]
    return [origin for origin in values if origin]


__all__ = [
    "AZURE_TEXT_API_KEY",
    "AZURE_TEXT_API_VERSION",
    "AZURE_TEXT_ENDPOINT",
    "DATA_URL_PATTERN",
    "DEFAULT_SORA_RESOLUTION",
    "DIRECTOR_REFINEMENT_DEPLOYMENT",
    "FINAL_VIDEO_PATH",
    "GPT5_REFINEMENT_DEPLOYMENT",
    "HEADER_PATTERN",
    "OUTPUT_DIR",
    "PROMPTS_DIR",
    "RESOLUTION_PATTERN",
    "ROOT",
    "SCENE_IMAGE_DIR",
    "SCENE_IMAGE_PATTERN",
    "SCENE_IMAGE_RESAMPLE",
    "SCENE_IMAGE_SIZE",
    "SCENES_DIR",
    "SCRIPT_PATH",
    "SINGLE_VIDEO_DB_PATH",
    "SINGLE_VIDEO_JOBS_DIR",
    "SORA_CLIENT",
    "SORA_CLIP_DURATION_SECONDS",
    "SORA_PROMPT_GUIDE_URL",
    "SORA_BLOCK_RETRIES",
    "STATIC_URL_PREFIX",
    "TMP_DIR",
    "WORKFLOW_DB_PATH",
    "allowed_origins",
    "azure_text_client",
    "extract_response_text",
    "logger",
    "video_client",
]
