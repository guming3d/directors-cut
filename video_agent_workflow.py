"""Entry point for the Sora multi-agent workflow CLI and FastAPI app."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from backend import (
    DEFAULT_SORA_RESOLUTION,
    SCRIPT_PATH,
    create_sora_video_workflow,
    fastapi_app,
    workflow,
)
from backend.config import (
    DEFAULT_VIDEO_PROVIDER,
    FINAL_VIDEO_PATH,
    SORA_CLIP_DURATION_SECONDS,
    allowed_durations_for_provider,
    normalize_video_provider,
)
from backend.models import VideoWorkflowRequest


async def _run_once(request: VideoWorkflowRequest) -> None:
    result = await workflow.run(request)
    for output in result.get_outputs():
        if output:
            print(output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Three-agent Azure Sora workflow runner."
    )
    parser.add_argument(
        "--script-path",
        default=str(SCRIPT_PATH),
        help="Path to video script (default: Chapters/video_script.txt).",
    )
    parser.add_argument(
        "--threads", type=int, default=3, help="Parallel Sora jobs (default 3)."
    )
    parser.add_argument(
        "--resolution",
        default=DEFAULT_SORA_RESOLUTION,
        help=f"Clip resolution passed to Sora (must remain {DEFAULT_SORA_RESOLUTION}).",
    )
    parser.add_argument(
        "--final-video", default=str(FINAL_VIDEO_PATH), help="Output MP4 path."
    )
    parser.add_argument(
        "--devui", action="store_true", help="Launch Agent Framework DevUI."
    )
    parser.add_argument(
        "--devui-port", type=int, default=8091, help="Port for DevUI (default 8091)."
    )
    parser.add_argument(
        "--scene-duration",
        type=int,
        default=SORA_CLIP_DURATION_SECONDS,
        help="Seconds to assign each scene clip (Sora: 4/8/12, Kling/Jimeng: 5/10).",
    )
    parser.add_argument(
        "--ai-first-frames",
        action="store_true",
        help="Generate AI first-frame references for each scene before calling Sora2.",
    )
    parser.add_argument(
        "--video-provider",
        choices=("sora2", "kling", "jimeng"),
        default=DEFAULT_VIDEO_PROVIDER,
        help="Video backend to use for rendering scenes.",
    )
    args = parser.parse_args()

    cli_resolution = (args.resolution or "").strip() or DEFAULT_SORA_RESOLUTION
    if cli_resolution != DEFAULT_SORA_RESOLUTION:
        parser.error(
            f"--resolution must be {DEFAULT_SORA_RESOLUTION} to align with portrait workflow assets."
        )

    video_provider = normalize_video_provider(args.video_provider)
    allowed_durations = allowed_durations_for_provider(video_provider)
    if args.scene_duration not in allowed_durations:
        allowed_str = ", ".join(str(value) for value in allowed_durations)
        parser.error(
            f"{video_provider.upper()} supports scene durations of {allowed_str} seconds."
        )

    request = VideoWorkflowRequest(
        script_path=str(Path(args.script_path)),
        thread_count=args.threads,
        resolution=DEFAULT_SORA_RESOLUTION,
        final_video_path=str(args.final_video),
        scene_duration=args.scene_duration,
        generate_ai_first_frame=args.ai_first_frames,
        video_provider=video_provider,
    )

    if args.devui:
        from agent_framework.devui import serve

        serve(entities=[workflow], port=args.devui_port, auto_open=True)
        return

    asyncio.run(_run_once(request))


if __name__ == "__main__":
    main()
