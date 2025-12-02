"""Persistence helpers and in-memory stores for workflow jobs."""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from tinydb import Query, TinyDB

from .config import (
    DEFAULT_IMAGE_GENERATOR,
    DEFAULT_REPRODUCE_SEGMENTATION,
    DEFAULT_SORA_RESOLUTION,
    DEFAULT_VIDEO_PROVIDER,
    OUTPUT_DIR,
    SINGLE_VIDEO_DB_PATH,
    VIDEO_INSIGHT_DB_PATH,
    VIDEO_REPRODUCE_DB_PATH,
    SORA_CLIP_DURATION_SECONDS,
    WORKFLOW_DB_PATH,
    logger,
)
from .models import (
    JobMessageModel,
    JobStatusResponse,
    SceneStatus,
    SceneStatusModel,
    SceneVersionEntry,
    SceneVersionModel,
    SingleVideoJobResponse,
    SingleVideoJobState,
    SingleVideoVersionEntry,
    SingleVideoVersionModel,
    VideoInsightJobResponse,
    VideoInsightJobState,
    VideoInsightKeyFrame,
    VideoInsightKeyFrameModel,
    VideoInsightSegment,
    VideoInsightSegmentModel,
    VideoReproduceJobResponse,
    VideoReproduceJobState,
    VideoReproduceSegment,
    VideoReproduceSegmentModel,
    ReproduceSegmentVersion,
    ReproduceSegmentVersionModel,
    WorkflowJobState,
)
from .utils import build_static_asset_url, utc_now


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


def _serialize_progress_message(message) -> dict[str, Any]:
    return {
        "timestamp": _serialize_datetime(message.timestamp),
        "stage": message.stage,
        "text": message.text,
    }


def _deserialize_progress_message(payload: dict[str, Any]):
    from .models import ProgressMessage

    return ProgressMessage(
        timestamp=_deserialize_datetime(payload.get("timestamp")) or utc_now(),
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
        "first_frame_path": version.first_frame_path,
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
        created_at=_deserialize_datetime(payload.get("created_at")) or utc_now(),
        last_frame_path=payload.get("last_frame_path"),
        first_frame_path=payload.get("first_frame_path"),
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
        "first_frame_path": scene.first_frame_path,
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
        first_frame_path=payload.get("first_frame_path"),
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
        "video_provider": job.video_provider,
        "resolution": job.resolution,
        "script": job.script,
        "script_path": job.script_path,
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
        "scene_duration": job.scene_duration,
        "generate_ai_first_frame": job.generate_ai_first_frame,
    }


