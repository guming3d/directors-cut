from __future__ import annotations

import sys
import types
from pathlib import Path


class _StubExecutor:
    def __init__(self, *_, **__):
        pass


class _StubWorkflowContext:
    def __class_getitem__(cls, _item):
        return cls


def _stub_handler(func):
    return func


class _StubWorkflowBuilder:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def set_start_executor(self, *_args, **_kwargs):
        return self

    def add_edge(self, *_args, **_kwargs):
        return self

    def build(self):
        return object()


if "agent_framework" not in sys.modules:
    sys.modules["agent_framework"] = types.SimpleNamespace(
        Executor=_StubExecutor,
        WorkflowBuilder=_StubWorkflowBuilder,
        WorkflowContext=_StubWorkflowContext,
        handler=_stub_handler,
    )

from backend import reproduce
from backend.models import ReproduceSegmentVersion, VideoReproduceSegment


def _build_segment_with_versions(
    tmp_path: Path,
    version_payloads: list[tuple[str, str, bool]],
    selected_id: str | None = None,
) -> VideoReproduceSegment:
    versions: list[ReproduceSegmentVersion] = []
    for version_id, status, write_file in version_payloads:
        clip_path = tmp_path / f"{version_id}.mp4"
        if write_file:
            clip_path.write_bytes(b"test-bytes")
        versions.append(
            ReproduceSegmentVersion(
                version_id=version_id,
                prompt="make video",
                status=status,
                video_path=str(clip_path) if write_file else str(clip_path),
            )
        )
    return VideoReproduceSegment(
        order=1,
        label="Segment 1",
        description="desc",
        start_seconds=0.0,
        end_seconds=5.0,
        duration_seconds=5.0,
        kling_duration_seconds=5,
        versions=versions,
        selected_version_id=selected_id,
    )


def test_pick_cached_version_prefers_selected_completed(tmp_path):
    segment = _build_segment_with_versions(
        tmp_path,
        [
            ("v1", "completed", True),
            ("v2", "completed", True),
        ],
        selected_id="v1",
    )
    chosen = reproduce._pick_cached_version(segment)
    assert chosen is not None
    assert chosen.version_id == "v1"


def test_pick_cached_version_falls_back_when_selected_missing(tmp_path):
    segment = _build_segment_with_versions(
        tmp_path,
        [
            ("v1", "completed", False),
            ("v2", "running", True),
        ],
        selected_id="v1",
    )
    # Remove the empty file for v1 to simulate a missing artifact.
    selected_path = tmp_path / "v1.mp4"
    selected_path.unlink(missing_ok=True)

    chosen = reproduce._pick_cached_version(segment)
    assert chosen is not None
    assert chosen.version_id == "v2"


def test_artifact_exists_handles_missing_files(tmp_path):
    missing = tmp_path / "ghost.mp4"
    assert reproduce._artifact_exists(str(missing)) is False
    missing.write_bytes(b"x")
    assert reproduce._artifact_exists(str(missing)) is True
