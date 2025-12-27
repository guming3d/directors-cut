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
        output_path.write_bytes(b"image-bytes")
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
