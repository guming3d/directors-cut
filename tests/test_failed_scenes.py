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
