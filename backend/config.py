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
SORA_CLIENT = ROOT / "backend" / "sora-openai-client.py"
TMP_DIR = OUTPUT_DIR / "tmp"
SINGLE_VIDEO_JOBS_DIR = OUTPUT_DIR / "single_videos"
VIDEO_INSIGHT_JOBS_DIR = OUTPUT_DIR / "video_insights"
VIDEO_REPRODUCE_JOBS_DIR = OUTPUT_DIR / "video_reproduce"
WORKFLOW_DB_PATH = OUTPUT_DIR / "db" / "workflow_jobs.json"
SINGLE_VIDEO_DB_PATH = OUTPUT_DIR / "db" / "single_video_jobs.json"
VIDEO_INSIGHT_DB_PATH = OUTPUT_DIR / "db" / "video_insight_jobs.json"
VIDEO_REPRODUCE_DB_PATH = OUTPUT_DIR / "db" / "video_reproduce_jobs.json"
SCENE_IMAGE_DIR = ROOT / "scene_start_screen"

# Static assets & patterns
DEFAULT_SORA_RESOLUTION = "720x1280"
SORA_CLIP_DURATION_SECONDS = 8
SORA_PROMPT_GUIDE_URL = "https://cookbook.openai.com/examples/sora/sora2_prompting_guide"
STATIC_URL_PREFIX = "/static"
SCENE_IMAGE_PATTERN = re.compile(r"scene0*(\d+)\.(?:png|jpe?g)$", re.IGNORECASE)
SCENE_TAIL_PATTERN = re.compile(r"scene0*(\d+)_tail\.(?:png|jpe?g)$", re.IGNORECASE)
DATA_URL_PATTERN = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.IGNORECASE)
RESOLUTION_PATTERN = re.compile(r"^\d+x\d+$")
HEADER_PATTERN = re.compile(r"^\*\*\s*(Scene\s+(\d+)[^*]*)\*\*$", re.IGNORECASE)
SCENE_IMAGE_SIZE = (720, 1280)
try:
    SCENE_IMAGE_RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - Pillow < 10 fallback
    SCENE_IMAGE_RESAMPLE = Image.LANCZOS

UVICORN_LOGGER_NAMES = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "uvicorn.asgi",
)


def _quiet_uvicorn_logs() -> None:
    """Force uvicorn loggers to WARNING to suppress noisy INFO output."""

    for name in UVICORN_LOGGER_NAMES:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.setLevel(logging.WARNING)

try:
    SORA_BLOCK_RETRIES = max(1, int(os.getenv("SORA_BLOCK_RETRIES", "3")))
except ValueError:  # pragma: no cover - defensive fallback
    SORA_BLOCK_RETRIES = 3

try:
    _reproduce_threads = int(os.getenv("REPRODUCE_THREADS", "3"))
except ValueError:
    _reproduce_threads = 3
REPRODUCE_DEFAULT_THREADS = max(1, _reproduce_threads)

try:
    _segment_frame_samples = int(os.getenv("REPRODUCE_SEGMENT_FRAME_SAMPLES", "5"))
except ValueError:
    _segment_frame_samples = 5
REPRODUCE_SEGMENT_FRAME_SAMPLES = max(0, _segment_frame_samples)

# Video provider defaults
SUPPORTED_VIDEO_PROVIDERS = ("sora2", "kling")
_provider_env = (os.getenv("VIDEO_PROVIDER") or "sora2").strip().lower()
DEFAULT_VIDEO_PROVIDER = _provider_env if _provider_env in SUPPORTED_VIDEO_PROVIDERS else "sora2"
SORA_ALLOWED_DURATIONS = (4, 8, 12)
KLING_ALLOWED_DURATIONS = (5, 10)

# Image generation provider defaults
SUPPORTED_IMAGE_GENERATORS = ("gpt-image-1", "kling")
_image_generator_env = (os.getenv("REPRODUCE_IMAGE_GENERATOR") or "gpt-image-1").strip().lower()
DEFAULT_IMAGE_GENERATOR = (
    _image_generator_env if _image_generator_env in SUPPORTED_IMAGE_GENERATORS else "gpt-image-1"
)

# Video reproduce segmentation defaults
SUPPORTED_REPRODUCE_SEGMENTERS = ("content_understanding", "opencv")
_segmentation_env = (os.getenv("REPRODUCE_SEGMENTATION_BACKEND") or "content_understanding").strip().lower()
DEFAULT_REPRODUCE_SEGMENTATION = (
    _segmentation_env if _segmentation_env in SUPPORTED_REPRODUCE_SEGMENTERS else "content_understanding"
)


