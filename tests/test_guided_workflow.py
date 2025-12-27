import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from backend.models import AnimatedFilmPayload, CharacterUpload, EncodedImagePayload
from backend.services import load_guided_artifacts, run_animated_workflow_job
from backend.workflow_store import WorkflowJobStore


def _encode_tiny_png() -> str:
    image = Image.new("RGB", (2, 2), color=(255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


@pytest.mark.asyncio
async def test_guided_workflow_runs_with_stubs(tmp_path, monkeypatch):
    payload = AnimatedFilmPayload(
        characters=[
            CharacterUpload(
                name="Hero",
                image=EncodedImagePayload(filename="hero.png", data_url=_encode_tiny_png()),
            )
        ],
        scene_description="A hero walks through a neon-lit alley.",
        workflow_type="story_short",
        film_length="short",
        aspect_ratio="9:16",
        language="en",
        emotions=["tense"],
        scene_duration=4,
        video_provider="sora2",
    )

    store = WorkflowJobStore()
    monkeypatch.setattr("backend.services.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.agents.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.progress.WORKFLOW_JOB_STORE", store)

    def fake_job_root(job_id: str) -> Path:
        return tmp_path / job_id

    monkeypatch.setattr("backend.services.job_root", fake_job_root)

    async def fake_generate_nanobanana_image(
        prompt, image_paths, output_path, aspect_ratio=None, image_size=None, resolution_hint=None, dry_run=None
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (8, 8), color=(0, 255, 0))
        image.save(output_path, format="PNG")
        return output_path

    monkeypatch.setattr("backend.services.generate_nanobanana_image", fake_generate_nanobanana_image)

    async def fake_run_workflow_job(job_id, request, skip_mark_running=False):
        final = Path(request.final_video_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("video", encoding="utf-8")
        await store.mark_completed(job_id, str(final), {"scenes": []})

    monkeypatch.setattr("backend.services.run_workflow_job", fake_run_workflow_job)

    job_id = "guided-job"
    await store.create_job(job_id)
    await run_animated_workflow_job(job_id, payload)

    status = await store.get_status(job_id)
    assert status.status == "completed"

    artifacts = await load_guided_artifacts(job_id)
    assert artifacts.script_path and Path(artifacts.script_path).is_file()
    assert artifacts.first_frame_path and Path(artifacts.first_frame_path).is_file()
    assert artifacts.scene_image_dir and (Path(artifacts.scene_image_dir) / "scene1.png").is_file()
    for concept_path in artifacts.concept_art_paths:
        assert Path(concept_path).is_file()


@pytest.mark.asyncio
async def test_guided_workflow_uses_gpt5_templates_when_available(tmp_path, monkeypatch):
    payload = AnimatedFilmPayload(
        characters=[
            CharacterUpload(
                name="Hero",
                image=EncodedImagePayload(filename="hero.png", data_url=_encode_tiny_png()),
            )
        ],
        scene_description="A hero finds a mysterious glowing key in a rainy alley.",
        workflow_type="story_short",
        film_length="short",
        aspect_ratio="9:16",
        language="zh",
        emotions=["warm"],
        scene_duration=4,
        video_provider="sora2",
    )

    store = WorkflowJobStore()
    monkeypatch.setattr("backend.services.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.agents.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.progress.WORKFLOW_JOB_STORE", store)

    def fake_job_root(job_id: str) -> Path:
        return tmp_path / job_id

    monkeypatch.setattr("backend.services.job_root", fake_job_root)

    monkeypatch.setattr("backend.services.gpt5_client_ready", lambda: True)

    calls: list[tuple[str, str]] = []

    async def fake_call_gpt5_with_templates(system_key, user_key, *, max_output_tokens, **template_values):
        calls.append((system_key, user_key))
        if system_key == "guided_art_director_system":
            return "ART DIRECTOR OUTPUT"
        if system_key == "guided_scriptwriter_system":
            return (
                "剧本梗概：一个温暖的小故事。\n"
                "角色列表：英雄。\n\n"
                "**Scene 1 (Discovery)**\n"
                "Key Frame: A rainy neon alley, the hero holds a softly glowing key near a puddle reflection.\n"
                "Visual Description: Medium shot, soft rim light, rain particles, cinematic depth of field.\n"
                "Dialogue: 【英雄】：这把钥匙……是给谁的？\n\n"
                "**Scene 2 (Resolve)**\n"
                "Key Frame: The hero smiles under an awning, key glowing brighter, warm light cutting through rain.\n"
                "Visual Description: Close-up, warm highlights, gentle bokeh, hopeful tone.\n"
                "Dialogue: 【英雄】：我知道该去哪里了。\n"
            )
        return ""

    monkeypatch.setattr("backend.services.call_gpt5_with_templates", fake_call_gpt5_with_templates)

    async def fake_generate_nanobanana_image(
        prompt, image_paths, output_path, aspect_ratio=None, image_size=None, resolution_hint=None, dry_run=None
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (8, 8), color=(0, 0, 255))
        image.save(output_path, format="PNG")
        return output_path

    monkeypatch.setattr("backend.services.generate_nanobanana_image", fake_generate_nanobanana_image)

    async def fake_run_workflow_job(job_id, request, skip_mark_running=False):
        final = Path(request.final_video_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("video", encoding="utf-8")
        await store.mark_completed(job_id, str(final), {"scenes": []})

    monkeypatch.setattr("backend.services.run_workflow_job", fake_run_workflow_job)

    job_id = "guided-gpt5-job"
    await store.create_job(job_id)
    await run_animated_workflow_job(job_id, payload)

    artifacts = await load_guided_artifacts(job_id)
    storyboard_text = Path(artifacts.storyboard_path).read_text(encoding="utf-8")
    assert "ART DIRECTOR OUTPUT" in storyboard_text

    script_text = Path(artifacts.script_path).read_text(encoding="utf-8")
    assert "**Scene 1 (Discovery)**" in script_text
    assert "**Scene 2 (Resolve)**" in script_text

    assert (Path(artifacts.scene_image_dir) / "scene1.png").is_file()
    assert (Path(artifacts.scene_image_dir) / "scene2.png").is_file()

    assert any(system_key == "guided_art_director_system" for system_key, _ in calls)
    assert any(system_key == "guided_scriptwriter_system" for system_key, _ in calls)


@pytest.mark.asyncio
async def test_guided_workflow_passes_all_character_images_to_scene_frames(tmp_path, monkeypatch):
    payload = AnimatedFilmPayload(
        characters=[
            CharacterUpload(
                name="绿绒欢欢",
                image=EncodedImagePayload(filename="a.png", data_url=_encode_tiny_png()),
            ),
            CharacterUpload(
                name="星图",
                image=EncodedImagePayload(filename="b.png", data_url=_encode_tiny_png()),
            ),
        ],
        scene_description="A cozy kitchen scene with friends cooking together.",
        workflow_type="story_short",
        film_length="short",
        aspect_ratio="9:16",
        language="zh",
        emotions=["warm"],
        scene_duration=4,
        video_provider="sora2",
    )

    store = WorkflowJobStore()
    monkeypatch.setattr("backend.services.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.agents.WORKFLOW_JOB_STORE", store)
    monkeypatch.setattr("backend.progress.WORKFLOW_JOB_STORE", store)

    def fake_job_root(job_id: str) -> Path:
        return tmp_path / job_id

    monkeypatch.setattr("backend.services.job_root", fake_job_root)

    scene_frame_image_paths: list[list[str]] = []

    async def fake_generate_nanobanana_image(
        prompt, image_paths, output_path, aspect_ratio=None, image_size=None, resolution_hint=None, dry_run=None
    ):
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (8, 8), color=(128, 128, 128))
        image.save(output_path, format="PNG")
        if "guided_scene_images" in str(output_path):
            scene_frame_image_paths.append([str(Path(p)) for p in (image_paths or [])])
        return output_path

    monkeypatch.setattr("backend.services.generate_nanobanana_image", fake_generate_nanobanana_image)

    async def fake_run_workflow_job(job_id, request, skip_mark_running=False):
        final = Path(request.final_video_path)
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text("video", encoding="utf-8")
        await store.mark_completed(job_id, str(final), {"scenes": []})

    monkeypatch.setattr("backend.services.run_workflow_job", fake_run_workflow_job)

    job_id = "guided-multi-character"
    await store.create_job(job_id)
    await run_animated_workflow_job(job_id, payload)

    original_dir = tmp_path / job_id / "characters" / "original"
    originals = sorted(original_dir.glob("*.png"))
    assert len(originals) == 2
    originals_set = {str(path) for path in originals}

    assert scene_frame_image_paths, "Expected at least one NanoBanana scene-frame call."
    assert any(originals_set.issubset(set(call)) for call in scene_frame_image_paths)
