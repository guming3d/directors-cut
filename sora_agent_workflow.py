"""Three-agent workflow for stitching a 35-second Azure Sora video."""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

# -------------------------
# Paths & constants
# -------------------------

ROOT = Path(__file__).parent
SCRIPT_PATH = ROOT / "Chapters" / "video_script.txt"
OUTPUT_DIR = ROOT / "output"
PROMPTS_DIR = OUTPUT_DIR / "prompts"
SCENES_DIR = OUTPUT_DIR / "scenes"
FINAL_VIDEO_PATH = OUTPUT_DIR / "sora_story_compilation.mp4"
SORA_CLIENT = ROOT / "sora-openai-client.py"
TMP_DIR = OUTPUT_DIR / "tmp"

ALLOWED_DURATIONS = (4, 8, 12)
HEADER_PATTERN = re.compile(r"^\*\*\s*(Scene\s+(\d+)[^*]*)\*\*$", re.IGNORECASE)
DURATION_PATTERN = re.compile(r"(\d+)(?:\s*[–-]\s*(\d+))?\s*s", re.IGNORECASE)


def _configure_logging() -> logging.Logger:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = OUTPUT_DIR / "sora_workflow.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("sora_workflow")
    if logger.handlers:
        return logger
    logger.setLevel(level)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.propagate = False
    logger.debug("Logging configured at %s", level_name)
    return logger


logger = _configure_logging()


# -------------------------
# Data models
# -------------------------


class VideoWorkflowRequest(BaseModel):
    """Configuration for the multi-agent video workflow."""

    script_path: str = Field(default=str(SCRIPT_PATH), description="Path to the master video script.")
    output_dir: str = Field(default=str(OUTPUT_DIR), description="Root output folder.")
    prompt_dir: str = Field(default=str(PROMPTS_DIR), description="Directory for generated prompt text files.")
    scenes_dir: str = Field(default=str(SCENES_DIR), description="Directory for downloaded scene MP4s.")
    tmp_dir: str = Field(default=str(TMP_DIR), description="Directory for intermediate artifacts.")
    final_video_path: str = Field(default=str(FINAL_VIDEO_PATH), description="Final stitched MP4 path.")
    sora_client_path: str = Field(default=str(SORA_CLIENT), description="Path to sora-openai-client.py.")
    target_seconds: int = Field(default=35, ge=4, description="Desired final runtime.")
    resolution: str = Field(default=os.getenv("VIDEO_SIZE", "1280x720"), description="Clip resolution.")
    thread_count: int = Field(
        default=int(os.getenv("SORA_WORKFLOW_THREADS", "3")),
        ge=1,
        le=10,
        description="Parallel Sora job count.",
    )
    download_variant: str = Field(
        default=os.getenv("VIDEO_DOWNLOAD_VARIANT", "video"),
        description="Variant name passed to sora-openai-client.",
    )


@dataclass
class SceneDraft:
    order: int
    scene_number: int
    title: str
    description_lines: list[str]
    suggested_seconds: int | None


@dataclass
class ScenePlan:
    order: int
    label: str
    duration_seconds: int
    description: str
    prompt_path: str
    prompt_text: str


@dataclass
class ScenePlanBatch:
    plans: list[ScenePlan]
    assigned_total_seconds: int
    target_seconds: int


@dataclass
class SceneRender:
    order: int
    label: str
    duration_seconds: int
    prompt_path: str
    video_path: str
    log_path: str | None
    client_log_path: str | None


@dataclass
class SceneRenderBatch:
    renders: list[SceneRender]
    assigned_total_seconds: int


# -------------------------
# Helpers
# -------------------------


def read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required script file: {path}")
    return path.read_text(encoding="utf-8").strip()


def parse_script(script_text: str) -> list[SceneDraft]:
    drafts: list[SceneDraft] = []
    current: SceneDraft | None = None
    order = 0

    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        header_match = HEADER_PATTERN.match(line)
        if header_match:
            if current:
                drafts.append(current)
            order += 1
            header_text = header_match.group(1)
            number_text = header_match.group(2)
            title_match = re.search(r"\((.*?)\)", header_text)
            title = title_match.group(1) if title_match else header_text
            suggested = parse_duration_hint(header_text)
            current = SceneDraft(
                order=order,
                scene_number=int(number_text or order),
                title=title.strip(),
                description_lines=[],
                suggested_seconds=suggested,
            )
            continue
        if current and line:
            line = line.lstrip("*").strip()
            if line:
                current.description_lines.append(line)

    if current:
        drafts.append(current)
    return drafts


def parse_duration_hint(text: str) -> int | None:
    normalized = text.replace("—", "-")
    match = DURATION_PATTERN.search(normalized)
    if not match:
        return None
    first = int(match.group(1))
    second = match.group(2)
    if second:
        return (first + int(second)) // 2
    return first


