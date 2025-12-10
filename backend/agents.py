"""Agent definitions for the Sora workflow."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from agent_framework import Executor, WorkflowContext, handler

from .config import ROOT, SCENE_IMAGE_DIR, logger
from .first_frames import generate_first_frame_image
from .images import prepare_scene_start_images, prepare_scene_tail_image
from .kling import render_kling_clip
from .jimeng import render_jimeng_clip
from .models import (
    ScenePlan,
    ScenePlanBatch,
    SceneRender,
    SceneRenderBatch,
    VideoWorkflowRequest,
)
from .pipeline import summarize_manifest
from .progress import (
    mark_completed,
    mark_composing,
    record_scene_completion,
    record_total_scenes,
    report_progress,
)
from .prompts import (
    build_prompt_text,
    ensure_scene_headers,
    parse_script,
    refine_scene_description,
)
from .stores import WORKFLOW_JOB_STORE
from .utils import (
    escape_for_concat,
    extract_last_frame,
    read_text,
    run_subprocess,
    slugify,
)


class DirectorAgent(Executor):
    """Break the master script into Sora-ready scene prompts."""

    def __init__(self) -> None:
        super().__init__(id="director_agent")

    async def _prepare_script_text(self, request: VideoWorkflowRequest) -> str:
        script_path = Path(request.script_path)
        raw_script = read_text(script_path)
        script_text, has_headers, used_refinement = await ensure_scene_headers(
            raw_script, request.refine_with_gpt5, request.video_provider
        )

        if script_text != raw_script:
            script_path.write_text(script_text, encoding="utf-8")
        if used_refinement and request.job_id:
            await WORKFLOW_JOB_STORE.add_message(
                request.job_id,
                "planning",
                "Storyboard refined with GPT-5 before scene planning.",
            )
        if not has_headers:
            raise RuntimeError(
                "No scene headers found in the script. Enable GPT-5 refinement or format it with **Scene N (Title)** blocks."
            )
        return script_text

    @handler
    async def handle(
        self, request: VideoWorkflowRequest, ctx: WorkflowContext[ScenePlanBatch]
    ) -> None:
        await ctx.set_shared_state("request", request)
        await report_progress(
            request,
            "planning",
            "Director reviewing storyboard script.",
            progress=0.05,
        )
        script_text = await self._prepare_script_text(request)
        drafts = parse_script(script_text)
        if not drafts:
            raise RuntimeError(
                "No scene headers found in the script. Ensure it follows **Scene N (...)** format."
            )
        await record_total_scenes(request, len(drafts))
        await report_progress(
            request,
            "planning",
            f"Identified {len(drafts)} scene outline(s) from script.",
            progress=0.1,
        )

        prompt_dir = Path(request.prompt_dir)
        prompt_dir.mkdir(parents=True, exist_ok=True)

        plans: list[ScenePlan] = []
        for idx, draft in enumerate(drafts, start=1):
            duration = request.scene_duration
            description = "\n".join(draft.description_lines)
            slug = slugify(f"{draft.scene_number:02d}-{draft.title}")
            prompt_path = prompt_dir / f"scene_{idx:02d}_{slug}.txt"
            refined_description = (
                await refine_scene_description(draft.title, description, duration)
                if request.refine_with_gpt5
                else description
            )
            prompt_text = build_prompt_text(
                draft.title, refined_description, duration, request.resolution
            )
            prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

            plans.append(
                ScenePlan(
                    order=idx,
                    label=f"Scene {draft.scene_number}: {draft.title}",
                    duration_seconds=duration,
                    description=refined_description,
                    prompt_path=str(prompt_path),
                    prompt_text=prompt_text,
                )
            )

        tmp_dir = Path(request.tmp_dir)
        if request.generation_mode == "sequence":
            initial_reference = request.sequence_initial_image
            scene_images = [initial_reference] if initial_reference else []
            if request.generate_ai_first_frame:
                note = "Sequence mode active; AI first-frame generation will supply references for each scene."
            elif initial_reference:
                note = "Prepared initial reference frame for sequence chaining."
            else:
                note = "Sequence mode active without a starting reference; chaining begins from scene one."
            await report_progress(
                request,
                "planning",
                note,
                progress=0.2,
            )
        elif request.use_scene_images:
            scene_images = prepare_scene_start_images(
                Path(request.scene_image_dir),
                tmp_dir,
                len(plans),
                request.resolution,
            )
            await report_progress(
                request,
                "planning",
                f"Prepared {len(plans)} prompt file(s) and start screens.",
                progress=0.2,
            )
        elif request.generate_ai_first_frame:
            scene_images = []
            await report_progress(
                request,
                "planning",
                f"Prepared {len(plans)} prompt file(s); AI first-frame generation enabled.",
                progress=0.2,
            )
        else:
            scene_images = []
            await report_progress(
                request,
                "planning",
                f"Prepared {len(plans)} prompt file(s); scene start images disabled.",
                progress=0.2,
            )
        total_assigned = sum(plan.duration_seconds for plan in plans)
        await ctx.set_shared_state("scene_manifest", plans)
        await ctx.set_shared_state("scene_images", scene_images)
        await ctx.yield_output(
            f"Director planned {len(plans)} scene(s) totaling {total_assigned}s based on script detail."
        )
        await ctx.send_message(
            ScenePlanBatch(plans=plans, assigned_total_seconds=total_assigned)
        )


class Sora2Agent(Executor):
    """Generate each clip concurrently via the configured video backend (Sora2, Kling, or Jimeng)."""

    def __init__(self) -> None:
        super().__init__(id="sora2_agent")

    @staticmethod
    def _is_moderation_block(message: str) -> bool:
        lowered = message.lower()
        return "moderation_blocked" in lowered or "blocked by our moderation system" in lowered

    @handler
    async def handle(
        self, batch: ScenePlanBatch, ctx: WorkflowContext[SceneRenderBatch]
    ) -> None:
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        scene_images: Sequence[str] = await ctx.get_shared_state("scene_images")  # type: ignore
        scenes_dir = Path(request.scenes_dir)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        if request.generation_mode == "sequence":
            renders = await self._render_sequence(batch, ctx, request, scenes_dir, scene_images)
        else:
            renders = await self._render_parallel(batch, ctx, request, scenes_dir, scene_images)

        await ctx.send_message(
            SceneRenderBatch(
                renders=renders,
                assigned_total_seconds=batch.assigned_total_seconds,
            )
        )

    async def _render_parallel(
        self,
        batch: ScenePlanBatch,
        ctx: WorkflowContext[SceneRenderBatch],
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> list[SceneRender]:
        semaphore = asyncio.Semaphore(max(1, request.thread_count))
        provider_label = request.video_provider.upper()
        await report_progress(
            request,
            "rendering",
            f"Rendering {len(batch.plans)} scene(s) with up to {request.thread_count} concurrent {provider_label} jobs.",
            progress=0.25,
        )

        errors: list[tuple[ScenePlan, Exception]] = []

        async def render_scene(plan: ScenePlan) -> tuple[str, ScenePlan | SceneRender, Exception | None]:
            async with semaphore:
                try:
                    render = await self._invoke_scene(plan, request, scenes_dir, scene_images)
                    return ("ok", render, None)
                except asyncio.CancelledError:  # pragma: no cover - propagation only
                    raise
                except Exception as exc:
                    return ("error", plan, exc)

        tasks = [asyncio.create_task(render_scene(plan)) for plan in batch.plans]
        renders: list[SceneRender] = []
        for task in asyncio.as_completed(tasks):
            try:
                outcome, payload, exc = await task
            except asyncio.CancelledError:  # pragma: no cover - propagation only
                raise
            if outcome == "ok":
                render = payload  # type: ignore[assignment]
                assert isinstance(render, SceneRender)
                renders.append(render)
                await record_scene_completion(request, render)
                await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")
            else:
                assert isinstance(payload, ScenePlan)
                assert exc is not None
                errors.append((payload, exc))
                logger.error("Render failed for %s: %s", payload.label, exc)
                if request.job_id:
                    await WORKFLOW_JOB_STORE.add_message(
                        request.job_id,
                        "failed",
                        f"{payload.label} failed after retries: {exc}",
                    )

        renders.sort(key=lambda item: item.order)
        if errors and request.job_id:
            for plan, exc in errors:
                try:
                    await WORKFLOW_JOB_STORE.record_scene_failure(request.job_id, plan, str(exc))
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Unable to record failed scene %s for job %s", plan.label, request.job_id)
        if errors:
            details = "; ".join(f"{plan.label}: {exc}" for plan, exc in errors if plan.label)
            raise RuntimeError(f"{len(errors)} scene(s) failed: {details or 'Unknown scenes'}")
        return renders

    async def _render_sequence(
        self,
        batch: ScenePlanBatch,
        ctx: WorkflowContext[SceneRenderBatch],
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> list[SceneRender]:
        provider_label = request.video_provider.upper()
        await report_progress(
            request,
            "rendering",
            f"Sequence mode: rendering {len(batch.plans)} scene(s) one after another with {provider_label} for visual continuity.",
            progress=0.25,
        )
        next_reference = request.sequence_initial_image or (scene_images[0] if scene_images else None)
        frame_dir = Path(request.tmp_dir) / "sequence_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        renders: list[SceneRender] = []
        errors: list[tuple[ScenePlan, Exception]] = []
        for plan in batch.plans:
            try:
                render = await self._invoke_scene(
                    plan,
                    request,
                    scenes_dir,
                    scene_images,
                    reference_override=next_reference,
                )
                renders.append(render)
                await record_scene_completion(request, render)
                await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")
                frame_path = frame_dir / f"scene_{plan.order:02d}_tail.png"
                try:
                    captured = await extract_last_frame(Path(render.video_path), frame_path)
                    render.last_frame_path = str(captured)
                    next_reference = str(captured)
                except Exception as exc:  # pragma: no cover - diagnostic logging only
                    logger.warning("Failed to capture last frame for %s: %s", render.label, exc)
                    next_reference = None
            except asyncio.CancelledError:  # pragma: no cover - propagation only
                raise
            except Exception as exc:
                errors.append((plan, exc))
                logger.error("Sequence render failed for %s: %s", plan.label, exc)
                next_reference = None
                if request.job_id:
                    try:
                        await WORKFLOW_JOB_STORE.record_scene_failure(request.job_id, plan, str(exc))
                    except Exception:  # pragma: no cover - defensive logging
                        logger.exception(
                            "Unable to record failed scene %s for job %s", plan.label, request.job_id
                        )
                    await WORKFLOW_JOB_STORE.add_message(
                        request.job_id,
                        "failed",
                        f"{plan.label} failed after retries: {exc}",
                    )

        renders.sort(key=lambda item: item.order)
        if errors:
            details = "; ".join(f"{plan.label}: {exc}" for plan, exc in errors if plan.label)
            raise RuntimeError(f"{len(errors)} scene(s) failed: {details or 'Unknown scenes'}")
        return renders

    async def _prepare_reference_image(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scene_images: Sequence[str],
        reference_override: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        input_reference = reference_override
        first_frame_path: Optional[str] = None
        if request.generate_ai_first_frame:
            await report_progress(
                request,
                "rendering",
                f"Generating AI first frame for {plan.label}.",
            )
            try:
                first_frame_path = await generate_first_frame_image(plan, request)
                input_reference = first_frame_path
            except Exception as exc:
                logger.error("First-frame generation failed for %s: %s", plan.label, exc)
                raise

        if not input_reference and request.use_scene_images:
            scene_index = plan.order - 1
            if scene_index < 0 or scene_index >= len(scene_images):
                raise RuntimeError(
                    f"Missing prepared start screen for {plan.label} (index {scene_index}). Verify plan/image alignment."
                )
            input_reference = scene_images[scene_index]
        return input_reference, first_frame_path

    async def _invoke_scene(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
        reference_override: Optional[str] = None,
    ) -> SceneRender:
        if request.video_provider == "kling":
            return await self._invoke_kling(plan, request, scenes_dir, scene_images, reference_override)
        if request.video_provider == "jimeng":
            return await self._invoke_jimeng(plan, request, scenes_dir, scene_images, reference_override)
        return await self._invoke_sora(plan, request, scenes_dir, scene_images, reference_override)

    async def _invoke_jimeng(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
        reference_override: Optional[str] = None,
    ) -> SceneRender:
        scene_basename = f"scene_{plan.order:02d}.mp4"
        video_path = scenes_dir / scene_basename
        await report_progress(
            request,
            "rendering",
            f"Jimeng: starting render for {plan.label}.",
        )

        input_reference, first_frame_path = await self._prepare_reference_image(
            plan, request, scene_images, reference_override
        )
        result = await render_jimeng_clip(
            prompt_text=plan.prompt_text,
            duration_seconds=plan.duration_seconds,
            resolution=request.resolution,
            output_path=video_path,
            primary_image_path=input_reference,
        )

        log_path = video_path.with_suffix(".json")
        client_log_path = video_path.with_suffix(".client.log")
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

        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=result.task_id,
            first_frame_path=first_frame_path,
        )

    async def _invoke_sora(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
        reference_override: Optional[str] = None,
    ) -> SceneRender:
        scene_basename = f"scene_{plan.order:02d}.mp4"
        video_path = scenes_dir / scene_basename
        client_path = Path(request.sora_client_path)
        if not client_path.is_file():
            raise FileNotFoundError(f"sora-openai-client.py not found at {client_path}")
        await report_progress(
            request,
            "rendering",
            f"Starting render for {plan.label}.",
        )
        env = os.environ.copy()
        env.update(
            {
                "VIDEO_OUTPUT_PATH": str(video_path),
                "VIDEO_DOWNLOAD_VARIANT": request.download_variant,
            }
        )
        cmd = [
            sys.executable,
            str(client_path),
            "--prompt-file",
            plan.prompt_path,
            "--resolution",
            request.resolution,
            "--duration",
            str(plan.duration_seconds),
        ]

        input_reference, first_frame_path = await self._prepare_reference_image(
            plan, request, scene_images, reference_override
        )
        if input_reference:
            cmd.extend(["--input-image", input_reference])

        attempts = max(1, request.blocked_retry_attempts)
        attempt = 0
        stdout = ""
        stderr = ""
        last_error: RuntimeError | None = None
        while attempt < attempts:
            attempt += 1
            try:
                stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
                break
            except asyncio.CancelledError:  # pragma: no cover - propagation only
                raise
            except RuntimeError as exc:
                last_error = exc
                message = str(exc)
                if not self._is_moderation_block(message):
                    raise
                if attempt >= attempts:
                    await report_progress(
                        request,
                        "failed",
                        f"{plan.label} was blocked by moderation after {attempts} attempt(s).",
                    )
                    raise RuntimeError(
                        f"{plan.label} was blocked by moderation after {attempts} attempt(s)."
                    ) from exc
                await report_progress(
                    request,
                    "rendering",
                    f"{plan.label} blocked by moderation; retrying ({attempt}/{attempts}).",
                )
                logger.warning(
                    "Moderation blocked %s (attempt %s/%s); retrying.", plan.label, attempt, attempts
                )
                await asyncio.sleep(1.0)
        else:  # pragma: no cover - defensive guard
            raise last_error or RuntimeError(f"{plan.label} failed to render.")

        client_log_path = video_path.with_suffix(".client.log")
        client_log_path.write_text(
            f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n",
            encoding="utf-8",
        )

        if not video_path.is_file():
            raise FileNotFoundError(
                f"Sora client reported success but {video_path} was not created. "
                "Check your Azure credentials and VIDEO_OUTPUT_PATH permissions."
            )

        sora_log_path = video_path.with_suffix(".json")
        video_id = None
        if sora_log_path.is_file():
            try:
                metadata = json.loads(sora_log_path.read_text(encoding="utf-8"))
                video_id = metadata.get("video_id") or metadata.get("job_id")
            except json.JSONDecodeError:
                video_id = None

        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=video_id,
            first_frame_path=first_frame_path,
        )

    async def _invoke_kling(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
        reference_override: Optional[str] = None,
    ) -> SceneRender:
        scene_basename = f"scene_{plan.order:02d}.mp4"
        video_path = scenes_dir / scene_basename
        await report_progress(
            request,
            "rendering",
            f"Kling: starting render for {plan.label}.",
        )

        input_reference, first_frame_path = await self._prepare_reference_image(
            plan, request, scene_images, reference_override
        )
        tail_reference: Optional[str] = None
        try:
            tail_reference = prepare_scene_tail_image(
                Path(request.scene_image_dir),
                Path(request.tmp_dir),
                plan.order,
                request.resolution,
            )
        except Exception as exc:  # pragma: no cover - best-effort helper
            logger.debug("Unable to prepare tail image for %s: %s", plan.label, exc)

        external_id = (
            f"{request.job_id or 'cli'}-scene{plan.order:02d}"
        )
        result = await render_kling_clip(
            prompt_text=plan.prompt_text,
            duration_seconds=plan.duration_seconds,
            resolution=request.resolution,
            output_path=video_path,
            primary_image_path=input_reference,
            tail_image_path=tail_reference,
            extra_image_paths=[],
            external_task_id=external_id,
        )

        log_path = video_path.with_suffix(".json")
        client_log_path = video_path.with_suffix(".client.log")
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

        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            prompt_text=plan.prompt_text,
            video_id=result.task_id,
            first_frame_path=first_frame_path,
        )


class ComposerAgent(Executor):
    """Concatenate clips with ffmpeg, preserving the scripted runtime."""

    def __init__(self) -> None:
        super().__init__(id="composer_agent")

    @handler
    async def handle(
        self, batch: SceneRenderBatch, ctx: WorkflowContext[None, str]
    ) -> None:  # type: ignore
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        final_video = Path(request.final_video_path)
        final_video.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(request.tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        concat_list = tmp_dir / "concat.txt"
        await mark_composing(
            request, "Preparing final composition with ffmpeg concat."
        )

        with open(concat_list, "w", encoding="utf-8") as fh:
            for render in batch.renders:
                fh.write(escape_for_concat(Path(render.video_path)) + "\n")

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

        manifest = summarize_manifest(
            batch.renders,
            final_video,
            batch.assigned_total_seconds,
            request.resolution,
        )
        manifest_path = final_video.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        await mark_completed(request, str(final_video), manifest)

        await ctx.yield_output(f"Composer saved final video to {final_video}")
        await ctx.yield_output(f"Manifest stored at {manifest_path}")
        await ctx.yield_output("Workflow complete!")


__all__ = ["ComposerAgent", "DirectorAgent", "Sora2Agent"]
