"""Three-agent workflow for stitching a Video using Azure Sora2 api."""

import argparse
import asyncio
import base64
import copy
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
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from openai import AsyncOpenAI, AzureOpenAI
from pydantic import BaseModel, ConfigDict, Field
from tinydb import Query, TinyDB

# -------------------------
# Paths & constants
# -------------------------

ROOT = Path(__file__).parent
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


def _video_client() -> AsyncOpenAI:
    global _VIDEO_CLIENT  # type: ignore[assignment]
    if _VIDEO_CLIENT is not None:
        return _VIDEO_CLIENT
    endpoint = (os.getenv("ENDPOINT_URL") or os.getenv("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not endpoint:
        raise RuntimeError("Configure ENDPOINT_URL or AZURE_OPENAI_ENDPOINT before using remix features.")
    if not api_key:
        raise RuntimeError("Configure AZURE_OPENAI_API_KEY or OPENAI_API_KEY before using remix features.")
    _VIDEO_CLIENT = AsyncOpenAI(api_key=api_key, base_url=f"{endpoint}/openai/v1/")
    return _VIDEO_CLIENT


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


def _resolution_to_size(resolution: str) -> tuple[int, int]:
    """Convert a WIDTHxHEIGHT string into an integer tuple with validation."""

    try:
        width_str, height_str = resolution.lower().split("x", 1)
        width = int(width_str)
        height = int(height_str)
    except (AttributeError, ValueError):
        logger.warning(
            "Invalid resolution '%s'; falling back to default scene image size %sx%s.",
            resolution,
            SCENE_IMAGE_SIZE[0],
            SCENE_IMAGE_SIZE[1],
        )
        return SCENE_IMAGE_SIZE

    if width <= 0 or height <= 0:
        logger.warning(
            "Non-positive resolution '%s'; falling back to default scene image size %sx%s.",
            resolution,
            SCENE_IMAGE_SIZE[0],
            SCENE_IMAGE_SIZE[1],
        )
        return SCENE_IMAGE_SIZE

    return width, height


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
    generation_mode: Literal["parallel", "sequence"] = Field(
        default="parallel",
        description="Parallel (default) renders scenes concurrently; sequence enforces frame-to-frame chaining.",
    )
    sequence_initial_image: Optional[str] = Field(
        default=None,
        description="Optional prepared PNG used to seed the first clip when generation_mode=sequence.",
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
class SceneVersionEntry:
    version_id: str
    video_path: str
    label: str = ""
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    video_id: Optional[str] = None
    prompt: Optional[str] = None
    prompt_path: Optional[str] = None
    source: str = "original"
    created_at: datetime = field(default_factory=_utc_now)
    last_frame_path: Optional[str] = None


@dataclass
class SceneStatus:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    selected_version_id: Optional[str] = None
    versions: list[SceneVersionEntry] = field(default_factory=list)
    last_frame_path: Optional[str] = None

    def set_selected_version(self, version: SceneVersionEntry) -> None:
        self.selected_version_id = version.version_id
        self.video_path = version.video_path
        self.log_path = version.log_path
        self.client_log_path = version.client_log_path
        self.last_frame_path = version.last_frame_path

    def get_version(self, version_id: Optional[str]) -> SceneVersionEntry:
        if version_id:
            for candidate in self.versions:
                if candidate.version_id == version_id:
                    return candidate
        if self.versions:
            return self.versions[-1]
        raise KeyError(f"Scene {self.order} has no recorded versions.")


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
    download_variant: str = field(default_factory=lambda: os.getenv("VIDEO_DOWNLOAD_VARIANT", "video"))
    generation_mode: str = "parallel"

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=_utc_now(), stage=stage, text=text))


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _serialize_progress_message(message: ProgressMessage) -> dict[str, Any]:
    return {
        "timestamp": _serialize_datetime(message.timestamp),
        "stage": message.stage,
        "text": message.text,
    }


def _deserialize_progress_message(payload: dict[str, Any]) -> ProgressMessage:
    return ProgressMessage(
        timestamp=_deserialize_datetime(payload.get("timestamp")) or _utc_now(),
        stage=payload.get("stage", "queued"),
        text=payload.get("text", ""),
    )


def _scene_version_to_record(version: SceneVersionEntry) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "label": version.label,
        "video_path": version.video_path,
        "log_path": version.log_path,
        "client_log_path": version.client_log_path,
        "video_id": version.video_id,
        "prompt": version.prompt,
        "prompt_path": version.prompt_path,
        "source": version.source,
        "created_at": _serialize_datetime(version.created_at),
        "last_frame_path": version.last_frame_path,
    }


def _scene_version_from_record(payload: dict[str, Any]) -> SceneVersionEntry:
    return SceneVersionEntry(
        version_id=payload.get("version_id", str(uuid.uuid4())),
        video_path=payload.get("video_path", ""),
        label=payload.get("label", ""),
        log_path=payload.get("log_path"),
        client_log_path=payload.get("client_log_path"),
        video_id=payload.get("video_id"),
        prompt=payload.get("prompt"),
        prompt_path=payload.get("prompt_path"),
        source=payload.get("source", "original"),
        created_at=_deserialize_datetime(payload.get("created_at")) or _utc_now(),
        last_frame_path=payload.get("last_frame_path"),
    )