def pick_allowed_duration(suggested: int | None) -> int:
    if suggested is None:
        return 4
    if suggested <= 4:
        return 4
    if suggested <= 8:
        return 8
    return 12


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "scene"


def build_prompt_text(title: str, description: str, duration: int, resolution: str) -> str:
    bullet_lines = "\n".join(f"- {line.strip()}" for line in description.splitlines() if line.strip())
    return (
        f"Scene title: {title}\n"
        f"Clip duration: {duration} seconds (limit choices: {ALLOWED_DURATIONS}).\n"
        f"Output resolution: {resolution} portrait.\n"
        "Character continuity: keep the same heroine (Brown hair, casual flight outfit) and hero (casual outfit) across clips.\n"
        "Visual direction:\n"
        f"{bullet_lines}\n"
        "Cinematic tone: energetic, emotional, with tasteful motion graphics.\n"
        "Audio: ambient sound design only; no background music."
    )


def escape_for_concat(path: Path) -> str:
    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


async def run_subprocess(cmd: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[str, str]:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await process.communicate()
    stdout = stdout_bytes.decode(errors="ignore")
    stderr = stderr_bytes.decode(errors="ignore")
    if process.returncode != 0:
        raise RuntimeError(f"Command {' '.join(cmd)} failed ({process.returncode}): {stderr or stdout}")
    return stdout, stderr


def summarize_manifest(renders: list[SceneRender], final_video: Path, total_seconds: int, target_seconds: int) -> dict[str, Any]:
    return {
        "final_video": str(final_video),
        "target_seconds": target_seconds,
        "assigned_seconds": total_seconds,
        "scenes": [
            {
                "order": render.order,
                "label": render.label,
                "duration_seconds": render.duration_seconds,
                "prompt_path": render.prompt_path,
                "video_path": render.video_path,
                "sora_log_path": render.log_path,
                "client_capture": render.client_log_path,
            }
            for render in renders
        ],
    }


# -------------------------
# Agents
# -------------------------


class DirectorAgent(Executor):
    """Break the master script into Sora-ready scene prompts."""

    def __init__(self) -> None:
        super().__init__(id="director_agent")

    @handler
    async def handle(self, request: VideoWorkflowRequest, ctx: WorkflowContext[ScenePlanBatch]) -> None:
        await ctx.set_shared_state("request", request)
        script_text = read_text(Path(request.script_path))
        drafts = parse_script(script_text)
        if not drafts:
            raise RuntimeError("No scene headers found in the script. Ensure it follows **Scene N (...)** format.")

        prompt_dir = Path(request.prompt_dir)
        prompt_dir.mkdir(parents=True, exist_ok=True)

        plans: list[ScenePlan] = []
        for idx, draft in enumerate(drafts, start=1):
            duration = pick_allowed_duration(draft.suggested_seconds)
            description = "\n".join(draft.description_lines)
            slug = slugify(f"{draft.scene_number:02d}-{draft.title}")
            prompt_path = prompt_dir / f"scene_{idx:02d}_{slug}.txt"
            prompt_text = build_prompt_text(draft.title, description, duration, request.resolution)
            prompt_path.write_text(prompt_text + "\n", encoding="utf-8")

            plans.append(
                ScenePlan(
                    order=idx,
                    label=f"Scene {draft.scene_number}: {draft.title}",
                    duration_seconds=duration,
                    description=description,
                    prompt_path=str(prompt_path),
                    prompt_text=prompt_text,
                )
            )

        total_assigned = sum(plan.duration_seconds for plan in plans)
        await ctx.set_shared_state("scene_manifest", plans)
        await ctx.yield_output(
            f"Director planned {len(plans)} scene(s) for {total_assigned}s "
            f"(target {request.target_seconds}s, composer trims excess)."
        )
        await ctx.send_message(
            ScenePlanBatch(plans=plans, assigned_total_seconds=total_assigned, target_seconds=request.target_seconds)
        )


class Sora2Agent(Executor):
    """Generate each clip concurrently via the sora-openai-client."""

    def __init__(self) -> None:
        super().__init__(id="sora2_agent")

    @handler
    async def handle(self, batch: ScenePlanBatch, ctx: WorkflowContext[SceneRenderBatch]) -> None:
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        scenes_dir = Path(request.scenes_dir)
        scenes_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(max(1, request.thread_count))

        async def render_scene(plan: ScenePlan) -> SceneRender:
            async with semaphore:
                return await self._invoke_sora(plan, request, scenes_dir)

        tasks = [asyncio.create_task(render_scene(plan)) for plan in batch.plans]
        renders: list[SceneRender] = []
        for task in asyncio.as_completed(tasks):
            render = await task
            renders.append(render)
            await ctx.yield_output(f"Sora completed {render.label} -> {render.video_path}")

        renders.sort(key=lambda item: item.order)
        await ctx.send_message(SceneRenderBatch(renders=renders, assigned_total_seconds=batch.assigned_total_seconds))

    async def _invoke_sora(
        self,
        plan: ScenePlan,
        request: VideoWorkflowRequest,
        scenes_dir: Path,
    ) -> SceneRender:
        scene_basename = f"scene_{plan.order:02d}.mp4"
        video_path = scenes_dir / scene_basename
        client_path = Path(request.sora_client_path)
        if not client_path.is_file():
            raise FileNotFoundError(f"sora-openai-client.py not found at {client_path}")
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

        stdout, stderr = await run_subprocess(cmd, cwd=ROOT, env=env)
        client_log_path = video_path.with_suffix(".client.log")
        client_log_path.write_text(
            "STDOUT:\n"
            f"{stdout}\n"
            "STDERR:\n"
            f"{stderr}\n",
            encoding="utf-8",
        )

        if not video_path.is_file():
            raise FileNotFoundError(
                f"Sora client reported success but {video_path} was not created. "
                "Check your Azure credentials and VIDEO_OUTPUT_PATH permissions."
            )

        sora_log_path = video_path.with_suffix(".json")
        return SceneRender(
            order=plan.order,
            label=plan.label,
            duration_seconds=plan.duration_seconds,
            prompt_path=plan.prompt_path,
            video_path=str(video_path),
            log_path=str(sora_log_path) if sora_log_path.is_file() else None,
            client_log_path=str(client_log_path),
        )


class ComposerAgent(Executor):
    """Concatenate clips with ffmpeg and trim/pad to the target runtime."""

    def __init__(self) -> None:
        super().__init__(id="composer_agent")

    @handler
    async def handle(self, batch: SceneRenderBatch, ctx: WorkflowContext[None, str]) -> None:  # type: ignore
        request: VideoWorkflowRequest = await ctx.get_shared_state("request")  # type: ignore
        final_video = Path(request.final_video_path)
        final_video.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(request.tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        concat_list = tmp_dir / "concat.txt"
        temp_full = tmp_dir / "temp_full.mp4"

        with open(concat_list, "w", encoding="utf-8") as fh:
            for render in batch.renders:
                fh.write(escape_for_concat(Path(render.video_path)) + "\n")

        await run_subprocess(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(temp_full)]
        )

        await run_subprocess(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(temp_full),
                "-t",
                str(request.target_seconds),
                "-c",
                "copy",
                str(final_video),
            ]
        )

        manifest = summarize_manifest(
            batch.renders,
            final_video,
            batch.assigned_total_seconds,
            request.target_seconds,
        )
        manifest_path = final_video.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        await ctx.yield_output(f"Composer saved final video to {final_video}")
        await ctx.yield_output(f"Manifest stored at {manifest_path}")
        await ctx.yield_output("Workflow complete!")


