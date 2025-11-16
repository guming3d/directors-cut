"""Three-agent workflow for stitching a Video using Azure Sora2 api."""

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from openai import AzureOpenAI
from pydantic import BaseModel, ConfigDict, Field

# -------------------------
# Paths & constants
# -------------------------

ROOT = Path(__file__).parent
SCRIPT_PATH = ROOT / "Chapters" / "video_script.txt"
OUTPUT_DIR = ROOT / "output"
PROMPTS_DIR = OUTPUT_DIR / "prompts"
SCENES_DIR = OUTPUT_DIR / "scenes"
FINAL_VIDEO_PATH = OUTPUT_DIR / "sora_story_compilation.mp4"
SORA_CLIENT = ROOT / "sora-openai-client.py"
TMP_DIR = OUTPUT_DIR / "tmp"
SINGLE_VIDEO_JOBS_DIR = OUTPUT_DIR / "single_videos"
DEFAULT_SORA_RESOLUTION = "720x1280"
SCENE_IMAGE_DIR = ROOT / "scene_start_screen"
SCENE_IMAGE_PATTERN = re.compile(r"scene0*(\d+)\.(?:png|jpe?g)$", re.IGNORECASE)
DATA_URL_PATTERN = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.IGNORECASE)
SCENE_IMAGE_SIZE = (720, 1280)
try:
    SCENE_IMAGE_RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - Pillow < 10 fallback
    SCENE_IMAGE_RESAMPLE = Image.LANCZOS

SORA_CLIP_DURATION_SECONDS = 4
HEADER_PATTERN = re.compile(r"^\*\*\s*(Scene\s+(\d+)[^*]*)\*\*$", re.IGNORECASE)
SORA_PROMPT_GUIDE_URL = "https://cookbook.openai.com/examples/sora/sora2_prompting_guide"
RESOLUTION_PATTERN = re.compile(r"^\d+x\d+$")

DIRECTOR_REFINEMENT_DEPLOYMENT = (
    os.getenv("DIRECTOR_REFINEMENT_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_GPT41_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_RESPONSES_DEPLOYMENT")
    or os.getenv("AZURE_OPENAI_DEPLOYMENT")
)
AZURE_TEXT_ENDPOINT = (os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("ENDPOINT_URL") or "").rstrip("/")
AZURE_TEXT_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_TEXT_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
_TEXT_CLIENT: AzureOpenAI | None = None


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
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


def _azure_text_client() -> AzureOpenAI | None:
    """Create or reuse a shared Azure OpenAI client for GPT-4.1 refinement."""

    if not DIRECTOR_REFINEMENT_DEPLOYMENT:
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


def _extract_response_text(response: Any) -> str:
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


def _static_asset_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    file_path = Path(path)
    try:
        rel = file_path.relative_to(OUTPUT_DIR)
    except ValueError:
        return None
    return f"{STATIC_URL_PREFIX}/{rel.as_posix()}"