def _workflow_state_from_record(payload: dict[str, Any]) -> WorkflowJobState:
    return WorkflowJobState(
        job_id=payload.get("job_id", str(uuid.uuid4())),
        status=payload.get("status", "queued"),
        stage=payload.get("stage"),
        progress=float(payload.get("progress", 0.0)),
        video_provider=payload.get("video_provider") or DEFAULT_VIDEO_PROVIDER,
        resolution=payload.get("resolution") or DEFAULT_SORA_RESOLUTION,
        script=payload.get("script") or "",
        script_path=payload.get("script_path"),
        started_at=_deserialize_datetime(payload.get("started_at")) or utc_now(),
        completed_at=_deserialize_datetime(payload.get("completed_at")),
        error=payload.get("error"),
        final_video_path=payload.get("final_video_path"),
        manifest=copy.deepcopy(payload.get("manifest")),
        total_scenes=int(payload.get("total_scenes", 0)),
        completed_scenes=int(payload.get("completed_scenes", 0)),
        messages=[_deserialize_progress_message(item) for item in payload.get("messages", [])],
        scenes=[_scene_status_from_record(item) for item in payload.get("scenes", [])],
        download_variant=payload.get("download_variant"),
        generation_mode=payload.get("generation_mode", "parallel"),
        scene_duration=int(payload.get("scene_duration", SORA_CLIP_DURATION_SECONDS)),
        generate_ai_first_frame=bool(payload.get("generate_ai_first_frame", False)),
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

    async def set_video_provider(self, job_id: str, video_provider: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_provider = video_provider
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

    async def set_scene_duration(self, job_id: str, scene_duration: int) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.scene_duration = scene_duration
            self._persist(job)

    async def set_generate_ai_first_frame(self, job_id: str, enabled: bool) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.generate_ai_first_frame = enabled
            self._persist(job)

    async def set_script(self, job_id: str, script_text: str, script_path: Optional[str] = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.script = script_text
            job.script_path = script_path
            self._persist(job)

    async def get_resolution(self, job_id: str) -> str:
        async with self._lock:
            job = self._require(job_id)
            return job.resolution

    async def get_video_provider(self, job_id: str) -> str:
        async with self._lock:
            job = self._require(job_id)
            return job.video_provider

    async def record_scene_completion(self, job_id: str, render) -> None:
        from .models import SceneRender

        assert isinstance(render, SceneRender)
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
                first_frame_path=render.first_frame_path,
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
                first_frame_path=render.first_frame_path,
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
            job.completed_at = utc_now()
            job.error = None
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
            job.completed_at = utc_now()
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
                video_url=build_static_asset_url(scene.video_path),
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
                        video_url=build_static_asset_url(version.video_path),
                        log_path=version.log_path,
                        client_log_path=version.client_log_path,
                        video_id=version.video_id,
                        created_at=version.created_at,
                        last_frame_path=version.last_frame_path,
                        last_frame_url=build_static_asset_url(version.last_frame_path),
                        first_frame_path=version.first_frame_path,
                        first_frame_url=build_static_asset_url(version.first_frame_path),
                    )
                    for idx, version in enumerate(scene.versions)
                ],
                last_frame_path=scene.last_frame_path,
                last_frame_url=build_static_asset_url(scene.last_frame_path),
                first_frame_path=scene.first_frame_path,
                first_frame_url=build_static_asset_url(scene.first_frame_path),
            )
            for scene in sorted(job.scenes, key=lambda item: item.order)
        ]
        return JobStatusResponse(
            job_id=job.job_id,
            status=job.status,
            stage=job.stage,
            progress=job.progress,
            video_provider=job.video_provider,
            resolution=job.resolution,
            script=job.script,
            script_path=job.script_path,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            final_video_path=job.final_video_path,
            final_video_url=build_static_asset_url(job.final_video_path),
            manifest=job.manifest,
            messages=messages,
            scenes=scenes,
            generation_mode=job.generation_mode,
            scene_duration=job.scene_duration,
            generate_ai_first_frame=job.generate_ai_first_frame,
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


class SingleVideoJobDatabase:
    """TinyDB-backed persistence for single video jobs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(path, indent=2, ensure_ascii=False)
        self._table = self._db.table("single_video_jobs")

    def load_jobs(self) -> list[SingleVideoJobState]:
        try:
            records = self._table.all()
        except Exception:
            logger.exception("Failed to read single video job database.")
            return []
        jobs: list[SingleVideoJobState] = []
        for record in records:
            try:
                jobs.append(_single_video_state_from_record(record))
            except Exception:
                logger.exception("Unable to parse single video job record: %s", record.get("job_id"))
        return jobs

    def upsert(self, job: SingleVideoJobState) -> None:
        try:
            self._table.upsert(_single_video_state_to_record(job), Query().job_id == job.job_id)
        except Exception:
            logger.exception("Failed to persist single video job: %s", job.job_id)

    def delete(self, job_id: str) -> None:
        try:
            self._table.remove(Query().job_id == job_id)
        except Exception:
            logger.exception("Failed to delete single video job: %s", job_id)


def _single_video_version_to_record(version: SingleVideoVersionEntry) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "label": version.label,
        "prompt": version.prompt,
        "prompt_path": version.prompt_path,
        "video_path": version.video_path,
        "log_path": version.log_path,
        "client_log_path": version.client_log_path,
        "video_id": version.video_id,
        "source": version.source,
        "created_at": _serialize_datetime(version.created_at),
    }


def _single_video_version_from_record(payload: dict[str, Any]) -> SingleVideoVersionEntry:
    return SingleVideoVersionEntry(
        version_id=payload.get("version_id", str(uuid.uuid4())),
        label=payload.get("label", ""),
        prompt=payload.get("prompt", ""),
        prompt_path=payload.get("prompt_path", ""),
        video_path=payload.get("video_path", ""),
        log_path=payload.get("log_path"),
        client_log_path=payload.get("client_log_path"),
        video_id=payload.get("video_id"),
        source=payload.get("source", "original"),
        created_at=_deserialize_datetime(payload.get("created_at")) or utc_now(),
    )


def _single_video_state_to_record(job: SingleVideoJobState) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "prompt": job.prompt,
        "video_provider": job.video_provider,
        "resolution": job.resolution,
        "duration": job.duration,
        "download_variant": job.download_variant,
        "status": job.status,
        "progress": job.progress,
        "started_at": _serialize_datetime(job.started_at),
        "completed_at": _serialize_datetime(job.completed_at),
        "error": job.error,
        "video_path": job.video_path,
        "log_path": job.log_path,
        "client_log_path": job.client_log_path,
        "video_id": job.video_id,
        "messages": [_serialize_progress_message(msg) for msg in job.messages],
        "versions": [_single_video_version_to_record(v) for v in job.versions],
        "selected_version_id": job.selected_version_id,
    }


def _single_video_state_from_record(payload: dict[str, Any]) -> SingleVideoJobState:
    return SingleVideoJobState(
        job_id=payload.get("job_id", str(uuid.uuid4())),
        prompt=payload.get("prompt", ""),
        video_provider=payload.get("video_provider") or DEFAULT_VIDEO_PROVIDER,
        resolution=payload.get("resolution") or DEFAULT_SORA_RESOLUTION,
        duration=int(payload.get("duration", 8)),
        download_variant=payload.get("download_variant", "video"),
        status=payload.get("status", "queued"),
        progress=float(payload.get("progress", 0.0)),
        started_at=_deserialize_datetime(payload.get("started_at")) or utc_now(),
        completed_at=_deserialize_datetime(payload.get("completed_at")),
        error=payload.get("error"),
        video_path=payload.get("video_path"),
        log_path=payload.get("log_path"),
        client_log_path=payload.get("client_log_path"),
        video_id=payload.get("video_id"),
        messages=[_deserialize_progress_message(msg) for msg in payload.get("messages", [])],
        versions=[_single_video_version_from_record(v) for v in payload.get("versions", [])],
        selected_version_id=payload.get("selected_version_id"),
    )


def _insight_key_frame_to_record(frame: VideoInsightKeyFrame) -> dict[str, Any]:
    return {
        "frame_id": frame.frame_id,
        "timestamp": frame.timestamp,
        "description": frame.description,
        "confidence": frame.confidence,
        "tags": list(frame.tags),
    }


def _insight_key_frame_from_record(payload: dict[str, Any]) -> VideoInsightKeyFrame:
    return VideoInsightKeyFrame(
        frame_id=payload.get("frame_id"),
        timestamp=payload.get("timestamp"),
        description=payload.get("description"),
        confidence=payload.get("confidence"),
        tags=list(payload.get("tags", [])),
    )


def _insight_segment_to_record(segment: VideoInsightSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "label": segment.label,
        "start": segment.start,
        "end": segment.end,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "summary": segment.summary,
        "tags": list(segment.tags),
        "start_frame_path": segment.start_frame_path,
        "end_frame_path": segment.end_frame_path,
    }


def _insight_segment_from_record(payload: dict[str, Any]) -> VideoInsightSegment:
    return VideoInsightSegment(
        segment_id=payload.get("segment_id"),
        label=payload.get("label"),
        start=payload.get("start"),
        end=payload.get("end"),
        start_seconds=payload.get("start_seconds"),
        end_seconds=payload.get("end_seconds"),
        summary=payload.get("summary"),
        tags=list(payload.get("tags", [])),
        start_frame_path=payload.get("start_frame_path"),
        end_frame_path=payload.get("end_frame_path"),
    )


def _video_insight_state_to_record(job: VideoInsightJobState) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status,
        "progress": job.progress,
        "started_at": _serialize_datetime(job.started_at),
        "completed_at": _serialize_datetime(job.completed_at),
        "error": job.error,
        "video_path": job.video_path,
        "result_path": job.result_path,
        "content_type": job.original_content_type,
        "notes": job.notes,
        "messages": [_serialize_progress_message(msg) for msg in job.messages],
        "key_frames": [_insight_key_frame_to_record(frame) for frame in job.key_frames],
        "segments": [_insight_segment_to_record(segment) for segment in job.segments],
    }


def _video_insight_state_from_record(payload: dict[str, Any]) -> VideoInsightJobState:
    return VideoInsightJobState(
        job_id=payload.get("job_id", str(uuid.uuid4())),
        filename=payload.get("filename", "uploaded-video"),
        status=payload.get("status", "queued"),
        progress=float(payload.get("progress", 0.0)),
        started_at=_deserialize_datetime(payload.get("started_at")) or utc_now(),
        completed_at=_deserialize_datetime(payload.get("completed_at")),
        error=payload.get("error"),
        video_path=payload.get("video_path"),
        result_path=payload.get("result_path"),
        original_content_type=payload.get("content_type"),
        notes=payload.get("notes"),
        messages=[_deserialize_progress_message(msg) for msg in payload.get("messages", [])],
        key_frames=[_insight_key_frame_from_record(item) for item in payload.get("key_frames", [])],
        segments=[_insight_segment_from_record(item) for item in payload.get("segments", [])],
    )


def _reproduce_version_to_record(version: ReproduceSegmentVersion) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "prompt": version.prompt,
        "status": version.status,
        "video_path": version.video_path,
        "log_path": version.log_path,
        "client_log_path": version.client_log_path,
        "task_id": version.task_id,
        "error": version.error,
        "prompt_path": version.prompt_path,
        "created_at": _serialize_datetime(version.created_at),
    }


def _reproduce_version_from_record(payload: dict[str, Any]) -> ReproduceSegmentVersion:
    return ReproduceSegmentVersion(
        version_id=payload.get("version_id", str(uuid.uuid4())),
        prompt=payload.get("prompt", ""),
        status=payload.get("status", "queued"),
        video_path=payload.get("video_path"),
        log_path=payload.get("log_path"),
        client_log_path=payload.get("client_log_path"),
        task_id=payload.get("task_id"),
        error=payload.get("error"),
        prompt_path=payload.get("prompt_path"),
        created_at=_deserialize_datetime(payload.get("created_at")) or utc_now(),
    )


def _reproduce_segment_to_record(segment: VideoReproduceSegment) -> dict[str, Any]:
    return {
        "segment_id": segment.segment_id,
        "order": segment.order,
        "label": segment.label,
        "description": segment.description,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "duration_seconds": segment.duration_seconds,
        "kling_duration_seconds": segment.kling_duration_seconds,
        "tags": list(segment.tags),
        "start_frame_path": segment.start_frame_path,
        "end_frame_path": segment.end_frame_path,
        "stylized_start_frame_path": segment.stylized_start_frame_path,
        "stylized_end_frame_path": segment.stylized_end_frame_path,
        "image_prompt_path": segment.image_prompt_path,
        "gpt_image_prompt_path": segment.image_prompt_path,
        "original_video_path": segment.original_video_path,
        "status": segment.status,
        "selected_version_id": segment.selected_version_id,
        "versions": [_reproduce_version_to_record(version) for version in segment.versions],
    }


def _reproduce_segment_from_record(payload: dict[str, Any]) -> VideoReproduceSegment:
    versions: list[ReproduceSegmentVersion] = []
    for record in payload.get("versions", []):
        try:
            versions.append(_reproduce_version_from_record(record))
        except Exception:  # pragma: no cover - defensive
            continue
    segment = VideoReproduceSegment(
        segment_id=payload.get("segment_id"),
        order=int(payload.get("order", 0)),
        label=payload.get("label", ""),
        description=payload.get("description", ""),
        start_seconds=float(payload.get("start_seconds", 0.0) or 0.0),
        end_seconds=float(payload.get("end_seconds", 0.0) or 0.0),
        duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
        kling_duration_seconds=int(payload.get("kling_duration_seconds", 5) or 5),
        tags=list(payload.get("tags", [])),
        start_frame_path=payload.get("start_frame_path"),
        end_frame_path=payload.get("end_frame_path"),
        stylized_start_frame_path=payload.get("stylized_start_frame_path"),
        stylized_end_frame_path=payload.get("stylized_end_frame_path"),
        image_prompt_path=(
            payload.get("image_prompt_path") or payload.get("gpt_image_prompt_path")
        ),
        original_video_path=payload.get("original_video_path"),
        status=payload.get("status", "queued"),
        versions=versions,
        selected_version_id=payload.get("selected_version_id"),
    )
    if segment.selected_version_id:
        try:
            segment.select_version(segment.get_version(segment.selected_version_id))
        except KeyError:
            pass
    return segment


def _reproduce_job_to_record(job: VideoReproduceJobState) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "filename": job.filename,
        "content_type": job.content_type,
        "status": job.status,
        "progress": job.progress,
        "thread_count": job.thread_count,
        "resolution": job.resolution,
        "started_at": _serialize_datetime(job.started_at),
        "completed_at": _serialize_datetime(job.completed_at),
        "error": job.error,
        "video_path": job.video_path,
        "final_video_path": job.final_video_path,
        "notes": job.notes,
        "messages": [_serialize_progress_message(msg) for msg in job.messages],
        "segments": [_reproduce_segment_to_record(segment) for segment in job.segments],
        "image_generation_backend": job.image_generation_backend,
        "segmentation_backend": job.segmentation_backend,
    }


def _reproduce_job_from_record(payload: dict[str, Any]) -> VideoReproduceJobState:
    segments: list[VideoReproduceSegment] = []
    for record in payload.get("segments", []):
        try:
            segments.append(_reproduce_segment_from_record(record))
        except Exception:  # pragma: no cover - defensive
            continue
    return VideoReproduceJobState(
        job_id=payload.get("job_id", str(uuid.uuid4())),
        filename=payload.get("filename", "uploaded-video.mp4"),
        content_type=payload.get("content_type"),
        status=payload.get("status", "queued"),
        progress=float(payload.get("progress", 0.0)),
        thread_count=int(payload.get("thread_count", 3) or 3),
        resolution=payload.get("resolution") or DEFAULT_SORA_RESOLUTION,
        started_at=_deserialize_datetime(payload.get("started_at")) or utc_now(),
        completed_at=_deserialize_datetime(payload.get("completed_at")),
        error=payload.get("error"),
        video_path=payload.get("video_path"),
        final_video_path=payload.get("final_video_path"),
        notes=payload.get("notes"),
        messages=[_deserialize_progress_message(msg) for msg in payload.get("messages", [])],
        segments=segments,
        image_generation_backend=(
            payload.get("image_generation_backend") or DEFAULT_IMAGE_GENERATOR
        ),
        segmentation_backend=(
            payload.get("segmentation_backend") or DEFAULT_REPRODUCE_SEGMENTATION
        ),
    )


class SingleVideoJobStore:
    def __init__(self, persistence: Optional[SingleVideoJobDatabase] = None) -> None:
        self._jobs: dict[str, SingleVideoJobState] = {}
        self._lock = asyncio.Lock()
        self._persistence = persistence
        if self._persistence:
            for job in self._persistence.load_jobs():
                self._jobs[job.job_id] = job

    def _persist(self, job: SingleVideoJobState) -> None:
        if self._persistence:
            self._persistence.upsert(job)

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
        video_provider: str,
    ) -> None:
        async with self._lock:
            state = SingleVideoJobState(
                job_id=job_id,
                prompt=prompt,
                resolution=resolution,
                duration=duration,
                download_variant=download_variant,
                video_provider=video_provider,
            )
            state.add_message("queued", "Job queued.")
            self._jobs[job_id] = state
            self._persist(state)

    async def set_video_provider(self, job_id: str, video_provider: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_provider = video_provider
            self._persist(job)

    async def get_video_provider(self, job_id: str) -> str:
        async with self._lock:
            job = self._require(job_id)
            return job.video_provider

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.progress = max(job.progress, 0.05)
            job.add_message(
                "running",
                f"Submitting video generation request to {job.video_provider.upper()} backend.",
            )
            self._persist(job)

    async def update_progress(self, job_id: str, progress: float, message: str | None = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.progress = max(job.progress, min(max(progress, 0.0), 1.0))
            if message:
                job.add_message("running", message)
            self._persist(job)

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
            self._persist(job)

    async def mark_completed(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "completed"
            job.progress = 1.0
            job.completed_at = utc_now()
            job.add_message("completed", "Video generation finished.")
            self._persist(job)

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.error = error
            job.completed_at = utc_now()
            job.add_message("failed", error)
            self._persist(job)

    async def set_single_selection(self, job_id: str, version_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            version = next((v for v in job.versions if v.version_id == version_id), None)
            if version is None:
                raise KeyError(version_id)
            job.set_active_version(version)
            job.add_message("running", f"Selected {version.label or version.version_id} as active version.")
            self._persist(job)

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
            self._persist(job)

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
            removed = self._jobs.pop(job_id)
            if self._persistence:
                self._persistence.delete(job_id)
            return removed

    async def add_message(self, job_id: str, stage: str, text: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.add_message(stage, text)
            self._persist(job)

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
                video_url=build_static_asset_url(version.video_path),
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
            video_provider=job.video_provider,
            resolution=job.resolution,
            duration=job.duration,
            download_variant=job.download_variant,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            video_path=job.video_path,
            video_url=build_static_asset_url(job.video_path),
            log_path=job.log_path,
            log_url=build_static_asset_url(job.log_path),
            client_log_path=job.client_log_path,
            video_id=job.video_id,
            selected_version_id=job.selected_version_id,
            versions=versions,
            messages=messages,
        )


class VideoInsightJobDatabase:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(path, indent=2, ensure_ascii=False)
        self._table = self._db.table("video_insight_jobs")

    def load_jobs(self) -> list[VideoInsightJobState]:
        try:
            records = self._table.all()
        except Exception:
            logger.exception("Failed to read video insight job database.")
            return []
        jobs: list[VideoInsightJobState] = []
        for record in records:
            try:
                jobs.append(_video_insight_state_from_record(record))
            except Exception:
                logger.exception("Unable to parse video insight job record: %s", record.get("job_id"))
        return jobs

    def upsert(self, job: VideoInsightJobState) -> None:
        try:
            self._table.upsert(_video_insight_state_to_record(job), Query().job_id == job.job_id)
        except Exception:
            logger.exception("Failed to persist video insight job: %s", job.job_id)

    def delete(self, job_id: str) -> None:
        try:
            self._table.remove(Query().job_id == job_id)
        except Exception:
            logger.exception("Failed to delete video insight job: %s", job_id)


class VideoInsightJobStore:
    def __init__(self, persistence: Optional[VideoInsightJobDatabase] = None) -> None:
        self._jobs: dict[str, VideoInsightJobState] = {}
        self._lock = asyncio.Lock()
        self._persistence = persistence
        if self._persistence:
            for job in self._persistence.load_jobs():
                self._jobs[job.job_id] = job

    def _persist(self, job: VideoInsightJobState) -> None:
        if self._persistence:
            self._persistence.upsert(job)

    def _require(self, job_id: str) -> VideoInsightJobState:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def create_job(
        self,
        job_id: str,
        filename: str,
        content_type: Optional[str],
        notes: Optional[str],
    ) -> None:
        async with self._lock:
            state = VideoInsightJobState(
                job_id=job_id,
                filename=filename,
                original_content_type=content_type,
                notes=notes,
            )
            state.add_message("queued", "Video insight job queued.")
            self._jobs[job_id] = state
            self._persist(state)

    async def set_video_path(self, job_id: str, video_path: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_path = video_path
            job.add_message("queued", "Video uploaded to workspace.")
            self._persist(job)

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.progress = max(job.progress, 0.1)
            job.add_message("running", "Submitting analysis request to Azure Content Understanding.")
            self._persist(job)

    async def update_progress(self, job_id: str, progress: float, message: Optional[str] = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.progress = max(job.progress, min(max(progress, 0.0), 1.0))
            if message:
                job.add_message("running", message)
            self._persist(job)

    async def set_result(
        self,
        job_id: str,
        result_path: str,
        key_frames: list[VideoInsightKeyFrame],
        segments: list[VideoInsightSegment],
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.result_path = result_path
            job.key_frames = key_frames
            job.segments = segments
            job.progress = max(job.progress, 0.9)
            job.add_message("running", "Analysis complete. Aggregating insights.")
            self._persist(job)

    async def mark_completed(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "completed"
            job.progress = 1.0
            job.completed_at = utc_now()
            job.add_message("completed", "Video insight job finished.")
            self._persist(job)

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.error = error
            job.completed_at = utc_now()
            job.add_message("failed", error)
            self._persist(job)

    async def add_message(self, job_id: str, stage: str, text: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.add_message(stage, text)
            self._persist(job)

    async def get_job_state(self, job_id: str) -> VideoInsightJobState:
        async with self._lock:
            job = self._require(job_id)
            return copy.deepcopy(job)

    async def snapshot(self, job_id: str) -> VideoInsightJobResponse:
        async with self._lock:
            job = self._require(job_id)
            return self._snapshot(job)

    async def list_statuses(self) -> list[VideoInsightJobResponse]:
        async with self._lock:
            return [
                self._snapshot(job)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda item: item.started_at,
                    reverse=True,
                )
            ]

    async def delete_job(self, job_id: str) -> VideoInsightJobState:
        async with self._lock:
            job = self._require(job_id)
            if job.status == "running":
                raise ValueError("Cannot delete a video insight job while it is running.")
            removed = self._jobs.pop(job_id)
        if self._persistence:
            self._persistence.delete(job_id)
        return removed

    def _snapshot(self, job: VideoInsightJobState) -> VideoInsightJobResponse:
        messages = [
            JobMessageModel(timestamp=item.timestamp, stage=item.stage, text=item.text)
            for item in job.messages
        ]
        key_frames = [
            VideoInsightKeyFrameModel(
                frame_id=frame.frame_id,
                timestamp=frame.timestamp,
                description=frame.description,
                confidence=frame.confidence,
                tags=list(frame.tags),
            )
            for frame in job.key_frames
        ]
        segments = [
            VideoInsightSegmentModel(
                segment_id=segment.segment_id,
                label=segment.label,
                start=segment.start,
                end=segment.end,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                summary=segment.summary,
                tags=list(segment.tags),
                start_frame_path=segment.start_frame_path,
                start_frame_url=build_static_asset_url(segment.start_frame_path),
                end_frame_path=segment.end_frame_path,
                end_frame_url=build_static_asset_url(segment.end_frame_path),
            )
            for segment in job.segments
        ]
        return VideoInsightJobResponse(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress,
            filename=job.filename,
            content_type=job.original_content_type,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            video_path=job.video_path,
            video_url=build_static_asset_url(job.video_path),
            result_path=job.result_path,
            result_url=build_static_asset_url(job.result_path),
            notes=job.notes,
            messages=messages,
            key_frames=key_frames,
            segments=segments,
        )


class VideoReproduceJobDatabase:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = TinyDB(path, indent=2, ensure_ascii=False)
        self._table = self._db.table("video_reproduce_jobs")

    def load_jobs(self) -> list[VideoReproduceJobState]:
        try:
            records = self._table.all()
        except Exception:
            logger.exception("Failed to read video reproduce job database.")
            return []
        jobs: list[VideoReproduceJobState] = []
        for record in records:
            try:
                jobs.append(_reproduce_job_from_record(record))
            except Exception:
                logger.exception("Unable to parse video reproduce job record: %s", record.get("job_id"))
        return jobs

    def upsert(self, job: VideoReproduceJobState) -> None:
        try:
            self._table.upsert(_reproduce_job_to_record(job), Query().job_id == job.job_id)
        except Exception:
            logger.exception("Failed to persist video reproduce job: %s", job.job_id)

    def delete(self, job_id: str) -> None:
        try:
            self._table.remove(Query().job_id == job_id)
        except Exception:
            logger.exception("Failed to delete video reproduce job: %s", job_id)


class VideoReproduceJobStore:
    def __init__(self, persistence: Optional[VideoReproduceJobDatabase] = None) -> None:
        self._jobs: dict[str, VideoReproduceJobState] = {}
        self._lock = asyncio.Lock()
        self._persistence = persistence
        if self._persistence:
            for job in self._persistence.load_jobs():
                self._jobs[job.job_id] = job

    def _persist(self, job: VideoReproduceJobState) -> None:
        if self._persistence:
            self._persistence.upsert(job)

    def _require(self, job_id: str) -> VideoReproduceJobState:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    async def create_job(
        self,
        job_id: str,
        filename: str,
        content_type: Optional[str],
        notes: Optional[str],
        thread_count: int,
        image_generation_backend: str,
        segmentation_backend: str,
    ) -> None:
        async with self._lock:
            state = VideoReproduceJobState(
                job_id=job_id,
                filename=filename,
                content_type=content_type,
                notes=notes,
                thread_count=thread_count,
                image_generation_backend=image_generation_backend,
                segmentation_backend=segmentation_backend,
            )
            state.add_message("queued", "Video reproduce workflow queued.")
            self._jobs[job_id] = state
            self._persist(state)

    async def set_video_path(self, job_id: str, video_path: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.video_path = video_path
            job.add_message("queued", "Video uploaded to workspace.")
            self._persist(job)

    async def queue_rerun(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            if job.status == "running":
                raise ValueError("Cannot rerun a reproduce job while it is running.")
            job.status = "queued"
            job.progress = min(job.progress, 0.4)
            job.error = None
            job.completed_at = None
            job.final_video_path = None
            job.add_message("queued", "Rerun requested; cached artifacts will be reused when possible.")
            for segment in job.segments:
                if segment.status not in ("completed",):
                    segment.status = "queued"
                for version in segment.versions:
                    if version.status == "running":
                        version.status = "failed"
                        if not version.error:
                            version.error = "Render interrupted before completion."
            self._persist(job)

    async def mark_running(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "running"
            job.progress = max(job.progress, 0.05)
            job.add_message("running", "Preparing reproduce workflow.")
            self._persist(job)

    async def update_progress(self, job_id: str, progress: float, message: Optional[str] = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.progress = max(job.progress, min(max(progress, 0.0), 1.0))
            if message:
                job.add_message("running", message)
            self._persist(job)

    async def set_segments(
        self,
        job_id: str,
        segments: list[VideoReproduceSegment],
        *,
        source: str = "insight analysis",
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.segments = segments
            job.add_message(
                "running",
                f"Detected {len(segments)} segment(s) from {source}.",
            )
            self._persist(job)

    async def set_resolution(self, job_id: str, resolution: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.resolution = resolution
            self._persist(job)

    async def update_segment_attributes(
        self,
        job_id: str,
        order: int,
        *,
        status: Optional[str] = None,
        description: Optional[str] = None,
        original_video_path: Optional[str] = None,
        stylized_start_frame_path: Optional[str] = None,
        stylized_end_frame_path: Optional[str] = None,
        image_prompt_path: Optional[str] = None,
        gpt_image_prompt_path: Optional[str] = None,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            segment = next((item for item in job.segments if item.order == order), None)
            if segment is None:
                raise KeyError(f"segment-{order}")
            if status:
                segment.status = status
            if description is not None:
                segment.description = description
            if original_video_path is not None:
                segment.original_video_path = original_video_path
            if stylized_start_frame_path is not None:
                segment.stylized_start_frame_path = stylized_start_frame_path
            if stylized_end_frame_path is not None:
                segment.stylized_end_frame_path = stylized_end_frame_path
            prompt_path_value = image_prompt_path if image_prompt_path is not None else gpt_image_prompt_path
            if prompt_path_value is not None:
                segment.image_prompt_path = prompt_path_value
            self._persist(job)

    async def add_segment_version(
        self,
        job_id: str,
        order: int,
        version: ReproduceSegmentVersion,
        make_active: bool = True,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            segment = next((item for item in job.segments if item.order == order), None)
            if segment is None:
                raise KeyError(f"segment-{order}")
            segment.versions.append(version)
            if make_active:
                segment.select_version(version)
            self._persist(job)

    async def update_segment_version(
        self,
        job_id: str,
        order: int,
        version_id: str,
        **updates: Any,
    ) -> None:
        async with self._lock:
            job = self._require(job_id)
            segment = next((item for item in job.segments if item.order == order), None)
            if segment is None:
                raise KeyError(f"segment-{order}")
            version = next((item for item in segment.versions if item.version_id == version_id), None)
            if version is None:
                raise KeyError(version_id)
            for key, value in updates.items():
                if hasattr(version, key):
                    setattr(version, key, value)
            self._persist(job)

    async def select_segment_version(self, job_id: str, order: int, version_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            segment = next((item for item in job.segments if item.order == order), None)
            if segment is None:
                raise KeyError(f"segment-{order}")
            version = segment.get_version(version_id)
            segment.select_version(version)
            self._persist(job)

    async def set_final_video(self, job_id: str, final_video_path: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.final_video_path = final_video_path
            job.add_message("running", "Updated merged Kling video output.")
            self._persist(job)

    async def mark_completed(self, job_id: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "completed"
            job.progress = 1.0
            job.completed_at = utc_now()
            job.add_message("completed", "Video reproduce workflow finished.")
            self._persist(job)

    async def mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.status = "failed"
            job.error = error
            job.completed_at = utc_now()
            job.add_message("failed", error)
            self._persist(job)

    async def add_message(self, job_id: str, stage: str, text: str) -> None:
        async with self._lock:
            job = self._require(job_id)
            job.add_message(stage, text)
            self._persist(job)

    async def get_job_state(self, job_id: str) -> VideoReproduceJobState:
        async with self._lock:
            job = self._require(job_id)
            return copy.deepcopy(job)

    async def snapshot(self, job_id: str) -> VideoReproduceJobResponse:
        async with self._lock:
            job = self._require(job_id)
            return self._snapshot(job)

    async def list_statuses(self) -> list[VideoReproduceJobResponse]:
        async with self._lock:
            return [
                self._snapshot(job)
                for job in sorted(
                    self._jobs.values(),
                    key=lambda item: item.started_at,
                    reverse=True,
                )
            ]

    async def delete_job(self, job_id: str) -> VideoReproduceJobState:
        async with self._lock:
            job = self._require(job_id)
            if job.status == "running":
                raise ValueError("Cannot delete a reproduce job while it is running.")
            removed = self._jobs.pop(job_id)
            if self._persistence:
                self._persistence.delete(job_id)
            return removed

    def _snapshot(self, job: VideoReproduceJobState) -> VideoReproduceJobResponse:
        messages = [
            JobMessageModel(timestamp=item.timestamp, stage=item.stage, text=item.text)
            for item in job.messages
        ]
        segments = []
        for segment in job.segments:
            versions = [
                ReproduceSegmentVersionModel(
                    version_id=version.version_id,
                    prompt=version.prompt,
                    status=version.status,
                    video_path=version.video_path,
                    video_url=build_static_asset_url(version.video_path),
                    log_path=version.log_path,
                    log_url=build_static_asset_url(version.log_path),
                    client_log_path=version.client_log_path,
                    client_log_url=build_static_asset_url(version.client_log_path),
                    task_id=version.task_id,
                    error=version.error,
                    created_at=version.created_at,
                )
                for version in segment.versions
            ]
            segments.append(
                VideoReproduceSegmentModel(
                    segment_id=segment.segment_id,
                    order=segment.order,
                    label=segment.label,
                    description=segment.description,
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    duration_seconds=segment.duration_seconds,
                    kling_duration_seconds=segment.kling_duration_seconds,
                    tags=list(segment.tags),
                    start_frame_path=segment.start_frame_path,
                    start_frame_url=build_static_asset_url(segment.start_frame_path),
                    end_frame_path=segment.end_frame_path,
                    end_frame_url=build_static_asset_url(segment.end_frame_path),
                    stylized_start_frame_path=segment.stylized_start_frame_path,
                    stylized_start_frame_url=build_static_asset_url(segment.stylized_start_frame_path),
                    stylized_end_frame_path=segment.stylized_end_frame_path,
                    stylized_end_frame_url=build_static_asset_url(segment.stylized_end_frame_path),
                    image_prompt_path=segment.image_prompt_path,
                    image_prompt_url=build_static_asset_url(segment.image_prompt_path),
                    original_video_path=segment.original_video_path,
                    original_video_url=build_static_asset_url(segment.original_video_path),
                    status=segment.status,
                    selected_version_id=segment.selected_version_id,
                    versions=versions,
                )
            )
        return VideoReproduceJobResponse(
            job_id=job.job_id,
            filename=job.filename,
            content_type=job.content_type,
            status=job.status,
            progress=job.progress,
            thread_count=job.thread_count,
            resolution=job.resolution,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
            notes=job.notes,
            video_path=job.video_path,
            video_url=build_static_asset_url(job.video_path),
            final_video_path=job.final_video_path,
            final_video_url=build_static_asset_url(job.final_video_path),
            image_generation_backend=job.image_generation_backend,
            segmentation_backend=job.segmentation_backend,
            messages=messages,
            segments=segments,
        )


def _build_single_video_job_store() -> SingleVideoJobStore:
    try:
        persistence = SingleVideoJobDatabase(SINGLE_VIDEO_DB_PATH)
    except Exception:
        logger.exception("Persistent single video database unavailable, using in-memory store.")
        return SingleVideoJobStore()
    return SingleVideoJobStore(persistence=persistence)


def _build_video_insight_job_store() -> VideoInsightJobStore:
    try:
        persistence = VideoInsightJobDatabase(VIDEO_INSIGHT_DB_PATH)
    except Exception:
        logger.exception("Persistent video insight database unavailable, using in-memory store.")
        return VideoInsightJobStore()
    return VideoInsightJobStore(persistence=persistence)


def _build_video_reproduce_job_store() -> VideoReproduceJobStore:
    try:
        persistence = VideoReproduceJobDatabase(VIDEO_REPRODUCE_DB_PATH)
    except Exception:
        logger.exception("Persistent video reproduce database unavailable, using in-memory store.")
        return VideoReproduceJobStore()
    return VideoReproduceJobStore(persistence=persistence)


WORKFLOW_JOB_STORE = _build_workflow_job_store()
SINGLE_VIDEO_STORE = _build_single_video_job_store()
VIDEO_INSIGHT_STORE = _build_video_insight_job_store()
VIDEO_REPRODUCE_STORE = _build_video_reproduce_job_store()


__all__ = [
    "SINGLE_VIDEO_STORE",
    "SingleVideoJobDatabase",
    "SingleVideoJobStore",
    "VIDEO_INSIGHT_STORE",
    "VideoInsightJobDatabase",
    "VideoInsightJobStore",
    "VIDEO_REPRODUCE_STORE",
    "VideoReproduceJobDatabase",
    "VideoReproduceJobStore",
    "WORKFLOW_JOB_STORE",
    "WorkflowJobDatabase",
    "WorkflowJobStore",
]