def normalize_video_provider(value: Optional[str]) -> str:
    if not value:
        return DEFAULT_VIDEO_PROVIDER
    lowered = value.strip().lower()
    if lowered not in SUPPORTED_VIDEO_PROVIDERS:
        return DEFAULT_VIDEO_PROVIDER
    return lowered


def allowed_durations_for_provider(value: Optional[str]) -> tuple[int, ...]:
    provider = normalize_video_provider(value)
    return KLING_ALLOWED_DURATIONS if provider == "kling" else SORA_ALLOWED_DURATIONS


# Kling configuration
def _float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


KLING_API_BASE_URL = (os.getenv("KLING_API_BASE_URL") or "https://api-beijing.klingai.com").rstrip("/")
KLING_API_KEY = os.getenv("KLING_API_KEY")
KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY")
KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY")
try:
    _kling_token_ttl = int(os.getenv("KLING_TOKEN_TTL", "1800"))
except ValueError:
    _kling_token_ttl = 1800
KLING_TOKEN_TTL_SECONDS = max(300, _kling_token_ttl)
KLING_DEFAULT_MODEL_NAME = os.getenv("KLING_MODEL_NAME", "kling-v1")
KLING_DEFAULT_MODE = os.getenv("KLING_MODE", "std")
KLING_POLL_INTERVAL_SECONDS = max(1.0, _float_env("KLING_POLL_INTERVAL", "3"))
KLING_POLL_TIMEOUT_SECONDS = max(60.0, _float_env("KLING_POLL_TIMEOUT", "900"))
KLING_DOWNLOAD_CHUNK_SIZE = max(262144, int(os.getenv("KLING_DOWNLOAD_CHUNK_SIZE", "1048576")))

# Azure content understanding configuration
AZURE_CONTENT_UNDERSTANDING_ENDPOINT = (os.getenv("AZURE_CONTENT_UNDERSTANDING_ENDPOINT") or "").rstrip("/")
AZURE_CONTENT_UNDERSTANDING_API_VERSION = os.getenv(
    "AZURE_CONTENT_UNDERSTANDING_API_VERSION",
    "2025-05-01-preview",
)
AZURE_CONTENT_UNDERSTANDING_API_KEY = os.getenv("AZURE_CONTENT_UNDERSTANDING_API_KEY")
AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID = os.getenv(
    "AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID",
    "prebuilt-videoAnalyzer",
)
CONTENT_UNDERSTANDING_POLL_INTERVAL = max(
    1.0, _float_env("CONTENT_UNDERSTANDING_POLL_INTERVAL", "2")
)
CONTENT_UNDERSTANDING_POLL_TIMEOUT = max(
    60.0, _float_env("CONTENT_UNDERSTANDING_POLL_TIMEOUT", "600")
)
try:
    _max_upload_mb = int(os.getenv("CONTENT_UNDERSTANDING_MAX_UPLOAD_MB", "200"))
except ValueError:
    _max_upload_mb = 200
CONTENT_UNDERSTANDING_MAX_UPLOAD_BYTES = max(1, _max_upload_mb) * 1024 * 1024

# Azure configuration
DIRECTOR_REFINEMENT_DEPLOYMENT = (
    os.getenv("DIRECTOR_REFINEMENT_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT41_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT41_DEPLOYMENT_NAME")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
)
GPT5_REFINEMENT_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_GPT5_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT5_DEPLOYMENT_NAME")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
)
AZURE_TEXT_ENDPOINT = (os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("ENDPOINT_URL") or "").rstrip("/")
AZURE_TEXT_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_TEXT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
AZURE_IMAGE_API_VERSION = os.getenv("AZURE_OPENAI_IMAGE_API_VERSION", "2025-04-01-preview")
AZURE_IMAGE_DEPLOYMENT = (
    os.getenv("AZURE_OPENAI_GPT_IMAGE_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT_IMAGE_DEPLOYMENT_NAME")
    or os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_IMAGE_DEPLOYMENT_NAME")
    or "gpt-image-1"
)
_TEXT_CLIENT: AzureOpenAI | None = None
_VIDEO_CLIENT: AsyncOpenAI | None = None
_IMAGE_CLIENT: AzureOpenAI | None = None


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    log_path = OUTPUT_DIR / "video_workflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("video_workflow")
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
    _quiet_uvicorn_logs()
    logger.debug("Logging configured at %s", level_name)
    return logger


logger = _configure_logging()


