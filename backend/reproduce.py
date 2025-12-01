"""Video reproduce workflow combining content understanding and Kling renders."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Sequence

from .config import (
    DEFAULT_SORA_RESOLUTION,
    KLING_ALLOWED_DURATIONS,
    REPRODUCE_DEFAULT_THREADS,
    VIDEO_REPRODUCE_JOBS_DIR,
    logger,
)
from .content_insights.client import AzureContentUnderstandingClient, ContentUnderstandingError
from .content_insights.parser import summarize_content_understanding_result
from .content_insights.service import attach_segment_frames, ensure_content_understanding_ready
from .image_generation import ImageGenerationError, get_image_generator
from .kling import KlingAPIError, render_kling_clip
from .models import ReproduceSegmentVersion, VideoInsightSegment, VideoReproduceSegment
from .prompts import render_prompt_template
from .stores import VIDEO_REPRODUCE_STORE
from .utils import escape_for_concat, run_subprocess, utc_now


FINAL_VIDEO_NAME = "kling_reproduce_compilation.mp4"


class _AsyncNoopLock:
    async def __aenter__(self):  # pragma: no cover - trivial
        return None

    async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - trivial
        return False


def reproduce_job_root(job_id: str, *, ensure_exists: bool = True) -> Path:
    path = VIDEO_REPRODUCE_JOBS_DIR / job_id
    if ensure_exists:
        path.mkdir(parents=True, exist_ok=True)
    return path


async def run_reproduce_job(job_id: str) -> None:
    try:
        ensure_content_understanding_ready()
        job_state = await VIDEO_REPRODUCE_STORE.get_job_state(job_id)
        if not job_state.video_path:
            raise RuntimeError("Uploaded video is missing from workspace.")
        source_video = Path(job_state.video_path)
        if not source_video.is_file():
            raise FileNotFoundError(f"Source video {source_video} not found.")

        await VIDEO_REPRODUCE_STORE.mark_running(job_id)
        job_dir = reproduce_job_root(job_id)
        frames_dir = job_dir / "frames"
        prompts_dir = job_dir / "prompts"
        segments_dir = job_dir / "segments"
        kling_dir = job_dir / "kling"
        stylized_dir = job_dir / "stylized_frames"
        tmp_dir = job_dir / "tmp"
        for folder in (frames_dir, prompts_dir, segments_dir, kling_dir, stylized_dir, tmp_dir):
            folder.mkdir(parents=True, exist_ok=True)

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id, 0.1, "Submitting video to Azure Content Understanding."
        )
        client = AzureContentUnderstandingClient(user_agent="sora2-workflow/reproduce")
        insight_payload = await client.analyze_video(source_video)
        _, insight_segments = summarize_content_understanding_result(insight_payload)
        if not insight_segments:
            raise RuntimeError("Content Understanding did not return any segments to reproduce.")

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id, 0.2, "Extracting start/end frames for each segment."
        )
        insight_segments = await attach_segment_frames(
            job_id,
            source_video,
            insight_segments,
            frames_dir=frames_dir,
        )

        try:
            duration_seconds, resolution = await _probe_video_metadata(source_video)
        except Exception:
            logger.warning("Unable to derive metadata for %s; using defaults.", source_video)
            duration_seconds, resolution = 0.0, DEFAULT_SORA_RESOLUTION
        await VIDEO_REPRODUCE_STORE.set_resolution(job_id, resolution)

        segment_states = _build_segment_states(insight_segments, duration_seconds)
        await VIDEO_REPRODUCE_STORE.set_segments(job_id, segment_states)

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id, 0.3, "Splitting original video into segment clips."
        )
        await _segment_original_video(job_id, source_video, segment_states, segments_dir)

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id,
            0.35,
            (
                "Converting key frames to stylized "
                f"{job_state.image_generation_backend or 'gpt-image-1'} references."
            ),
        )
        await _stylize_segment_frames(
            job_id,
            segment_states,
            stylized_dir,
            prompts_dir,
            resolution,
            job_state.image_generation_backend,
        )

        thread_count = max(1, job_state.thread_count or REPRODUCE_DEFAULT_THREADS)
        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id,
            0.4,
            f"Reproducing {len(segment_states)} segment(s) via Kling with up to {thread_count} concurrent tasks.",
        )
        await _render_segments_with_kling(
            job_id,
            segment_states,
            kling_dir,
            prompts_dir,
            resolution,
            thread_count,
        )

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id, 0.9, "Merging Kling segments into final video."
        )
        final_video = await _render_final_video(job_id, job_dir)
        await VIDEO_REPRODUCE_STORE.set_final_video(job_id, str(final_video))
        await VIDEO_REPRODUCE_STORE.mark_completed(job_id)
    except (ContentUnderstandingError, KlingAPIError, RuntimeError, FileNotFoundError) as exc:
        logger.exception("Video reproduce job %s failed", job_id)
        await VIDEO_REPRODUCE_STORE.mark_failed(job_id, str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected reproduce failure for job %s", job_id)
        await VIDEO_REPRODUCE_STORE.mark_failed(job_id, str(exc))


async def regenerate_segment(job_id: str, order: int, prompt: str) -> None:
    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("Prompt text is required for regeneration.")
    state = await VIDEO_REPRODUCE_STORE.get_job_state(job_id)
    if state.status == "running":
        raise RuntimeError("Cannot regenerate segments while the reproduce job is still running.")
    segment = next((item for item in state.segments if item.order == order), None)
    if segment is None:
        raise KeyError(f"Segment {order} not found for job {job_id}.")
    await VIDEO_REPRODUCE_STORE.add_message(job_id, "running", f"Regenerating segment {order} with a new prompt.")
    job_dir = reproduce_job_root(job_id)
    prompts_dir = job_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    kling_dir = job_dir / "kling"
    kling_dir.mkdir(parents=True, exist_ok=True)
    version_id = uuid.uuid4().hex
    prompt_path = prompts_dir / f"segment_{segment.order:03d}_{version_id}.txt"
    prompt_path.write_text(cleaned_prompt, encoding="utf-8")
    version = ReproduceSegmentVersion(
        version_id=version_id,
        prompt=cleaned_prompt,
        prompt_path=str(prompt_path),
    )
    await VIDEO_REPRODUCE_STORE.add_segment_version(job_id, segment.order, version)
    output_path = kling_dir / f"segment_{segment.order:03d}_{version_id}.mp4"
    _, success, error = await _run_kling_version(
        job_id,
        segment,
        version,
        cleaned_prompt,
        output_path,
        state.resolution or DEFAULT_SORA_RESOLUTION,
    )
    if not success:
        raise RuntimeError(error or "Segment regeneration failed.")
    await VIDEO_REPRODUCE_STORE.update_progress(
        job_id, min(state.progress + 0.05, 0.92), f"Segment {segment.order} regenerated."
    )
    await VIDEO_REPRODUCE_STORE.select_segment_version(job_id, segment.order, version.version_id)
    final_video = await _render_final_video(job_id, job_dir)
    await VIDEO_REPRODUCE_STORE.set_final_video(job_id, str(final_video))
    if state.status == "failed":
        await VIDEO_REPRODUCE_STORE.add_message(
            job_id, "running", "Queued re-run after failed segment regenerated."
        )


def _build_segment_states(
    segments: Sequence[VideoInsightSegment],
    video_duration: float,
) -> list[VideoReproduceSegment]:
    total = max(1, len(segments))
    fallback = max(1.0, video_duration / total if video_duration > 0 else 5.0)
    cleaned: list[VideoReproduceSegment] = []
    ordered_segments = list(segments)
    for index, segment in enumerate(ordered_segments):
        start = segment.start_seconds if segment.start_seconds is not None else None
        end = segment.end_seconds if segment.end_seconds is not None else None
        if start is None:
            start = cleaned[-1].end_seconds if cleaned else 0.0
        start = max(0.0, start)
        if index > 0:
            start = max(start, cleaned[-1].end_seconds)
        if end is None:
            next_start = ordered_segments[index + 1].start_seconds if index + 1 < total else None
            if next_start is not None and next_start > start:
                end = next_start
        if end is None:
            end = start + fallback
        if video_duration:
            end = min(end, video_duration)
        if end <= start:
            end = start + fallback
        duration = max(1.0, end - start)
        kling_duration = _normalize_kling_duration(duration)
        description = _compose_segment_description(segment, start, end)
        cleaned.append(
            VideoReproduceSegment(
                order=index + 1,
                label=segment.label or f"Segment {index + 1}",
                description=description,
                start_seconds=start,
                end_seconds=end,
                duration_seconds=duration,
                kling_duration_seconds=kling_duration,
                segment_id=segment.segment_id,
                tags=list(segment.tags),
                start_frame_path=segment.start_frame_path,
                end_frame_path=segment.end_frame_path,
            )
        )
    return cleaned


def _compose_segment_description(segment: VideoInsightSegment, start: float, end: float) -> str:
    parts: list[str] = []
    if segment.label:
        parts.append(segment.label.strip())
    if segment.summary:
        parts.append(segment.summary.strip())
    if segment.tags:
        parts.append("Key visual elements: " + ", ".join(segment.tags))
    parts.append(f"Timestamp range: {start:.2f}s to {end:.2f}s.")
    return " ".join(part for part in parts if part).strip()


def _normalize_kling_duration(duration: float) -> int:
    allowed = sorted(KLING_ALLOWED_DURATIONS)
    if not allowed:
        return max(5, int(round(duration)))
    if duration <= allowed[0]:
        return allowed[0]
    if duration >= allowed[-1]:
        return allowed[-1]
    return min(allowed, key=lambda value: abs(value - duration))


async def _segment_original_video(
    job_id: str,
    source_video: Path,
    segments: Sequence[VideoReproduceSegment],
    dest_dir: Path,
) -> None:
    for segment in segments:
        start = max(0.0, segment.start_seconds)
        duration = max(0.5, segment.end_seconds - segment.start_seconds)
        output = dest_dir / f"segment_{segment.order:03d}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source_video),
            "-t",
            f"{duration:.3f}",
            "-c",
            "copy",
            str(output),
        ]
        await run_subprocess(cmd)
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(
            job_id,
            segment.order,
            original_video_path=str(output),
        )


async def _render_segments_with_kling(
    job_id: str,
    segments: Sequence[VideoReproduceSegment],
    kling_dir: Path,
    prompts_dir: Path,
    resolution: str,
    thread_count: int,
) -> None:
    semaphore = asyncio.Semaphore(max(1, thread_count))
    tasks: list[asyncio.Task[tuple[int, bool, str | None]]] = []
    for segment in segments:
        prompt_text = _default_segment_prompt(segment)
        version_id = uuid.uuid4().hex
        prompt_path = prompts_dir / f"segment_{segment.order:03d}_{version_id}.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        version = ReproduceSegmentVersion(
            version_id=version_id,
            prompt=prompt_text,
            prompt_path=str(prompt_path),
        )
        await VIDEO_REPRODUCE_STORE.add_segment_version(job_id, segment.order, version)
        output_path = kling_dir / f"segment_{segment.order:03d}_{version_id}.mp4"
        tasks.append(
            asyncio.create_task(
                _run_kling_version(
                    job_id,
                    segment,
                    version,
                    prompt_text,
                    output_path,
                    resolution,
                    semaphore=semaphore,
                )
            )
        )

    failures: list[str] = []
    completed = 0
    total = max(1, len(tasks))
    for task in asyncio.as_completed(tasks):
        order, success, error = await task
        completed += 1
        progress = 0.4 + (completed / total) * 0.45
        message = (
            f"Segment {order} completed via Kling."
            if success
            else f"Segment {order} failed: {error}"
        )
        await VIDEO_REPRODUCE_STORE.update_progress(job_id, progress, message)
        if not success and error:
            failures.append(f"Segment {order}: {error}")
    if failures:
        raise RuntimeError("; ".join(failures))


async def _run_kling_version(
    job_id: str,
    segment: VideoReproduceSegment,
    version: ReproduceSegmentVersion,
    prompt_text: str,
    output_path: Path,
    resolution: str,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[int, bool, str | None]:
    lock = semaphore or _AsyncNoopLock()
    async with lock:
        await VIDEO_REPRODUCE_STORE.update_segment_version(
            job_id,
            segment.order,
            version.version_id,
            status="running",
            error=None,
        )
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(job_id, segment.order, status="rendering")
        try:
            primary_image = segment.stylized_start_frame_path or segment.start_frame_path
            tail_image = segment.stylized_end_frame_path or segment.end_frame_path
            result = await render_kling_clip(
                prompt_text=prompt_text,
                duration_seconds=segment.kling_duration_seconds,
                resolution=resolution or DEFAULT_SORA_RESOLUTION,
                output_path=output_path,
                primary_image_path=primary_image,
                tail_image_path=tail_image,
                external_task_id=f"{job_id}-segment{segment.order:03d}-{version.version_id}",
            )
        except Exception as exc:  # KlingAPIError bubbles here
            logger.exception("Kling render failed for reproduce job %s segment %s", job_id, segment.order)
            error = str(exc)
            await VIDEO_REPRODUCE_STORE.update_segment_version(
                job_id,
                segment.order,
                version.version_id,
                status="failed",
                error=error,
            )
            await VIDEO_REPRODUCE_STORE.update_segment_attributes(job_id, segment.order, status="failed")
            return segment.order, False, error

        log_path = output_path.with_suffix(".json")
        client_log_path = output_path.with_suffix(".client.log")
        log_payload = {
            "provider": "kling",
            "task_id": result.task_id,
            "endpoint": result.endpoint,
            "request": result.request_payload,
            "response": result.final_response,
            "video_url": result.video_url,
        }
        log_path.write_text(json.dumps(log_payload, indent=2), encoding="utf-8")
        client_log_path.write_text(
            f"Kling task {result.task_id} completed via {result.endpoint}.\nVideo URL: {result.video_url}\n",
            encoding="utf-8",
        )
        await VIDEO_REPRODUCE_STORE.update_segment_version(
            job_id,
            segment.order,
            version.version_id,
            status="completed",
            video_path=str(output_path),
            log_path=str(log_path),
            client_log_path=str(client_log_path),
            task_id=result.task_id,
            error=None,
        )
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(job_id, segment.order, status="completed")
        return segment.order, True, None


async def _render_final_video(job_id: str, job_dir: Path) -> Path:
    state = await VIDEO_REPRODUCE_STORE.get_job_state(job_id)
    if not state.segments:
        raise RuntimeError("No segments available for final merge.")
    concat_file = job_dir / "tmp" / f"concat_{uuid.uuid4().hex}.txt"
    concat_file.parent.mkdir(parents=True, exist_ok=True)
    scene_paths: list[Path] = []
    for segment in sorted(state.segments, key=lambda item: item.order):
        version = segment.get_version(segment.selected_version_id)
        if not version.video_path:
            raise RuntimeError(f"Segment {segment.order} is missing a Kling video.")
        scene_paths.append(Path(version.video_path))
    with concat_file.open("w", encoding="utf-8") as fh:
        for path in scene_paths:
            fh.write(escape_for_concat(path) + "\n")
    final_video = job_dir / FINAL_VIDEO_NAME
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(final_video),
    ]
    await run_subprocess(cmd)
    return final_video


async def _stylize_segment_frames(
    job_id: str,
    segments: Sequence[VideoReproduceSegment],
    stylized_dir: Path,
    prompts_dir: Path,
    resolution: str,
    image_backend: str | None,
) -> None:
    stylized_dir.mkdir(parents=True, exist_ok=True)
    try:
        generator = get_image_generator(image_backend)
    except ImageGenerationError as exc:
        logger.warning("Image generator unavailable (%s): %s", image_backend, exc)
        await VIDEO_REPRODUCE_STORE.add_message(
            job_id,
            "running",
            "Image generator unavailable; using original frames for Kling references.",
        )
        generator = None
    normalized_resolution = resolution or DEFAULT_SORA_RESOLUTION
    for segment in segments:
        prompt_path, prompt_text = _ensure_stylize_prompt(prompts_dir, segment)
        stylized_start_path = stylized_dir / f"segment_{segment.order:03d}_start_stylized.png"
        stylized_end_path = stylized_dir / f"segment_{segment.order:03d}_end_stylized.png"
        start_result = await _stylize_single_frame(
            generator,
            segment.start_frame_path,
            prompt_text,
            stylized_start_path,
            normalized_resolution,
        )
        end_result = await _stylize_single_frame(
            generator,
            segment.end_frame_path,
            prompt_text,
            stylized_end_path,
            normalized_resolution,
        )
        segment.stylized_start_frame_path = start_result
        segment.stylized_end_frame_path = end_result
        segment.image_prompt_path = str(prompt_path)
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(
            job_id,
            segment.order,
            stylized_start_frame_path=start_result,
            stylized_end_frame_path=end_result,
            image_prompt_path=str(prompt_path),
        )


def _ensure_stylize_prompt(prompts_dir: Path, segment: VideoReproduceSegment) -> tuple[Path, str]:
    prompt_path = prompts_dir / f"segment_{segment.order:03d}_stylize_prompt.txt"
    if prompt_path.is_file():
        existing = prompt_path.read_text(encoding="utf-8").strip()
        if existing:
            return prompt_path, existing
    prompt_text = _default_stylize_prompt(segment)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return prompt_path, prompt_text


def _default_stylize_prompt(segment: VideoReproduceSegment) -> str:
    return render_prompt_template(
        "reproduce_stylize",
        segment_description=segment.description or "",
    )


async def _stylize_single_frame(
    generator,
    source_path: str | None,
    prompt_text: str,
    destination: Path,
    resolution: str,
) -> str | None:
    if not source_path:
        return None
    src = Path(source_path)
    if not src.is_file():
        logger.warning("Reference frame missing for stylization: %s", source_path)
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return str(destination)
    if generator is None:
        return None
    try:
        stylized_bytes = await generator.generate(
            prompt=prompt_text,
            resolution=resolution,
            image_path=src,
        )
    except ImageGenerationError as exc:
        logger.warning("Image generator failed for %s: %s", source_path, exc)
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Unexpected image generation failure for %s: %s", source_path, exc)
        return None
    destination.write_bytes(stylized_bytes)
    return str(destination)


def _default_segment_prompt(segment: VideoReproduceSegment) -> str:
    return render_prompt_template(
        "reproduce_segment",
        segment_description=segment.description or "",
    )


async def _probe_video_metadata(video_path: Path) -> tuple[float, str]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    stdout, _ = await run_subprocess(cmd)
    duration = 0.0
    width = None
    height = None
    try:
        payload = json.loads(stdout)
        duration = float(payload.get("format", {}).get("duration", 0.0) or 0.0)
        streams = payload.get("streams", [])
        if streams:
            width = streams[0].get("width")
            height = streams[0].get("height")
    except Exception:
        logger.warning("Unable to parse ffprobe metadata for %s", video_path)
    resolution = DEFAULT_SORA_RESOLUTION
    if width and height:
        resolution = f"{int(width)}x{int(height)}"
    return duration, resolution


__all__ = [
    "reproduce_job_root",
    "regenerate_segment",
    "run_reproduce_job",
]
