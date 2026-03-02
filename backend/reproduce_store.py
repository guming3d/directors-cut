"""Video reproduce job persistence and tracking."""

from __future__ import annotations

import asyncio
import copy
import uuid
from pathlib import Path
from typing import Any, Optional

from tinydb import Query, TinyDB

from .config import (
    DEFAULT_IMAGE_GENERATOR,
    DEFAULT_REPRODUCE_REFERENCE_MODE,
    DEFAULT_REPRODUCE_SEGMENTATION,
    DEFAULT_SORA_RESOLUTION,
    VIDEO_REPRODUCE_DB_PATH,
    logger,
)
from .models import (
    JobMessageModel,
    ReproduceSegmentVersion,
    ReproduceSegmentVersionModel,
    VideoReproduceJobResponse,
    VideoReproduceJobState,
    VideoReproduceSegment,
    VideoReproduceSegmentModel,
)
from .store_common import (
    _UNSET,
    _deserialize_datetime,
    _deserialize_progress_message,
    _serialize_datetime,
    _serialize_progress_message,
)
from .utils import build_static_asset_url, utc_now


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
        "reference_mode": job.reference_mode,
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
        reference_mode=(payload.get("reference_mode") or DEFAULT_REPRODUCE_REFERENCE_MODE),
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
        reference_mode: str = DEFAULT_REPRODUCE_REFERENCE_MODE,
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
                reference_mode=reference_mode or DEFAULT_REPRODUCE_REFERENCE_MODE,
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
        stylized_start_frame_path: Any = _UNSET,
        stylized_end_frame_path: Any = _UNSET,
        image_prompt_path: Any = _UNSET,
        gpt_image_prompt_path: Any = _UNSET,
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
            if stylized_start_frame_path is not _UNSET:
                segment.stylized_start_frame_path = stylized_start_frame_path or None
            if stylized_end_frame_path is not _UNSET:
                segment.stylized_end_frame_path = stylized_end_frame_path or None
            prompt_path_value = (
                image_prompt_path if image_prompt_path is not _UNSET else gpt_image_prompt_path
            )
            if prompt_path_value is not _UNSET:
                segment.image_prompt_path = prompt_path_value or None
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
            job.error = None
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
            reference_mode=job.reference_mode,
            messages=messages,
            segments=segments,
        )


def build_video_reproduce_job_store() -> VideoReproduceJobStore:
    try:
        persistence = VideoReproduceJobDatabase(VIDEO_REPRODUCE_DB_PATH)
    except Exception:
        logger.exception("Persistent video reproduce database unavailable, using in-memory store.")
        return VideoReproduceJobStore()
    return VideoReproduceJobStore(persistence=persistence)


__all__ = [
    "VideoReproduceJobDatabase",
    "VideoReproduceJobStore",
    "build_video_reproduce_job_store",
]