# -------------------------
# Workflow builder
# -------------------------


def create_sora_video_workflow():
    director = DirectorAgent()
    sora_agent = Sora2Agent()
    composer = ComposerAgent()

    builder = WorkflowBuilder(
        name="Azure Sora multi-agent workflow",
        description="Director -> Sora2 -> Composer pipeline that follows the provided storyboard script.",
    )
    (
        builder.set_start_executor(director)
        .add_edge(director, sora_agent)
        .add_edge(sora_agent, composer)
    )
    return builder.build()


workflow = create_sora_video_workflow()


async def _run_once(request: VideoWorkflowRequest) -> None:
    result = await workflow.run(request)
    for output in result.get_outputs():
        if output:
            print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Three-agent Azure Sora workflow runner.")
    parser.add_argument("--script-path", default=str(SCRIPT_PATH), help="Path to video script (default: Chapters/video_script.txt).")
    parser.add_argument("--target-seconds", type=int, default=35, help="Final video runtime target.")
    parser.add_argument("--threads", type=int, default=3, help="Parallel Sora jobs (default 3).")
    parser.add_argument("--resolution", default="1280x720", help="Clip resolution passed to Sora.")
    parser.add_argument("--final-video", default=str(FINAL_VIDEO_PATH), help="Output MP4 path.")
    parser.add_argument("--devui", action="store_true", help="Launch Agent Framework DevUI.")
    parser.add_argument("--devui-port", type=int, default=8091, help="Port for DevUI (default 8091).")
    args = parser.parse_args()

    request = VideoWorkflowRequest(
        script_path=args.script_path,
        target_seconds=args.target_seconds,
        thread_count=args.threads,
        resolution=args.resolution,
        final_video_path=args.final_video,
    )

    if args.devui:
        from agent_framework.devui import serve

        serve(entities=[workflow], port=args.devui_port, auto_open=True)
        return

    asyncio.run(_run_once(request))


if __name__ == "__main__":
    main()
