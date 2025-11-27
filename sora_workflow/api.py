"""FastAPI application wiring for the Sora workflow."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import (
    DEFAULT_SORA_RESOLUTION,
    OUTPUT_DIR,
    RESOLUTION_PATTERN,
    SINGLE_VIDEO_JOBS_DIR,
    STATIC_URL_PREFIX,
    allowed_origins,
    logger,
)
from .models import (
    JobStatusResponse,
    RefineSinglePayload,
    RefineWorkflowPayload,
    RefinedPromptResponse,
    RefinedScriptResponse,
    SceneRemixPayload,
    SceneRegeneratePayload,
    SceneSelectionPayload,
    SingleVideoJobResponse,
    SingleVideoPayload,
    SingleVideoRemixPayload,
    SingleVideoSelectionPayload,
    StartWorkflowPayload,
)
from .prompts import refine_text_with_gpt5
from .services import (
    job_root,
    prepare_request_for_job,
    prepare_single_video_job,
    refine_script_for_payload,
    run_recompose_job,
    run_scene_regeneration_job,
    run_scene_remix_job,
    run_single_video_job,
    run_single_video_remix,
    run_workflow_job,
)
from .stores import SINGLE_VIDEO_STORE, WORKFLOW_JOB_STORE
from .utils import normalize_script_text


fastapi_app = FastAPI(
    title="Sora Agent Workflow API",
    description="Async workflow orchestrator for Azure Sora multi-agent pipeline.",
)

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

fastapi_app.mount(
    STATIC_URL_PREFIX,
    StaticFiles(directory=str(OUTPUT_DIR), check_dir=False),
    name="output",
)


@fastapi_app.post("/api/refine/workflow", response_model=RefinedScriptResponse)
async def refine_workflow_script(payload: RefineWorkflowPayload) -> RefinedScriptResponse:
    script = (payload.script or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script text is required for refinement.")
    try:
        refined = await refine_text_with_gpt5(script, mode="workflow")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("GPT-5 refinement failed for workflow preview: %s", exc)
        raise HTTPException(status_code=503, detail=f"Unable to refine script: {exc}") from exc
    cleaned = normalize_script_text(refined.strip() or script)
    return RefinedScriptResponse(refined_script=cleaned)


@fastapi_app.post("/api/refine/single", response_model=RefinedPromptResponse)
async def refine_single_prompt(payload: RefineSinglePayload) -> RefinedPromptResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt text is required for refinement.")
    try:
        refined = await refine_text_with_gpt5(prompt, mode="single")
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("GPT-5 refinement failed for single prompt preview: %s", exc)
        raise HTTPException(status_code=503, detail=f"Unable to refine prompt: {exc}") from exc
    final_prompt = refined.strip() or prompt
    return RefinedPromptResponse(refined_prompt=final_prompt)


@fastapi_app.post("/api/workflows", response_model=JobStatusResponse)
async def start_workflow(payload: StartWorkflowPayload) -> JobStatusResponse:
    script = payload.script or ""
    if not script.strip():
        raise HTTPException(status_code=400, detail="Script text is required.")

    job_id = uuid.uuid4().hex
    await WORKFLOW_JOB_STORE.create_job(job_id)

    if payload.refine_with_gpt5:
        try:
            await refine_script_for_payload(payload)
            await WORKFLOW_JOB_STORE.add_message(
                job_id, "planning", "Storyboard refined with GPT-5 before scene planning."
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-5 refinement failed for workflow %s: %s", job_id, exc)

    try:
        request = prepare_request_for_job(job_id, payload)
        await WORKFLOW_JOB_STORE.set_resolution(job_id, request.resolution)
        await WORKFLOW_JOB_STORE.set_download_variant(job_id, request.download_variant)
        await WORKFLOW_JOB_STORE.set_generation_mode(job_id, request.generation_mode)
        await WORKFLOW_JOB_STORE.set_scene_duration(job_id, request.scene_duration)
        try:
            script_text = Path(request.script_path).read_text(encoding="utf-8")
        except OSError:
            script_text = normalize_script_text(payload.script)
        await WORKFLOW_JOB_STORE.set_script(job_id, script_text, request.script_path)
    except ValueError as exc:
        await WORKFLOW_JOB_STORE.mark_failed(job_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    await WORKFLOW_JOB_STORE.update_stage(job_id, "queued", "Script accepted.", progress=0.0)
    asyncio.create_task(run_workflow_job(job_id, request))
    return await WORKFLOW_JOB_STORE.get_status(job_id)


@fastapi_app.get("/api/workflows", response_model=list[JobStatusResponse])
async def list_workflows() -> list[JobStatusResponse]:
    return await WORKFLOW_JOB_STORE.list_statuses()


@fastapi_app.get("/api/workflows/{job_id}", response_model=JobStatusResponse)
async def get_workflow(job_id: str) -> JobStatusResponse:
    try:
        return await WORKFLOW_JOB_STORE.get_status(job_id)
    except KeyError as exc:  # pragma: no cover - FastAPI translates to HTTP response
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/workflows/{job_id}")
async def delete_workflow(job_id: str) -> dict[str, str]:
    try:
        await WORKFLOW_JOB_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(job_root(job_id), ignore_errors=True)
    return {"status": "deleted"}


@fastapi_app.post("/api/single-videos", response_model=SingleVideoJobResponse)
async def start_single_video(payload: SingleVideoPayload) -> SingleVideoJobResponse:
    job_id = uuid.uuid4().hex
    prompt_text = (payload.prompt or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt text is required.")
    if payload.refine_with_gpt5:
        try:
            prompt_text = await refine_text_with_gpt5(prompt_text, mode="single")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-5 prompt refinement failed for single job %s: %s", job_id, exc)
        if prompt_text.strip():
            payload.prompt = prompt_text
        else:
            prompt_text = payload.prompt or ""

    resolution = (payload.resolution or DEFAULT_SORA_RESOLUTION).strip()
    if not RESOLUTION_PATTERN.match(resolution):
        raise HTTPException(status_code=400, detail="Resolution must be in WIDTHxHEIGHT format, e.g. 720x1280.")
    duration = int(payload.duration)
    download_variant = payload.download_variant or os.getenv("VIDEO_DOWNLOAD_VARIANT", "video")
    try:
        config = prepare_single_video_job(
            job_id,
            prompt_text.strip(),
            resolution,
            duration,
            download_variant,
            payload.reference_image,
        )
    except ValueError as exc:
        shutil.rmtree(SINGLE_VIDEO_JOBS_DIR / job_id, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await SINGLE_VIDEO_STORE.create_job(
        job_id,
        config.prompt_text,
        config.resolution,
        config.duration,
        config.download_variant,
    )
    if payload.refine_with_gpt5:
        await SINGLE_VIDEO_STORE.add_message(
            job_id, "queued", "Prompt refined with GPT-5 before submission."
        )

    asyncio.create_task(run_single_video_job(config))
    return await SINGLE_VIDEO_STORE.snapshot(job_id)


@fastapi_app.get("/api/single-videos", response_model=list[SingleVideoJobResponse])
async def list_single_videos() -> list[SingleVideoJobResponse]:
    return await SINGLE_VIDEO_STORE.list_statuses()


@fastapi_app.get("/api/single-videos/{job_id}", response_model=SingleVideoJobResponse)
async def get_single_video(job_id: str) -> SingleVideoJobResponse:
    try:
        return await SINGLE_VIDEO_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/single-videos/{job_id}")
async def delete_single_video(job_id: str) -> dict[str, str]:
    try:
        await SINGLE_VIDEO_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(job_root(job_id), ignore_errors=True)
    return {"status": "deleted"}


@fastapi_app.post(
    "/api/single-videos/{job_id}/remix",
    response_model=SingleVideoJobResponse,
)
async def remix_single_video(job_id: str, payload: SingleVideoRemixPayload) -> SingleVideoJobResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Remix prompt is required.")
    try:
        status = await SINGLE_VIDEO_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc

    if not status.video_id:
        raise HTTPException(
            status_code=400,
            detail="This job is missing Azure video metadata. Generate a clip before remixing.",
        )

    await SINGLE_VIDEO_STORE.add_message(job_id, "running", "Remix request submitted.")
    asyncio.create_task(run_single_video_remix(job_id, prompt))
    return await SINGLE_VIDEO_STORE.snapshot(job_id)

@fastapi_app.post("/api/single-videos/{job_id}/select", response_model=SingleVideoJobResponse)
async def select_single_video_version(job_id: str, payload: SingleVideoSelectionPayload) -> SingleVideoJobResponse:
    version_id = (payload.version_id or "").strip()
    if not version_id:
        raise HTTPException(status_code=400, detail="versionId is required.")
    try:
        await SINGLE_VIDEO_STORE.set_single_selection(job_id, version_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unable to select version {version_id} for job {job_id}.") from exc
    return await SINGLE_VIDEO_STORE.snapshot(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/scenes/{scene_order}/remix",
    response_model=JobStatusResponse,
)
async def remix_scene_version(
    job_id: str, scene_order: int, payload: SceneRemixPayload
) -> JobStatusResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Remix prompt is required.")
    try:
        scene = await WORKFLOW_JOB_STORE.get_scene(job_id, scene_order)
        version = scene.get_version(scene.selected_version_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Scene {scene_order} not found for job {job_id}.",
        ) from exc
    if not version.video_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Scene is missing Azure video metadata; regenerate the clip before requesting a remix."
            ),
        )
    await WORKFLOW_JOB_STORE.add_message(job_id, "rendering", f"Queued remix for {scene.label}.")
    asyncio.create_task(run_scene_remix_job(job_id, scene_order, prompt))
    return await WORKFLOW_JOB_STORE.get_status(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/scenes/{scene_order}/regenerate",
    response_model=JobStatusResponse,
)
async def regenerate_scene_version(
    job_id: str, scene_order: int, payload: SceneRegeneratePayload | None = None
) -> JobStatusResponse:
    prompt_override = (payload.prompt or "").strip() if payload else None
    try:
        scene = await WORKFLOW_JOB_STORE.get_scene(job_id, scene_order)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Scene {scene_order} not found for job {job_id}.",
        ) from exc
    await WORKFLOW_JOB_STORE.add_message(job_id, "rendering", f"Queued regeneration for {scene.label}.")
    asyncio.create_task(run_scene_regeneration_job(job_id, scene_order, prompt_override or None))
    return await WORKFLOW_JOB_STORE.get_status(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/scenes/{scene_order}/select",
    response_model=JobStatusResponse,
)
async def select_scene_version(
    job_id: str, scene_order: int, payload: SceneSelectionPayload
) -> JobStatusResponse:
    version_id = (payload.version_id or "").strip()
    if not version_id:
        raise HTTPException(status_code=400, detail="versionId is required.")
    try:
        await WORKFLOW_JOB_STORE.set_scene_selection(job_id, scene_order, version_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Unable to select version {version_id} for scene {scene_order}.",
        ) from exc
    return await WORKFLOW_JOB_STORE.get_status(job_id)


@fastapi_app.post(
    "/api/workflows/{job_id}/recompose",
    response_model=JobStatusResponse,
)
async def recompose_final_video(job_id: str) -> JobStatusResponse:
    try:
        await WORKFLOW_JOB_STORE.mark_composing(
            job_id, "Regenerating final video from selected scene versions."
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    asyncio.create_task(run_recompose_job(job_id))
    return await WORKFLOW_JOB_STORE.get_status(job_id)


__all__ = ["fastapi_app"]
