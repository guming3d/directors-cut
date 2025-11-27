"""Service layer for preparing, running, and remixing jobs."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import (
    DEFAULT_SORA_RESOLUTION,
    HEADER_PATTERN,
    RESOLUTION_PATTERN,
    ROOT,
    SCENE_IMAGE_DIR,
    SORA_CLIENT,
    logger,
)
from .images import prepare_sequence_initial_image, resolve_scene_image_dir, save_payload_as_png
from .models import (
    EncodedImagePayload,
    SceneRender,
    SceneVersionEntry,
    SingleVideoJobConfig,
    SingleVideoVersionEntry,
    StartWorkflowPayload,
    VideoWorkflowRequest,
    get_active_single_version,
)
from .pipeline import summarize_manifest
from .progress import mark_completed, report_progress
from .stores import SINGLE_VIDEO_STORE, WORKFLOW_JOB_STORE
from .utils import escape_for_concat, normalize_script_text, run_subprocess
from .workflow import workflow
from .config import SINGLE_VIDEO_JOBS_DIR, OUTPUT_DIR
from .prompts import ensure_scene_headers, refine_text_with_gpt5
from .config import video_client


def job_root(job_id: str) -> Path:
    return OUTPUT_DIR / job_id


def prepare_request_for_job(job_id: str, payload: StartWorkflowPayload) -> VideoWorkflowRequest:
    job_dir = job_root(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = job_dir / "prompts"
    scenes_dir = job_dir / "scenes"
    tmp_dir = job_dir / "tmp"
    final_video = job_dir / "sora_story_compilation.mp4"

    for path in (prompts_dir, scenes_dir, tmp_dir):
        path.mkdir(parents=True, exist_ok=True)

    script_path = job_dir / "script.txt"
    script_path.write_text(normalize_script_text(payload.script), encoding="utf-8")

    resolution = (payload.resolution or DEFAULT_SORA_RESOLUTION).strip()
    if not RESOLUTION_PATTERN.match(resolution):
        raise ValueError(
            "Resolution must be in WIDTHxHEIGHT format, e.g. 720x1280."
        )
    scene_duration = int(payload.scene_duration)

    use_scene_images = payload.use_scene_images
    if use_scene_images is None:
        use_scene_images = True

    generation_mode = payload.generation_mode or "parallel"
    thread_count = payload.threads or int(os.getenv("SORA_WORKFLOW_THREADS", "3"))
    if generation_mode == "sequence":
        thread_count = 1

    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
    uploads = list(payload.scene_images or [])
    if generation_mode == "sequence" and len(uploads) > 1:
        raise ValueError("Sequence generation mode accepts only one uploaded reference image.")
    if generation_mode == "sequence" and uploads:
        uploads = uploads[:1]
    scene_image_override = payload.scene_image_dir

    scene_image_dir_path = SCENE_IMAGE_DIR
    if use_scene_images:
        try:
            scene_image_dir_path = resolve_scene_image_dir(
                job_dir,
                uploads,
                scene_image_override,
            )
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    sequence_initial_image: Optional[str] = None
    if generation_mode == "sequence" and use_scene_images:
        sequence_initial_image = prepare_sequence_initial_image(
            Path(scene_image_dir_path),
            tmp_dir,
            resolution,
        )
        use_scene_images = False

    request = VideoWorkflowRequest(
        script_path=str(script_path),
        output_dir=str(job_dir),
        prompt_dir=str(prompts_dir),
        scenes_dir=str(scenes_dir),
        tmp_dir=str(tmp_dir),
        final_video_path=str(final_video),
        sora_client_path=str(SORA_CLIENT),
        scene_image_dir=str(scene_image_dir_path),
        use_scene_images=use_scene_images,
        resolution=resolution,
        scene_duration=scene_duration,
        thread_count=thread_count,
        download_variant=download_variant,
        job_id=job_id,
        generation_mode=generation_mode,
        sequence_initial_image=sequence_initial_image,
        refine_with_gpt5=payload.refine_with_gpt5,
    )
    return request


def prepare_single_video_job(
    job_id: str,
    prompt: str,
    resolution: str,
    duration: int,
    download_variant: str,
    reference_image: EncodedImagePayload | None,
) -> SingleVideoJobConfig:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt text is required.")

    job_dir = SINGLE_VIDEO_JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = job_dir / "prompt.txt"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    reference_path: Optional[Path] = None
    if reference_image:
        reference_path = save_payload_as_png(reference_image, job_dir / "reference.png")

    video_path = job_dir / "sora_single.mp4"

    return SingleVideoJobConfig(
        job_id=job_id,
        prompt_path=prompt_path,
        prompt_text=prompt,
        resolution=resolution,
        duration=duration,
        download_variant=download_variant,
        video_path=video_path,
        reference_image_path=reference_path,
        job_dir=job_dir,
    )


async def _ensure_script_headers(job_id: str, request: VideoWorkflowRequest) -> None:
    """Lightweight preflight: confirm script exists and optionally note header state.

    Full normalization and GPT-5 refinement now occur inside DirectorAgent to avoid
    pre-run failures when the model returns late or empty headers.
    """

    script_path = Path(request.script_path)
    if not script_path.is_file():
        raise FileNotFoundError("Script file missing before planning scenes.")

    script_text = script_path.read_text(encoding="utf-8")
    has_headers = bool(HEADER_PATTERN.search(script_text))
    if has_headers:
        return

    note = (
        "Storyboard missing explicit scene headers; DirectorAgent will attempt GPT-5 refinement during planning."
        if request.refine_with_gpt5
        else "Storyboard missing scene headers; DirectorAgent will parse as-is."
    )
    await WORKFLOW_JOB_STORE.add_message(job_id, "planning", note)


async def run_workflow_job(job_id: str, request: VideoWorkflowRequest) -> None:
    try:
        await WORKFLOW_JOB_STORE.mark_running(job_id)
        await _ensure_script_headers(job_id, request)
        await workflow.run(request)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Workflow %s failed: %s", job_id, exc)
        await WORKFLOW_JOB_STORE.mark_failed(job_id, str(exc))


async def run_single_video_job(config: SingleVideoJobConfig) -> None:
    job_id = config.job_id
    try:
        await SINGLE_VIDEO_STORE.mark_running(job_id)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.1, "Render request submitted.")

        env = os.environ.copy()
        env.update(
            {
                "VIDEO_OUTPUT_PATH": str(config.video_path),
                "VIDEO_DOWNLOAD_VARIANT": config.download_variant,
            }
        )

        cmd = [
            sys.executable,
            str(SORA_CLIENT),
            "--prompt-file",
            str(config.prompt_path),
            "--resolution",
            config.resolution,
            "--duration",
            str(config.duration),
        ]
        if config.reference_image_path:
            cmd.extend(["--input-image", str(config.reference_image_path)])

        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.75, "Download completed, finalizing artifacts.")

        client_log_path = config.video_path.with_suffix(".client.log")
        client_log_path.write_text(
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )

        if not config.video_path.is_file():
            raise FileNotFoundError(
                "Sora client did not produce the expected video file. Check Azure credentials and VIDEO_OUTPUT_PATH."
            )

        sora_log_path = config.video_path.with_suffix(".json")
        video_id: Optional[str] = None
        if sora_log_path.is_file():
            try:
                metadata = json.loads(sora_log_path.read_text(encoding="utf-8"))
                video_id = metadata.get("video_id") or metadata.get("job_id")
            except json.JSONDecodeError:
                video_id = None
        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(config.video_path),
            str(sora_log_path) if sora_log_path.is_file() else None,
            str(client_log_path),
            video_id=video_id,
        )
        version = SingleVideoVersionEntry(
            version_id=uuid.uuid4().hex,
            label="Original",
            prompt=config.prompt_text,
            prompt_path=str(config.prompt_path),
            video_path=str(config.video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
            video_id=video_id,
        )
        await SINGLE_VIDEO_STORE.record_version(
            job_id,
            version,
            make_active=True,
            message="Initial render saved.",
            stage="completed",
        )
        await SINGLE_VIDEO_STORE.mark_completed(job_id)
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Single video job %s failed: %s", job_id, exc)
        await SINGLE_VIDEO_STORE.mark_failed(job_id, str(exc))


async def run_single_video_remix(job_id: str, prompt: str) -> None:
    prompt_text = prompt.strip()
    if not prompt_text:
        await SINGLE_VIDEO_STORE.add_message(job_id, "failed", "Remix prompt cannot be empty.")
        return
    try:
        job_state = await SINGLE_VIDEO_STORE.get_job_state(job_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Remix requested for unknown single job %s: %s", job_id, exc)
        return

    base_version = get_active_single_version(job_state)
    if not base_version or not base_version.video_id:
        await SINGLE_VIDEO_STORE.add_message(
            job_id,
            "failed",
            "This job is missing Azure video metadata; render a clip before requesting a remix.",
        )
        return

    try:
        await SINGLE_VIDEO_STORE.mark_running(job_id)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.1, "Submitting remix to Azure Sora2.")
        client = video_client()
        remix_job = await client.videos.remix(video_id=base_version.video_id, prompt=prompt_text)
        remix_job_id = getattr(remix_job, "id", None)
        if not remix_job_id:
            raise RuntimeError("Azure remix API did not return a job id.")

        status = getattr(remix_job, "status", None)
        while status not in {"completed", "failed", "canceled"}:
            await asyncio.sleep(4)
            poll = await client.videos.retrieve(remix_job_id)
            status = getattr(poll, "status", None)
        if status != "completed":
            raise RuntimeError(f"Remix job ended with status {status}.")

        job_dir = SINGLE_VIDEO_JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        video_path = job_dir / f"sora_single_remix_{timestamp}.mp4"
        prompt_path = job_dir / f"sora_single_remix_{timestamp}.txt"
        log_path = video_path.with_suffix(".json")
        client_log_path = video_path.with_suffix(".remix.log")
        prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

        download_variant = job_state.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
        content = await client.videos.download_content(remix_job_id, variant=download_variant)
        content.write_to_file(str(video_path))
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.75, "Remix downloaded, finalizing artifacts.")

        log_data = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "prompt": prompt_text,
            "video_id": remix_job_id,
            "base_video_id": base_version.video_id,
            "output_file": str(video_path),
            "source": "remix",
        }
        log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
        client_log_path.write_text(
            f"Remix job {remix_job_id} completed for single-video job {job_id}.\nBase video: {base_version.video_id}\n",
            encoding="utf-8",
        )

        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(video_path),
            str(log_path),
            str(client_log_path),
            video_id=remix_job_id,
        )
        version = SingleVideoVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Remix {len(job_state.versions) + 1}",
            prompt=prompt_text,
            prompt_path=str(prompt_path),
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            video_id=remix_job_id,
            source="remix",
        )
        await SINGLE_VIDEO_STORE.record_version(
            job_id,
            version,
            make_active=True,
            message="Remix completed.",
            stage="completed",
        )
        await SINGLE_VIDEO_STORE.mark_completed(job_id)
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Remix job for single video %s failed: %s", job_id, exc)
        await SINGLE_VIDEO_STORE.mark_failed(job_id, f"Remix failed: {exc}")


async def run_scene_remix_job(job_id: str, scene_order: int, prompt: str) -> None:
    from .models import SceneStatus

    prompt_text = prompt.strip()
    if not prompt_text:
        await WORKFLOW_JOB_STORE.add_message(job_id, "failed", "Remix prompt cannot be empty.")
        return
    try:
        scene = await WORKFLOW_JOB_STORE.get_scene(job_id, scene_order)
        base_version = scene.get_version(scene.selected_version_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Remix request dropped for missing scene %s/%s: %s", job_id, scene_order, exc)
        await WORKFLOW_JOB_STORE.add_message(
            job_id, "failed", f"Unable to remix scene {scene_order}; render not found."
        )
        return

    if not base_version.video_id:
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            f"Scene {scene_order} is missing Azure video metadata; regenerate before remixing.",
        )
        return

    job_dir = job_root(job_id)
    scenes_dir = Path(job_dir) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = Path(scene.prompt_path).parent if scene.prompt_path else (Path(job_dir) / "prompts")
    prompt_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    video_path = scenes_dir / f"scene_{scene_order:02d}_remix_{timestamp}.mp4"
    log_path = video_path.with_suffix(".json")
    client_log_path = video_path.with_suffix(".remix.log")
    prompt_path = prompt_dir / f"scene_{scene_order:02d}_remix_{timestamp}.txt"

    try:
        client = video_client()
        download_variant = await WORKFLOW_JOB_STORE.get_download_variant(job_id)
        remix_job = await client.videos.remix(video_id=base_version.video_id, prompt=prompt_text)
        remix_job_id = getattr(remix_job, "id", None)
        if not remix_job_id:
            raise RuntimeError("Azure remix API did not return a job id.")

        status = getattr(remix_job, "status", None)
        while status not in {"completed", "failed", "canceled"}:
            await asyncio.sleep(4)
            poll = await client.videos.retrieve(remix_job_id)
            status = getattr(poll, "status", None)
        if status != "completed":
            raise RuntimeError(f"Remix job ended with status {status}.")

        content = await client.videos.download_content(remix_job_id, variant=download_variant)
        content.write_to_file(str(video_path))

        prompt_path.write_text(prompt_text + "\n", encoding="utf-8")
        log_data = {
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
            "prompt": prompt_text,
            "video_id": remix_job_id,
            "base_video_id": base_version.video_id,
            "output_file": str(video_path),
            "source": "remix",
        }
        log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
        client_log_path.write_text(
            f"Remix job {remix_job_id} completed for scene {scene_order}.\nBase video: {base_version.video_id}\n",
            encoding="utf-8",
        )

        version = SceneVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Remix {len(scene.versions) + 1}",
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            video_id=remix_job_id,
            prompt=prompt_text,
            prompt_path=str(prompt_path),
            source="remix",
        )
        await WORKFLOW_JOB_STORE.add_scene_version(
            job_id,
            scene_order,
            version,
            make_active=False,
            message=f"Remix ready for {scene.label}.",
        )
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Remix job for %s scene %s failed: %s", job_id, scene_order, exc)
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            f"Remix for scene {scene_order} failed: {exc}",
        )


async def run_scene_regeneration_job(job_id: str, scene_order: int, prompt_override: str | None = None) -> None:
    try:
        scene = await WORKFLOW_JOB_STORE.get_scene(job_id, scene_order)
        resolution = await WORKFLOW_JOB_STORE.get_resolution(job_id)
        download_variant = await WORKFLOW_JOB_STORE.get_download_variant(job_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Regeneration requested for unknown scene %s/%s: %s", job_id, scene_order, exc)
        return

    job_dir = job_root(job_id)
    scenes_dir = Path(job_dir) / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    video_path = scenes_dir / f"scene_{scene_order:02d}_regen_{timestamp}.mp4"
    prompt_dir = Path(scene.prompt_path).parent if scene.prompt_path else (Path(job_dir) / "prompts")
    prompt_dir.mkdir(parents=True, exist_ok=True)
    base_prompt_path = Path(scene.prompt_path) if scene.prompt_path else None
    prompt_override_text = (prompt_override or "").strip()
    if prompt_override_text:
        prompt_path = prompt_dir / f"scene_{scene_order:02d}_regen_{timestamp}.txt"
        prompt_path.write_text(prompt_override_text + "\n", encoding="utf-8")
    else:
        prompt_path = base_prompt_path

    if not prompt_path or not prompt_path.is_file():
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            f"Cannot regenerate {scene.label}; prompt file missing.",
        )
        return

    env = os.environ.copy()
    env.update(
        {
            "VIDEO_OUTPUT_PATH": str(video_path),
            "VIDEO_DOWNLOAD_VARIANT": download_variant,
        }
    )

    cmd = [
        sys.executable,
        str(SORA_CLIENT),
        "--prompt-file",
        str(prompt_path),
        "--resolution",
        resolution,
        "--duration",
        str(scene.duration_seconds),
    ]

    try:
        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
        client_log_path = video_path.with_suffix(".regen.log")
        client_log_path.write_text(
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )

        if not video_path.is_file():
            raise FileNotFoundError(
                "Sora client reported success but regenerated video file is missing."
            )

        log_path = video_path.with_suffix(".json")
        video_id = None
        if log_path.is_file():
            try:
                metadata = json.loads(log_path.read_text(encoding="utf-8"))
                video_id = metadata.get("video_id") or metadata.get("job_id")
            except json.JSONDecodeError:
                video_id = None

        if prompt_override_text:
            prompt_text = prompt_override_text
        else:
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8").strip()
            except OSError:
                prompt_text = ""

        version = SceneVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Regenerate {len(scene.versions) + 1}",
            video_path=str(video_path),
            log_path=str(log_path) if log_path.is_file() else None,
            client_log_path=str(client_log_path),
            video_id=video_id,
            prompt=prompt_text,
            prompt_path=str(prompt_path),
            source="regenerate",
        )
        await WORKFLOW_JOB_STORE.add_scene_version(
            job_id,
            scene_order,
            version,
            make_active=True,
            message=(
                f"Regenerated {scene.label} with updated prompt."
                if prompt_override_text
                else f"Regenerated {scene.label}."
            ),
        )
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Regeneration job for %s scene %s failed: %s", job_id, scene_order, exc)
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            f"Regeneration for scene {scene_order} failed: {exc}",
        )


async def run_recompose_job(job_id: str) -> None:
    try:
        status = await WORKFLOW_JOB_STORE.get_status(job_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Recompose requested for unknown job %s: %s", job_id, exc)
        return

    job_dir = job_root(job_id)
    tmp_dir = job_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    final_video = job_dir / f"sora_story_compilation_{timestamp}.mp4"
    concat_list = tmp_dir / f"concat_recompose_{timestamp}.txt"

    scenes_sorted = sorted(status.scenes, key=lambda item: item.order)
    renders: list[SceneRender] = []
    for scene in scenes_sorted:
        version = next(
            (v for v in scene.versions if v.version_id == scene.selected_version_id),
            None,
        )
        if version is None and scene.versions:
            version = scene.versions[-1]
        if version is None or not version.video_path:
            await WORKFLOW_JOB_STORE.add_message(
                job_id,
                "failed",
                f"Cannot recompose; scene {scene.order} has no completed versions.",
            )
            return
        renders.append(
            SceneRender(
                order=scene.order,
                label=scene.label,
                duration_seconds=scene.duration_seconds,
                prompt_path=version.prompt_path or scene.prompt_path,
                video_path=version.video_path,
                log_path=version.log_path,
                client_log_path=version.client_log_path,
                prompt_text=version.prompt,
                version_id=version.version_id,
                version_label=version.label,
                video_id=version.video_id,
                version_source=version.source,
            )
        )

    with open(concat_list, "w", encoding="utf-8") as fh:
        for render in renders:
            fh.write(escape_for_concat(Path(render.video_path)) + "\n")

    try:
        await run_subprocess(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(final_video),
            ]
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Recompose failed for job %s: %s", job_id, exc)
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            f"Recompose failed: {exc}",
        )
        return

    total_seconds = sum(render.duration_seconds for render in renders)
    manifest = summarize_manifest(renders, final_video, total_seconds, status.resolution)
    manifest_path = final_video.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    await WORKFLOW_JOB_STORE.mark_completed(job_id, str(final_video), manifest)
    await WORKFLOW_JOB_STORE.add_message(job_id, "completed", "Regenerated final video with selected scene versions.")


async def refine_script_for_payload(payload: StartWorkflowPayload) -> None:
    if not payload.refine_with_gpt5:
        return
    refined_script = await refine_text_with_gpt5(payload.script, mode="workflow")
    if refined_script.strip():
        payload.script = refined_script


__all__ = [
    "job_root",
    "prepare_request_for_job",
    "prepare_single_video_job",
    "refine_script_for_payload",
    "run_recompose_job",
     "run_scene_regeneration_job",
    "run_scene_remix_job",
    "run_single_video_job",
    "run_single_video_remix",
    "run_workflow_job",
]