class EncodedImagePayload(BaseModel):
    """Base64-encoded image sent from the frontend."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    filename: str
    data_url: str = Field(
        ..., alias="dataUrl", description="data URL string containing base64 image content"
    )
    content_type: Optional[str] = Field(default=None, alias="contentType")


def _decode_image_payload(upload: EncodedImagePayload) -> tuple[Optional[str], bytes]:
    data_url = upload.data_url.strip()
    match = DATA_URL_PATTERN.match(data_url)
    if not match:
        raise ValueError("Unsupported image encoding; expected data URL with base64 content.")
    encoded = match.group("data")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (base64.binascii.Error, ValueError) as exc:  # type: ignore[attr-defined]
        raise ValueError("Failed to decode base64 image content.") from exc
    mime = match.group("mime") or upload.content_type
    return mime, data


def _save_payload_as_png(upload: EncodedImagePayload, dest: Path) -> Path:
    mime, data = _decode_image_payload(upload)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as img:
            mode = "RGBA" if "A" in img.getbands() else "RGB"
            converted = img.convert(mode)
            converted.save(dest, format="PNG")
    except Exception:
        # Fallback to raw bytes if Pillow cannot parse (should be rare)
        dest.write_bytes(data)
    logger.debug("Saved image upload %s (%s) -> %s", upload.filename, mime, dest)
    return dest


def _persist_scene_image_uploads(dest_dir: Path, uploads: Sequence[EncodedImagePayload]) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx, upload in enumerate(uploads, start=1):
        filename = dest_dir / f"scene{idx}.png"
        _save_payload_as_png(upload, filename)


def _resolve_scene_image_dir(
    job_dir: Path,
    uploads: Sequence[EncodedImagePayload] | None,
    override_dir: Optional[str],
) -> Path:
    if uploads:
        dest_dir = job_dir / "scene_images"
        dest_dir.mkdir(parents=True, exist_ok=True)
        base_dir = (
            Path(override_dir).expanduser().resolve()
            if override_dir
            else SCENE_IMAGE_DIR
        )
        if base_dir.is_dir():
            for item in base_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_dir / item.name)
        elif override_dir:
            logger.warning(
                "Scene image override directory %s not found; using uploads only.",
                override_dir,
            )
        _persist_scene_image_uploads(dest_dir, uploads)
        return dest_dir

    if override_dir:
        resolved = Path(override_dir).expanduser().resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(
                f"Scene image directory override not found: {resolved}"
            )
        return resolved

    return SCENE_IMAGE_DIR


# -------------------------
# Data models
# -------------------------


class VideoWorkflowRequest(BaseModel):
    """Configuration for the multi-agent video workflow."""

    script_path: str = Field(
        default=str(SCRIPT_PATH), description="Path to the master video script."
    )
    output_dir: str = Field(default=str(OUTPUT_DIR), description="Root output folder.")
    prompt_dir: str = Field(
        default=str(PROMPTS_DIR),
        description="Directory for generated prompt text files.",
    )
    scenes_dir: str = Field(
        default=str(SCENES_DIR), description="Directory for downloaded scene MP4s."
    )
    tmp_dir: str = Field(
        default=str(TMP_DIR), description="Directory for intermediate artifacts."
    )
    final_video_path: str = Field(
        default=str(FINAL_VIDEO_PATH), description="Final stitched MP4 path."
    )
    sora_client_path: str = Field(
        default=str(SORA_CLIENT), description="Path to sora-openai-client.py."
    )
    scene_image_dir: str = Field(
        default=str(SCENE_IMAGE_DIR),
        description="Directory containing scene start screen images (scene1.png, scene2.png, ...).",
    )
    use_scene_images: bool = Field(
        default=True,
        description="Whether to include scene start screen images when rendering Sora clips.",
    )
    resolution: str = Field(
        default=DEFAULT_SORA_RESOLUTION,
        description="Clip resolution string, e.g. 720x1280.",
    )
    thread_count: int = Field(
        default=int(os.getenv("SORA_WORKFLOW_THREADS", "3")),
        ge=1,
        le=10,
        description="Parallel Sora job count.",
    )
    download_variant: str = Field(
        default=os.getenv("VIDEO_DOWNLOAD_VARIANT", "video"),
        description="Variant name passed to sora-openai-client.",
    )
    job_id: Optional[str] = Field(
        default=None,
        description="Server-assigned workflow job identifier used for progress tracking.",
    )


STATIC_URL_PREFIX = "/static"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProgressMessage:
    timestamp: datetime
    stage: str
    text: str


@dataclass
class SceneStatus:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None


@dataclass
class WorkflowJobState:
    job_id: str
    status: str = "queued"
    stage: Optional[str] = None
    progress: float = 0.0
    resolution: str = DEFAULT_SORA_RESOLUTION
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    final_video_path: Optional[str] = None
    manifest: Optional[dict[str, Any]] = None
    total_scenes: int = 0
    completed_scenes: int = 0
    messages: list[ProgressMessage] = field(default_factory=list)
    scenes: list[SceneStatus] = field(default_factory=list)

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=_utc_now(), stage=stage, text=text))


class JobMessageModel(BaseModel):
    timestamp: datetime
    stage: str
    text: str


class SceneStatusModel(BaseModel):
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    video_url: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: Optional[str]
    progress: float
    resolution: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    final_video_path: Optional[str]
    final_video_url: Optional[str]
    manifest: Optional[dict[str, Any]]
    messages: list[JobMessageModel]
    scenes: list[SceneStatusModel]


class WorkflowJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, WorkflowJobState] = {}
        self._lock = asyncio.Lock()

    def _require(self, job_id: str) -> WorkflowJobState:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def create_job(self, job_id: str) -> None:
        async with self._lock:
            state = WorkflowJobState(job_id=job_id)
            state.add_message("queued", "Job queued.")
            self._jobs[job_id] = state

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.stage = "initializing"
            job.progress = max(job.progress, 0.01)
            job.add_message("initializing", "Workflow execution started.")

    async def update_stage(
        self,
        job_id: str,
        stage: str,
        message: Optional[str] = None,
        progress: Optional[float] = None,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.stage = stage
            if progress is not None:
                job.progress = max(job.progress, min(max(progress, 0.0), 1.0))
            if message:
                job.add_message(stage, message)

    async def set_total_scenes(self, job_id: str, total: int) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.total_scenes = max(total, 0)

    async def set_resolution(self, job_id: str, resolution: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.resolution = resolution

    async def record_scene_completion(self, job_id: str, render: "SceneRender") -> None:
        async with self._lock:
            job = self._require(job_id)
            job.completed_scenes += 1
            job.scenes.append(
                SceneStatus(
                    order=render.order,
                    label=render.label,
                    duration_seconds=render.duration_seconds,
                    prompt_path=render.prompt_path,
                    video_path=render.video_path,
                    log_path=render.log_path,
                    client_log_path=render.client_log_path,
                )
            )
            total = job.total_scenes or 1
            fraction = min(job.completed_scenes / total, 1.0)
            progress = 0.2 + 0.6 * fraction
            job.progress = max(job.progress, progress)
            job.stage = "rendering"
            job.add_message("rendering", f"Completed {render.label}.")

    async def mark_composing(self, job_id: str, message: str | None = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.stage = "composing"
            job.progress = max(job.progress, 0.85)
            if message:
                job.add_message("composing", message)

    async def mark_completed(
        self, job_id: str, final_video_path: str, manifest: dict[str, Any]
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1.0
            job.completed_at = _utc_now()
            job.final_video_path = final_video_path
            job.manifest = manifest
            job.add_message("completed", "Workflow complete.")

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.stage = "failed"
            job.error = error
            job.completed_at = _utc_now()
            job.add_message("failed", error)

    def _snapshot(self, job: WorkflowJobState) -> JobStatusResponse:
        messages = [
            JobMessageModel(timestamp=item.timestamp, stage=item.stage, text=item.text)
            for item in job.messages
        ]
        scenes = [
            SceneStatusModel(
                order=scene.order,
                label=scene.label,
                duration_seconds=scene.duration_seconds,
                prompt_path=scene.prompt_path,
                video_path=scene.video_path,
                video_url=_static_asset_url(scene.video_path),
                log_path=scene.log_path,
                client_log_path=scene.client_log_path,
            )
            for scene in sorted(job.scenes, key=lambda item: item.order)
        ]
        return JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            resolution=job.resolution,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            final_video_path=job.final_video_path,
            final_video_url=_static_asset_url(job.final_video_path),
            manifest=job.manifest,
            messages=messages,
            scenes=scenes,
        )

    async def get_status(self, job_id: str) -> JobStatusResponse:
        async with self._lock:
            job = self._require(job_id)
            return self._snapshot(job)

    async def list_statuses(self) -> list[JobStatusResponse]:
        async with self._lock:
            return [
                self._snapshot(job)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda item: item.started_at,
                    reverse=True,
                )
            ]

    async def delete_job(self, job_id: str) -> WorkflowJobState:
        async with self._lock:
            job = self._require(job_id)
            if job.status == "running":
                raise ValueError("Cannot delete a workflow while it is running.")
            return self._jobs.pop(job_id)


JOB_STORE = WorkflowJobStore()


@dataclass
class SingleVideoJobState:
    job_id: str
    prompt: str
    resolution: str
    duration: int
    download_variant: str
    status: str = "queued"
    progress: float = 0.0
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    video_path: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    messages: list[ProgressMessage] = field(default_factory=list)

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=_utc_now(), stage=stage, text=text))


@dataclass
class SingleVideoJobConfig:
    job_id: str
    prompt_path: Path
    prompt_text: str
    resolution: str
    duration: int
    download_variant: str
    video_path: Path
    reference_image_path: Optional[Path]
    job_dir: Path


class SingleVideoJobResponse(BaseModel):
    job_id: str
    status: str
    progress: float
    prompt: str
    resolution: str
    duration: int
    download_variant: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[str]
    video_path: Optional[str]
    video_url: Optional[str]
    log_path: Optional[str]
    log_url: Optional[str]
    client_log_path: Optional[str]
    messages: list[JobMessageModel]


class SingleVideoJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, SingleVideoJobState] = {}
        self._lock = asyncio.Lock()

    def _require(self, job_id: str) -> SingleVideoJobState:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def create_job(
        self,
        job_id: str,
        prompt: str,
        resolution: str,
        duration: int,
        download_variant: str,
    ) -> None:
        async with self._lock:
            state = SingleVideoJobState(
                job_id=job_id,
                prompt=prompt,
                resolution=resolution,
                duration=duration,
                download_variant=download_variant,
            )
            state.add_message("queued", "Job queued.")
            self._jobs[job_id] = state

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.progress = max(job.progress, 0.05)
            job.add_message("running", "Submitting video generation request to Sora2.")

    async def update_progress(self, job_id: str, progress: float, message: str | None = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.progress = max(job.progress, min(max(progress, 0.0), 1.0))
            if message:
                job.add_message("running", message)

    async def set_artifacts(
        self,
        job_id: str,
        video_path: Optional[str],
        log_path: Optional[str],
        client_log_path: Optional[str],
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_path = video_path
            job.log_path = log_path
            job.client_log_path = client_log_path

    async def mark_completed(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "completed"
            job.progress = 1.0
            job.completed_at = _utc_now()
            job.add_message("completed", "Video generation finished.")

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.error = error
            job.completed_at = _utc_now()
            job.add_message("failed", error)

    async def snapshot(self, job_id: str) -> SingleVideoJobResponse:
        async with self._lock:
            job = self._require(job_id)
            return self._snapshot(job)

    async def list_statuses(self) -> list[SingleVideoJobResponse]:
        async with self._lock:
            return [
                self._snapshot(job)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda item: item.started_at,
                    reverse=True,
                )
            ]

    async def delete_job(self, job_id: str) -> SingleVideoJobState:
        async with self._lock:
            job = self._require(job_id)
            if job.status == "running":
                raise ValueError("Cannot delete a single-video job while it is running.")
            return self._jobs.pop(job_id)

    def _snapshot(self, job: SingleVideoJobState) -> SingleVideoJobResponse:
        messages = [
            JobMessageModel(timestamp=item.timestamp, stage=item.stage, text=item.text)
            for item in job.messages
        ]
        return SingleVideoJobResponse(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress,
            prompt=job.prompt,
            resolution=job.resolution,
            duration=job.duration,
            download_variant=job.download_variant,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            video_path=job.video_path,
            video_url=_static_asset_url(job.video_path),
            log_path=job.log_path,
            log_url=_static_asset_url(job.log_path),
            client_log_path=job.client_log_path,
            messages=messages,
        )


SINGLE_VIDEO_STORE = SingleVideoJobStore()


class StartWorkflowPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    script: str = Field(
        ..., description="Storyboard script content for the Director agent to process."
    )
    threads: Optional[int] = Field(
        default=None, ge=1, le=10, description="Override concurrent Sora render threads."
    )
    resolution: Optional[str] = Field(
        default=None,
        description="Optional override for render resolution in WIDTHxHEIGHT format.",
    )
    scene_image_dir: Optional[str] = Field(
        default=None,
        description="Optional override for the directory containing scene start screen images.",
    )
    use_scene_images: Optional[bool] = Field(
        default=None,
        alias="useSceneImages",
        description="Set to false to skip providing scene start screen images.",
    )
    download_variant: Optional[str] = Field(
        default=None,
        description="Optional override for the VIDEO_DOWNLOAD_VARIANT sent to the Sora client.",
    )
    scene_images: Optional[list[EncodedImagePayload]] = Field(
        default=None,
        alias="sceneImages",
        description="Optional list of uploaded scene start screen images (data URLs).",
    )


class SingleVideoPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str = Field(..., description="Prompt text for a single Sora2 video generation.")
    resolution: str = Field(
        default=DEFAULT_SORA_RESOLUTION,
        description="Output resolution (e.g. 720x1280).",
    )
    duration: Literal[4, 8, 12] = Field(
        default=8, description="Video duration in seconds (allowed: 4, 8, 12)."
    )
    download_variant: Optional[str] = Field(
        default=None,
        alias="downloadVariant",
        description="Optional override for VIDEO_DOWNLOAD_VARIANT when downloading the asset.",
    )
    reference_image: Optional[EncodedImagePayload] = Field(
        default=None,
        alias="referenceImage",
        description="Optional input reference image encoded as a base64 data URL.",
    )


def _normalize_script_text(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Script text cannot be empty.")
    if not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def _job_root(job_id: str) -> Path:
    return OUTPUT_DIR / job_id


def _prepare_request_for_job(job_id: str, payload: StartWorkflowPayload) -> VideoWorkflowRequest:
    job_dir = _job_root(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = job_dir / "prompts"
    scenes_dir = job_dir / "scenes"
    tmp_dir = job_dir / "tmp"
    final_video = job_dir / "sora_story_compilation.mp4"

    for path in (prompts_dir, scenes_dir, tmp_dir):
        path.mkdir(parents=True, exist_ok=True)

    script_path = job_dir / "script.txt"
    script_path.write_text(_normalize_script_text(payload.script), encoding="utf-8")

    resolution = (payload.resolution or DEFAULT_SORA_RESOLUTION).strip()
    if not RESOLUTION_PATTERN.match(resolution):
        raise ValueError(
            "Resolution must be in WIDTHxHEIGHT format, e.g. 720x1280."
        )

    use_scene_images = payload.use_scene_images
    if use_scene_images is None:
        use_scene_images = True

    thread_count = payload.threads or int(os.getenv("SORA_WORKFLOW_THREADS", "3"))
    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
    uploads = payload.scene_images or []
    scene_image_override = payload.scene_image_dir

    scene_image_dir_path = SCENE_IMAGE_DIR
    if use_scene_images:
        try:
            scene_image_dir_path = _resolve_scene_image_dir(
                job_dir,
                uploads,
                scene_image_override,
            )
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    request = VideoWorkflowRequest(
        script_path=str(script_path),
        output_dir=str(job_dir),
        prompt_dir=str(prompts_dir),
        scenes_dir=str(scenes_dir),
        tmp_dir=str(tmp_dir),
        final_video_path=str(final_video),
        sora_client_path=str(SORA_CLIENT),
        scene_image_dir=str(scene_image_dir_path),
        use_scene_images=use_scene_images,
        resolution=resolution,
        thread_count=thread_count,
        download_variant=download_variant,
        job_id=job_id,
    )
    return request


def _prepare_single_video_job(job_id: str, payload: SingleVideoPayload) -> SingleVideoJobConfig:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt text is required.")

    resolution = (payload.resolution or DEFAULT_SORA_RESOLUTION).strip()
    if not RESOLUTION_PATTERN.match(resolution):
        raise ValueError("Resolution must be in WIDTHxHEIGHT format, e.g. 720x1280.")

    duration = int(payload.duration)
    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")

    job_dir = SINGLE_VIDEO_JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = job_dir / "prompt.txt"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    reference_path: Optional[Path] = None
    if payload.reference_image:
        reference_path = _save_payload_as_png(payload.reference_image, job_dir / "reference.png")

    video_path = job_dir / "sora_single.mp4"

    return SingleVideoJobConfig(
        job_id=job_id,
        prompt_path=prompt_path,
        prompt_text=prompt,
        resolution=resolution,
        duration=duration,
        download_variant=download_variant,
        video_path=video_path,
        reference_image_path=reference_path,
        job_dir=job_dir,
    )


async def _run_workflow_job(job_id: str, request: VideoWorkflowRequest) -> None:
    try:
        await JOB_STORE.mark_running(job_id)
        await workflow.run(request)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Workflow %s failed: %s", job_id, exc)
        await JOB_STORE.mark_failed(job_id, str(exc))


async def _run_single_video_job(config: SingleVideoJobConfig) -> None:
    job_id = config.job_id
    try:
        await SINGLE_VIDEO_STORE.mark_running(job_id)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.1, "Render request submitted.")

        env = os.environ.copy()
        env.update(
            {
                "VIDEO_OUTPUT_PATH": str(config.video_path),
                "VIDEO_DOWNLOAD_VARIANT": config.download_variant,
            }
        )

        cmd = [
            sys.executable,
            str(SORA_CLIENT),
            "--prompt-file",
            str(config.prompt_path),
            "--resolution",
            config.resolution,
            "--duration",
            str(config.duration),
        ]
        if config.reference_image_path:
            cmd.extend(["--input-image", str(config.reference_image_path)])

        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.75, "Download completed, finalizing artifacts.")

        client_log_path = config.video_path.with_suffix(".client.log")
        client_log_path.write_text(
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )

        if not config.video_path.is_file():
            raise FileNotFoundError(
                "Sora client did not produce the expected video file. Check Azure credentials and VIDEO_OUTPUT_PATH."
            )

        sora_log_path = config.video_path.with_suffix(".json")
        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(config.video_path),
            str(sora_log_path) if sora_log_path.is_file() else None,
            str(client_log_path),
        )
        await SINGLE_VIDEO_STORE.mark_completed(job_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Single video job %s failed: %s", job_id, exc)
        await SINGLE_VIDEO_STORE.mark_failed(job_id, str(exc))


def _allowed_origins() -> list[str]:
    configured = os.getenv("SORA_WORKFLOW_ALLOWED_ORIGINS")
    if not configured:
        return ["*"]
    values = [origin.strip() for origin in configured.split(",")]
    return [origin for origin in values if origin]


fastapi_app = FastAPI(
    title="Sora Agent Workflow API",
    description="Async workflow orchestrator for Azure Sora multi-agent pipeline.",
)

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fastapi_app.mount(
    STATIC_URL_PREFIX,
    StaticFiles(directory=str(OUTPUT_DIR), check_dir=False),
    name="output",
)


@fastapi_app.post("/api/workflows", response_model=JobStatusResponse)
async def start_workflow(payload: StartWorkflowPayload) -> JobStatusResponse:
    script = payload.script or ""
    if not script.strip():
        raise HTTPException(status_code=400, detail="Script text is required.")

    job_id = uuid.uuid4().hex
    await JOB_STORE.create_job(job_id)
    try:
        request = _prepare_request_for_job(job_id, payload)
        await JOB_STORE.set_resolution(job_id, request.resolution)
    except ValueError as exc:
        await JOB_STORE.mark_failed(job_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    await JOB_STORE.update_stage(job_id, "queued", "Script accepted.", progress=0.0)
    asyncio.create_task(_run_workflow_job(job_id, request))
    return await JOB_STORE.get_status(job_id)


@fastapi_app.get("/api/workflows", response_model=list[JobStatusResponse])
async def list_workflows() -> list[JobStatusResponse]:
    return await JOB_STORE.list_statuses()


@fastapi_app.get("/api/workflows/{job_id}", response_model=JobStatusResponse)
async def get_workflow(job_id: str) -> JobStatusResponse:
    try:
        return await JOB_STORE.get_status(job_id)
    except KeyError as exc:  # pragma: no cover - FastAPI translates to HTTP response
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/workflows/{job_id}")
async def delete_workflow(job_id: str) -> dict[str, str]:
    try:
        await JOB_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(_job_root(job_id), ignore_errors=True)
    return {"status": "deleted"}


@fastapi_app.post("/api/single-videos", response_model=SingleVideoJobResponse)
async def start_single_video(payload: SingleVideoPayload) -> SingleVideoJobResponse:
    job_id = uuid.uuid4().hex
    try:
        config = _prepare_single_video_job(job_id, payload)
    except ValueError as exc:
        shutil.rmtree(SINGLE_VIDEO_JOBS_DIR / job_id, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await SINGLE_VIDEO_STORE.create_job(
        job_id,
        config.prompt_text,
        config.resolution,
        config.duration,
        config.download_variant,
    )
    asyncio.create_task(_run_single_video_job(config))
    return await SINGLE_VIDEO_STORE.snapshot(job_id)


@fastapi_app.get("/api/single-videos", response_model=list[SingleVideoJobResponse])
async def list_single_videos() -> list[SingleVideoJobResponse]:
    return await SINGLE_VIDEO_STORE.list_statuses()


@fastapi_app.get("/api/single-videos/{job_id}", response_model=SingleVideoJobResponse)
async def get_single_video(job_id: str) -> SingleVideoJobResponse:
    try:
        return await SINGLE_VIDEO_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/single-videos/{job_id}")
async def delete_single_video(job_id: str) -> dict[str, str]:
    try:
        await SINGLE_VIDEO_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(SINGLE_VIDEO_JOBS_DIR / job_id, ignore_errors=True)
    return {"status": "deleted"}


async def _report_progress(
    request: VideoWorkflowRequest,
    stage: str,
    message: str,
    progress: Optional[float] = None,
) -> None:
    logger.info("[%s] %s", stage, message)
    if request.job_id:
        await JOB_STORE.update_stage(request.job_id, stage, message, progress)


async def _record_total_scenes(request: VideoWorkflowRequest, total: int) -> None:
    if request.job_id:
        await JOB_STORE.set_total_scenes(request.job_id, total)


async def _record_scene_completion(
    request: VideoWorkflowRequest, render: "SceneRender"
) -> None:
    if request.job_id:
        await JOB_STORE.record_scene_completion(request.job_id, render)


async def _mark_composing(request: VideoWorkflowRequest, message: str | None = None) -> None:
    if request.job_id:
        await JOB_STORE.mark_composing(request.job_id, message)


async def _mark_completed(
    request: VideoWorkflowRequest, final_video_path: str, manifest: dict[str, Any]
) -> None:
    if request.job_id:
        await JOB_STORE.mark_completed(request.job_id, final_video_path, manifest)


@dataclass
class SceneDraft:
    order: int
    scene_number: int
    title: str
    description_lines: list[str]


@dataclass
class ScenePlan:
    order: int
    label: str
    duration_seconds: int
    description: str
    prompt_path: str
    prompt_text: str


@dataclass
class ScenePlanBatch:
    plans: list[ScenePlan]
    assigned_total_seconds: int


@dataclass
class SceneRender:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: str | None
    client_log_path: str | None


@dataclass
class SceneRenderBatch:
    renders: list[SceneRender]
    assigned_total_seconds: int

# -------------------------
# Helpers
# -------------------------
def read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required script file: {path}")
    return path.read_text(encoding="utf-8").strip()


def parse_script(script_text: str) -> list[SceneDraft]:
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


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "scene"


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


async def refine_scene_description(title: str, description: str) -> str:
    normalized = description.strip()
    if not normalized:
        return description

    client = _azure_text_client()
    if client is None:
        return description

    system_prompt = (
        "You are an expert cinematic prompt engineer for Azure OpenAI Sora2. "
        f"Incorporate the best practices from {SORA_PROMPT_GUIDE_URL} to polish scene descriptions. "
        "Produce concise bullet-point guidance that covers camera direction, subject detail, motion, and audio cues suitable for a 4-second clip."
    )
    user_prompt = (
        f"Scene title: {title}\n"
        "Clip length: 4 seconds.\n"
        "Original storyboard notes:\n"
        f"{normalized}\n\n"
        "Rewrite the notes into 4-6 focused bullet points optimized for Sora2."
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
        return _extract_response_text(response)

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


def escape_for_concat(path: Path) -> str:
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def _resize_scene_image(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        has_alpha = "A" in img.getbands()
        mode = "RGBA" if has_alpha else "RGB"
        converted = img.convert(mode)

        target_w, target_h = SCENE_IMAGE_SIZE
        src_w, src_h = converted.size
        if src_w == 0 or src_h == 0:
            raise ValueError(f"Invalid source image dimensions for {source}")

        scale = min(target_w / src_w, target_h / src_h)
        resized_w = max(1, int(round(src_w * scale)))
        resized_h = max(1, int(round(src_h * scale)))
        resized = converted.resize((resized_w, resized_h), SCENE_IMAGE_RESAMPLE)

        background_color = (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0)
        canvas = Image.new(mode, (target_w, target_h), background_color)
        offset_x = (target_w - resized_w) // 2
        offset_y = (target_h - resized_h) // 2
        mask = resized if mode == "RGBA" else None
        canvas.paste(resized, (offset_x, offset_y), mask)
        canvas.save(dest, format="PNG")


def prepare_scene_start_images(
    scene_dir: Path, tmp_dir: Path, scene_count: int
) -> list[str]:
    if scene_count <= 0:
        return []
    if not scene_dir.is_dir():
        raise FileNotFoundError(
            f"Scene start screen directory not found: {scene_dir}. Provide one with scene1.png, scene2.png, ..."
        )

    matches: dict[int, Path] = {}
    for entry in scene_dir.iterdir():
        if not entry.is_file():
            continue
        match = SCENE_IMAGE_PATTERN.fullmatch(entry.name)
        if match:
            index = int(match.group(1))
            matches[index] = entry

    if not matches:
        raise RuntimeError(
            f"No scene start screens found in {scene_dir}. Expected files named scene1.png, scene2.png, ..."
        )

    expected = set(range(1, scene_count + 1))
    available = set(matches)
    missing = sorted(expected - available)
    if missing:
        raise RuntimeError(
            "Scene/image count mismatch: missing "
            + ", ".join(f"scene{idx}.png" for idx in missing)
            + f". Ensure there is at least one start image per scene in {scene_dir}."
        )

    prepared_dir = tmp_dir / "scene_start_images"
    prepared_paths: list[str] = []
    for idx in range(1, scene_count + 1):
        source = matches[idx]
        dest = prepared_dir / f"scene{idx}_720x1280.png"
        _resize_scene_image(source, dest)
        prepared_paths.append(str(dest))
    return prepared_paths


async def run_subprocess(
    cmd: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode(errors="ignore")
    stderr = stderr_bytes.decode(errors="ignore")
    if process.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)} failed ({process.returncode}): {stderr or stdout}"
        )
    return stdout, stderr


def summarize_manifest(
    renders: list[SceneRender],
    final_video: Path,
    total_seconds: int,
    request: VideoWorkflowRequest,
) -> dict[str, Any]:
    return {
        "final_video": str(final_video),
        "assigned_seconds": total_seconds,
        "resolution": request.resolution,
        "scenes": [
            {
                "order": render.order,
                "label": render.label,
                "duration_seconds": render.duration_seconds,
                "prompt_path": render.prompt_path,
                "video_path": render.video_path,
                "sora_log_path": render.log_path,
                "client_capture": render.client_log_path,
            }
            for render in renders
        ],
    }


# -------------------------
# Agents
# -------------------------
class DirectorAgent(Executor):
    """Break the master script into Sora-ready scene prompts."""

    def __init__(self) -> None:
        super().__init__(id="director_agent")

    @handler
    async def handle(
        self, request: VideoWorkflowRequest, ctx: WorkflowContext[ScenePlanBatch]
    ) -> None:
        await ctx.set_shared_state("request", request)
        await _report_progress(
            request,
            "planning",
            "Director reviewing storyboard script.",
            progress=0.05,
        )
        script_text = read_text(Path(request.script_path))
        drafts = parse_script(script_text)
        if not drafts:
            raise RuntimeError(
                "No scene headers found in the script. Ensure it follows **Scene N (...)** format."
            )
        await _record_total_scenes(request, len(drafts))
        await _report_progress(
            request,
            "planning",
            f"Identified {len(drafts)} scene outline(s) from script.",
            progress=0.1,
        )

        prompt_dir = Path(request.prompt_dir)
        prompt_dir.mkdir(parents=True, exist_ok=True)

        plans: list[ScenePlan] = []
        for idx, draft in enumerate(drafts, start=1):
            duration = SORA_CLIP_DURATION_SECONDS
            description = "\n".join(draft.description_lines)
            slug = slugify(f"{draft.scene_number:02d}-{draft.title}")
            prompt_path = prompt_dir / f"scene_{idx:02d}_{slug}.txt"
            refined_description = await refine_scene_description(draft.title, description)
            prompt_text = build_prompt_text(
                draft.title, refined_description, duration, request.resolution
            )
            prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

            plans.append(
                ScenePlan(
                    order=idx,
                    label=f"Scene {draft.scene_number}: {draft.title}",
                    duration_seconds=duration,
                    description=refined_description,
                    prompt_path=str(prompt_path),
                    prompt_text=prompt_text,
                )
        )

        tmp_dir = Path(request.tmp_dir)
        if request.use_scene_images:
            scene_images = prepare_scene_start_images(
                Path(request.scene_image_dir), tmp_dir, len(plans)
            )
            await _report_progress(
                request,
                "planning",
                f"Prepared {len(plans)} prompt file(s) and start screens.",
                progress=0.2,
            )
        else:
            scene_images = []
            await _report_progress(
                request,
                "planning",
                f"Prepared {len(plans)} prompt file(s); scene start images disabled.",
                progress=0.2,
            )
        total_assigned = sum(plan.duration_seconds for plan in plans)
        await ctx.set_shared_state("scene_manifest", plans)
        await ctx.set_shared_state("scene_images", scene_images)
        await ctx.yield_output(
            f"Director planned {len(plans)} scene(s) totaling {total_assigned}s based on script detail."
        )
        await ctx.send_message(
            ScenePlanBatch(plans=plans, assigned_total_seconds=total_assigned)
        )


class Sora2Agent(Executor):
    """Generate each clip concurrently via the sora-openai-client."""

    def __init__(self) -> None:
        super().__init__(id="sora2_agent")

    @handler
    async def handle(
        self, batch: ScenePlanBatch, ctx: WorkflowContext[SceneRenderBatch]
    ) -> None:
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        scene_images: Sequence[str] = await ctx.get_shared_state("scene_images")  # type: ignore
        scenes_dir = Path(request.scenes_dir)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(max(1, request.thread_count))
        await _report_progress(
            request,
            "rendering",
            f"Rendering {len(batch.plans)} scene(s) with up to {request.thread_count} concurrent Sora jobs.",
            progress=0.25,
        )

        async def render_scene(plan: ScenePlan) -> SceneRender:
            async with semaphore:
                return await self._invoke_sora(plan, request, scenes_dir, scene_images)

        tasks = [asyncio.create_task(render_scene(plan)) for plan in batch.plans]
        renders: list[SceneRender] = []
        for task in asyncio.as_completed(tasks):
            render = await task
            renders.append(render)
            await _record_scene_completion(request, render)
            await ctx.yield_output(
                f"Sora completed {render.label} -> {render.video_path}"
            )

        renders.sort(key=lambda item: item.order)
        await ctx.send_message(
            SceneRenderBatch(
                renders=renders, assigned_total_seconds=batch.assigned_total_seconds
            )
        )

    async def _invoke_sora(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> SceneRender:
        scene_basename = f"scene_{plan.order:02d}.mp4"
        video_path = scenes_dir / scene_basename
        client_path = Path(request.sora_client_path)
        if not client_path.is_file():
            raise FileNotFoundError(f"sora-openai-client.py not found at {client_path}")
        await _report_progress(
            request,
            "rendering",
            f"Starting render for {plan.label}.",
        )
        env = os.environ.copy()
        env.update(
            {
                "VIDEO_OUTPUT_PATH": str(video_path),
                "VIDEO_DOWNLOAD_VARIANT": request.download_variant,
            }
        )
        cmd = [
            sys.executable,
            str(client_path),
            "--prompt-file",
            plan.prompt_path,
            "--resolution",
            request.resolution,
            "--duration",
            str(plan.duration_seconds),
        ]

        scene_index = plan.order - 1
        if request.use_scene_images:
            if scene_index < 0 or scene_index >= len(scene_images):
                raise RuntimeError(
                    f"Missing prepared start screen for {plan.label} (index {scene_index}). Verify plan/image alignment."
                )
            cmd.extend(["--input-image", scene_images[scene_index]])

        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
        client_log_path = video_path.with_suffix(".client.log")
        client_log_path.write_text(
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )

        if not video_path.is_file():
            raise FileNotFoundError(
                f"Sora client reported success but {video_path} was not created. "
                "Check your Azure credentials and VIDEO_OUTPUT_PATH permissions."
            )

        sora_log_path = video_path.with_suffix(".json")
        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
        )


class ComposerAgent(Executor):
    """Concatenate clips with ffmpeg, preserving the scripted runtime."""

    def __init__(self) -> None:
        super().__init__(id="composer_agent")

    @handler
    async def handle(
        self, batch: SceneRenderBatch, ctx: WorkflowContext[None, str]
    ) -> None:  # type: ignore
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        final_video = Path(request.final_video_path)
        final_video.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(request.tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        concat_list = tmp_dir / "concat.txt"
        await _mark_composing(
            request, "Preparing final composition with ffmpeg concat."
        )

        with open(concat_list, "w", encoding="utf-8") as fh:
            for render in batch.renders:
                fh.write(escape_for_concat(Path(render.video_path)) + "\n")

        await run_subprocess(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(final_video),
            ]
        )

        manifest = summarize_manifest(
            batch.renders,
            final_video,
            batch.assigned_total_seconds,
            request,
        )
        manifest_path = final_video.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        await _mark_completed(request, str(final_video), manifest)

        await ctx.yield_output(f"Composer saved final video to {final_video}")
        await ctx.yield_output(f"Manifest stored at {manifest_path}")
        await ctx.yield_output("Workflow complete!")


# -------------------------
# Workflow builder
# -------------------------


def create_sora_video_workflow():
    director = DirectorAgent()
    sora_agent = Sora2Agent()
    composer = ComposerAgent()

    builder = WorkflowBuilder(
        name="Azure Sora multi-agent workflow",
        description="Director -> Sora2 -> Composer pipeline that follows the provided storyboard script.",
    )
    (
        builder.set_start_executor(director)
        .add_edge(director, sora_agent)
        .add_edge(sora_agent, composer)
    )
    return builder.build()


workflow = create_sora_video_workflow()


async def _run_once(request: VideoWorkflowRequest) -> None:
    result = await workflow.run(request)
    for output in result.get_outputs():
        if output:
            print(output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Three-agent Azure Sora workflow runner."
    )
    parser.add_argument(
        "--script-path",
        default=str(SCRIPT_PATH),
        help="Path to video script (default: Chapters/video_script.txt).",
    )
    parser.add_argument(
        "--threads", type=int, default=3, help="Parallel Sora jobs (default 3)."
    )
    parser.add_argument(
        "--resolution",
        default=DEFAULT_SORA_RESOLUTION,
        help=f"Clip resolution passed to Sora (must remain {DEFAULT_SORA_RESOLUTION}).",
    )
    parser.add_argument(
        "--final-video", default=str(FINAL_VIDEO_PATH), help="Output MP4 path."
    )
    parser.add_argument(
        "--devui", action="store_true", help="Launch Agent Framework DevUI."
    )
    parser.add_argument(
        "--devui-port", type=int, default=8091, help="Port for DevUI (default 8091)."
    )
    args = parser.parse_args()

    cli_resolution = (args.resolution or "").strip() or DEFAULT_SORA_RESOLUTION
    if cli_resolution != DEFAULT_SORA_RESOLUTION:
        parser.error(
            f"--resolution must be {DEFAULT_SORA_RESOLUTION} to align with portrait workflow assets."
        )

    request = VideoWorkflowRequest(
        script_path=args.script_path,
        thread_count=args.threads,
        resolution=DEFAULT_SORA_RESOLUTION,
        final_video_path=args.final_video,
    )

    if args.devui:
        from agent_framework.devui import serve

        serve(entities=[workflow], port=args.devui_port, auto_open=True)
        return

    asyncio.run(_run_once(request))


if __name__ == "__main__":
    main()
