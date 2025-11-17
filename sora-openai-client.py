import argparse
import asyncio
import json
import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

# Load environment variables from .env file with override=True
load_dotenv(override=True)

# Resolve endpoint, deployment, and API key using the same environment defaults
endpoint = os.getenv(
    "ENDPOINT_URL", "https://ai-minggu9321ai222939745136.openai.azure.com/"
).rstrip("/")
deployment = os.getenv("DEPLOYMENT_NAME", "sora-2")
api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

if not api_key:
    raise RuntimeError("Set AZURE_OPENAI_API_KEY or OPENAI_API_KEY before running.")

VIDEO_PROMPT = os.getenv(
    "VIDEO_PROMPT",
    """
Style: 1970s romantic drama, shot on 35 mm film with natural flares, soft focus, and warm halation. Slight gate weave and handheld micro-shake evoke vintage intimacy. Warm Kodak-inspired grade; light halation on bulbs; film grain and soft vignette for period authenticity.At golden hour, a brick tenement rooftop transforms into a small stage. Laundry lines strung with white sheets sway in the wind, catching the last rays of sunlight. Strings of mismatched fairy bulbs hum faintly overhead. A young woman in a flowing red silk dress dances barefoot, curls glowing in the fading light. Her partner — sleeves rolled, suspenders loose — claps along, his smile wide and unguarded. Below, the city hums with car horns, subway tremors, and distant laughter.Cinematography:Camera: medium-wide shot, slow dolly-in from eye levelLens: 40 mm spherical; shallow focus to isolate the couple from skylineLighting: golden natural key with tungsten bounce; edge from fairy bulbsMood: nostalgic, tender, cinematicActions:- She spins; her dress flares, catching sunlight.- Woman (laughing): "See? Even the city dances with us tonight."- He steps in, catches her hand, and dips her into shadow.- Man (smiling): "Only because you lead."- Sheets drift across frame, briefly veiling the skyline before parting again.Background Sound:Natural ambience only: faint wind, fabric flutter, street noise, muffled music. No added score
""",
).strip()

default_seconds = (os.getenv("VIDEO_DURATION_SECONDS") or "8").strip()
default_size = (os.getenv("VIDEO_SIZE") or "720x1280").strip()

def resolve_reference_path(cli_path: Optional[str]) -> Optional[str]:
    """Return the CLI-supplied reference image path when provided."""
    if cli_path:
        stripped = cli_path.strip()
        if stripped:
            return stripped
    return None

