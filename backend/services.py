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
from typing import Optional, Sequence

from .config import (
    DEFAULT_SORA_RESOLUTION,
    HEADER_PATTERN,
    RESOLUTION_PATTERN,
    ROOT,
    SCENE_IMAGE_DIR,
    SORA_CLIENT,
    allowed_durations_for_provider,
    logger,
    normalize_video_provider,
)
from .images import (
    prepare_sequence_initial_image,
    resolve_scene_image_dir,
    resize_scene_image,
    save_payload_as_png,
)
from .kling import render_kling_clip
from .jimeng import render_jimeng_clip
from .models import (
    AnimatedFilmPayload,
    CharacterUpload,
    EncodedImagePayload,
    GuidedWorkflowArtifacts,
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
from .nanobanana import aspect_ratio_from_resolution, generate_nanobanana_image
from .utils import escape_for_concat, normalize_script_text, run_subprocess, slugify, resolution_to_size
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
    first_frame_dir = job_dir / "first_frames"

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
    video_provider = normalize_video_provider(payload.video_provider)
    allowed_durations = allowed_durations_for_provider(video_provider)
    if scene_duration not in allowed_durations:
        allowed_str = ", ".join(str(value) for value in allowed_durations)
        raise ValueError(
            f"{video_provider.upper()} supports scene durations of {allowed_str} seconds."
        )

    use_scene_images = payload.use_scene_images
    if use_scene_images is None:
        use_scene_images = True
    generate_ai_first_frame = payload.generate_ai_first_frame
    if generate_ai_first_frame:
        use_scene_images = False

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
        generate_ai_first_frame=generate_ai_first_frame,
        first_frame_dir=str(first_frame_dir),
        video_provider=video_provider,
    )
    return request


def prepare_single_video_job(
    job_id: str,
    prompt: str,
    resolution: str,
    duration: int,
    download_variant: str,
    reference_image: EncodedImagePayload | None,
    reference_images: Optional[list[EncodedImagePayload]] = None,
    video_provider: str | None = None,
) -> SingleVideoJobConfig:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Prompt text is required.")
    provider = normalize_video_provider(video_provider)
    allowed_durations = allowed_durations_for_provider(provider)
    if duration not in allowed_durations:
        allowed_str = ", ".join(str(value) for value in allowed_durations)
        raise ValueError(
            f"{provider.upper()} supports single clip durations of {allowed_str} seconds."
        )

    job_dir = SINGLE_VIDEO_JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = job_dir / "prompt.txt"
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    reference_paths: list[Path] = []
    uploads: list[EncodedImagePayload] = []
    if reference_image:
        uploads.append(reference_image)
    if reference_images:
        uploads.extend(reference_images)

    reference_path: Optional[Path] = None
    for idx, upload in enumerate(uploads):
        suffix = "reference.png" if idx == 0 else f"reference_extra_{idx}.png"
        saved = save_payload_as_png(upload, job_dir / suffix)
        reference_paths.append(saved)
        if idx == 0:
            reference_path = saved

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
        reference_image_paths=reference_paths,
        video_provider=provider,
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


