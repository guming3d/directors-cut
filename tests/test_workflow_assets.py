from datetime import datetime, timezone

import pytest

from backend import api
from backend.models import JobStatusResponse, SceneStatusModel, SceneVersionModel


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    root = tmp_path / "job-1"
    for sub in ("character_refs", "first_frames", "scene_images", "scenes", "prompts"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setattr(api, "job_root", lambda job_id: root)
    return root


def _touch(path, content=b"data"):
    path.write_bytes(content)
    return str(path)


def _job(job_dir, *, versions, scene_first_frame=None, character_refs=()):
    scene = SceneStatusModel(
        order=1,
        label="Scene 1: Mango Wakes",
        duration_seconds=4,
        prompt_path=_touch(job_dir / "prompts" / "scene_01.txt", b"prompt"),
        video_path=str(job_dir / "scenes" / "scene_01.mp4"),
        versions=versions,
        first_frame_path=scene_first_frame,
    )
    return JobStatusResponse(
        job_id="job-1",
        status="completed",
        stage="completed",
        progress=1.0,
        resolution="720x1280",
        started_at=datetime.now(timezone.utc),
        completed_at=None,
        error=None,
        final_video_path=None,
        final_video_url=None,
        manifest=None,
        messages=[],
        scenes=[scene],
        character_ref_paths=list(character_refs),
    )


def _version(job_dir, label, **frames):
    return SceneVersionModel(
        version_id=label.lower(),
        label=label,
        source="original",
        prompt=None,
        prompt_path=None,
        video_path=_touch(job_dir / "scenes" / f"scene_01_{label.lower()}.mp4"),
        video_url=None,
        log_path=None,
        client_log_path=None,
        video_id=None,
        created_at=datetime.now(timezone.utc),
        **frames,
    )


def _arcnames(job):
    return {arcname for _, arcname in api._collect_workflow_assets(job)}


SCENE_IMAGES = "workflow_job-1/scene-files/scene-01-scene-1-mango-wakes/images"


def test_bundle_includes_character_reference_images(job_dir):
    ref = _touch(job_dir / "character_refs" / "Mango_ref.png")
    job = _job(job_dir, versions=[_version(job_dir, "Original")], character_refs=[ref])

    assert "workflow_job-1/character-references/Mango_ref.png" in _arcnames(job)


def test_bundle_includes_scene_first_frame(job_dir):
    first = _touch(job_dir / "first_frames" / "scene_01_first_frame.png")
    job = _job(job_dir, versions=[_version(job_dir, "Original")], scene_first_frame=first)

    assert f"{SCENE_IMAGES}/first-frame.png" in _arcnames(job)


def test_bundle_stores_a_first_frame_shared_by_versions_once(job_dir):
    first = _touch(job_dir / "first_frames" / "scene_01_first_frame.png")
    versions = [
        _version(job_dir, "Original", first_frame_path=first),
        _version(job_dir, "Regenerate", first_frame_path=first),
    ]
    job = _job(job_dir, versions=versions, scene_first_frame=first)

    images = [name for name in _arcnames(job) if name.startswith(SCENE_IMAGES)]
    assert images == [f"{SCENE_IMAGES}/first-frame.png"]


def test_bundle_includes_version_last_frames(job_dir):
    last = _touch(job_dir / "scenes" / "scene_01_tail.png")
    job = _job(job_dir, versions=[_version(job_dir, "Original", last_frame_path=last)])

    assert f"{SCENE_IMAGES}/01-original-last-frame.png" in _arcnames(job)


def test_bundle_includes_uploaded_start_image_for_its_scene(job_dir):
    _touch(job_dir / "scene_images" / "scene1.png")
    _touch(job_dir / "scene_images" / "scene2.png")
    job = _job(job_dir, versions=[_version(job_dir, "Original")])

    assert f"{SCENE_IMAGES}/uploaded-start-image.png" in _arcnames(job)


def test_bundle_skips_image_paths_that_no_longer_exist(job_dir):
    job = _job(
        job_dir,
        versions=[_version(job_dir, "Original")],
        scene_first_frame=str(job_dir / "first_frames" / "missing.png"),
        character_refs=[str(job_dir / "character_refs" / "gone.png")],
    )

    assert not any(name.endswith(".png") for name in _arcnames(job))
