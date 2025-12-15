"""Workflow job persistence and in-memory tracking."""

from __future__ import annotations

import asyncio
import copy
import uuid
from pathlib import Path
from typing import Any, Optional

from tinydb import Query, TinyDB

from .config import (
    DEFAULT_SORA_RESOLUTION,
    DEFAULT_VIDEO_PROVIDER,
    SORA_CLIP_DURATION_SECONDS,
    WORKFLOW_DB_PATH,
    logger,
)
from .models import (
    JobMessageModel,
    JobStatusResponse,
    ScenePlan,
    SceneStatus,
    SceneStatusModel,
    SceneVersionEntry,
    SceneVersionModel,
    WorkflowJobState,
)
from .store_common import (
    _deserialize_datetime,
    _deserialize_progress_message,
    _serialize_datetime,
    _serialize_progress_message,
)
from .utils import build_static_asset_url, utc_now


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
            scene = next((item for item in job.scenes if item.order == render.order), None)
            if scene is None:
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
                job.scenes.append(scene)
            else:
                scene.label = render.label
                scene.duration_seconds = render.duration_seconds
                scene.prompt_path = render.prompt_path
                scene.video_path = render.video_path
                scene.log_path = render.log_path
                scene.client_log_path = render.client_log_path
                scene.last_frame_path = render.last_frame_path
                scene.first_frame_path = render.first_frame_path
            scene.versions.append(version)
            scene.set_selected_version(version)
            total = job.total_scenes or 1
            fraction = min(job.completed_scenes / total, 1.0)
            progress = 0.2 + 0.6 * fraction
            job.progress = max(job.progress, progress)
            job.stage = "rendering"
            job.add_message("rendering", f"Completed {render.label}.")
            self._persist(job)

    async def record_scene_failure(self, job_id: str, plan: ScenePlan, error: Optional[str] = None) -> None:
        async with self._lock:
            job = self._require(job_id)
            scene = next((item for item in job.scenes if item.order == plan.order), None)
            if scene is None:
                scene = SceneStatus(
                    order=plan.order,
                    label=plan.label,
                    duration_seconds=plan.duration_seconds,
                    prompt_path=plan.prompt_path,
                    video_path="",
                )
                job.scenes.append(scene)
            else:
                scene.label = plan.label or scene.label
                scene.duration_seconds = plan.duration_seconds or scene.duration_seconds
                scene.prompt_path = plan.prompt_path or scene.prompt_path
            version = SceneVersionEntry(
                version_id=uuid.uuid4().hex,
                label=f"Attempt {len(scene.versions) + 1} (failed)",
                video_path="",
                prompt=plan.prompt_text,
                prompt_path=plan.prompt_path,
                source="failed",
            )
            scene.versions.append(version)
            if not scene.selected_version_id:
                scene.selected_version_id = version.version_id
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
            for scene in job.scenes
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


def build_workflow_job_store() -> WorkflowJobStore:
    try:
        persistence = WorkflowJobDatabase(WORKFLOW_DB_PATH)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Persistent workflow database unavailable, using in-memory store.")
        return WorkflowJobStore()
    return WorkflowJobStore(persistence=persistence)


__all__ = [
    "WorkflowJobDatabase",
    "WorkflowJobStore",
    "build_workflow_job_store",
]
