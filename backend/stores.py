"""Aggregate store implementations for workflow, single video, insights, and reproduction."""

from __future__ import annotations

from .insight_store import (
    VideoInsightJobDatabase,
    VideoInsightJobStore,
    build_video_insight_job_store,
)
from .reproduce_store import (
    VideoReproduceJobDatabase,
    VideoReproduceJobStore,
    build_video_reproduce_job_store,
)
from .single_video_store import (
    SingleVideoJobDatabase,
    SingleVideoJobStore,
    build_single_video_job_store,
)
from .workflow_store import WorkflowJobDatabase, WorkflowJobStore, build_workflow_job_store

WORKFLOW_JOB_STORE = build_workflow_job_store()
SINGLE_VIDEO_STORE = build_single_video_job_store()
VIDEO_INSIGHT_STORE = build_video_insight_job_store()
VIDEO_REPRODUCE_STORE = build_video_reproduce_job_store()

__all__ = [
    "INTERRUPTED_JOB_MESSAGE",
    "fail_interrupted_jobs",
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


INTERRUPTED_JOB_MESSAGE = (
    "Interrupted because the backend restarted before this job finished. "
    "Regenerate the unfinished scenes or start a new job."
)


async def fail_interrupted_jobs() -> int:
    """Mark jobs left queued/running by a previous backend process as failed.

    Renders run as in-process tasks, so a restart, crash, or redeploy ends
    them; without this the job would show as running forever.
    """

    count = 0
    for store in (WORKFLOW_JOB_STORE, SINGLE_VIDEO_STORE, VIDEO_INSIGHT_STORE, VIDEO_REPRODUCE_STORE):
        for job_id in store.active_job_ids():
            await store.mark_failed(job_id, INTERRUPTED_JOB_MESSAGE)
            count += 1
    return count