def load_prompt(prompt_file=None) -> str:
    """Resolve prompt from CLI file path, env path, or env/default string.

    Priority:
      1) --prompt-file argument (path to UTF-8 text)
      2) VIDEO_PROMPT_PATH environment variable
      3) VIDEO_PROMPT environment variable/default string in this file
    """
    # 1) CLI argument path
    if prompt_file:
        path = str(prompt_file).strip()
        if not os.path.isfile(path):
            raise FileNotFoundError(f'Prompt file "{path}" not found.')
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError(f'Prompt file "{path}" is empty.')
        return content

    # 2) Env path
    env_path = (os.getenv("VIDEO_PROMPT_PATH") or "").strip()
    if env_path:
        if not os.path.isfile(env_path):
            raise FileNotFoundError(
                f'Prompt file "{env_path}" not found (VIDEO_PROMPT_PATH).'
            )
        with open(env_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            raise ValueError(
                f'Prompt file "{env_path}" is empty (VIDEO_PROMPT_PATH).'
            )
        return content

    # 3) Fallback to env string default
    return VIDEO_PROMPT

 

async def main() -> None:
    """Submit a Sora job and wait for completion."""
    # CLI: allow selecting a prompt file
    parser = argparse.ArgumentParser(description="Sora video generation client")
    parser.add_argument(
        "-p",
        "--prompt-file",
        dest="prompt_file",
        help="Path to a text file containing the video prompt (UTF-8)",
    )
    parser.add_argument(
        "-i",
        "--input-image",
        dest="input_image",
        help="Path to an input reference image file",
    )
    parser.add_argument(
        "-r",
        "--resolution",
        dest="resolution",
        help="Override the output resolution, e.g. 1280x720",
    )
    parser.add_argument(
        "-t",
        "--duration",
        dest="duration",
        help="Override the output duration in seconds (choose 4, 8, or 12)",
    )
    args = parser.parse_args()

    # Resolve effective prompt text
    prompt_text = load_prompt(args.prompt_file)
    user_supplied_image = bool(args.input_image and args.input_image.strip())
    reference_path = resolve_reference_path(args.input_image)
    cli_resolution = (args.resolution or "").strip()
    video_size = cli_resolution or default_size
    if not video_size:
        raise ValueError(
            "Video resolution is empty. Set VIDEO_SIZE or supply --resolution."
        )
    allowed_durations = ("4", "8", "12")
    cli_duration = (args.duration or "").strip()
    if cli_duration and cli_duration not in allowed_durations:
        raise ValueError(
            f"Invalid duration '{cli_duration}'. Choose from: {', '.join(allowed_durations)}."
        )
    video_seconds = cli_duration or default_seconds or "8"
    if video_seconds not in allowed_durations:
        raise ValueError(
            f"Invalid duration '{video_seconds}' from environment. Choose from: {', '.join(allowed_durations)}."
        )

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=f"{endpoint}/openai/v1/",
    )

    try:
        create_kwargs = {
            "model": deployment,
            "prompt": prompt_text,
            "size": video_size,
            "seconds": video_seconds,
        }
        if reference_path:
            if not os.path.isfile(reference_path):
                raise FileNotFoundError(
                    f'Reference file "{reference_path}" not found. '
                    "Set VIDEO_REFERENCE_PATH or place the file."
                )
            with open(reference_path, "rb") as reference_file:
                video = await client.videos.create_and_poll(
                    input_reference=reference_file, **create_kwargs
                )
        else:
            video = await client.videos.create_and_poll(**create_kwargs)

        job_id = getattr(video, "id", None)
        status = getattr(video, "status", None)

        if status == "completed":
            print("Video generation completed successfully.", f"Job ID: {job_id}")
            print(video)

            if not job_id:
                raise RuntimeError("Job completed but no ID returned; cannot download video.")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_output_name = f"output_{timestamp}.mp4"
            env_output_path = (os.getenv("VIDEO_OUTPUT_PATH") or "").strip()
            if env_output_path:
                output_path = env_output_path
            else:
                output_dir = (os.getenv("VIDEO_OUTPUT_DIR") or "output").strip() or "output"
                output_path = os.path.join(output_dir, default_output_name)
            download_variant = os.getenv("VIDEO_DOWNLOAD_VARIANT", "video").strip() or "video"

            # The client returns a streaming file-like helper; write it straight to disk.
            content = await client.videos.download_content(job_id, variant=download_variant)
            output_dirname = os.path.dirname(output_path) or "."
            os.makedirs(output_dirname, exist_ok=True)
            content.write_to_file(output_path)

            print(f'Saved {download_variant} variant to "{output_path}".')

            log_data = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "prompt": prompt_text,
                "output_file": output_path,
                "output_filename": os.path.basename(output_path),
                "video_id": job_id,
                "status": status,
            }
            if user_supplied_image and reference_path:
                log_data["input_image"] = reference_path
            log_path = os.path.splitext(output_path)[0] + ".json"
            log_dirname = os.path.dirname(log_path) or "."
            os.makedirs(log_dirname, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as log_file:
                json.dump(log_data, log_file, indent=2)
            print(f'Wrote run log to "{log_path}".')
        else:
            print(
                "Video generation did not complete successfully.",
                f"Status: {status}",
                f"Job ID: {job_id}",
            )
            error_info = getattr(video, "error", None)
            if error_info:
                print("Error details:", error_info)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
