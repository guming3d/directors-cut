import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import DEFAULT_SORA_RESOLUTION
from backend.agents import Sora2Agent
from backend.models import ScenePlan, SceneRender, VideoWorkflowRequest
from backend.services import _render_scene_regen_with_kling
from backend.workflow import create_sora_video_workflow


@pytest.mark.asyncio
async def test_workflow_runs_with_stubbed_agents(tmp_path, monkeypatch):
    script_path = tmp_path / "script.txt"
    script_path.write_text("**Scene 1 (Intro)**\nA short intro line.\n", encoding="utf-8")

    output_dir = tmp_path / "job"
    request = VideoWorkflowRequest(
        script_path=str(script_path),
        output_dir=str(output_dir),
        prompt_dir=str(output_dir / "prompts"),
        scenes_dir=str(output_dir / "scenes"),
        tmp_dir=str(output_dir / "tmp"),
        final_video_path=str(output_dir / "final.mp4"),
        sora_client_path=str(tmp_path / "dummy_client.py"),
        use_scene_images=False,
        resolution=DEFAULT_SORA_RESOLUTION,
        thread_count=1,
    )

    workflow = create_sora_video_workflow()

    async def fake_refine(title: str, description: str, duration: int) -> str:
        return description

    async def fake_invoke(
        self,
        plan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images,
        reference_override=None,
        character_refs=None,
    ) -> SceneRender:
        scenes_dir.mkdir(parents=True, exist_ok=True)
        video_path = scenes_dir / f"scene_{plan.order:02d}.mp4"
        video_path.write_text("video-bytes", encoding="utf-8")
        log_path = video_path.with_suffix(".json")
        log_path.write_text(json.dumps({"video_id": f"vid-{plan.order}"}), encoding="utf-8")
        client_log_path = video_path.with_suffix(".client.log")
        client_log_path.write_text("log", encoding="utf-8")
        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=f"vid-{plan.order}",
        )

    async def fake_run_subprocess(cmd, cwd=None, env=None):
        target = Path(cmd[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("final-video", encoding="utf-8")
        return "ok", ""

    monkeypatch.setattr("backend.agents.refine_scene_description", fake_refine)
    monkeypatch.setattr(Sora2Agent, "_invoke_sora", fake_invoke)
    monkeypatch.setattr("backend.agents.run_subprocess", fake_run_subprocess)

    result = await workflow.run(request)
    assert result is not None

    final_video = Path(request.final_video_path)
    manifest_path = final_video.with_suffix(".json")
    assert final_video.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["scenes"][0]["label"].startswith("Scene 1")


@pytest.mark.asyncio
async def test_skips_refinement_when_flag_disabled(tmp_path, monkeypatch):
    script_path = tmp_path / "script.txt"
    script_path.write_text("**Scene 1 (Intro)**\nA short intro line.\n", encoding="utf-8")

    output_dir = tmp_path / "job"
    request = VideoWorkflowRequest(
        script_path=str(script_path),
        output_dir=str(output_dir),
        prompt_dir=str(output_dir / "prompts"),
        scenes_dir=str(output_dir / "scenes"),
        tmp_dir=str(output_dir / "tmp"),
        final_video_path=str(output_dir / "final.mp4"),
        sora_client_path=str(tmp_path / "dummy_client.py"),
        use_scene_images=False,
        resolution=DEFAULT_SORA_RESOLUTION,
        thread_count=1,
        refine_with_gpt5=False,
    )

    workflow = create_sora_video_workflow()

    async def fail_refine(*args, **kwargs):
        raise AssertionError("refine_scene_description should not run when refine_with_gpt5 is False")

    async def fake_invoke(
        self,
        plan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images,
        reference_override=None,
        character_refs=None,
    ) -> SceneRender:
        scenes_dir.mkdir(parents=True, exist_ok=True)
        video_path = scenes_dir / f"scene_{plan.order:02d}.mp4"
        video_path.write_text("video-bytes", encoding="utf-8")
        log_path = video_path.with_suffix(".json")
        log_path.write_text(json.dumps({"video_id": f"vid-{plan.order}"}), encoding="utf-8")
        client_log_path = video_path.with_suffix(".client.log")
        client_log_path.write_text("log", encoding="utf-8")
        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=f"vid-{plan.order}",
        )

    async def fake_run_subprocess(cmd, cwd=None, env=None):
        target = Path(cmd[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("final-video", encoding="utf-8")
        return "ok", ""

    monkeypatch.setattr("backend.agents.refine_scene_description", fail_refine)
    monkeypatch.setattr(Sora2Agent, "_invoke_sora", fake_invoke)
    monkeypatch.setattr("backend.agents.run_subprocess", fake_run_subprocess)

    result = await workflow.run(request)
    assert result is not None

    final_video = Path(request.final_video_path)
    manifest_path = final_video.with_suffix(".json")
    assert final_video.is_file()
    assert manifest_path.is_file()


@pytest.mark.asyncio
async def test_kling_regen_uses_unique_external_task_ids(tmp_path, monkeypatch):
    job_id = "job123"
    scene = SimpleNamespace(
        order=14,
        duration_seconds=8,
        first_frame_path=None,
        last_frame_path=None,
        label="Scene 14",
        versions=[],
        prompt_path=None,
    )
    captured_ids: list[str] = []

    async def fake_render_kling_clip(**kwargs):
        external_task_id = kwargs.get("external_task_id")
        captured_ids.append(external_task_id)
        output_path: Path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("video-bytes", encoding="utf-8")
        return SimpleNamespace(
            task_id=f"task-{len(captured_ids)}",
            endpoint="text2video",
            request_payload={"external_task_id": external_task_id},
            final_response={"data": {"task_status": "succeed"}},
            video_url="https://example.com/video.mp4",
        )

    monkeypatch.setattr("backend.services.render_kling_clip", fake_render_kling_clip)

    output_path1 = tmp_path / "scene_regen_01.mp4"
    output_path2 = tmp_path / "scene_regen_02.mp4"

    await _render_scene_regen_with_kling(job_id, scene, "Prompt 1", "720x1280", output_path1)
    await _render_scene_regen_with_kling(job_id, scene, "Prompt 2", "720x1280", output_path2)

    assert len(captured_ids) == 2
    assert captured_ids[0] != captured_ids[1]


@pytest.mark.asyncio
async def test_invoke_scene_with_kling_backend(tmp_path, monkeypatch):
    scenes_dir = tmp_path / "job" / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Prompt body", encoding="utf-8")
    plan = ScenePlan(
        order=1,
        label="Scene 1",
        duration_seconds=5,
        description="Desc",
        prompt_path=str(prompt_path),
        prompt_text="Prompt body",
    )
    request = VideoWorkflowRequest(
        script_path=str(prompt_path),
        output_dir=str(tmp_path / "job"),
        prompt_dir=str(tmp_path / "job" / "prompts"),
        scenes_dir=str(scenes_dir),
        tmp_dir=str(tmp_path / "job" / "tmp"),
        final_video_path=str(tmp_path / "job" / "final.mp4"),
        scene_image_dir=str(tmp_path / "job" / "scene_images"),
        use_scene_images=False,
        resolution=DEFAULT_SORA_RESOLUTION,
        thread_count=1,
        video_provider="kling",
        scene_duration=5,
    )
    Path(request.scene_image_dir).mkdir(parents=True, exist_ok=True)
    Path(request.tmp_dir).mkdir(parents=True, exist_ok=True)

    async def fake_render_kling_clip(**kwargs):
        output_path = kwargs["output_path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("kling-bytes", encoding="utf-8")
        return SimpleNamespace(
            task_id="kling-task",
            endpoint="text2video",
            request_payload={"prompt": kwargs["prompt_text"]},
            final_response={"data": {"task_status": "succeed"}},
            video_url="https://example.com/video.mp4",
        )

    monkeypatch.setattr("backend.agents.render_kling_clip", fake_render_kling_clip)

    agent = Sora2Agent()
    render = await agent._invoke_scene(plan, request, Path(request.scenes_dir), scene_images=[])

    assert render.video_id == "kling-task"
    assert render.log_path is not None
    log_data = json.loads(Path(render.log_path).read_text())
    assert log_data["provider"] == "kling"