def azure_text_client() -> AzureOpenAI | None:
    """Create or reuse a shared Azure OpenAI client for GPT refinement."""

    logger.debug(
        "Azure refinement config deployments director=%s gpt5=%s endpoint=%s",
        DIRECTOR_REFINEMENT_DEPLOYMENT,
        GPT5_REFINEMENT_DEPLOYMENT,
        AZURE_TEXT_ENDPOINT,
    )

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
        logger.debug("Creating AzureOpenAI client for refinement")
        client = AzureOpenAI(
            api_key=AZURE_TEXT_API_KEY,
            azure_endpoint=AZURE_TEXT_ENDPOINT,
            api_version=AZURE_TEXT_API_VERSION,
        )
        _TEXT_CLIENT = client  # type: ignore[assignment]
    else:
        logger.debug("Reusing cached AzureOpenAI client for refinement")
    
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


def azure_image_client() -> AzureOpenAI | None:
    """Create or reuse a shared Azure OpenAI client for image generation."""

    if not AZURE_TEXT_ENDPOINT or not AZURE_TEXT_API_KEY:
        logger.debug("Azure OpenAI credentials not configured; skipping image client.")
        return None

    global _IMAGE_CLIENT  # type: ignore[assignment]
    try:
        client = _IMAGE_CLIENT
    except NameError:
        client = None

    if client is None:
        client = AzureOpenAI(
            api_key=AZURE_TEXT_API_KEY,
            azure_endpoint=AZURE_TEXT_ENDPOINT,
            api_version=AZURE_IMAGE_API_VERSION,
        )
        _IMAGE_CLIENT = client  # type: ignore[assignment]
    return client


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
    "AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID",
    "AZURE_CONTENT_UNDERSTANDING_API_KEY",
    "AZURE_CONTENT_UNDERSTANDING_API_VERSION",
    "AZURE_CONTENT_UNDERSTANDING_ENDPOINT",
    "AZURE_IMAGE_API_VERSION",
    "AZURE_IMAGE_DEPLOYMENT",
    "AZURE_TEXT_API_KEY",
    "AZURE_TEXT_API_VERSION",
    "AZURE_TEXT_ENDPOINT",
    "CONTENT_UNDERSTANDING_MAX_UPLOAD_BYTES",
    "CONTENT_UNDERSTANDING_POLL_INTERVAL",
    "CONTENT_UNDERSTANDING_POLL_TIMEOUT",
    "DATA_URL_PATTERN",
    "DEFAULT_SORA_RESOLUTION",
    "DEFAULT_IMAGE_GENERATOR",
    "DEFAULT_REPRODUCE_SEGMENTATION",
    "DEFAULT_VIDEO_PROVIDER",
    "DIRECTOR_REFINEMENT_DEPLOYMENT",
    "FINAL_VIDEO_PATH",
    "GPT5_REFINEMENT_DEPLOYMENT",
    "HEADER_PATTERN",
    "KLING_API_BASE_URL",
    "KLING_API_KEY",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "KLING_TOKEN_TTL_SECONDS",
    "KLING_DEFAULT_MODE",
    "KLING_DEFAULT_MODEL_NAME",
    "KLING_DOWNLOAD_CHUNK_SIZE",
    "KLING_POLL_INTERVAL_SECONDS",
    "KLING_POLL_TIMEOUT_SECONDS",
    "KLING_ALLOWED_DURATIONS",
    "OUTPUT_DIR",
    "PROMPTS_DIR",
    "RESOLUTION_PATTERN",
    "ROOT",
    "REPRODUCE_DEFAULT_THREADS",
    "REPRODUCE_SEGMENT_FRAME_SAMPLES",
    "SCENE_IMAGE_DIR",
    "SCENE_IMAGE_PATTERN",
    "SCENE_IMAGE_RESAMPLE",
    "SCENE_IMAGE_SIZE",
    "SCENE_TAIL_PATTERN",
    "SCENES_DIR",
    "SCRIPT_PATH",
    "SINGLE_VIDEO_DB_PATH",
    "SUPPORTED_VIDEO_PROVIDERS",
    "SUPPORTED_IMAGE_GENERATORS",
    "SUPPORTED_REPRODUCE_SEGMENTERS",
    "SINGLE_VIDEO_JOBS_DIR",
    "VIDEO_INSIGHT_DB_PATH",
    "VIDEO_INSIGHT_JOBS_DIR",
    "SORA_CLIENT",
    "SORA_CLIP_DURATION_SECONDS",
    "SORA_ALLOWED_DURATIONS",
    "SORA_PROMPT_GUIDE_URL",
    "SORA_BLOCK_RETRIES",
    "STATIC_URL_PREFIX",
    "TMP_DIR",
    "WORKFLOW_DB_PATH",
    "allowed_origins",
    "azure_image_client",
    "azure_text_client",
    "extract_response_text",
    "logger",
    "normalize_video_provider",
    "allowed_durations_for_provider",
    "video_client",
]
