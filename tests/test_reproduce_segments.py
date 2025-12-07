from __future__ import annotations

from pathlib import Path

import pytest

from backend import reproduce
from backend.models import VideoInsightSegment, VideoReproduceSegment
from backend.stores import VideoReproduceJobStore


def test_build_segment_states_splits_long_segments() -> None:
    segments = [
        VideoInsightSegment(
            segment_id="seg-001",
            label="Opening",
            start="PT0S",
            end="PT12S",
            summary="Long intro",
            tags=["intro"],
            start_seconds=0.0,
            end_seconds=12.0,
        ),
        VideoInsightSegment(
            segment_id="seg-002",
            label="Outro",
            start="PT12S",
            end="PT15S",
            summary="Closing shot",
            tags=["outro"],
            start_seconds=12.0,
            end_seconds=15.0,
        ),
    ]

    states = reproduce._build_segment_states(segments, video_duration=15.0)

    assert [segment.order for segment in states] == [1, 2, 3, 4]
    assert states[0].start_seconds == pytest.approx(0.0, rel=1e-3)
    assert states[1].start_seconds == pytest.approx(5.0, rel=1e-3)
    assert states[2].start_seconds == pytest.approx(10.0, rel=1e-3)
    assert states[2].duration_seconds == pytest.approx(2.0, rel=1e-3)
    assert states[0].label == "Opening (Part 1)"
    assert states[1].label == "Opening (Part 2)"
    assert states[2].label == "Opening (Part 3)"
    assert states[3].label == "Outro"
    assert all(segment.kling_duration_seconds == 5 for segment in states[:3])


@pytest.mark.asyncio
async def test_ensure_segment_duration_trims_long_kling_clip(tmp_path, monkeypatch) -> None:
    segment = VideoReproduceSegment(
        order=1,
        label="Segment 1",
        description="desc",
        start_seconds=0.0,
        end_seconds=2.0,
        duration_seconds=2.0,
        kling_duration_seconds=5,
    )
    source = tmp_path / "kling_segment.mp4"
    source.write_bytes(b"video-bytes")

    async def fake_probe(_video_path: Path) -> float:
        return 5.0

    recorded_cmds: list[list[str]] = []

    async def fake_run(cmd, cwd=None, env=None):
        recorded_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"trimmed")
        return "", ""

    monkeypatch.setattr(reproduce, "_probe_clip_duration", fake_probe)
    monkeypatch.setattr(reproduce, "run_subprocess", fake_run)

    adjusted = await reproduce._ensure_segment_duration(tmp_path, segment, source)

    assert adjusted != source
    assert adjusted.exists()
    assert adjusted.read_bytes() == b"trimmed"
    assert recorded_cmds, "Expected trim command to run."
    assert "-t" in recorded_cmds[0]


@pytest.mark.asyncio
async def test_update_segment_attributes_can_clear_stylized_paths() -> None:
    store = VideoReproduceJobStore()
    job_id = "job-clear-stylized"
    await store.create_job(
        job_id,
        "clip.mp4",
        "video/mp4",
        None,
        3,
        "original",
        "opencv",
    )
    segment = VideoReproduceSegment(
        order=1,
        label="Segment 1",
        description="desc",
        start_seconds=0.0,
        end_seconds=2.0,
        duration_seconds=2.0,
        kling_duration_seconds=5,
        stylized_start_frame_path="start.png",
        stylized_end_frame_path="end.png",
        image_prompt_path="prompt.txt",
    )
    await store.set_segments(job_id, [segment], source="test")

    await store.update_segment_attributes(
        job_id,
        segment.order,
        stylized_start_frame_path=None,
        stylized_end_frame_path=None,
        image_prompt_path=None,
    )

    state = await store.get_job_state(job_id)
    stored_segment = state.segments[0]
    assert stored_segment.stylized_start_frame_path is None
    assert stored_segment.stylized_end_frame_path is None
    assert stored_segment.image_prompt_path is None
