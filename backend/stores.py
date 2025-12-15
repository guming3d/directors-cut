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
