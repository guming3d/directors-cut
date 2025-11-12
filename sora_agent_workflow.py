"""Three-agent workflow for stitching a Video using Azure Sora2 api."""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, Optional

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

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
DEFAULT_SORA_RESOLUTION = "720x1280"
SCENE_IMAGE_DIR = ROOT / "scene_start_screen"
SCENE_IMAGE_PATTERN = re.compile(r"scene0*(\d+)\.(?:png|jpe?g)$", re.IGNORECASE)
SCENE_IMAGE_SIZE = (720, 1280)
try:
    SCENE_IMAGE_RESAMPLE = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - Pillow < 10 fallback
    SCENE_IMAGE_RESAMPLE = Image.LANCZOS

ALLOWED_DURATIONS = (4, 8, 12)
HEADER_PATTERN = re.compile(r"^\*\*\s*(Scene\s+(\d+)[^*]*)\*\*$", re.IGNORECASE)


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
    resolution: str = Field(
        default=DEFAULT_SORA_RESOLUTION,
        description="Clip resolution (fixed 720x1280 portrait).",
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

    def _static_url(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        file_path = Path(path)
        try:
            rel = file_path.relative_to(OUTPUT_DIR)
        except ValueError:
            return None
        return f"{STATIC_URL_PREFIX}/{rel.as_posix()}"

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
                video_url=self._static_url(scene.video_path),
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
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            final_video_path=job.final_video_path,
            final_video_url=self._static_url(job.final_video_path),
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


JOB_STORE = WorkflowJobStore()


class StartWorkflowPayload(BaseModel):
    script: str = Field(
        ..., description="Storyboard script content for the Director agent to process."
    )
    threads: Optional[int] = Field(
        default=None, ge=1, le=10, description="Override concurrent Sora render threads."
    )
    resolution: Optional[str] = Field(
        default=None,
        description=f"Optional override for render resolution (must remain {DEFAULT_SORA_RESOLUTION}).",
    )
    scene_image_dir: Optional[str] = Field(
        default=None,
        description="Optional override for the directory containing scene start screen images.",
    )
    download_variant: Optional[str] = Field(
        default=None,
        description="Optional override for the VIDEO_DOWNLOAD_VARIANT sent to the Sora client.",
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

    if payload.resolution and payload.resolution != DEFAULT_SORA_RESOLUTION:
        raise ValueError(
            f"Resolution must remain {DEFAULT_SORA_RESOLUTION} to align with prepared assets."
        )

    thread_count = payload.threads or int(os.getenv("SORA_WORKFLOW_THREADS", "3"))
    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
    scene_image_dir = (
        Path(payload.scene_image_dir).expanduser().resolve()
        if payload.scene_image_dir
        else SCENE_IMAGE_DIR
    )

    request = VideoWorkflowRequest(
        script_path=str(script_path),
        output_dir=str(job_dir),
        prompt_dir=str(prompts_dir),
        scenes_dir=str(scenes_dir),
        tmp_dir=str(tmp_dir),
        final_video_path=str(final_video),
        sora_client_path=str(SORA_CLIENT),
        scene_image_dir=str(scene_image_dir),
        resolution=DEFAULT_SORA_RESOLUTION,
        thread_count=thread_count,
        download_variant=download_variant,
        job_id=job_id,
    )
    return request


async def _run_workflow_job(job_id: str, request: VideoWorkflowRequest) -> None:
    try:
        await JOB_STORE.mark_running(job_id)
        await workflow.run(request)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Workflow %s failed: %s", job_id, exc)
        await JOB_STORE.mark_failed(job_id, str(exc))


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


def estimate_duration_from_description(lines: Sequence[str]) -> int:
    """Choose 4/8/12 seconds based on description density."""

    text = " ".join(lines)
    word_count = len(re.findall(r"\w+", text))
    line_count = len([line for line in lines if line.strip()])

    if word_count >= 150 or line_count >= 6:
        return 12
    if word_count >= 70 or line_count >= 3:
        return 8
    return 4


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "scene"


def build_prompt_text(
    title: str, description: str, duration: int, resolution: str
) -> str:
    bullet_lines = "\n".join(
        f"- {line.strip()}" for line in description.splitlines() if line.strip()
    )
    return (
        f"Scene title: {title}\n"
        f"Clip duration: {duration} seconds (limit choices: {ALLOWED_DURATIONS}).\n"
        f"Output resolution: {resolution} portrait.\n"
        "Character continuity: keep the same heroine (Brown hair, casual flight outfit) and hero (casual outfit) across clips.\n"
        "Visual direction:\n"
        f"{bullet_lines}\n"
        "Cinematic tone: energetic, emotional, with tasteful motion graphics.\n"
        "Audio: ambient sound design only; no background music."
    )


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
    if expected != available:
        missing = sorted(expected - available)
        extra = sorted(available - expected)
        issues: list[str] = []
        if missing:
            issues.append("missing " + ", ".join(f"scene{idx}.png" for idx in missing))
        if extra:
            issues.append("unexpected " + ", ".join(f"scene{idx}.png" for idx in extra))
        raise RuntimeError(
            "Scene/image count mismatch: "
            + "; ".join(issues)
            + f". Ensure there is exactly one file per scene in {scene_dir}."
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
    renders: list[SceneRender], final_video: Path, total_seconds: int
) -> dict[str, Any]:
    return {
        "final_video": str(final_video),
        "assigned_seconds": total_seconds,
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
            duration = estimate_duration_from_description(draft.description_lines)
            description = "\n".join(draft.description_lines)
            slug = slugify(f"{draft.scene_number:02d}-{draft.title}")
            prompt_path = prompt_dir / f"scene_{idx:02d}_{slug}.txt"
            prompt_text = build_prompt_text(
                draft.title, description, duration, request.resolution
            )
            prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

            plans.append(
                ScenePlan(
                    order=idx,
                    label=f"Scene {draft.scene_number}: {draft.title}",
                    duration_seconds=duration,
                    description=description,
                    prompt_path=str(prompt_path),
                    prompt_text=prompt_text,
                )
            )

        tmp_dir = Path(request.tmp_dir)
        scene_images = prepare_scene_start_images(
            Path(request.scene_image_dir), tmp_dir, len(plans)
        )
        await _report_progress(
            request,
            "planning",
            f"Prepared {len(plans)} prompt file(s) and start screens.",
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
