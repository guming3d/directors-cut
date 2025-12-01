# Copyright (c) Microsoft. All rights reserved.
"""End-to-end Magentic workflow for producing a 35-second stitched video with Azure Sora."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from agent_framework import (
    ChatAgent,
    HostedCodeInterpreterTool,
    MagenticAgentDeltaEvent,
    MagenticAgentMessageEvent,
    MagenticBuilder,
    MagenticFinalResultEvent,
    MagenticOrchestratorMessageEvent,
    WorkflowOutputEvent,
)
from agent_framework.azure import AzureOpenAIChatClient, AzureOpenAIResponsesClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
GUIDE_PATH = ROOT / "Chapters" / "video_guide.txt"
SCRIPT_PATH = ROOT / "Chapters" / "video_script.txt"
IMAGES_DIR = ROOT / "images_references"
EXPECTED_VIDEO_SECONDS = 35
TARGET_RESOLUTION = "720x1280"
FINAL_OUTPUT_PATH = ROOT / "output" / "brush_up_life_compilation.mp4"


@dataclass
class SceneBrief:
    scene_id: str
    title: str
    summary: str


def load_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Required context file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def extract_scene_briefs(script_text: str) -> list[SceneBrief]:
    matches: list[SceneBrief] = []
    pattern = re.compile(r"\*\*(Scene\s+\d+.*?)\*\*", flags=re.IGNORECASE)
    lines = script_text.splitlines()
    current_header: str | None = None
    buffer: list[str] = []
    for line in lines:
        header_match = pattern.match(line.strip())
        if header_match:
            if current_header:
                matches.append(_build_scene_brief(current_header, buffer))
                buffer = []
            current_header = header_match.group(1)
            continue
        if current_header:
            buffer.append(line)
    if current_header:
        matches.append(_build_scene_brief(current_header, buffer))
    return matches


def _build_scene_brief(header: str, body_lines: list[str]) -> SceneBrief:
    match = re.search(r"Scene\s+(\d+)\s*\((.*?)\)", header)
    if match:
        scene_id = match.group(1)
        title = match.group(2)
    else:
        scene_id = header.split(" ")[1]
        title = header
    summary = " ".join(line.strip("* ").strip() for line in body_lines if line.strip()).strip()
    return SceneBrief(scene_id=scene_id, title=title, summary=summary)


def list_reference_images(image_dir: Path) -> list[str]:
    if not image_dir.is_dir():
        return []
    allowed = {".png", ".jpg", ".jpeg"}
    return sorted(
        str(path.relative_to(ROOT))
        for path in image_dir.iterdir()
        if path.suffix.lower() in allowed
    )


def build_task_payload(
    guide_text: str,
    script_text: str,
    scene_briefs: list[SceneBrief],
    reference_images: list[str],
) -> str:
    scene_lines = []
    for brief in scene_briefs:
        scene_lines.append(
            f"- Scene {brief.scene_id} ({brief.title}): {brief.summary}"
        )
    images_section = "\n".join(f"  * {img}" for img in reference_images) or "  * No reference images found."
    return dedent(
        f"""
        DirectorAgent owns delivery. Use the following assets to craft a 35-second video made of multiple Sora v2 clips:

        Video guidance ({GUIDE_PATH}):
        {guide_text}

        Video script ({SCRIPT_PATH}):
        {script_text}

        Canonical scene breakdown:
        {'\n'.join(scene_lines)}

        Available character/reference images under {IMAGES_DIR}:
        {images_section}

        Production notes:
        - Total runtime must equal {EXPECTED_VIDEO_SECONDS} seconds; use multiple Sora clips (4s/8s/12s each) and direct ComposerAgent to trim/pad to the target duration.
        - All clips must be {TARGET_RESOLUTION} and portrait-friendly. StoryboardAgent must pick an image per scene and Sora2Agent must resize it to {TARGET_RESOLUTION} before invoking Sora.
        - Every scene requires a dedicated Sora prompt text file saved under `output/prompts/scene_<n>.txt`.
        - Name rendered scene videos `output/scenes/scene_<n>.mp4` and compose the final cut to {FINAL_OUTPUT_PATH}.
        - Respect the emotional beats in the script yet keep pacing snappy; mention any timing adjustments explicitly.
        """
    ).strip()


def build_agents() -> dict[str, ChatAgent]:
    director_agent = ChatAgent(
        name="DirectorAgent",
        description="Creative director who orchestrates the workflow and enforces timing and continuity.",
        instructions=dedent(
            f"""
            You are the production lead. Break the story into sections that sum to {EXPECTED_VIDEO_SECONDS} seconds by assigning each section to a Sora clip (4/8/12s choices only).
            Workflow:
            1. Summarize the plan and send each scene brief to ScreenwriterAgent sequentially, requesting Sora-ready prompts saved under `output/prompts`.
            2. After receiving structured prompts (scene_id, prompt_path, duration_seconds), call StoryboardAgent once with the aggregated list to find matching image files.
            3. Merge prompt + image selections and pass them scene-by-scene to Sora2Agent. Ensure you specify: prompt_text, prompt_path, image_path, desired_seconds, clip_output_path.
            4. When all scene MP4 paths are received, create an ordered manifest and call ComposerAgent to stitch them into {FINAL_OUTPUT_PATH}, trimming or padding to hit exactly {EXPECTED_VIDEO_SECONDS} seconds.
            5. Provide a final report listing prompts, reference images, generated clip names, and any risks.
            Communicate clearly, cite file paths, and do not skip steps.
            """
        ).strip(),
        chat_client=AzureOpenAIChatClient(),
    )

    screenwriter_agent = ChatAgent(
        name="ScreenwriterAgent",
        description="Transforms scene descriptions into detailed Sora prompt files.",
        instructions=dedent(
            """
            Receive one scene brief at a time. Produce:
            - A JSON summary with: scene_id, section_title, target_seconds (4/8/12), emotional_beats, shot_plan.
            - A Sora-ready prose prompt of 5-8 sentences focusing on visual detail, tone, motion, and dialogue.
            Write the prompt to `output/prompts/scene_<scene_id>.txt` before responding. Confirm file paths and durations.
            """
        ).strip(),
        chat_client=AzureOpenAIChatClient(),
    )

    storyboard_agent = ChatAgent(
        name="StoryboardAgent",
        description="Selects the best character reference image for each scene's first frame.",
        instructions=dedent(
            """
            Inspect `images_references/` to find files representing the leads. Use Python via the hosted tool to list files if needed.
            Return a table mapping scene_id to the chosen relative image path and rationale focused on framing and tone consistency.
            """
        ).strip(),
        chat_client=AzureOpenAIResponsesClient(),
        tools=HostedCodeInterpreterTool(),
    )

    sora_agent = ChatAgent(
        name="Sora2Agent",
        description="Runs Azure OpenAI Sora jobs with reference images and saves scene MP4s.",
        instructions=dedent(
            f"""
            For each scene you receive, you must:
            1. Resize the reference image to {TARGET_RESOLUTION} using Pillow, using aspect fill (center crop) to occupy the whole screen (save under `output/references/scene_<scene_id>.png`).
            2. Call the Azure Sora client by invoking `python backend/sora-openai-client.py --prompt-file <prompt_path> --input-image <resized_image> --resolution {TARGET_RESOLUTION} --duration <seconds>` so duration matches the plan.
            3. Move/rename the downloaded MP4 to `output/scenes/scene_<scene_id>.mp4`.
            4. Log metadata (prompt, image, job_id) into `output/scenes/scene_<scene_id>.json`.
            Use the HostedCodeInterpreterTool for Python + shell (ffmpeg, Pillow). Ensure environment variables `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_RESPONSES_DEPLOYMENT_NAME`, `ENDPOINT_URL`, `DEPLOYMENT_NAME` are set before running the client.
            Report success with paths for video, log, and resized image.
            """
        ).strip(),
        chat_client=AzureOpenAIResponsesClient(),
        tools=HostedCodeInterpreterTool(),
    )

    composer_agent = ChatAgent(
        name="ComposerAgent",
        description="Concatenates scene clips and enforces the final runtime.",
        instructions=dedent(
            f"""
            Expect a manifest containing ordered scene MP4 paths with target offsets. Use ffmpeg (available in the hosted tool) to:
            1. Generate a concat file listing the clips in order.
            2. Run: `ffmpeg -y -f concat -safe 0 -i concat_list.txt -c copy temp_full.mp4`.
            3. If runtime != {EXPECTED_VIDEO_SECONDS}s, run `ffmpeg -y -i temp_full.mp4 -t {EXPECTED_VIDEO_SECONDS} -c copy {FINAL_OUTPUT_PATH}` to trim/pad.
            4. Output a companion JSON log summarizing clip order, durations, and total runtime (store next to the final MP4).
            Verify audio continuity and report any warnings.
            """
        ).strip(),
        chat_client=AzureOpenAIResponsesClient(),
        tools=HostedCodeInterpreterTool(),
    )

    return {
        "director": director_agent,
        "screenwriter": screenwriter_agent,
        "storyboard": storyboard_agent,
        "sora": sora_agent,
        "composer": composer_agent,
    }


async def run_workflow() -> None:
    guide_text = load_text(GUIDE_PATH)
    script_text = load_text(SCRIPT_PATH)
    scene_briefs = extract_scene_briefs(script_text)
    reference_images = list_reference_images(IMAGES_DIR)

    task_payload = build_task_payload(guide_text, script_text, scene_briefs, reference_images)

    agents = build_agents()
    workflow = (
        MagenticBuilder()
        .participants(
            director=agents["director"],
            screenwriter=agents["screenwriter"],
            storyboard=agents["storyboard"],
            sora=agents["sora"],
            composer=agents["composer"],
        )
        .with_standard_manager(
            chat_client=AzureOpenAIChatClient(),
            max_round_count=100,
            max_stall_count=4,
            max_reset_count=2,
        )
        .build()
    )

    print("\nLaunching multi-agent Sora workflow...\n")

    last_stream_agent_id: str | None = None
    stream_line_open = False
    workflow_result: str | None = None

    async for event in workflow.run_stream(task_payload):
        if isinstance(event, MagenticOrchestratorMessageEvent):
            print(f"\n[MANAGER:{event.kind}]\n{getattr(event.message, 'text', '')}\n{'-' * 40}")
        elif isinstance(event, MagenticAgentDeltaEvent):
            if last_stream_agent_id != event.agent_id or not stream_line_open:
                if stream_line_open:
                    print()
                print(f"\n[STREAM:{event.agent_id}]: ", end="", flush=True)
                last_stream_agent_id = event.agent_id
                stream_line_open = True
            if event.text:
                print(event.text, end="", flush=True)
        elif isinstance(event, MagenticAgentMessageEvent):
            if stream_line_open:
                print(" (final)")
                stream_line_open = False
                print()
            if event.message is not None:
                text = (event.message.text or "").replace("\n", " ")
                print(f"\n[AGENT:{event.agent_id}] {event.message.role.value}\n{text}\n{'-' * 40}")
        elif isinstance(event, MagenticFinalResultEvent):
            print("\n" + "=" * 60)
            print("FINAL DIRECTOR REPORT")
            print("=" * 60)
            if event.message is not None:
                print(event.message.text)
        elif isinstance(event, WorkflowOutputEvent):
            workflow_result = str(event.data) if event.data is not None else None

    if stream_line_open:
        print()

    if workflow_result:
        print("\nWorkflow output payload:\n", workflow_result)


def main() -> None:
    asyncio.run(run_workflow())


if __name__ == "__main__":
    main()
