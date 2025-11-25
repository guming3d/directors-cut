import json
from pathlib import Path

import pytest

from sora_workflow import DEFAULT_SORA_RESOLUTION
from sora_workflow.agents import Sora2Agent
from sora_workflow.models import SceneRender, VideoWorkflowRequest
from sora_workflow.workflow import create_sora_video_workflow


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

    monkeypatch.setattr("sora_workflow.agents.refine_scene_description", fake_refine)
    monkeypatch.setattr(Sora2Agent, "_invoke_sora", fake_invoke)
    monkeypatch.setattr("sora_workflow.agents.run_subprocess", fake_run_subprocess)

    result = await workflow.run(request)
    assert result is not None

    final_video = Path(request.final_video_path)
    manifest_path = final_video.with_suffix(".json")
    assert final_video.is_file()
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["scenes"][0]["label"].startswith("Scene 1")