async def run_workflow_job(
    job_id: str, request: VideoWorkflowRequest, *, skip_mark_running: bool = False
) -> None:
    try:
        if not skip_mark_running:
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

        if config.video_provider == "kling":
            log_path, client_log_path, video_id = await _render_single_with_kling(config)
        elif config.video_provider == "jimeng":
            log_path, client_log_path, video_id = await _render_single_with_jimeng(config)
        else:
            log_path, client_log_path, video_id = await _render_single_with_sora(config)
        await SINGLE_VIDEO_STORE.update_progress(job_id, 0.75, "Download completed, finalizing artifacts.")

        await SINGLE_VIDEO_STORE.set_artifacts(
            job_id,
            str(config.video_path),
            str(log_path) if log_path else None,
            str(client_log_path),
            video_id=video_id,
        )
        version = SingleVideoVersionEntry(
            version_id=uuid.uuid4().hex,
            label="Original",
            prompt=config.prompt_text,
            prompt_path=str(config.prompt_path),
            video_path=str(config.video_path),
            log_path=str(log_path) if log_path else None,
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


async def _render_single_with_sora(
    config: SingleVideoJobConfig,
) -> tuple[Optional[Path], Path, Optional[str]]:
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
    return sora_log_path if sora_log_path.is_file() else None, client_log_path, video_id


async def _render_single_with_kling(
    config: SingleVideoJobConfig,
) -> tuple[Path, Path, Optional[str]]:
    reference_paths = [str(path) for path in config.reference_image_paths]
    if not reference_paths and config.reference_image_path:
        reference_paths.append(str(config.reference_image_path))

    primary_image = reference_paths[0] if reference_paths else None
    tail_image: Optional[str] = None
    extra_images: list[str] = []
    if len(reference_paths) == 2:
        # Treat the second image as the tail frame when exactly two references exist.
        tail_image = reference_paths[1]
    elif len(reference_paths) > 1:
        extra_images = reference_paths[1:]

    result = await render_kling_clip(
        prompt_text=config.prompt_text,
        duration_seconds=config.duration,
        resolution=config.resolution,
        output_path=config.video_path,
        primary_image_path=primary_image,
        tail_image_path=tail_image,
        extra_image_paths=extra_images,
        external_task_id=f"{config.job_id}-single",
    )

    log_path = config.video_path.with_suffix(".json")
    client_log_path = config.video_path.with_suffix(".client.log")
    log_data = {
        "provider": "kling",
        "task_id": result.task_id,
        "endpoint": result.endpoint,
        "request": result.request_payload,
        "response": result.final_response,
        "video_url": result.video_url,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    client_log_path.write_text(
        f"Kling task {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path, result.task_id


async def _render_single_with_jimeng(
    config: SingleVideoJobConfig,
) -> tuple[Path, Path, Optional[str]]:
    reference_path = (
        str(config.reference_image_path)
        if config.reference_image_path
        else (str(config.reference_image_paths[0]) if config.reference_image_paths else None)
    )
    result = await render_jimeng_clip(
        prompt_text=config.prompt_text,
        duration_seconds=config.duration,
        resolution=config.resolution,
        output_path=config.video_path,
        primary_image_path=reference_path,
    )

    log_path = config.video_path.with_suffix(".json")
    client_log_path = config.video_path.with_suffix(".client.log")
    sanitized_request = dict(result.request_payload)
    if "binary_data_base64" in sanitized_request:
        sanitized_request["binary_data_base64"] = ["<omitted>"]
    log_data = {
        "provider": "jimeng",
        "task_id": result.task_id,
        "endpoint": result.endpoint,
        "request": sanitized_request,
        "response": result.final_response,
        "video_url": result.video_url,
        "status": result.status,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    client_log_path.write_text(
        f"Jimeng task {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path, result.task_id


async def _render_scene_regen_with_kling(
    job_id: str,
    scene,
    prompt_text: str,
    resolution: str,
    output_path: Path,
) -> tuple[Path, Path, str]:
    regen_token = uuid.uuid4().hex[:8]
    result = await render_kling_clip(
        prompt_text=prompt_text,
        duration_seconds=scene.duration_seconds,
        resolution=resolution,
        output_path=output_path,
        primary_image_path=scene.first_frame_path,
        tail_image_path=scene.last_frame_path,
        extra_image_paths=[],
        external_task_id=f"{job_id}-scene{scene.order:02d}-regen-{regen_token}",
    )
    log_path = output_path.with_suffix(".json")
    client_log_path = output_path.with_suffix(".regen.log")
    log_data = {
        "provider": "kling",
        "task_id": result.task_id,
        "endpoint": result.endpoint,
        "request": result.request_payload,
        "response": result.final_response,
        "video_url": result.video_url,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    client_log_path.write_text(
        f"Kling regeneration {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path, result.task_id


async def _render_scene_regen_with_jimeng(
    job_id: str,
    scene,
    prompt_text: str,
    resolution: str,
    output_path: Path,
) -> tuple[Path, Path, str]:
    reference_path = scene.first_frame_path or scene.last_frame_path
    result = await render_jimeng_clip(
        prompt_text=prompt_text,
        duration_seconds=scene.duration_seconds,
        resolution=resolution,
        output_path=output_path,
        primary_image_path=reference_path,
    )
    log_path = output_path.with_suffix(".json")
    client_log_path = output_path.with_suffix(".regen.log")
    sanitized_request = dict(result.request_payload)
    if "binary_data_base64" in sanitized_request:
        sanitized_request["binary_data_base64"] = ["<omitted>"]
    log_data = {
        "provider": "jimeng",
        "task_id": result.task_id,
        "endpoint": result.endpoint,
        "request": sanitized_request,
        "response": result.final_response,
        "video_url": result.video_url,
        "status": result.status,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    client_log_path.write_text(
        f"Jimeng regeneration {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
        encoding="utf-8",
    )
    return log_path, client_log_path, result.task_id


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

    if job_state.video_provider in {"kling", "jimeng"}:
        await SINGLE_VIDEO_STORE.add_message(
            job_id,
            "failed",
            "Selected backend does not support remix operations. Please generate a new version instead.",
        )
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
        video_provider = await WORKFLOW_JOB_STORE.get_video_provider(job_id)
        base_version = scene.get_version(scene.selected_version_id)
    except KeyError as exc:  # pragma: no cover - defensive logging
        logger.warning("Remix request dropped for missing scene %s/%s: %s", job_id, scene_order, exc)
        await WORKFLOW_JOB_STORE.add_message(
            job_id, "failed", f"Unable to remix scene {scene_order}; render not found."
        )
        return

    if video_provider in {"kling", "jimeng"}:
        await WORKFLOW_JOB_STORE.add_message(
            job_id,
            "failed",
            "Selected backend does not support remix operations. Please regenerate the scene instead.",
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
        video_provider = await WORKFLOW_JOB_STORE.get_video_provider(job_id)
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

    prompt_text_current = prompt_override_text or ""
    if not prompt_text_current:
        try:
            prompt_text_current = prompt_path.read_text(encoding="utf-8").strip()
        except OSError:
            prompt_text_current = ""

    if video_provider == "kling":
        try:
            log_path, client_log_path, video_id = await _render_scene_regen_with_kling(
                job_id,
                scene,
                prompt_text_current,
                resolution,
                video_path,
            )
            version = SceneVersionEntry(
                version_id=uuid.uuid4().hex,
                label=f"Regenerate {len(scene.versions) + 1}",
                video_path=str(video_path),
                log_path=str(log_path),
                client_log_path=str(client_log_path),
                video_id=video_id,
                prompt=prompt_text_current,
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
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Kling regeneration job for %s scene %s failed: %s", job_id, scene_order, exc)
            await WORKFLOW_JOB_STORE.add_message(
                job_id,
                "failed",
                f"Regeneration for scene {scene_order} failed: {exc}",
            )
        return

    if video_provider == "jimeng":
        try:
            log_path, client_log_path, video_id = await _render_scene_regen_with_jimeng(
                job_id,
                scene,
                prompt_text_current,
                resolution,
                video_path,
            )
            version = SceneVersionEntry(
                version_id=uuid.uuid4().hex,
                label=f"Regenerate {len(scene.versions) + 1}",
                video_path=str(video_path),
                log_path=str(log_path),
                client_log_path=str(client_log_path),
                video_id=video_id,
                prompt=prompt_text_current,
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
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Jimeng regeneration job for %s scene %s failed: %s", job_id, scene_order, exc)
            await WORKFLOW_JOB_STORE.add_message(
                job_id,
                "failed",
                f"Regeneration for scene {scene_order} failed: {exc}",
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

        version = SceneVersionEntry(
            version_id=uuid.uuid4().hex,
            label=f"Regenerate {len(scene.versions) + 1}",
            video_path=str(video_path),
            log_path=str(log_path) if log_path.is_file() else None,
            client_log_path=str(client_log_path),
            video_id=video_id,
            prompt=prompt_text_current,
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
    refined_script = await refine_text_with_gpt5(
        payload.script, mode="workflow", video_provider=payload.video_provider
    )
    if refined_script.strip():
        payload.script = refined_script


def _pick_default_emotions(payload: AnimatedFilmPayload) -> list[str]:
    if payload.emotions:
        return [item.strip() for item in payload.emotions if item.strip()]
    if payload.workflow_type == "music_short":
        return ["atmospheric", "immersive"]
    if payload.workflow_type == "direct_video":
        return ["energetic", "cinematic"]
    return ["warm", "hopeful"]


def _select_workflow_beats(payload: AnimatedFilmPayload) -> list[str]:
    base_map = {
        "story_short": ["Hook", "Challenge", "Resolution"],
        "music_short": ["Visual overture", "Rhythm build", "Chorus moment"],
        "direct_video": ["Opening shot", "Main action", "Signature pose"],
    }
    beats = list(base_map.get(payload.workflow_type, base_map["story_short"]))
    if payload.film_length == "long" and len(beats) == 3:
        beats.append("Closing beat")
    return beats


def _resolve_workflow_resolution(payload: AnimatedFilmPayload) -> str:
    if payload.resolution:
        return payload.resolution.strip()
    aspect = (payload.aspect_ratio or "").strip()
    mapping = {
        "16:9": "1280x720",
        "9:16": "720x1280",
        "1:1": "1024x1024",
        "4:3": "960x720",
        "3:4": "720x960",
        "21:9": "1680x720",
    }
    return mapping.get(aspect, DEFAULT_SORA_RESOLUTION)


def _build_guided_script_and_storyboard(
    payload: AnimatedFilmPayload, characters: Sequence[CharacterUpload]
) -> tuple[str, str, int]:
    beats = _select_workflow_beats(payload)
    emotions = _pick_default_emotions(payload)
    character_blurbs = []
    for character in characters:
        detail = character.design_notes or character.description or ""
        segment = f"{character.name}"
        if detail:
            segment += f": {detail}"
        character_blurbs.append(segment)
    character_summary = "; ".join(character_blurbs) or "Characters supplied by user."

    if payload.script and payload.script.strip():
        script_body = normalize_script_text(payload.script)
        if not HEADER_PATTERN.search(script_body):
            script_body = f"**Scene 1 (User Script)**\n{script_body}"
    else:
        script_lines: list[str] = []
        for idx, beat in enumerate(beats, start=1):
            script_lines.append(f"**Scene {idx} ({beat})**")
            script_lines.append(
                f"Language: {payload.language}. Mood: {', '.join(emotions)}. Aspect ratio: {payload.aspect_ratio or '9:16 portrait'}."
            )
            script_lines.append(
                f"{payload.scene_description.strip()} — focus on the {beat.lower()} beat with characters {character_summary}."
            )
            script_lines.append("")
        script_body = normalize_script_text("\n".join(script_lines))

    if payload.storyboard and payload.storyboard.strip():
        storyboard_text = normalize_script_text(payload.storyboard)
    else:
        storyboard_lines = [
            f"Workflow type: {payload.workflow_type}",
            f"Film length: {payload.film_length}",
            f"Aspect ratio: {payload.aspect_ratio or '9:16 portrait'}",
            f"Language: {payload.language}",
            f"Emotions: {', '.join(emotions)}",
            f"Characters: {character_summary}",
        ]
        if payload.character_notes:
            storyboard_lines.append(f"Character notes: {payload.character_notes}")
        storyboard_lines.append("Beats:")
        for idx, beat in enumerate(beats, start=1):
            storyboard_lines.append(f"- Scene {idx} ({payload.scene_duration}s): {beat}")
        storyboard_lines.append(f"Scene description: {payload.scene_description.strip()}")
        storyboard_text = normalize_script_text("\n".join(storyboard_lines))

    return script_body, storyboard_text, len(beats)


def _persist_guided_artifacts(job_dir: Path, artifacts: GuidedWorkflowArtifacts) -> GuidedWorkflowArtifacts:
    manifest_path = job_dir / "guided_artifacts.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(artifacts.model_dump_json(indent=2), encoding="utf-8")
    return artifacts


async def load_guided_artifacts(job_id: str) -> GuidedWorkflowArtifacts:
    job_dir = job_root(job_id)
    manifest_path = job_dir / "guided_artifacts.json"
    if not manifest_path.is_file():
        return GuidedWorkflowArtifacts(job_id=job_id, concept_art_paths=[])
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "job_id" not in payload:
            payload["job_id"] = job_id
        return GuidedWorkflowArtifacts(**payload)
    except Exception:  # pragma: no cover - defensive fallback
        return GuidedWorkflowArtifacts(job_id=job_id, concept_art_paths=[])


def _save_character_uploads(job_dir: Path, characters: Sequence[CharacterUpload]) -> list[dict[str, str]]:
    dest = job_dir / "characters" / "original"
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, str]] = []
    for character in characters:
        filename = dest / f"{slugify(character.name)}.png"
        save_payload_as_png(character.image, filename)
        saved.append(
            {
                "name": character.name,
                "path": str(filename),
                "description": character.description or "",
                "design_notes": character.design_notes or "",
            }
        )
    return saved


async def _generate_character_concepts(
    job_id: str,
    payload: AnimatedFilmPayload,
    saved_characters: list[dict[str, str]],
    resolution: str,
) -> list[str]:
    concept_dir = job_root(job_id) / "characters" / "concept"
    concept_dir.mkdir(parents=True, exist_ok=True)
    concept_paths: list[str] = []
    for character in saved_characters:
        prompt_segments = [
            f"Create polished concept art for the character {character['name']} for an animated short film.",
            f"Scene focus: {payload.scene_description.strip()}",
        ]
        detail = character.get("design_notes") or character.get("description")
        if detail:
            prompt_segments.append(f"Role and notes: {detail}")
        if payload.character_notes:
            prompt_segments.append(f"Global direction: {payload.character_notes}")
        prompt_segments.append(
            f"Mood: {', '.join(_pick_default_emotions(payload))}. Keep the character on-model and animation friendly."
        )
        prompt = " ".join(prompt_segments)
        output_path = concept_dir / f"{slugify(character['name'])}_concept.png"
        await generate_nanobanana_image(
            prompt,
            image_paths=[character["path"]],
            output_path=output_path,
            aspect_ratio=payload.aspect_ratio,
            resolution_hint=resolution,
        )
        concept_paths.append(str(output_path))
        await WORKFLOW_JOB_STORE.add_message(
            job_id, "design", f"Concept art completed for {character['name']}."
        )
    return concept_paths


async def _generate_first_frame(
    job_id: str,
    payload: AnimatedFilmPayload,
    concept_art_paths: Sequence[str],
    resolution: str,
) -> str:
    first_frame_dir = job_root(job_id) / "first_frames"
    first_frame_dir.mkdir(parents=True, exist_ok=True)
    first_frame_path = first_frame_dir / "scene_01_first_frame.png"
    prompt = (
        f"Create the opening frame for the animated short. "
        f"Emphasize the mood ({', '.join(_pick_default_emotions(payload))}) and include the main cast. "
        f"Scene description: {payload.scene_description.strip()}. "
        "Use the supplied concept art to keep characters consistent."
    )
    await generate_nanobanana_image(
        prompt,
        image_paths=concept_art_paths,
        output_path=first_frame_path,
        aspect_ratio=payload.aspect_ratio,
        resolution_hint=resolution,
    )
    return str(first_frame_path)


async def run_animated_workflow_job(job_id: str, payload: AnimatedFilmPayload) -> None:
    try:
        await WORKFLOW_JOB_STORE.mark_running(job_id)
        await WORKFLOW_JOB_STORE.update_stage(job_id, "intake", "Processing user inputs.", progress=0.02)

        job_dir = job_root(job_id)
        saved_characters = _save_character_uploads(job_dir, payload.characters)
        await WORKFLOW_JOB_STORE.add_message(
            job_id, "intake", f"Saved {len(saved_characters)} character reference(s)."
        )

        resolution = _resolve_workflow_resolution(payload)
        script_text, storyboard_text, scene_count = _build_guided_script_and_storyboard(payload, payload.characters)
        storyboard_path = job_dir / "storyboard.txt"
        storyboard_path.write_text(storyboard_text, encoding="utf-8")
        await WORKFLOW_JOB_STORE.update_stage(
            job_id, "planning", "Storyboard drafted for the guided workflow.", progress=0.08
        )

        concept_paths = await _generate_character_concepts(job_id, payload, saved_characters, resolution)
        await WORKFLOW_JOB_STORE.update_stage(
            job_id, "design", "Character concept art generated.", progress=0.15
        )

        first_frame_path = await _generate_first_frame(job_id, payload, concept_paths, resolution)
        await WORKFLOW_JOB_STORE.update_stage(
            job_id,
            "design",
            "First frame prepared from storyboard and character designs.",
            progress=0.22,
        )

        scene_image_dir = job_dir / "guided_scene_images"
        scene_image_dir.mkdir(parents=True, exist_ok=True)
        target_size = resolution_to_size(resolution)
        for idx in range(scene_count):
            dest = scene_image_dir / f"scene{idx + 1}.png"
            resize_scene_image(Path(first_frame_path), dest, target_size=target_size)

        design_manifest = []
        for character, concept_path in zip(saved_characters, concept_paths, strict=False):
            design_manifest.append(
                {
                    "name": character["name"],
                    "reference_path": character["path"],
                    "concept_art_path": concept_path,
                    "description": character.get("description") or "",
                    "design_notes": character.get("design_notes") or "",
                }
            )
        design_manifest_path = job_dir / "character_designs.json"
        design_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        design_manifest_path.write_text(json.dumps(design_manifest, indent=2), encoding="utf-8")

        start_payload = StartWorkflowPayload(
            script=script_text,
            video_provider=payload.video_provider,
            resolution=resolution,
            scene_image_dir=str(scene_image_dir),
            use_scene_images=True,
            generate_ai_first_frame=False,
            scene_duration=payload.scene_duration,
        )
        request = prepare_request_for_job(job_id, start_payload)
        await WORKFLOW_JOB_STORE.set_resolution(job_id, request.resolution)
        await WORKFLOW_JOB_STORE.set_download_variant(job_id, request.download_variant)
        await WORKFLOW_JOB_STORE.set_generation_mode(job_id, request.generation_mode)
        await WORKFLOW_JOB_STORE.set_scene_duration(job_id, request.scene_duration)
        await WORKFLOW_JOB_STORE.set_generate_ai_first_frame(job_id, request.generate_ai_first_frame)
        await WORKFLOW_JOB_STORE.set_video_provider(job_id, request.video_provider)
        try:
            script_text_final = Path(request.script_path).read_text(encoding="utf-8")
        except OSError:
            script_text_final = normalize_script_text(script_text)
        await WORKFLOW_JOB_STORE.set_script(job_id, script_text_final, request.script_path)
        await WORKFLOW_JOB_STORE.set_total_scenes(job_id, scene_count)
        await WORKFLOW_JOB_STORE.update_stage(
            job_id,
            "rendering",
            "Storyboard, concept art, and first frame ready. Starting Sora renders.",
            progress=0.25,
        )

        artifacts = GuidedWorkflowArtifacts(
            job_id=job_id,
            script_path=request.script_path,
            storyboard_path=str(storyboard_path),
            scene_image_dir=str(scene_image_dir),
            concept_art_paths=concept_paths,
            first_frame_path=first_frame_path,
            character_design_path=str(design_manifest_path),
        )
        _persist_guided_artifacts(job_dir, artifacts)

        await run_workflow_job(job_id, request, skip_mark_running=True)
    except asyncio.CancelledError:  # pragma: no cover - propagation only
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Guided workflow %s failed: %s", job_id, exc)
        await WORKFLOW_JOB_STORE.mark_failed(job_id, str(exc))


async def start_guided_workflow(payload: AnimatedFilmPayload) -> tuple[str, GuidedWorkflowArtifacts]:
    job_id = uuid.uuid4().hex
    await WORKFLOW_JOB_STORE.create_job(job_id)
    await WORKFLOW_JOB_STORE.update_stage(job_id, "queued", "Guided animated workflow queued.", progress=0.0)
    asyncio.create_task(run_animated_workflow_job(job_id, payload))
    artifacts = await load_guided_artifacts(job_id)
    return job_id, artifacts


__all__ = [
    "job_root",
    "prepare_request_for_job",
    "prepare_single_video_job",
    "refine_script_for_payload",
    "run_animated_workflow_job",
    "run_recompose_job",
    "run_scene_regeneration_job",
    "run_scene_remix_job",
    "run_single_video_job",
    "run_single_video_remix",
    "run_workflow_job",
    "start_guided_workflow",
    "load_guided_artifacts",
]
