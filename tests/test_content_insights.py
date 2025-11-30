"""Unit tests covering content insight parsing and store helpers."""

from __future__ import annotations

import shutil

import pytest

from backend.config import OUTPUT_DIR
from backend.content_insights.parser import summarize_content_understanding_result
from backend.models import VideoInsightKeyFrame, VideoInsightSegment
from backend.stores import VideoInsightJobStore


def test_summarize_content_understanding_result_extracts_key_frames_and_segments() -> None:
    payload = {
        "status": "succeeded",
        "result": {
            "insights": {
                "keyFrames": [
                    {
                        "id": "kf-1",
                        "timestamp": {"seconds": 1.25},
                        "text": "Hero pose",
                        "confidence": 0.98,
                        "tags": ["hero", {"name": "pose"}],
                    }
                ],
                "segments": [
                    {
                        "segmentId": "seg-1",
                        "label": "Intro",
                        "start": "PT0S",
                        "end": "PT10S",
                        "summary": "Opening scene",
                        "categories": ["establishing"],
                    }
                ],
            }
        },
    }

    key_frames, segments = summarize_content_understanding_result(payload)

    assert len(key_frames) == 1
    assert key_frames[0].frame_id == "kf-1"
    assert key_frames[0].timestamp in {"1.25s", "PT1.25S", "1.25s"}
    assert "hero" in key_frames[0].tags

    assert len(segments) == 1
    assert segments[0].segment_id == "seg-1"
    assert segments[0].label == "Intro"
    assert segments[0].start == "PT0S"
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 10.0
    assert "establishing" in segments[0].tags


def test_extract_segments_handles_value_objects() -> None:
    payload = {
        "fields": {
            "Segments": {
                "type": "array",
                "valueArray": [
                    {
                        "type": "object",
                        "valueObject": {
                            "SegmentId": {"type": "string", "valueString": "1"},
                            "StartTimeMs": {"type": "integer", "valueInteger": 1500},
                            "EndTimeMs": {"type": "integer", "valueInteger": 4200},
                            "Description": {
                                "type": "string",
                                "valueString": "Segment summary text.",
                            },
                        },
                    }
                ],
            }
        }
    }

    _, segments = summarize_content_understanding_result(payload)

    assert len(segments) == 1
    assert segments[0].segment_id == "1"
    assert segments[0].start_seconds == 1.5
    assert segments[0].end_seconds == 4.2
    assert segments[0].summary == "Segment summary text."


@pytest.mark.asyncio
async def test_video_insight_store_snapshot_includes_artifacts() -> None:
    store = VideoInsightJobStore()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_id = "job-test-123"
    await store.create_job(job_id, "clip.mp4", "video/mp4", "notes")

    job_dir = OUTPUT_DIR / job_id
    video_path = job_dir / "clip.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.touch()
    await store.set_video_path(job_id, str(video_path))

    result_path = job_dir / "analysis.json"
    start_frame = job_dir / "seg_start.jpg"
    end_frame = job_dir / "seg_end.jpg"
    start_frame.touch()
    end_frame.touch()
    await store.set_result(
        job_id,
        str(result_path),
        [VideoInsightKeyFrame(frame_id="kf", timestamp="1s", description="frame", confidence=0.9, tags=["tag"])],
        [
            VideoInsightSegment(
                segment_id="seg",
                label="Intro",
                start="0s",
                end="5s",
                start_seconds=0.0,
                end_seconds=5.0,
                summary="hi",
                tags=["intro"],
                start_frame_path=str(start_frame),
                end_frame_path=str(end_frame),
            )
        ],
    )
    await store.mark_completed(job_id)

    snapshot = await store.snapshot(job_id)
    assert snapshot.filename == "clip.mp4"
    assert snapshot.status == "completed"
    assert snapshot.key_frames[0].frame_id == "kf"
    assert snapshot.segments[0].label == "Intro"
    assert snapshot.segments[0].start_seconds == 0.0
    assert snapshot.segments[0].start_frame_url and snapshot.segments[0].start_frame_url.startswith("/static/")
    assert snapshot.video_url and job_id in snapshot.video_url

    shutil.rmtree(job_dir, ignore_errors=True)
