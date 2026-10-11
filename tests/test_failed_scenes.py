from __future__ import annotations

from pathlib import Path

import pytest

from backend.models import ScenePlan
from backend.stores import WorkflowJobStore


@pytest.mark.asyncio
async def test_failed_scene_is_exposed_for_regeneration(tmp_path: Path):
    store = WorkflowJobStore()
    job_id = "job-failed-scene"
    await store.create_job(job_id)

    prompt_path = tmp_path / "scene_01.txt"
    prompt_text = "test prompt for blocked scene"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    plan = ScenePlan(
        order=1,
        label="Scene 1: Blocked",
        duration_seconds=4,
        description="desc",
        prompt_path=str(prompt_path),
        prompt_text=prompt_text,
    )

    await store.record_scene_failure(job_id, plan, "blocked by provider")
    status = await store.get_status(job_id)

    assert len(status.scenes) == 1
    scene = status.scenes[0]
    assert scene.order == 1
    assert scene.selected_version_id is not None
    assert len(scene.versions) == 1
    version = scene.versions[0]
    assert version.source == "failed"
    assert version.prompt == prompt_text


@pytest.mark.asyncio
async def test_failed_regeneration_is_recorded_as_a_failed_version(tmp_path: Path, monkeypatch):
    from backend import services
    from backend.models import SceneVersionEntry

    store = WorkflowJobStore()
    monkeypatch.setattr(services, "WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr(services, "job_root", lambda job_id: tmp_path)
    job_id = "job-regen-failure"
    await store.create_job(job_id)
    await store.set_video_provider(job_id, "seedance")
    prompt_path = tmp_path / "prompts" / "scene_01.txt"
    prompt_path.parent.mkdir()
    prompt_path.write_text("A calm sea.", encoding="utf-8")
    plan = ScenePlan(order=1, label="Scene 1: Sea", duration_seconds=5, description="d",
                     prompt_path=str(prompt_path), prompt_text="A calm sea.")
    await store.record_scene_failure(job_id, plan)
    good = SceneVersionEntry(version_id="good", video_path=str(tmp_path / "good.mp4"), source="original")
    await store.add_scene_version(job_id, 1, good, make_active=True)

    async def failing_render(*args, **kwargs):
        raise RuntimeError("R2 upload of scene_01_first_frame.png failed (SSLError): EOF")

    monkeypatch.setattr(services, "_render_scene_regen_with_seedance", failing_render)

    await services.run_scene_regeneration_job(job_id, 1)

    scene = (await store.get_status(job_id)).scenes[0]
    latest = scene.versions[-1]
    assert (latest.source, latest.error, scene.selected_version_id) == (
        "failed", "R2 upload of scene_01_first_frame.png failed (SSLError): EOF", "good"
    )


def test_version_error_survives_a_restart():
    from backend.models import SceneVersionEntry
    from backend.workflow_store import _scene_version_from_record, _scene_version_to_record

    version = SceneVersionEntry(version_id="v", video_path="", source="failed", error="network down")

    assert _scene_version_from_record(_scene_version_to_record(version)).error == "network down"
