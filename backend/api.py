"""FastAPI application wiring for the Sora workflow."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from .auth import (
    auth_challenge_header,
    auth_settings,
    authorize_request,
    validate_auth_configuration,
)
from .config import (
    CONTENT_UNDERSTANDING_MAX_UPLOAD_BYTES,
    DEFAULT_IMAGE_GENERATOR,
    DEFAULT_REPRODUCE_REFERENCE_MODE,
    DEFAULT_REPRODUCE_SEGMENTATION,
    DEFAULT_SORA_RESOLUTION,
    DEFAULT_VIDEO_PROVIDER,
    OUTPUT_DIR,
    REPRODUCE_DEFAULT_THREADS,
    RESOLUTION_PATTERN,
    SCENE_IMAGE_PATTERN,
    SINGLE_VIDEO_JOBS_DIR,
    STATIC_URL_PREFIX,
    SUPPORTED_REPRODUCE_REFERENCE_MODES,
    SUPPORTED_REPRODUCE_SEGMENTERS,
    SUPPORTED_VIDEO_PROVIDERS,
    allowed_origins,
    logger,
    normalize_image_generator,
    normalize_video_provider,
)
from .models import (
    JobStatusResponse,
    RefineSinglePayload,
    RefineWorkflowPayload,
    RefinedPromptResponse,
    RefinedScriptResponse,
    ReproduceSegmentRegeneratePayload,
    SceneRemixPayload,
    SceneRegeneratePayload,
    SceneSelectionPayload,
    SingleVideoJobResponse,
    SingleVideoPayload,
    SingleVideoRemixPayload,
    SingleVideoSelectionPayload,
    StartWorkflowPayload,
    VideoInsightJobResponse,
    VideoReproduceJobResponse,
    WorkflowPromptTemplateResponse,
    WorkflowPromptTemplateUpdate,
)
from .prompts import (
    ProductionSettings,
    get_workflow_template_text,
    refine_text_with_llm,
    update_workflow_template_text,
)
from .content_insights.service import (
    ensure_content_understanding_ready,
    run_video_insight_job,
    video_insight_job_root,
)
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
from .reproduce import regenerate_segment, reproduce_job_root, run_reproduce_job
from .stores import (
    SINGLE_VIDEO_STORE,
    VIDEO_INSIGHT_STORE,
    VIDEO_REPRODUCE_STORE,
    WORKFLOW_JOB_STORE,
)
from .utils import normalize_script_text, slugify


@asynccontextmanager
async def _app_lifespan(_: FastAPI):
    validate_auth_configuration()
    yield


fastapi_app = FastAPI(
    title="Sora Agent Workflow API",
    description="Async workflow orchestrator for Azure Sora multi-agent pipeline.",
    lifespan=_app_lifespan,
)


_AUTH_EXEMPT_PATHS = {"/api/auth/session"}


def _auth_methods() -> list[str]:
    settings = auth_settings()
    methods: list[str] = []
    if settings.username and settings.password:
        methods.append("basic")
    if settings.bearer_token:
        methods.append("bearer")
    return methods


def _auth_unauthorized_response() -> JSONResponse:
    challenge = auth_challenge_header()
    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required."},
        headers={"WWW-Authenticate": challenge},
    )


@fastapi_app.middleware("http")
async def _enforce_api_auth(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if authorize_request(request.headers):
        return await call_next(request)
    return _auth_unauthorized_response()


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


def _normalize_insight_filename(filename: str | None) -> str:
    base = (filename or "uploaded-video").strip() or "uploaded-video"
    safe = Path(base).name or "uploaded-video"
    if "." not in safe:
        safe = f"{safe}.mp4"
    return safe


async def _write_upload_to_file(upload: UploadFile, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("wb") as buffer:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > CONTENT_UNDERSTANDING_MAX_UPLOAD_BYTES:
                    raise ValueError("Uploaded video exceeds configured size limit.")
                buffer.write(chunk)
    finally:
        await upload.close()
    return total


def _existing_file(path_like: str | Path | None) -> Path | None:
    if not path_like:
        return None
    path = Path(path_like)
    if path.is_file():
        return path
    return None


def _scene_directory_name(order: int, label: str | None) -> str:
    slug = slugify(label or f"scene-{order}")
    if not slug:
        slug = f"scene-{order}"
    return f"scene-{order:02d}-{slug}"


def _uploaded_start_images(job_id: str) -> dict[int, Path]:
    """Start images the user uploaded for this job, keyed by scene number.

    Only the job's own scene_images folder is read; the shared default
    scene_start_screen folder is not part of any one job's materials.
    """

    upload_dir = job_root(job_id) / "scene_images"
    if not upload_dir.is_dir():
        return {}
    images: dict[int, Path] = {}
    for item in sorted(upload_dir.iterdir()):
        match = SCENE_IMAGE_PATTERN.fullmatch(item.name)
        if match and item.is_file():
            images.setdefault(int(match.group(1)), item)
    return images


def _collect_workflow_assets(job: JobStatusResponse) -> list[tuple[Path, str]]:
    assets: list[tuple[Path, str]] = []
    base_dir = f"workflow_{job.job_id}"
    seen: set[tuple[Path, str]] = set()

    def _add(path: Path | None, arcname: str) -> None:
        if not path:
            return
        key = (path, arcname)
        if key in seen:
            return
        assets.append(key)
        seen.add(key)

    script_path = _existing_file(job.script_path) or _existing_file(job_root(job.job_id) / "script.txt")
    if script_path:
        _add(script_path, f"{base_dir}/script/{script_path.name}")

    final_video = _existing_file(job.final_video_path)
    if final_video:
        suffix = final_video.suffix or ".mp4"
        _add(final_video, f"{base_dir}/final-video/final{suffix}")

    for ref_path in job.character_ref_paths or []:
        character_ref = _existing_file(ref_path)
        if character_ref:
            _add(character_ref, f"{base_dir}/character-references/{character_ref.name}")

    uploaded_start_images = _uploaded_start_images(job.job_id)

    for scene in job.scenes or []:
        scene_dir = _scene_directory_name(scene.order, scene.label)
        prompt_dir = f"{base_dir}/scene-files/{scene_dir}/prompts"
        video_dir = f"{base_dir}/scene-files/{scene_dir}/videos"
        image_dir = f"{base_dir}/scene-files/{scene_dir}/images"
        # Versions usually share one first frame; store each image file once.
        scene_images: set[Path] = set()

        def _add_image(path_like: str | Path | None, name: str) -> None:
            image = _existing_file(path_like)
            if not image or image.resolve() in scene_images:
                return
            scene_images.add(image.resolve())
            _add(image, f"{image_dir}/{name}{image.suffix or '.png'}")

        _add_image(scene.first_frame_path, "first-frame")
        _add_image(uploaded_start_images.get(scene.order), "uploaded-start-image")

        scene_prompt = _existing_file(scene.prompt_path)
        if scene_prompt:
            suffix = scene_prompt.suffix or ".txt"
            _add(scene_prompt, f"{prompt_dir}/scene-prompt{suffix}")

        versions = scene.versions or []
        if not versions:
            scene_video = _existing_file(scene.video_path)
            if scene_video:
                suffix = scene_video.suffix or ".mp4"
                _add(scene_video, f"{video_dir}/scene-video{suffix}")

        for index, version in enumerate(versions, start=1):
            version_slug = slugify(version.label or version.version_id) or f"version-{index}"

            version_video = _existing_file(version.video_path)
            if version_video:
                suffix = version_video.suffix or ".mp4"
                _add(version_video, f"{video_dir}/{index:02d}-{version_slug}{suffix}")

            version_prompt = _existing_file(version.prompt_path)
            if version_prompt:
                suffix = version_prompt.suffix or ".txt"
                _add(version_prompt, f"{prompt_dir}/{index:02d}-{version_slug}{suffix}")

            _add_image(version.first_frame_path, f"{index:02d}-{version_slug}-first-frame")
            _add_image(version.last_frame_path, f"{index:02d}-{version_slug}-last-frame")

        _add_image(scene.last_frame_path, "last-frame")

    return assets


@fastapi_app.get("/api/providers/active")
async def get_active_provider() -> dict[str, object]:
    return {
        "active": DEFAULT_VIDEO_PROVIDER,
        "available": list(SUPPORTED_VIDEO_PROVIDERS),
    }


@fastapi_app.get("/api/auth/session")
async def get_auth_session(request: Request) -> dict[str, object]:
    settings = auth_settings()
    if settings.required and not authorize_request(request.headers, settings):
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": auth_challenge_header(settings)},
        )
    return {
        "authenticated": True,
        "required": settings.required,
        "methods": _auth_methods(),
    }


@fastapi_app.post("/api/refine/workflow", response_model=RefinedScriptResponse)
async def refine_workflow_script(payload: RefineWorkflowPayload) -> RefinedScriptResponse:
    script = (payload.script or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script text is required for refinement.")
    try:
        refined = await refine_text_with_llm(script, mode="workflow", settings=payload.production_settings())
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("GPT-6.1-sol refinement failed for workflow preview: %s", exc)
        raise HTTPException(status_code=503, detail=f"Unable to refine script: {exc}") from exc
    cleaned = normalize_script_text(refined.strip() or script)
    return RefinedScriptResponse(refined_script=cleaned)


@fastapi_app.post("/api/refine/single", response_model=RefinedPromptResponse)
async def refine_single_prompt(payload: RefineSinglePayload) -> RefinedPromptResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt text is required for refinement.")
    try:
        refined = await refine_text_with_llm(prompt, mode="single", settings=payload.production_settings())
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("GPT-6.1-sol refinement failed for single prompt preview: %s", exc)
        raise HTTPException(status_code=503, detail=f"Unable to refine prompt: {exc}") from exc
    final_prompt = refined.strip() or prompt
    return RefinedPromptResponse(refined_prompt=final_prompt)


@fastapi_app.get(
    "/api/prompts/workflow",
    response_model=WorkflowPromptTemplateResponse,
)
async def get_workflow_prompt_template(
    video_provider: str = Query(DEFAULT_VIDEO_PROVIDER, alias="videoProvider"),
) -> WorkflowPromptTemplateResponse:
    provider = normalize_video_provider(video_provider)
    try:
        template = get_workflow_template_text(provider)
    except RuntimeError as exc:  # pragma: no cover - filesystem failure
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkflowPromptTemplateResponse(video_provider=provider, template=template)


@fastapi_app.put(
    "/api/prompts/workflow",
    response_model=WorkflowPromptTemplateResponse,
)
async def update_workflow_prompt_template(
    payload: WorkflowPromptTemplateUpdate,
) -> WorkflowPromptTemplateResponse:
    provider = normalize_video_provider(payload.video_provider)
    try:
        template = update_workflow_template_text(provider, payload.template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WorkflowPromptTemplateResponse(video_provider=provider, template=template)


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
                job_id, "planning", "Storyboard refined with GPT-6.1-sol before scene planning."
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-6.1-sol refinement failed for workflow %s: %s", job_id, exc)

    try:
        request = prepare_request_for_job(job_id, payload)
        await WORKFLOW_JOB_STORE.set_resolution(job_id, request.resolution)
        await WORKFLOW_JOB_STORE.set_download_variant(job_id, request.download_variant)
        await WORKFLOW_JOB_STORE.set_generation_mode(job_id, request.generation_mode)
        await WORKFLOW_JOB_STORE.set_scene_duration(job_id, request.scene_duration)
        await WORKFLOW_JOB_STORE.set_generate_ai_first_frame(
            job_id, request.generate_ai_first_frame
        )
        await WORKFLOW_JOB_STORE.set_video_provider(job_id, request.video_provider)
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


@fastapi_app.get("/api/workflows/{job_id}/assets.zip")
async def download_workflow_assets(job_id: str):
    try:
        job = await WORKFLOW_JOB_STORE.get_status(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc

    assets = _collect_workflow_assets(job)
    if not assets:
        raise HTTPException(status_code=404, detail="No scene assets are available for this workflow.")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        zip_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for source_path, arcname in assets:
                try:
                    bundle.write(str(source_path), arcname)
                except OSError:
                    logger.warning("Unable to add asset %s to archive for job %s", source_path, job_id)
                    continue
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise

    background = BackgroundTask(lambda: zip_path.unlink(missing_ok=True))
    filename = f"{job_id}_assets.zip"
    return FileResponse(zip_path, media_type="application/zip", filename=filename, background=background)


@fastapi_app.post("/api/single-videos", response_model=SingleVideoJobResponse)
async def start_single_video(payload: SingleVideoPayload) -> SingleVideoJobResponse:
    job_id = uuid.uuid4().hex
    prompt_text = (payload.prompt or "").strip()
    if not prompt_text:
        raise HTTPException(status_code=400, detail="Prompt text is required.")
    if payload.refine_with_gpt5:
        try:
            prompt_text = await refine_text_with_llm(
                prompt_text,
                mode="single",
                settings=ProductionSettings(
                    video_provider=payload.video_provider,
                    clip_seconds=payload.duration,
                    resolution=payload.resolution,
                    uses_start_image=bool(payload.reference_image or payload.reference_images),
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("GPT-6.1-sol prompt refinement failed for single job %s: %s", job_id, exc)
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
            payload.reference_images,
            payload.video_provider,
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
        config.video_provider,
    )
    if payload.refine_with_gpt5:
        await SINGLE_VIDEO_STORE.add_message(
            job_id, "queued", "Prompt refined with GPT-6.1-sol before submission."
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


@fastapi_app.post("/api/video-insights", response_model=VideoInsightJobResponse)
async def create_video_insight_job(
    video_file: UploadFile = File(..., alias="file"),
    notes: str | None = Form(default=None),
) -> VideoInsightJobResponse:
    if video_file.content_type and not video_file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Upload a valid video file (mp4, mov, webm).")
    try:
        ensure_content_understanding_ready()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    job_id = uuid.uuid4().hex
    filename = _normalize_insight_filename(video_file.filename)
    normalized_notes = (notes or "").strip() or None
    await VIDEO_INSIGHT_STORE.create_job(job_id, filename, video_file.content_type, normalized_notes)
    job_dir = video_insight_job_root(job_id)
    destination = job_dir / filename
    try:
        await _write_upload_to_file(video_file, destination)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        await VIDEO_INSIGHT_STORE.mark_failed(job_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:  # pragma: no cover - filesystem failure
        destination.unlink(missing_ok=True)
        await VIDEO_INSIGHT_STORE.mark_failed(job_id, "Unable to store uploaded video.")
        raise HTTPException(status_code=500, detail="Unable to persist uploaded video.") from exc

    await VIDEO_INSIGHT_STORE.set_video_path(job_id, str(destination))
    asyncio.create_task(run_video_insight_job(job_id, destination))
    return await VIDEO_INSIGHT_STORE.snapshot(job_id)


@fastapi_app.get("/api/video-insights", response_model=list[VideoInsightJobResponse])
async def list_video_insights() -> list[VideoInsightJobResponse]:
    return await VIDEO_INSIGHT_STORE.list_statuses()


@fastapi_app.get("/api/video-insights/{job_id}", response_model=VideoInsightJobResponse)
async def get_video_insight(job_id: str) -> VideoInsightJobResponse:
    try:
        return await VIDEO_INSIGHT_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/video-insights/{job_id}")
async def delete_video_insight(job_id: str) -> dict[str, str]:
    try:
        await VIDEO_INSIGHT_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(video_insight_job_root(job_id, ensure_exists=False), ignore_errors=True)
    return {"status": "deleted"}


@fastapi_app.post("/api/reproduce", response_model=VideoReproduceJobResponse)
async def create_reproduce_job(
    video_file: UploadFile = File(..., alias="file"),
    notes: str | None = Form(default=None),
    thread_count: int | None = Form(default=None, alias="threadCount"),
    image_generator: str | None = Form(default=None, alias="imageGenerator"),
    segmentation_backend: str | None = Form(default=None, alias="segmentationBackend"),
    frame_reference_mode: str | None = Form(default=None, alias="frameReferenceMode"),
) -> VideoReproduceJobResponse:
    if video_file.content_type and not video_file.content_type.startswith("video/"):
        raise HTTPException(status_code=400, detail="Upload a valid video file (mp4, mov, webm).")
    job_id = uuid.uuid4().hex
    filename = _normalize_insight_filename(video_file.filename)
    normalized_notes = (notes or "").strip() or None
    try:
        threads = int(thread_count) if thread_count is not None else REPRODUCE_DEFAULT_THREADS
    except (TypeError, ValueError):
        threads = REPRODUCE_DEFAULT_THREADS
    threads = max(1, min(threads, 10))
    requested_generator = normalize_image_generator(image_generator, DEFAULT_IMAGE_GENERATOR)
    requested_segmentation = (
        (segmentation_backend or DEFAULT_REPRODUCE_SEGMENTATION).strip().lower()
    )
    if requested_segmentation not in SUPPORTED_REPRODUCE_SEGMENTERS:
        requested_segmentation = DEFAULT_REPRODUCE_SEGMENTATION
    if requested_segmentation == "content_understanding":
        try:
            ensure_content_understanding_ready()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    requested_reference_mode = (
        (frame_reference_mode or DEFAULT_REPRODUCE_REFERENCE_MODE).strip().lower()
    )
    if requested_reference_mode not in SUPPORTED_REPRODUCE_REFERENCE_MODES:
        requested_reference_mode = DEFAULT_REPRODUCE_REFERENCE_MODE
    await VIDEO_REPRODUCE_STORE.create_job(
        job_id,
        filename,
        video_file.content_type,
        normalized_notes,
        threads,
        requested_generator,
        requested_segmentation,
        requested_reference_mode,
    )
    job_dir = reproduce_job_root(job_id)
    destination = job_dir / filename
    try:
        await _write_upload_to_file(video_file, destination)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        await VIDEO_REPRODUCE_STORE.mark_failed(job_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:  # pragma: no cover - filesystem failure
        destination.unlink(missing_ok=True)
        await VIDEO_REPRODUCE_STORE.mark_failed(job_id, "Unable to store uploaded video.")
        raise HTTPException(status_code=500, detail="Unable to persist uploaded video.") from exc

    await VIDEO_REPRODUCE_STORE.set_video_path(job_id, str(destination))
    asyncio.create_task(run_reproduce_job(job_id))
    return await VIDEO_REPRODUCE_STORE.snapshot(job_id)


@fastapi_app.get("/api/reproduce", response_model=list[VideoReproduceJobResponse])
async def list_reproduce_jobs() -> list[VideoReproduceJobResponse]:
    return await VIDEO_REPRODUCE_STORE.list_statuses()


@fastapi_app.get("/api/reproduce/{job_id}", response_model=VideoReproduceJobResponse)
async def get_reproduce_job(job_id: str) -> VideoReproduceJobResponse:
    try:
        return await VIDEO_REPRODUCE_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc


@fastapi_app.delete("/api/reproduce/{job_id}")
async def delete_reproduce_job(job_id: str) -> dict[str, str]:
    try:
        await VIDEO_REPRODUCE_STORE.delete_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    shutil.rmtree(reproduce_job_root(job_id, ensure_exists=False), ignore_errors=True)
    return {"status": "deleted"}


@fastapi_app.post("/api/reproduce/{job_id}/rerun", response_model=VideoReproduceJobResponse)
async def rerun_reproduce_job(job_id: str) -> VideoReproduceJobResponse:
    try:
        await VIDEO_REPRODUCE_STORE.queue_rerun(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    asyncio.create_task(run_reproduce_job(job_id))
    return await VIDEO_REPRODUCE_STORE.snapshot(job_id)


@fastapi_app.post(
    "/api/reproduce/{job_id}/segments/{segment_order}/regenerate",
    response_model=VideoReproduceJobResponse,
)
async def regenerate_reproduce_segment(
    job_id: str,
    segment_order: int,
    payload: ReproduceSegmentRegeneratePayload,
) -> VideoReproduceJobResponse:
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt text is required.")
    try:
        await regenerate_segment(job_id, segment_order, prompt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Segment {segment_order} not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await VIDEO_REPRODUCE_STORE.snapshot(job_id)


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
