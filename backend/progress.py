"""Lightweight helpers for reporting workflow progress to the job store."""

from __future__ import annotations

from typing import Optional

from .models import SceneRender, VideoWorkflowRequest
from .stores import WORKFLOW_JOB_STORE


async def report_progress(
    request: VideoWorkflowRequest,
    stage: str,
    message: str,
    progress: Optional[float] = None,
) -> None:
    if request.job_id:
        await WORKFLOW_JOB_STORE.update_stage(request.job_id, stage, message, progress)


async def record_total_scenes(request: VideoWorkflowRequest, total: int) -> None:
    if request.job_id:
        await WORKFLOW_JOB_STORE.set_total_scenes(request.job_id, total)


async def record_scene_completion(request: VideoWorkflowRequest, render: SceneRender) -> None:
    if request.job_id:
        await WORKFLOW_JOB_STORE.record_scene_completion(request.job_id, render)


async def mark_composing(request: VideoWorkflowRequest, message: str | None = None) -> None:
    if request.job_id:
        await WORKFLOW_JOB_STORE.mark_composing(request.job_id, message)


async def mark_completed(
    request: VideoWorkflowRequest, final_video_path: str, manifest: dict
) -> None:
    if request.job_id:
        await WORKFLOW_JOB_STORE.mark_completed(request.job_id, final_video_path, manifest)


__all__ = [
    "mark_completed",
    "mark_composing",
    "record_scene_completion",
    "record_total_scenes",
    "report_progress",
]
