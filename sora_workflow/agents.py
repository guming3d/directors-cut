"""Agent definitions for the Sora workflow."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from agent_framework import Executor, WorkflowContext, handler

from .config import ROOT, SCENE_IMAGE_DIR, logger
from .images import prepare_scene_start_images
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
from .prompts import build_prompt_text, parse_script, refine_scene_description
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
        script_text = read_text(Path(request.script_path))
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
            refined_description = await refine_scene_description(draft.title, description, duration)
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
            note = (
                "Prepared initial reference frame for sequence chaining."
                if initial_reference
                else "Sequence mode active without a starting reference; chaining begins from scene one."
            )
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
    """Generate each clip concurrently via the sora-openai-client."""

    def __init__(self) -> None:
        super().__init__(id="sora2_agent")

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
        await report_progress(
            request,
            "rendering",
            f"Rendering {len(batch.plans)} scene(s) with up to {request.thread_count} concurrent Sora jobs.",
            progress=0.25,
        )

        async def render_scene(plan: ScenePlan) -> SceneRender:
            async with semaphore:
                return await self._invoke_sora(plan, request, scenes_dir, scene_images)

        tasks = [asyncio.create_task(render_scene(plan)) for plan in batch.plans]
        renders: list[SceneRender] = []
        for task in asyncio.as_completed(tasks):
            render = await task
            renders.append(render)
            await record_scene_completion(request, render)
            await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")

        renders.sort(key=lambda item: item.order)
        return renders

    async def _render_sequence(
        self,
        batch: ScenePlanBatch,
        ctx: WorkflowContext[SceneRenderBatch],
        request: VideoWorkflowRequest,
        scenes_dir: Path,
        scene_images: Sequence[str],
    ) -> list[SceneRender]:
        await report_progress(
            request,
            "rendering",
            f"Sequence mode: rendering {len(batch.plans)} scene(s) one after another for visual continuity.",
            progress=0.25,
        )
        next_reference = request.sequence_initial_image or (scene_images[0] if scene_images else None)
        frame_dir = Path(request.tmp_dir) / "sequence_frames"
        frame_dir.mkdir(parents=True, exist_ok=True)
        renders: list[SceneRender] = []
        for plan in batch.plans:
            render = await self._invoke_sora(
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

        renders.sort(key=lambda item: item.order)
        return renders

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

        input_reference = reference_override
        if not input_reference and request.use_scene_images:
            scene_index = plan.order - 1
            if scene_index < 0 or scene_index >= len(scene_images):
                raise RuntimeError(
                    f"Missing prepared start screen for {plan.label} (index {scene_index}). Verify plan/image alignment."
                )
            input_reference = scene_images[scene_index]
        if input_reference:
            cmd.extend(["--input-image", input_reference])

        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
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