def _scene_status_to_record(scene: SceneStatus) -> dict[str, Any]:
    return {
        "order": scene.order,
        "label": scene.label,
        "duration_seconds": scene.duration_seconds,
        "prompt_path": scene.prompt_path,
        "video_path": scene.video_path,
        "log_path": scene.log_path,
        "client_log_path": scene.client_log_path,
        "selected_version_id": scene.selected_version_id,
        "versions": [_scene_version_to_record(version) for version in scene.versions],
        "last_frame_path": scene.last_frame_path,
    }


def _scene_status_from_record(payload: dict[str, Any]) -> SceneStatus:
    versions = []
    for version_record in payload.get("versions", []):
        try:
            versions.append(_scene_version_from_record(version_record))
        except Exception:  # pragma: no cover - defensive
            continue
    scene = SceneStatus(
        order=int(payload.get("order", 0)),
        label=payload.get("label", ""),
        duration_seconds=int(payload.get("duration_seconds", 0)),
        prompt_path=payload.get("prompt_path") or "",
        video_path=payload.get("video_path") or "",
        log_path=payload.get("log_path"),
        client_log_path=payload.get("client_log_path"),
        selected_version_id=payload.get("selected_version_id"),
        versions=versions,
        last_frame_path=payload.get("last_frame_path"),
    )
    if scene.selected_version_id:
        try:
            scene.set_selected_version(scene.get_version(scene.selected_version_id))
        except KeyError:
            pass
    return scene


def _workflow_state_to_record(job: WorkflowJobState) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "resolution": job.resolution,
        "started_at": _serialize_datetime(job.started_at),
        "completed_at": _serialize_datetime(job.completed_at),
        "error": job.error,
        "final_video_path": job.final_video_path,
        "manifest": copy.deepcopy(job.manifest) if job.manifest else None,
        "total_scenes": job.total_scenes,
        "completed_scenes": job.completed_scenes,
        "messages": [_serialize_progress_message(message) for message in job.messages],
        "scenes": [_scene_status_to_record(scene) for scene in job.scenes],
        "download_variant": job.download_variant,
        "generation_mode": job.generation_mode,
    }


