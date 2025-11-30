"""Video insight job helpers built on Azure Content Understanding."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from ..config import (
    AZURE_CONTENT_UNDERSTANDING_API_KEY,
    AZURE_CONTENT_UNDERSTANDING_ENDPOINT,
    VIDEO_INSIGHT_JOBS_DIR,
    logger,
)
from ..models import VideoInsightSegment
from ..stores import VIDEO_INSIGHT_STORE
from ..utils import extract_frame_at_timestamp
from .client import AzureContentUnderstandingClient, ContentUnderstandingError
from .parser import summarize_content_understanding_result


def video_insight_job_root(job_id: str, *, ensure_exists: bool = True) -> Path:
    path = VIDEO_INSIGHT_JOBS_DIR / job_id
    if ensure_exists:
        path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_content_understanding_ready() -> None:
    if not AZURE_CONTENT_UNDERSTANDING_ENDPOINT or not AZURE_CONTENT_UNDERSTANDING_API_KEY:
        raise RuntimeError(
            "Configure AZURE_CONTENT_UNDERSTANDING_ENDPOINT and AZURE_CONTENT_UNDERSTANDING_API_KEY before running video insights.",
        )


async def run_video_insight_job(job_id: str, video_path: Path) -> None:
    try:
        ensure_content_understanding_ready()
        client = AzureContentUnderstandingClient()
        await VIDEO_INSIGHT_STORE.mark_running(job_id)
        await VIDEO_INSIGHT_STORE.update_progress(job_id, 0.35, "Video uploaded. Waiting for Azure analysis response.")
        result = await client.analyze_video(video_path)
        await VIDEO_INSIGHT_STORE.update_progress(job_id, 0.8, "Azure analysis completed. Extracting insights.")
        key_frames, segments = summarize_content_understanding_result(result)
        segments = await _attach_segment_frames(job_id, video_path, segments)
        result_path = video_path.with_suffix(".json")
        await asyncio.to_thread(
            result_path.write_text,
            json.dumps(result, indent=2, ensure_ascii=False),
        )
        await VIDEO_INSIGHT_STORE.set_result(job_id, str(result_path), key_frames, segments)
        await VIDEO_INSIGHT_STORE.mark_completed(job_id)
    except (ContentUnderstandingError, RuntimeError) as exc:
        logger.exception("Video insight job %s failed", job_id)
        await VIDEO_INSIGHT_STORE.mark_failed(job_id, str(exc))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Unexpected failure for video insight job %s", job_id)
        await VIDEO_INSIGHT_STORE.mark_failed(job_id, str(exc))


async def _attach_segment_frames(
    job_id: str,
    video_path: Path,
    segments: list[VideoInsightSegment],
) -> list[VideoInsightSegment]:
    if not segments:
        return segments
    frames_dir = video_insight_job_root(job_id) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for index, segment in enumerate(segments):
        base_name = f"segment_{index:03d}"
        if segment.start_seconds is not None:
            start_dest = frames_dir / f"{base_name}_start.jpg"
            segment.start_frame_path = await _maybe_extract_frame(
                job_id,
                video_path,
                segment.start_seconds,
                start_dest,
                "start",
            )
        if segment.end_seconds is not None:
            end_dest = frames_dir / f"{base_name}_end.jpg"
            segment.end_frame_path = await _maybe_extract_frame(
                job_id,
                video_path,
                segment.end_seconds,
                end_dest,
                "end",
            )
    return segments


async def _maybe_extract_frame(
    job_id: str,
    video_path: Path,
    timestamp: float,
    dest_path: Path,
    frame_label: str,
) -> Optional[str]:
    try:
        await extract_frame_at_timestamp(video_path, timestamp, dest_path)
        return str(dest_path)
    except Exception:
        logger.exception(
            "Failed to extract %s frame for insight job %s at %ss",
            frame_label,
            job_id,
            timestamp,
        )
        return None
