"""Single-video job persistence and in-memory tracking."""

from __future__ import annotations

import asyncio
import copy
import uuid
from pathlib import Path
from typing import Any, Optional

from tinydb import Query, TinyDB

from .config import DEFAULT_SORA_RESOLUTION, DEFAULT_VIDEO_PROVIDER, SINGLE_VIDEO_DB_PATH, logger
from .models import (
    JobMessageModel,
    SingleVideoJobResponse,
    SingleVideoJobState,
    SingleVideoVersionEntry,
    SingleVideoVersionModel,
)
from .store_common import (
    _deserialize_datetime,
    _deserialize_progress_message,
    _serialize_datetime,
    _serialize_progress_message,
)
from .utils import build_static_asset_url, utc_now


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


def build_single_video_job_store() -> SingleVideoJobStore:
    try:
        persistence = SingleVideoJobDatabase(SINGLE_VIDEO_DB_PATH)
    except Exception:
        logger.exception("Persistent single video database unavailable, using in-memory store.")
        return SingleVideoJobStore()
    return SingleVideoJobStore(persistence=persistence)


__all__ = [
    "SingleVideoJobDatabase",
    "SingleVideoJobStore",
    "build_single_video_job_store",
]