def _workflow_state_from_record(payload: dict[str, Any]) -> WorkflowJobState:
    return WorkflowJobState(
        job_id=payload.get("job_id", str(uuid.uuid4())),
        status=payload.get("status", "queued"),
        stage=payload.get("stage"),
        progress=float(payload.get("progress", 0.0)),
        resolution=payload.get("resolution", DEFAULT_SORA_RESOLUTION),
        started_at=_deserialize_datetime(payload.get("started_at")) or _utc_now(),
        completed_at=_deserialize_datetime(payload.get("completed_at")),
        error=payload.get("error"),
        final_video_path=payload.get("final_video_path"),
        manifest=copy.deepcopy(payload.get("manifest")),
        total_scenes=int(payload.get("total_scenes", 0)),
        completed_scenes=int(payload.get("completed_scenes", 0)),
        messages=[_deserialize_progress_message(item) for item in payload.get("messages", [])],
        scenes=[_scene_status_from_record(item) for item in payload.get("scenes", [])],
        download_variant=payload.get("download_variant", os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")),
        generation_mode=payload.get("generation_mode", "parallel"),
    )


class WorkflowJobDatabase:
    """TinyDB-backed persistence for workflow jobs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(path, indent=2, ensure_ascii=False)
        self._table = self._db.table("workflow_jobs")

    def load_jobs(self) -> list[WorkflowJobState]:
        try:
            records = self._table.all()
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to read workflow job database.")
            return []
        jobs: list[WorkflowJobState] = []
        for record in records:
            try:
                jobs.append(_workflow_state_from_record(record))
            except Exception:  # pragma: no cover - defensive
                logger.exception("Unable to parse workflow job record: %s", record.get("job_id"))
        return jobs

    def upsert(self, job: WorkflowJobState) -> None:
        try:
            self._table.upsert(_workflow_state_to_record(job), Query().job_id == job.job_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to persist workflow job: %s", job.job_id)

    def delete(self, job_id: str) -> None:
        try:
            self._table.remove(Query().job_id == job_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to delete workflow job: %s", job_id)


class JobMessageModel(BaseModel):
    timestamp: datetime
    stage: str
    text: str


class SceneVersionModel(BaseModel):
    version_id: str
    label: str
    source: str
    prompt: Optional[str]
    prompt_path: Optional[str]
    video_path: str
    video_url: Optional[str]
    log_path: Optional[str]
    client_log_path: Optional[str]
    video_id: Optional[str]
    created_at: datetime
    last_frame_path: Optional[str] = None
    last_frame_url: Optional[str] = None


class SceneStatusModel(BaseModel):
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    video_url: Optional[str] = None
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    selected_version_id: Optional[str] = None
    versions: list[SceneVersionModel] = Field(default_factory=list)
    last_frame_path: Optional[str] = None
    last_frame_url: Optional[str] = None


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
    generation_mode: str = "parallel"


class WorkflowJobStore:
    def __init__(self, persistence: Optional[WorkflowJobDatabase] = None) -> None:
        self._jobs: dict[str, WorkflowJobState] = {}
        self._lock = asyncio.Lock()
        self._persistence = persistence
        if self._persistence:
            for job in self._persistence.load_jobs():
                self._jobs[job.job_id] = job

    def _persist(self, job: WorkflowJobState) -> None:
        if self._persistence:
            self._persistence.upsert(job)

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
            self._persist(state)

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.stage = "initializing"
            job.progress = max(job.progress, 0.01)
            job.add_message("initializing", "Workflow execution started.")
            self._persist(job)

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
            self._persist(job)

    async def set_total_scenes(self, job_id: str, total: int) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.total_scenes = max(total, 0)
            self._persist(job)

    async def set_resolution(self, job_id: str, resolution: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.resolution = resolution
            self._persist(job)

    async def set_download_variant(self, job_id: str, download_variant: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.download_variant = download_variant
            self._persist(job)

    async def set_generation_mode(self, job_id: str, generation_mode: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.generation_mode = generation_mode
            self._persist(job)

    async def record_scene_completion(self, job_id: str, render: "SceneRender") -> None:
        async with self._lock:
            job = self._require(job_id)
            job.completed_scenes += 1
            version = SceneVersionEntry(
                version_id=render.version_id,
                label=render.version_label,
                video_path=render.video_path,
                log_path=render.log_path,
                client_log_path=render.client_log_path,
                video_id=render.video_id,
                prompt=render.prompt_text,
                prompt_path=render.prompt_path,
                source=render.version_source,
                last_frame_path=render.last_frame_path,
            )
            scene = SceneStatus(
                order=render.order,
                label=render.label,
                duration_seconds=render.duration_seconds,
                prompt_path=render.prompt_path,
                video_path=render.video_path,
                log_path=render.log_path,
                client_log_path=render.client_log_path,
                last_frame_path=render.last_frame_path,
            )
            scene.versions.append(version)
            scene.set_selected_version(version)
            job.scenes.append(scene)
            total = job.total_scenes or 1
            fraction = min(job.completed_scenes / total, 1.0)
            progress = 0.2 + 0.6 * fraction
            job.progress = max(job.progress, progress)
            job.stage = "rendering"
            job.add_message("rendering", f"Completed {render.label}.")
            self._persist(job)

    async def mark_composing(self, job_id: str, message: str | None = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.stage = "composing"
            job.progress = max(job.progress, 0.85)
            if message:
                job.add_message("composing", message)
            self._persist(job)

    async def add_scene_version(
        self,
        job_id: str,
        order: int,
        version: SceneVersionEntry,
        *,
        make_active: bool = False,
        message: Optional[str] = None,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            scene = next((item for item in job.scenes if item.order == order), None)
            if scene is None:
                raise KeyError(f"Scene {order} not found for job {job_id}.")
            if not version.label:
                version.label = f"Version {len(scene.versions) + 1}"
            scene.versions.append(version)
            if make_active or not scene.selected_version_id:
                scene.set_selected_version(version)
            if message:
                job.add_message("rendering", message)
            self._persist(job)

    async def set_scene_selection(self, job_id: str, order: int, version_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            scene = next((item for item in job.scenes if item.order == order), None)
            if scene is None:
                raise KeyError(f"Scene {order} not found for job {job_id}.")
            version = scene.get_version(version_id)
            scene.set_selected_version(version)
            job.add_message("rendering", f"Selected {version.label or version.version_id} for {scene.label}.")
            self._persist(job)

    async def get_scene(self, job_id: str, order: int) -> SceneStatus:
        async with self._lock:
            job = self._require(job_id)
            scene = next((item for item in job.scenes if item.order == order), None)
            if scene is None:
                raise KeyError(f"Scene {order} not found for job {job_id}.")
            return copy.deepcopy(scene)

    async def add_message(self, job_id: str, stage: str, text: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.add_message(stage, text)
            self._persist(job)

    async def get_download_variant(self, job_id: str) -> str:
        async with self._lock:
            job = self._require(job_id)
            return job.download_variant

    async def update_final_video(self, job_id: str, final_video_path: str, manifest: dict[str, Any]) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.final_video_path = final_video_path
            job.manifest = manifest
            job.add_message("composing", "Updated final video with selected scene versions.")
            self._persist(job)

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
            self._persist(job)

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.stage = "failed"
            job.error = error
            job.completed_at = _utc_now()
            job.add_message("failed", error)
            self._persist(job)

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
                selected_version_id=scene.selected_version_id,
                versions=[
                    SceneVersionModel(
                        version_id=version.version_id,
                        label=version.label or f"Version {idx + 1}",
                        source=version.source,
                        prompt=version.prompt,
                        prompt_path=version.prompt_path,
                        video_path=version.video_path,
                        video_url=_static_asset_url(version.video_path),
                        log_path=version.log_path,
                        client_log_path=version.client_log_path,
                        video_id=version.video_id,
                        created_at=version.created_at,
                        last_frame_path=version.last_frame_path,
                        last_frame_url=_static_asset_url(version.last_frame_path),
                    )
                    for idx, version in enumerate(scene.versions)
                ],
                last_frame_path=scene.last_frame_path,
                last_frame_url=_static_asset_url(scene.last_frame_path),
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
            generation_mode=job.generation_mode,
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
            removed = self._jobs.pop(job_id)
            if self._persistence:
                self._persistence.delete(job_id)
            return removed


def _build_workflow_job_store() -> WorkflowJobStore:
    try:
        persistence = WorkflowJobDatabase(WORKFLOW_DB_PATH)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Persistent workflow database unavailable, using in-memory store.")
        return WorkflowJobStore()
    return WorkflowJobStore(persistence=persistence)


JOB_STORE = _build_workflow_job_store()


@dataclass
class SingleVideoVersionEntry:
    version_id: str
    label: str
    prompt: str
    prompt_path: str
    video_path: str
    log_path: Optional[str] = None
    client_log_path: Optional[str] = None
    video_id: Optional[str] = None
    source: str = "original"
    created_at: datetime = field(default_factory=_utc_now)


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
    video_id: Optional[str] = None
    messages: list[ProgressMessage] = field(default_factory=list)
    versions: list[SingleVideoVersionEntry] = field(default_factory=list)
    selected_version_id: Optional[str] = None

    def add_message(self, stage: str, text: str) -> None:
        self.messages.append(ProgressMessage(timestamp=_utc_now(), stage=stage, text=text))

    def set_active_version(self, version: SingleVideoVersionEntry) -> None:
        self.selected_version_id = version.version_id
        self.video_path = version.video_path
        self.log_path = version.log_path
        self.client_log_path = version.client_log_path
        self.video_id = version.video_id


def _get_active_single_version(job: SingleVideoJobState) -> Optional[SingleVideoVersionEntry]:
    if job.selected_version_id:
        for version in job.versions:
            if version.version_id == job.selected_version_id:
                return version
    if job.versions:
        return job.versions[-1]
    return None


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


class SingleVideoVersionModel(BaseModel):
    version_id: str
    label: str
    source: str
    prompt: str
    prompt_path: str
    video_path: str
    video_url: Optional[str]
    log_path: Optional[str]
    client_log_path: Optional[str]
    video_id: Optional[str]
    created_at: datetime


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
    video_id: Optional[str]
    selected_version_id: Optional[str]
    versions: list[SingleVideoVersionModel] = Field(default_factory=list)
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
        video_id: Optional[str] = None,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_path = video_path
            job.log_path = log_path
            job.client_log_path = client_log_path
            if video_id is not None:
                job.video_id = video_id

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

    async def record_version(
        self,
        job_id: str,
        version: SingleVideoVersionEntry,
        make_active: bool = True,
        message: Optional[str] = None,
        stage: str = "running",
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.versions.append(version)
            if make_active:
                job.set_active_version(version)
            job.prompt = version.prompt
            if message:
                job.add_message(stage, message)

    async def get_job_state(self, job_id: str) -> SingleVideoJobState:
        async with self._lock:
            job = self._require(job_id)
            return copy.deepcopy(job)

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

    async def add_message(self, job_id: str, stage: str, text: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.add_message(stage, text)

    def _snapshot(self, job: SingleVideoJobState) -> SingleVideoJobResponse:
        messages = [
            JobMessageModel(timestamp=item.timestamp, stage=item.stage, text=item.text)
            for item in job.messages
        ]
        versions = [
            SingleVideoVersionModel(
                version_id=version.version_id,
                label=version.label,
                source=version.source,
                prompt=version.prompt,
                prompt_path=version.prompt_path,
                video_path=version.video_path,
                video_url=_static_asset_url(version.video_path),
                log_path=version.log_path,
                client_log_path=version.client_log_path,
                video_id=version.video_id,
                created_at=version.created_at,
            )
            for version in job.versions
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
             video_id=job.video_id,
             selected_version_id=job.selected_version_id,
             versions=versions,
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
    generation_mode: Literal["parallel", "sequence"] = Field(
        default="parallel",
        alias="generationMode",
        description="Choose between parallel (default) or sequence rendering with chained references.",
    )
    refine_with_gpt5: bool = Field(
        default=False,
        alias="refineWithGpt5",
        description="When true, runs the submitted script through GPT-5 before planning scenes.",
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
    refine_with_gpt5: bool = Field(
        default=False,
        alias="refineWithGpt5",
        description="When true, uses GPT-5 to refine the prompt before submitting to Sora2.",
    )


class SceneRemixPayload(BaseModel):
    prompt: str = Field(
        ..., description="New descriptive prompt to remix the previously generated scene."
    )


class SingleVideoRemixPayload(BaseModel):
    prompt: str = Field(
        ..., description="New descriptive prompt for remixing the previous single-video output."
    )


class SceneSelectionPayload(BaseModel):
    version_id: str = Field(
        ..., alias="versionId", description="Identifier of the scene version to use in the final cut."
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

    generation_mode = payload.generation_mode or "parallel"
    thread_count = payload.threads or int(os.getenv("SORA_WORKFLOW_THREADS", "3"))
    if generation_mode == "sequence":
        thread_count = 1

    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
    uploads = list(payload.scene_images or [])
    if generation_mode == "sequence" and len(uploads) > 1:
        raise ValueError("Sequence generation mode accepts only one uploaded reference image.")
    if generation_mode == "sequence" and uploads:
        uploads = uploads[:1]
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

    sequence_initial_image: Optional[str] = None
    if generation_mode == "sequence" and use_scene_images:
        sequence_initial_image = _prepare_sequence_initial_image(
            Path(scene_image_dir_path),
            tmp_dir,
            resolution,
        )
        # Sequence mode only uses a single seed frame; subsequent references come from rendered clips.
        use_scene_images = False

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
        generation_mode=generation_mode,
        sequence_initial_image=sequence_initial_image,
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
        video_id: Optional[str] = None
        if sora_log_path.is_file():
            try:
                metadata = json.loads(sora_log_path.read_text(encoding="utf-8"))
                video_id = metadata.get("video_id") or metadata.get("job_id")
            except json.JSONDecodeError:
                video_id = None
        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(config.video_path),
            str(sora_log_path) if sora_log_path.is_file() else None,
            str(client_log_path),
            video_id=video_id,
        )
        version = SingleVideoVersionEntry(
            version_id=uuid.uuid4().hex,
            label="Original",
            prompt=config.prompt_text,
            prompt_path=str(config.prompt_path),
            video_path=str(config.video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
            video_id=video_id,
        )
        await SINGLE_VIDEO_STORE.record_version(
            job_id,
            version,
            make_active=True,
            message="Initial render saved.",
            stage="completed",
        )
        await SINGLE_VIDEO_STORE.mark_completed(job_id)
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Single video job %s failed: %s", job_id, exc)
        await SINGLE_VIDEO_STORE.mark_failed(job_id, str(exc))


async def _run_single_video_remix(job_id: str, prompt: str) -> None:
    prompt_text = prompt.strip()
    if not prompt_text:
        await SINGLE_VIDEO_STORE.add_message(job_id, "failed", "Remix prompt cannot be empty.")
        return
    try:
        job_state = await SINGLE_VIDEO_STORE.get_job_state(job_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Remix requested for unknown single job %s: %s", job_id, exc)
        return

    base_version = _get_active_single_version(job_state)
    if not base_version or not base_version.video_id:
        await SINGLE_VIDEO_STORE.add_message(
            job_id,
            "failed",
            "This job is missing Azure video metadata; render a clip before requesting a remix.",
        )
        return

    try:
        await SINGLE_VIDEO_STORE.mark_running(job_id)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.1, "Submitting remix to Azure Sora2.")
        client = _video_client()
        remix_job = await client.videos.remix(video_id=base_version.video_id, prompt=prompt_text)
        remix_job_id = getattr(remix_job, "id", None)
        if not remix_job_id:
            raise RuntimeError("Azure remix API did not return a job id.")

        status = getattr(remix_job, "status", None)
        while status not in {"completed", "failed", "canceled"}:
            await asyncio.sleep(4)
            poll = await client.videos.retrieve(remix_job_id)
            status = getattr(poll, "status", None)
        if status != "completed":
            raise RuntimeError(f"Remix job ended with status {status}.")

        job_dir = SINGLE_VIDEO_JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        video_path = job_dir / f"sora_single_remix_{timestamp}.mp4"
        prompt_path = job_dir / f"sora_single_remix_{timestamp}.txt"
        log_path = video_path.with_suffix(".json")
        client_log_path = video_path.with_suffix(".remix.log")
        prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

        download_variant = job_state.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
        content = await client.videos.download_content(remix_job_id, variant=download_variant)
        content.write_to_file(str(video_path))
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.75, "Remix downloaded, finalizing artifacts.")

        log_data = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "prompt": prompt_text,
            "video_id": remix_job_id,
            "base_video_id": base_version.video_id,
            "output_file": str(video_path),
            "source": "remix",
        }
        log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
        client_log_path.write_text(
            f"Remix job {remix_job_id} completed for single-video job {job_id}.\nBase video: {base_version.video_id}\n",
            encoding="utf-8",
        )

        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(video_path),
            str(log_path),
            str(client_log_path),
            video_id=remix_job_id,
        )
        version = SingleVideoVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Remix {len(job_state.versions) + 1}",
            prompt=prompt_text,
            prompt_path=str(prompt_path),
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            video_id=remix_job_id,
            source="remix",
        )
        await SINGLE_VIDEO_STORE.record_version(
            job_id,
            version,
            make_active=True,
            message="Remix completed.",
            stage="completed",
        )
        await SINGLE_VIDEO_STORE.mark_completed(job_id)
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Remix job for single video %s failed: %s", job_id, exc)
        await SINGLE_VIDEO_STORE.mark_failed(job_id, f"Remix failed: {exc}")


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

    if payload.refine_with_gpt5:
        try:
            refined_script = await refine_text_with_gpt5(script, mode="workflow")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-5 refinement failed for workflow %s: %s", job_id, exc)
            refined_script = script
        if refined_script.strip():
            payload.script = refined_script
            await JOB_STORE.add_message(
                job_id, "planning", "Storyboard refined with GPT-5 before scene planning."
            )
        else:
            payload.script = script

    try:
        request = _prepare_request_for_job(job_id, payload)
        await JOB_STORE.set_resolution(job_id, request.resolution)
        await JOB_STORE.set_download_variant(job_id, request.download_variant)
        await JOB_STORE.set_generation_mode(job_id, request.generation_mode)
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
    prompt_text = payload.prompt or ""
    if payload.refine_with_gpt5:
        try:
            prompt_text = await refine_text_with_gpt5(prompt_text, mode="single")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-5 prompt refinement failed for single job %s: %s", job_id, exc)
        if prompt_text.strip():
            payload.prompt = prompt_text
        else:
            prompt_text = payload.prompt or ""

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
    if payload.refine_with_gpt5:
        await SINGLE_VIDEO_STORE.add_message(
            job_id, "queued", "Prompt refined with GPT-5 before submission."
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


@fastapi_app.post(
    "/api/single-videos/{job_id}/remix",
    response_model=SingleVideoJobResponse,
)
async def remix_single_video(job_id: str, payload: SingleVideoRemixPayload) -> SingleVideoJobResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Remix prompt is required.")
    try:
        status = await SINGLE_VIDEO_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc

    if not status.video_id:
        raise HTTPException(
            status_code=400,
            detail="This job is missing Azure video metadata. Generate a clip before remixing.",
        )

    await SINGLE_VIDEO_STORE.add_message(job_id, "running", "Remix request submitted.")
    asyncio.create_task(_run_single_video_remix(job_id, prompt))
    return await SINGLE_VIDEO_STORE.snapshot(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/scenes/{scene_order}/remix",
    response_model=JobStatusResponse,
)
async def remix_scene_version(
    job_id: str, scene_order: int, payload: SceneRemixPayload
) -> JobStatusResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Remix prompt is required.")
    try:
        scene = await JOB_STORE.get_scene(job_id, scene_order)
        version = scene.get_version(scene.selected_version_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Scene {scene_order} not found for job {job_id}.",
        ) from exc
    if not version.video_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Scene is missing Azure video metadata; regenerate the clip before requesting a remix."
            ),
        )
    await JOB_STORE.add_message(job_id, "rendering", f"Queued remix for {scene.label}.")
    asyncio.create_task(_run_scene_remix_job(job_id, scene_order, prompt))
    return await JOB_STORE.get_status(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/scenes/{scene_order}/select",
    response_model=JobStatusResponse,
)
async def select_scene_version(
    job_id: str, scene_order: int, payload: SceneSelectionPayload
) -> JobStatusResponse:
    version_id = (payload.version_id or "").strip()
    if not version_id:
        raise HTTPException(status_code=400, detail="versionId is required.")
    try:
        await JOB_STORE.set_scene_selection(job_id, scene_order, version_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Unable to select version {version_id} for scene {scene_order}.",
        ) from exc
    return await JOB_STORE.get_status(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/recompose",
    response_model=JobStatusResponse,
)
async def recompose_final_video(job_id: str) -> JobStatusResponse:
    try:
        await JOB_STORE.mark_composing(
            job_id, "Regenerating final video from selected scene versions."
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    asyncio.create_task(_run_recompose_job(job_id))
    return await JOB_STORE.get_status(job_id)


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


async def _run_scene_remix_job(job_id: str, scene_order: int, prompt: str) -> None:
    prompt_text = prompt.strip()
    if not prompt_text:
        await JOB_STORE.add_message(job_id, "failed", "Remix prompt cannot be empty.")
        return
    try:
        scene = await JOB_STORE.get_scene(job_id, scene_order)
        base_version = scene.get_version(scene.selected_version_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Remix request dropped for missing scene %s/%s: %s", job_id, scene_order, exc)
        await JOB_STORE.add_message(
            job_id, "failed", f"Unable to remix scene {scene_order}; render not found."
        )
        return

    if not base_version.video_id:
        await JOB_STORE.add_message(
            job_id,
            "failed",
            f"Scene {scene_order} is missing Azure video metadata; regenerate before remixing.",
        )
        return

    job_dir = _job_root(job_id)
    scenes_dir = Path(job_dir) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = Path(scene.prompt_path).parent if scene.prompt_path else (Path(job_dir) / "prompts")
    prompt_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    video_path = scenes_dir / f"scene_{scene_order:02d}_remix_{timestamp}.mp4"
    log_path = video_path.with_suffix(".json")
    client_log_path = video_path.with_suffix(".remix.log")
    prompt_path = prompt_dir / f"scene_{scene_order:02d}_remix_{timestamp}.txt"

    try:
        client = _video_client()
        download_variant = await JOB_STORE.get_download_variant(job_id)
        remix_job = await client.videos.remix(video_id=base_version.video_id, prompt=prompt_text)
        remix_job_id = getattr(remix_job, "id", None)
        if not remix_job_id:
            raise RuntimeError("Azure remix API did not return a job id.")

        status = getattr(remix_job, "status", None)
        while status not in {"completed", "failed", "canceled"}:
            await asyncio.sleep(4)
            poll = await client.videos.retrieve(remix_job_id)
            status = getattr(poll, "status", None)
        if status != "completed":
            raise RuntimeError(f"Remix job ended with status {status}.")

        content = await client.videos.download_content(remix_job_id, variant=download_variant)
        content.write_to_file(str(video_path))

        prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
        log_data = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "prompt": prompt_text,
            "video_id": remix_job_id,
            "base_video_id": base_version.video_id,
            "output_file": str(video_path),
            "source": "remix",
        }
        log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
        client_log_path.write_text(
            f"Remix job {remix_job_id} completed for scene {scene_order}.\nBase video: {base_version.video_id}\n",
            encoding="utf-8",
        )

        version = SceneVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Remix {len(scene.versions) + 1}",
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            video_id=remix_job_id,
            prompt=prompt_text,
            prompt_path=str(prompt_path),
            source="remix",
        )
        await JOB_STORE.add_scene_version(
            job_id,
            scene_order,
            version,
            make_active=False,
            message=f"Remix ready for {scene.label}.",
        )
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Remix job for %s scene %s failed: %s", job_id, scene_order, exc)
        await JOB_STORE.add_message(
            job_id,
            "failed",
            f"Remix for scene {scene_order} failed: {exc}",
        )


async def _run_recompose_job(job_id: str) -> None:
    try:
        status = await JOB_STORE.get_status(job_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Recompose requested for unknown job %s: %s", job_id, exc)
        return

    job_dir = _job_root(job_id)
    tmp_dir = job_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final_video = job_dir / f"sora_story_compilation_{timestamp}.mp4"
    concat_list = tmp_dir / f"concat_recompose_{timestamp}.txt"

    scenes_sorted = sorted(status.scenes, key=lambda item: item.order)
    renders: list[SceneRender] = []
    for scene in scenes_sorted:
        version = next(
            (v for v in scene.versions if v.version_id == scene.selected_version_id),
            None,
        )
        if version is None and scene.versions:
            version = scene.versions[-1]
        if version is None or not version.video_path:
            await JOB_STORE.add_message(
                job_id,
                "failed",
                f"Cannot recompose; scene {scene.order} has no completed versions.",
            )
            return
        renders.append(
            SceneRender(
                order=scene.order,
                label=scene.label,
                duration_seconds=scene.duration_seconds,
                prompt_path=version.prompt_path or scene.prompt_path,
                video_path=version.video_path,
                log_path=version.log_path,
                client_log_path=version.client_log_path,
                prompt_text=version.prompt,
                version_id=version.version_id,
                version_label=version.label,
                video_id=version.video_id,
                version_source=version.source,
            )
        )

    with open(concat_list, "w", encoding="utf-8") as fh:
        for render in renders:
            fh.write(escape_for_concat(Path(render.video_path)) + "\n")

    try:
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
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Recompose failed for job %s: %s", job_id, exc)
        await JOB_STORE.add_message(
            job_id,
            "failed",
            f"Recompose failed: {exc}",
        )
        return

    total_seconds = sum(render.duration_seconds for render in renders)
    manifest = summarize_manifest(renders, final_video, total_seconds, status.resolution)
    manifest_path = final_video.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    await JOB_STORE.update_final_video(job_id, str(final_video), manifest)
    await JOB_STORE.add_message(job_id, "completed", "Regenerated final video with selected scene versions.")
    await JOB_STORE.update_stage(job_id, "completed", "Final video refreshed.", progress=1.0)


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
    prompt_text: Optional[str] = None
    version_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    version_label: str = "Original"
    video_id: Optional[str] = None
    version_source: str = "original"
    last_frame_path: Optional[str] = None


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


async def refine_text_with_gpt5(content: str, mode: Literal["workflow", "single"]) -> str:
    normalized = content.strip()
    if not normalized:
        return content

    deployment = GPT5_REFINEMENT_DEPLOYMENT
    if not deployment:
        return content

    client = _azure_text_client()
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
        return _extract_response_text(response)

    try:
        refined = await asyncio.to_thread(_call)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("GPT-5 refinement failed (%s mode): %s", mode, exc)
        return content

    cleaned = refined.strip()
    return cleaned or content


def escape_for_concat(path: Path) -> str:
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def _resize_scene_image(
    source: Path, dest: Path, target_size: tuple[int, int] | None = None
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        has_alpha = "A" in img.getbands()
        mode = "RGBA" if has_alpha else "RGB"
        converted = img.convert(mode)

        target_w, target_h = target_size or SCENE_IMAGE_SIZE
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


def _find_scene_image(scene_dir: Path, index: int) -> Optional[Path]:
    if not scene_dir.is_dir():
        return None
    for entry in scene_dir.iterdir():
        if not entry.is_file():
            continue
        match = SCENE_IMAGE_PATTERN.fullmatch(entry.name)
        if match and int(match.group(1)) == index:
            return entry
    return None


def _prepare_sequence_initial_image(
    scene_dir: Path, tmp_dir: Path, resolution: str
) -> Optional[str]:
    source = _find_scene_image(scene_dir, 1)
    if source is None:
        return None
    dest = tmp_dir / "sequence_initial.png"
    _resize_scene_image(source, dest, target_size=_resolution_to_size(resolution))
    return str(dest)


def prepare_scene_start_images(
    scene_dir: Path, tmp_dir: Path, scene_count: int, resolution: str
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
    target_size = _resolution_to_size(resolution)
    target_w, target_h = target_size
    prepared_paths: list[str] = []
    for idx in range(1, scene_count + 1):
        source = matches[idx]
        dest = prepared_dir / f"scene{idx}_{target_w}x{target_h}.png"
        _resize_scene_image(source, dest, target_size=target_size)
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


async def extract_last_frame(video_path: Path, dest_path: Path) -> Path:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "select=eq(n\\,N-1)",
        "-vframes",
        "1",
        str(dest_path),
    ]
    await run_subprocess(cmd)
    return dest_path


def summarize_manifest(
    renders: list[SceneRender],
    final_video: Path,
    total_seconds: int,
    resolution: str,
) -> dict[str, Any]:
    return {
        "final_video": str(final_video),
        "assigned_seconds": total_seconds,
        "resolution": resolution,
        "scenes": [
            {
                "order": render.order,
                "label": render.label,
                "duration_seconds": render.duration_seconds,
                "prompt_path": render.prompt_path,
                "prompt_text": render.prompt_text,
                "video_path": render.video_path,
                "sora_log_path": render.log_path,
                "client_capture": render.client_log_path,
                "version_id": render.version_id,
                "version_label": render.version_label,
                "version_source": render.version_source,
                "video_id": render.video_id,
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
        if request.generation_mode == "sequence":
            initial_reference = request.sequence_initial_image
            scene_images = [initial_reference] if initial_reference else []
            note = (
                "Prepared initial reference frame for sequence chaining."
                if initial_reference
                else "Sequence mode active without a starting reference; chaining begins from scene one."
            )
            await _report_progress(
                request,
                "planning",
                note,
                progress=0.2,
            )
        elif request.use_scene_images:
            scene_images = prepare_scene_start_images(
                Path(request.scene_image_dir),
                tmp_dir,
                len(plans),
                request.resolution,
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
        if request.generation_mode == "sequence":
            renders = await self._render_sequence(batch, ctx, request, scenes_dir, scene_images)
        else:
            renders = await self._render_parallel(batch, ctx, request, scenes_dir, scene_images)

        await ctx.send_message(
            SceneRenderBatch(
                renders=renders,
                assigned_total_seconds=batch.assigned_total_seconds,
            )
        )

    async def _render_parallel(
        self,
        batch: ScenePlanBatch,
        ctx: WorkflowContext[SceneRenderBatch],
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> list[SceneRender]:
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
            await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")

        renders.sort(key=lambda item: item.order)
        return renders

    async def _render_sequence(
        self,
        batch: ScenePlanBatch,
        ctx: WorkflowContext[SceneRenderBatch],
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> list[SceneRender]:
        await _report_progress(
            request,
            "rendering",
            f"Sequence mode: rendering {len(batch.plans)} scene(s) one after another for visual continuity.",
            progress=0.25,
        )
        next_reference = request.sequence_initial_image or (scene_images[0] if scene_images else None)
        frame_dir = Path(request.tmp_dir) / "sequence_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        renders: list[SceneRender] = []
        for plan in batch.plans:
            render = await self._invoke_sora(
                plan,
                request,
                scenes_dir,
                scene_images,
                reference_override=next_reference,
            )
            renders.append(render)
            await _record_scene_completion(request, render)
            await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")
            frame_path = frame_dir / f"scene_{plan.order:02d}_tail.png"
            try:
                captured = await extract_last_frame(Path(render.video_path), frame_path)
                render.last_frame_path = str(captured)
                next_reference = str(captured)
            except Exception as exc:  # pragma: no cover - diagnostic logging only
                logger.warning("Failed to capture last frame for %s: %s", render.label, exc)
                next_reference = None

        renders.sort(key=lambda item: item.order)
        return renders

    async def _invoke_sora(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
        reference_override: Optional[str] = None,
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

        input_reference = reference_override
        if not input_reference and request.use_scene_images:
            scene_index = plan.order - 1
            if scene_index < 0 or scene_index >= len(scene_images):
                raise RuntimeError(
                    f"Missing prepared start screen for {plan.label} (index {scene_index}). Verify plan/image alignment."
                )
            input_reference = scene_images[scene_index]
        if input_reference:
            cmd.extend(["--input-image", input_reference])

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
        video_id = None
        if sora_log_path.is_file():
            try:
                metadata = json.loads(sora_log_path.read_text(encoding="utf-8"))
                video_id = metadata.get("video_id") or metadata.get("job_id")
            except json.JSONDecodeError:
                video_id = None

        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=video_id,
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
            request.resolution,
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
