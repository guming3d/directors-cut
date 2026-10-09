"""Shared configuration, constants, and client helpers for the Sora workflow."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI, AzureOpenAI, OpenAI
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

SUPPORTED_REPRODUCE_REFERENCE_MODES = ("start_end", "start_only")
_reference_mode_env = (os.getenv("REPRODUCE_REFERENCE_MODE") or "start_end").strip().lower()
DEFAULT_REPRODUCE_REFERENCE_MODE = (
    _reference_mode_env if _reference_mode_env in SUPPORTED_REPRODUCE_REFERENCE_MODES else "start_end"
)

# Video provider defaults
SUPPORTED_VIDEO_PROVIDERS = ("sora2", "kling", "jimeng", "seedance")
_provider_env = (os.getenv("VIDEO_PROVIDER") or "sora2").strip().lower()
DEFAULT_VIDEO_PROVIDER = _provider_env if _provider_env in SUPPORTED_VIDEO_PROVIDERS else "sora2"
SORA_ALLOWED_DURATIONS = (4, 8, 12)
KLING_ALLOWED_DURATIONS = (5, 10)
JIMENG_ALLOWED_DURATIONS = (5, 10)
# Seedance bills per generated second; 4s is the cheapest option for trials.
SEEDANCE_ALLOWED_DURATIONS = (4, 5, 8, 10, 12)

# Image generation provider defaults
SUPPORTED_IMAGE_GENERATORS = ("gpt-image-2.5", "kling", "original")
# Jobs stored before the GPT Image 2.5 switch reference the old generator name.
LEGACY_IMAGE_GENERATOR_ALIASES = {"gpt-image-1": "gpt-image-2.5"}


def normalize_image_generator(value: Optional[str], default: str = "gpt-image-2.5") -> str:
    lowered = (value or "").strip().lower()
    lowered = LEGACY_IMAGE_GENERATOR_ALIASES.get(lowered, lowered)
    return lowered if lowered in SUPPORTED_IMAGE_GENERATORS else default


DEFAULT_IMAGE_GENERATOR = normalize_image_generator(os.getenv("REPRODUCE_IMAGE_GENERATOR"))

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
    if provider == "kling":
        return KLING_ALLOWED_DURATIONS
    if provider == "jimeng":
        return JIMENG_ALLOWED_DURATIONS
    if provider == "seedance":
        return SEEDANCE_ALLOWED_DURATIONS
    return SORA_ALLOWED_DURATIONS


# Kling configuration
def _float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


try:
    _sora_transient_retries = int(os.getenv("SORA_TRANSIENT_RETRIES", "4"))
except ValueError:
    _sora_transient_retries = 4
SORA_TRANSIENT_RETRIES = max(1, _sora_transient_retries)
SORA_RETRY_BASE_DELAY_SECONDS = max(0.5, _float_env("SORA_RETRY_BASE_DELAY", "2"))
SORA_RETRY_MAX_DELAY_SECONDS = max(
    SORA_RETRY_BASE_DELAY_SECONDS,
    _float_env("SORA_RETRY_MAX_DELAY", "30"),
)
SORA_RETRY_JITTER_SECONDS = max(0.0, _float_env("SORA_RETRY_JITTER", "0.5"))


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
try:
    _kling_create_max_retries = int(os.getenv("KLING_CREATE_MAX_RETRIES", "5"))
except ValueError:
    _kling_create_max_retries = 5
KLING_CREATE_MAX_RETRIES = max(1, _kling_create_max_retries)
KLING_CREATE_RETRY_BASE_DELAY_SECONDS = max(
    0.5, _float_env("KLING_CREATE_RETRY_BASE_DELAY", "2")
)
KLING_CREATE_RETRY_MAX_DELAY_SECONDS = max(
    KLING_CREATE_RETRY_BASE_DELAY_SECONDS,
    _float_env("KLING_CREATE_RETRY_MAX_DELAY", "30"),
)
KLING_CREATE_RETRY_JITTER_SECONDS = max(0.0, _float_env("KLING_CREATE_RETRY_JITTER", "0.5"))

# Jimeng configuration
JIMENG_ACCESS_KEY_ID = os.getenv("JIMENG_ACCESS_KEY_ID") or os.getenv("JIMENG_ACCESS_KEY")
JIMENG_API_BASE_URL = (os.getenv("JIMENG_API_BASE_URL") or "https://visual.volcengineapi.com").rstrip("/")
JIMENG_POLL_INTERVAL_SECONDS = max(1.0, _float_env("JIMENG_POLL_INTERVAL", "3"))
JIMENG_POLL_TIMEOUT_SECONDS = max(60.0, _float_env("JIMENG_POLL_TIMEOUT", "900"))
JIMENG_REGION = os.getenv("JIMENG_REGION", "cn-north-1")
JIMENG_REQ_KEY = "jimeng_ti2v_v30_pro"
JIMENG_SECRET_ACCESS_KEY = os.getenv("JIMENG_SECRET_ACCESS_KEY") or os.getenv("JIMENG_SECRET_KEY")
JIMENG_SECURITY_TOKEN = os.getenv("JIMENG_SECURITY_TOKEN")
JIMENG_SERVICE = os.getenv("JIMENG_SERVICE", "cv")
JIMENG_SUPPORTED_ASPECT_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9")

# Seedance 2.5 (Volcengine Ark) configuration
def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


ARK_API_KEY = os.getenv("ARK_API_KEY")
SEEDANCE_API_URL = (
    os.getenv("SEEDANCE_API_URL")
    or "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
).rstrip("/")
SEEDANCE_MODEL = os.getenv("SEEDANCE_MODEL") or "doubao-seedance-2-5-260628"
SEEDANCE_GENERATE_AUDIO = _bool_env("SEEDANCE_GENERATE_AUDIO", True)
SEEDANCE_WATERMARK = _bool_env("SEEDANCE_WATERMARK", False)
# Optional output resolution such as "720p"; omitted from requests when unset.
SEEDANCE_RESOLUTION = (os.getenv("SEEDANCE_RESOLUTION") or "").strip() or None
# Role for the scene's start image: "first_frame" pins the opening frame;
# "reference_image" only conditions the look.
SEEDANCE_IMAGE_ROLE = (os.getenv("SEEDANCE_IMAGE_ROLE") or "first_frame").strip()
SEEDANCE_POLL_INTERVAL_SECONDS = max(2.0, _float_env("SEEDANCE_POLL_INTERVAL", "10"))
SEEDANCE_POLL_TIMEOUT_SECONDS = max(60.0, _float_env("SEEDANCE_POLL_TIMEOUT", "1200"))
SEEDANCE_SUPPORTED_ASPECT_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9")

# Cloudflare R2 public bucket used to host Seedance reference images, since Ark
# only accepts media by URL.
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_PUBLIC_BASE_URL = (os.getenv("R2_PUBLIC_BASE_URL") or "").rstrip("/")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_JURISDICTION = (os.getenv("R2_JURISDICTION") or "").strip().lower()
R2_UPLOAD_PREFIX = (os.getenv("R2_UPLOAD_PREFIX") or "seedance-refs").strip("/")

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

try:
    _max_inline_image_mb = int(os.getenv("JSON_IMAGE_MAX_UPLOAD_MB", "20"))
except ValueError:
    _max_inline_image_mb = 20
JSON_IMAGE_MAX_UPLOAD_BYTES = max(1, _max_inline_image_mb) * 1024 * 1024

try:
    _subprocess_timeout = float(os.getenv("SUBPROCESS_TIMEOUT_SECONDS", "1800"))
except (TypeError, ValueError):
    _subprocess_timeout = 1800.0
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = max(1.0, _subprocess_timeout)

# Azure configuration
# All text/vision reasoning (storyboard refinement, scene splitting, first-frame
# prompts, character extraction, segment descriptions) runs on one deployment.
REASONING_DEPLOYMENT = os.getenv("AZURE_OPENAI_REASONING_DEPLOYMENT") or "gpt-6.1-sol"
SUPPORTED_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
_reasoning_effort_env = (os.getenv("AZURE_OPENAI_REASONING_EFFORT") or "medium").strip().lower()
REASONING_EFFORT = (
    _reasoning_effort_env if _reasoning_effort_env in SUPPORTED_REASONING_EFFORTS else "medium"
)
try:
    _reasoning_headroom = int(os.getenv("AZURE_OPENAI_REASONING_TOKEN_HEADROOM", "8000"))
except ValueError:
    _reasoning_headroom = 8000
# Reasoning tokens count against max_output_tokens, so each call reserves extra
# budget on top of the visible answer it needs.
REASONING_TOKEN_HEADROOM = max(0, _reasoning_headroom)
# The reasoning deployment may live on a different resource than Sora. A
# separate endpoint needs its own key: the Sora key would only return 401 there.
_reasoning_endpoint = (os.getenv("AZURE_OPENAI_REASONING_ENDPOINT") or "").rstrip("/")
if _reasoning_endpoint:
    AZURE_TEXT_ENDPOINT = _reasoning_endpoint
    AZURE_TEXT_API_KEY = os.getenv("AZURE_OPENAI_REASONING_API_KEY")
else:
    AZURE_TEXT_ENDPOINT = (os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("ENDPOINT_URL") or "").rstrip("/")
    AZURE_TEXT_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_TEXT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

# Image generation runs on the Microsoft Foundry GPT Image 2.5 deployment, which
# is served from the OpenAI v1-compatible endpoint (openai.OpenAI, not AzureOpenAI).
FOUNDRY_IMAGE_ENDPOINT = (
    os.getenv("FOUNDRY_GPT_IMAGE_ENDPOINT")
    or "https://testaistudio0307049060.services.ai.azure.com/openai/v1"
).rstrip("/")
FOUNDRY_IMAGE_API_KEY = os.getenv("FOUNDRY_GPT_IMAGE_API_KEY")
AZURE_IMAGE_DEPLOYMENT = os.getenv("FOUNDRY_GPT_IMAGE_DEPLOYMENT") or "gpt-image-2.5-sunburst"
FOUNDRY_IMAGE_TIMEOUT_SECONDS = max(30.0, _float_env("FOUNDRY_GPT_IMAGE_TIMEOUT", "300"))
_TEXT_CLIENT: AzureOpenAI | OpenAI | None = None
_VIDEO_CLIENT: AsyncOpenAI | None = None
_IMAGE_CLIENT: OpenAI | None = None


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "ERROR").upper()
    level = getattr(logging, level_name, logging.ERROR)
    quiet_uvicorn = (os.getenv("QUIET_UVICORN_LOGS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
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
    if quiet_uvicorn:
        _quiet_uvicorn_logs()
    logger.debug("Logging configured at %s", level_name)
    return logger


logger = _configure_logging()


def azure_text_client() -> AzureOpenAI | OpenAI | None:
    """Create or reuse the shared client for the reasoning deployment.

    Endpoints ending in ``/openai/v1`` (Foundry v1 API) use ``openai.OpenAI``;
    classic Azure OpenAI resource endpoints use ``AzureOpenAI``.
    """

    logger.debug(
        "Azure reasoning config deployment=%s effort=%s endpoint=%s",
        REASONING_DEPLOYMENT,
        REASONING_EFFORT,
        AZURE_TEXT_ENDPOINT,
    )

    if not AZURE_TEXT_ENDPOINT or not AZURE_TEXT_API_KEY:
        logger.debug("Azure OpenAI credentials not configured; skipping prompt refinement client.")
        return None

    global _TEXT_CLIENT  # type: ignore[assignment]
    try:
        client = _TEXT_CLIENT
    except NameError:
        client = None

    if client is None:
        if AZURE_TEXT_ENDPOINT.endswith("/openai/v1"):
            logger.debug("Creating OpenAI v1 client for reasoning deployment")
            client = OpenAI(base_url=AZURE_TEXT_ENDPOINT, api_key=AZURE_TEXT_API_KEY)
        else:
            logger.debug("Creating AzureOpenAI client for reasoning deployment")
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


def reasoning_request_options(max_output_tokens: int) -> dict[str, Any]:
    """Return the shared Responses API kwargs for the reasoning deployment.

    ``max_output_tokens`` is the budget for the visible answer; reasoning
    headroom is added so medium-effort thinking cannot starve the output.
    """

    return {
        "model": REASONING_DEPLOYMENT,
        "reasoning": {"effort": REASONING_EFFORT},
        "max_output_tokens": max_output_tokens + REASONING_TOKEN_HEADROOM,
    }


def azure_image_client() -> OpenAI | None:
    """Create or reuse a shared Foundry GPT Image 2.5 client.

    SDK retries are disabled: a timed-out image request may already have been
    processed, and an automatic retry would be billed twice.
    """

    if not FOUNDRY_IMAGE_API_KEY:
        logger.debug("FOUNDRY_GPT_IMAGE_API_KEY not configured; skipping image client.")
        return None

    global _IMAGE_CLIENT  # type: ignore[assignment]
    if _IMAGE_CLIENT is None:
        _IMAGE_CLIENT = OpenAI(
            base_url=FOUNDRY_IMAGE_ENDPOINT,
            api_key=FOUNDRY_IMAGE_API_KEY,
            timeout=FOUNDRY_IMAGE_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _IMAGE_CLIENT


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
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3002",
            "http://127.0.0.1:3002",
        ]
    values = [origin.strip() for origin in configured.split(",")]
    return [origin for origin in values if origin]


__all__ = [
    "AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID",
    "AZURE_CONTENT_UNDERSTANDING_API_KEY",
    "AZURE_CONTENT_UNDERSTANDING_API_VERSION",
    "AZURE_CONTENT_UNDERSTANDING_ENDPOINT",
    "AZURE_IMAGE_DEPLOYMENT",
    "AZURE_TEXT_API_KEY",
    "AZURE_TEXT_API_VERSION",
    "AZURE_TEXT_ENDPOINT",
    "CONTENT_UNDERSTANDING_MAX_UPLOAD_BYTES",
    "JSON_IMAGE_MAX_UPLOAD_BYTES",
    "DEFAULT_SUBPROCESS_TIMEOUT_SECONDS",
    "CONTENT_UNDERSTANDING_POLL_INTERVAL",
    "CONTENT_UNDERSTANDING_POLL_TIMEOUT",
    "DATA_URL_PATTERN",
    "DEFAULT_SORA_RESOLUTION",
    "DEFAULT_IMAGE_GENERATOR",
    "DEFAULT_REPRODUCE_REFERENCE_MODE",
    "DEFAULT_REPRODUCE_SEGMENTATION",
    "DEFAULT_VIDEO_PROVIDER",
    "FINAL_VIDEO_PATH",
    "FOUNDRY_IMAGE_API_KEY",
    "FOUNDRY_IMAGE_ENDPOINT",
    "FOUNDRY_IMAGE_TIMEOUT_SECONDS",
    "HEADER_PATTERN",
    "JIMENG_ACCESS_KEY_ID",
    "JIMENG_ALLOWED_DURATIONS",
    "JIMENG_API_BASE_URL",
    "JIMENG_POLL_INTERVAL_SECONDS",
    "JIMENG_POLL_TIMEOUT_SECONDS",
    "JIMENG_REGION",
    "JIMENG_REQ_KEY",
    "JIMENG_SECRET_ACCESS_KEY",
    "JIMENG_SECURITY_TOKEN",
    "JIMENG_SERVICE",
    "JIMENG_SUPPORTED_ASPECT_RATIOS",
    "ARK_API_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_ACCOUNT_ID",
    "R2_BUCKET",
    "R2_JURISDICTION",
    "R2_PUBLIC_BASE_URL",
    "R2_SECRET_ACCESS_KEY",
    "R2_UPLOAD_PREFIX",
    "SEEDANCE_ALLOWED_DURATIONS",
    "SEEDANCE_API_URL",
    "SEEDANCE_GENERATE_AUDIO",
    "SEEDANCE_IMAGE_ROLE",
    "SEEDANCE_MODEL",
    "SEEDANCE_POLL_INTERVAL_SECONDS",
    "SEEDANCE_POLL_TIMEOUT_SECONDS",
    "SEEDANCE_RESOLUTION",
    "SEEDANCE_SUPPORTED_ASPECT_RATIOS",
    "SEEDANCE_WATERMARK",
    "KLING_API_BASE_URL",
    "KLING_API_KEY",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "KLING_TOKEN_TTL_SECONDS",
    "KLING_DEFAULT_MODE",
    "KLING_DEFAULT_MODEL_NAME",
    "KLING_DOWNLOAD_CHUNK_SIZE",
    "KLING_CREATE_MAX_RETRIES",
    "KLING_CREATE_RETRY_BASE_DELAY_SECONDS",
    "KLING_CREATE_RETRY_MAX_DELAY_SECONDS",
    "KLING_CREATE_RETRY_JITTER_SECONDS",
    "KLING_POLL_INTERVAL_SECONDS",
    "KLING_POLL_TIMEOUT_SECONDS",
    "KLING_ALLOWED_DURATIONS",
    "OUTPUT_DIR",
    "PROMPTS_DIR",
    "REASONING_DEPLOYMENT",
    "REASONING_EFFORT",
    "REASONING_TOKEN_HEADROOM",
    "SUPPORTED_REASONING_EFFORTS",
    "RESOLUTION_PATTERN",
    "ROOT",
    "REPRODUCE_DEFAULT_THREADS",
    "SUPPORTED_REPRODUCE_REFERENCE_MODES",
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
    "SORA_TRANSIENT_RETRIES",
    "SORA_RETRY_BASE_DELAY_SECONDS",
    "SORA_RETRY_MAX_DELAY_SECONDS",
    "SORA_RETRY_JITTER_SECONDS",
    "STATIC_URL_PREFIX",
    "TMP_DIR",
    "WORKFLOW_DB_PATH",
    "allowed_origins",
    "azure_image_client",
    "azure_text_client",
    "extract_response_text",
    "logger",
    "normalize_image_generator",
    "normalize_video_provider",
    "reasoning_request_options",
    "allowed_durations_for_provider",
    "video_client",
]
