import pytest

from backend.models import SceneRender, SingleVideoVersionEntry
from backend.stores import (
    SingleVideoJobDatabase,
    SingleVideoJobStore,
    WorkflowJobDatabase,
    WorkflowJobStore,
)


@pytest.mark.asyncio
async def test_workflow_store_persists_across_sessions(tmp_path):
    db_path = tmp_path / "workflow.json"
    store = WorkflowJobStore(WorkflowJobDatabase(db_path))
    job_id = "job-123"

    await store.create_job(job_id)
    await store.set_total_scenes(job_id, 1)

    render = SceneRender(
        order=1,
        label="Scene 1",
        duration_seconds=8,
        prompt_path="prompt.txt",
        video_path="scene1.mp4",
        log_path="scene1.json",
        client_log_path="scene1.client.log",
        prompt_text="Prompt text",
        video_id="video-1",
        version_label="Original",
    )
    await store.record_scene_completion(job_id, render)

    manifest = {"scenes": [{"order": 1, "video_path": render.video_path}]}
    await store.mark_completed(job_id, "final.mp4", manifest)

    snapshot = await store.get_status(job_id)
    assert snapshot.status == "completed"
    assert snapshot.manifest == manifest
    assert snapshot.scenes[0].selected_version_id == snapshot.scenes[0].versions[0].version_id
    assert snapshot.messages[-1].text == "Workflow complete."

    reloaded_store = WorkflowJobStore(WorkflowJobDatabase(db_path))
    reloaded_snapshot = await reloaded_store.get_status(job_id)
    assert reloaded_snapshot.stage == "completed"
    assert reloaded_snapshot.final_video_path == "final.mp4"
    assert reloaded_snapshot.scenes[0].versions[0].video_path == render.video_path


@pytest.mark.asyncio
async def test_single_video_store_round_trip(tmp_path):
    db_path = tmp_path / "single.json"
    store = SingleVideoJobStore(SingleVideoJobDatabase(db_path))
    job_id = "single-1"

    await store.create_job(
        job_id,
        prompt="A calm lake at dawn",
        resolution="720x1280",
        duration=8,
        download_variant="video",
        video_provider="sora2",
    )
    await store.mark_running(job_id)

    version = SingleVideoVersionEntry(
        version_id="v1",
        label="Original",
        prompt="A calm lake at dawn",
        prompt_path="prompt.txt",
        video_path="lake.mp4",
        log_path="lake.json",
        client_log_path="lake.client.log",
        video_id="video-1",
    )
    await store.record_version(job_id, version, make_active=True, message="Rendered clip")
    await store.set_artifacts(job_id, version.video_path, version.log_path, version.client_log_path, video_id=version.video_id)
    await store.mark_completed(job_id)

    snapshot = await store.snapshot(job_id)
    assert snapshot.status == "completed"
    assert snapshot.video_path == version.video_path
    assert snapshot.selected_version_id == version.version_id
    assert snapshot.messages[-1].stage == "completed"

    reloaded_store = SingleVideoJobStore(SingleVideoJobDatabase(db_path))
    reloaded_snapshot = await reloaded_store.snapshot(job_id)
    assert reloaded_snapshot.video_path == version.video_path
    assert reloaded_snapshot.versions[0].video_path == version.video_path
    assert reloaded_snapshot.selected_version_id == version.version_id


@pytest.mark.asyncio
async def test_workflow_status_exposes_character_reference_urls(tmp_path):
    from backend.config import OUTPUT_DIR

    store = WorkflowJobStore(WorkflowJobDatabase(tmp_path / "workflow.json"))
    await store.create_job("job-refs")
    await store.update_character_refs("job-refs", [str(OUTPUT_DIR / "job-refs" / "character_refs" / "Mango_ref.png")])

    snapshot = await store.get_status("job-refs")

    assert snapshot.character_ref_urls == ["/static/job-refs/character_refs/Mango_ref.png"]


@pytest.mark.asyncio
async def test_active_job_ids_lists_only_unfinished_jobs(tmp_path):
    store = WorkflowJobStore(WorkflowJobDatabase(tmp_path / "workflow.json"))
    await store.create_job("queued-job")
    await store.create_job("running-job")
    await store.mark_running("running-job")
    await store.create_job("failed-job")
    await store.mark_failed("failed-job", "boom")

    assert sorted(store.active_job_ids()) == ["queued-job", "running-job"]


@pytest.mark.asyncio
async def test_fail_interrupted_jobs_marks_running_jobs_failed_after_restart(tmp_path, monkeypatch):
    from backend import stores

    db_path = tmp_path / "workflow.json"
    before_restart = WorkflowJobStore(WorkflowJobDatabase(db_path))
    await before_restart.create_job("job-1")
    await before_restart.mark_running("job-1")

    after_restart = WorkflowJobStore(WorkflowJobDatabase(db_path))
    empty = SingleVideoJobStore(SingleVideoJobDatabase(tmp_path / "single.json"))
    monkeypatch.setattr(stores, "WORKFLOW_JOB_STORE", after_restart)
    for name in ("SINGLE_VIDEO_STORE", "VIDEO_INSIGHT_STORE", "VIDEO_REPRODUCE_STORE"):
        monkeypatch.setattr(stores, name, empty)

    await stores.fail_interrupted_jobs()

    snapshot = await after_restart.get_status("job-1")
    assert (snapshot.status, snapshot.error) == ("failed", stores.INTERRUPTED_JOB_MESSAGE)
