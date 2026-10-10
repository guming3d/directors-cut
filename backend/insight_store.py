"""Video insight job persistence and tracking."""

from __future__ import annotations

import asyncio
import copy
import uuid
from pathlib import Path
from typing import Any, Optional

from tinydb import Query, TinyDB

from .config import VIDEO_INSIGHT_DB_PATH, logger
from .models import (
    JobMessageModel,
    VideoInsightJobResponse,
    VideoInsightJobState,
    VideoInsightKeyFrame,
    VideoInsightKeyFrameModel,
    VideoInsightSegment,
    VideoInsightSegmentModel,
)
from .store_common import (
    _deserialize_datetime,
    _deserialize_progress_message,
    _serialize_datetime,
    _serialize_progress_message,
)
from .utils import build_static_asset_url, utc_now


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

    def active_job_ids(self) -> list[str]:
        """Jobs still queued or running; after a restart these can never finish."""

        return [job_id for job_id, job in self._jobs.items() if job.status in ("queued", "running")]

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


def build_video_insight_job_store() -> VideoInsightJobStore:
    try:
        persistence = VideoInsightJobDatabase(VIDEO_INSIGHT_DB_PATH)
    except Exception:
        logger.exception("Persistent video insight database unavailable, using in-memory store.")
        return VideoInsightJobStore()
    return VideoInsightJobStore(persistence=persistence)


__all__ = [
    "VideoInsightJobDatabase",
    "VideoInsightJobStore",
    "build_video_insight_job_store",
]
