"""Video reproduce workflow combining content understanding and Kling renders."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from pathlib import Path
from typing import Sequence

from .config import (
    DEFAULT_REPRODUCE_SEGMENTATION,
    DEFAULT_SORA_RESOLUTION,
    GPT5_REFINEMENT_DEPLOYMENT,
    KLING_ALLOWED_DURATIONS,
    REPRODUCE_DEFAULT_THREADS,
    REPRODUCE_SEGMENT_FRAME_SAMPLES,
    VIDEO_REPRODUCE_JOBS_DIR,
    azure_text_client,
    extract_response_text,
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
from .utils import escape_for_concat, extract_frame_at_timestamp, run_subprocess, utc_now


FINAL_VIDEO_NAME = "kling_reproduce_compilation.mp4"
MAX_SCENE_DURATION_SECONDS = 10.0
SEGMENT_DESCRIPTION_SYSTEM_PROMPT = (
    "You are a meticulous video analyst. Given evenly spaced frames from a short clip, "
    "write 2-3 sentences that describe the subjects, environment, motion, and mood across the shot. "
    "Reference only what can be inferred from the frames."
)

try:  # Optional import; errors handled at runtime if unavailable.
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import ContentDetector
except ImportError:  # pragma: no cover - fallback when dependency missing
    SceneManager = None  # type: ignore[assignment]
    open_video = None  # type: ignore[assignment]
    ContentDetector = None  # type: ignore[assignment]


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
        job_state = await VIDEO_REPRODUCE_STORE.get_job_state(job_id)
        if not job_state.video_path:
            raise RuntimeError("Uploaded video is missing from workspace.")
        source_video = Path(job_state.video_path)
        if not source_video.is_file():
            raise FileNotFoundError(f"Source video {source_video} not found.")
        segmentation_backend = (
            job_state.segmentation_backend or DEFAULT_REPRODUCE_SEGMENTATION
        ).strip().lower()

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

        try:
            duration_seconds, resolution = await _probe_video_metadata(source_video)
        except Exception:
            logger.warning("Unable to derive metadata for %s; using defaults.", source_video)
            duration_seconds, resolution = 0.0, DEFAULT_SORA_RESOLUTION
        await VIDEO_REPRODUCE_STORE.set_resolution(job_id, resolution)

        existing_segments = list(job_state.segments)
        if existing_segments:
            segment_states = existing_segments
            await VIDEO_REPRODUCE_STORE.add_message(
                job_id,
                "running",
                f"Reusing {len(existing_segments)} cached segment(s) from previous attempt.",
            )
        else:
            if segmentation_backend == "content_understanding":
                ensure_content_understanding_ready()
                await VIDEO_REPRODUCE_STORE.update_progress(
                    job_id, 0.1, "Submitting video to Azure Content Understanding."
                )
                client = AzureContentUnderstandingClient(user_agent="sora2-workflow/reproduce")
                insight_payload = await client.analyze_video(source_video)
                _, insight_segments = summarize_content_understanding_result(insight_payload)
                if not insight_segments:
                    raise RuntimeError("Content Understanding did not return any segments to reproduce.")
                segment_source = "Azure Content Understanding"
            else:
                await VIDEO_REPRODUCE_STORE.update_progress(
                    job_id, 0.1, "Detecting video segments via PySceneDetect."
                )
                insight_segments = await _detect_segments_with_pyscenedetect(
                    job_id,
                    source_video,
                    duration_seconds,
                )
                if not insight_segments:
                    raise RuntimeError("PySceneDetect did not detect any segments to reproduce.")
                segment_source = "OpenCV segmentation"

            await VIDEO_REPRODUCE_STORE.update_progress(
                job_id, 0.2, "Extracting start/end frames for each segment."
            )
            insight_segments = await attach_segment_frames(
                job_id,
                source_video,
                insight_segments,
                frames_dir=frames_dir,
            )

            fixed_kling_duration = 5 if segmentation_backend == "opencv" else None
            segment_states = _build_segment_states(
                insight_segments,
                duration_seconds,
                fixed_kling_duration=fixed_kling_duration,
            )
            await VIDEO_REPRODUCE_STORE.set_segments(
                job_id,
                segment_states,
                source=segment_source,
            )

        await _ensure_cached_segment_frames(
            job_id,
            source_video,
            segment_states,
            frames_dir,
        )

        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id,
            0.3,
            "Splitting original video into segment clips.",
        )
        await _segment_original_video(job_id, source_video, segment_states, segments_dir)

        if segmentation_backend == "opencv" and REPRODUCE_SEGMENT_FRAME_SAMPLES > 0:
            await VIDEO_REPRODUCE_STORE.update_progress(
                job_id,
                0.32,
                "Generating GPT-5 descriptions for OpenCV segments.",
            )
            await _describe_segments_with_frames(
                job_id,
                segment_states,
                job_dir / "frame-samples",
                REPRODUCE_SEGMENT_FRAME_SAMPLES,
                prompts_dir,
            )

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
    *,
    fixed_kling_duration: int | None = None,
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
        kling_duration = fixed_kling_duration or _normalize_kling_duration(duration)
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


async def _ensure_cached_segment_frames(
    job_id: str,
    source_video: Path,
    segments: Sequence[VideoReproduceSegment],
    frames_dir: Path,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    for segment in segments:
        updates: dict[str, str | None] = {}
        start_dest = frames_dir / f"segment_{segment.order:03d}_start.jpg"
        start_path = await _ensure_segment_frame(
            source_video,
            segment.start_seconds,
            start_dest,
            "start",
            segment.start_frame_path,
        )
        if start_path and start_path != segment.start_frame_path:
            segment.start_frame_path = start_path
            updates["start_frame_path"] = start_path
        end_dest = frames_dir / f"segment_{segment.order:03d}_end.jpg"
        end_path = await _ensure_segment_frame(
            source_video,
            segment.end_seconds,
            end_dest,
            "end",
            segment.end_frame_path,
        )
        if end_path and end_path != segment.end_frame_path:
            segment.end_frame_path = end_path
            updates["end_frame_path"] = end_path
        if updates:
            await VIDEO_REPRODUCE_STORE.update_segment_attributes(
                job_id,
                segment.order,
                **updates,
            )


async def _ensure_segment_frame(
    source_video: Path,
    timestamp: float | None,
    destination: Path,
    frame_label: str,
    current_path: str | None,
) -> str | None:
    if _artifact_exists(current_path):
        return current_path
    if destination.is_file() and destination.stat().st_size > 0:
        return str(destination)
    if timestamp is None:
        return current_path
    offsets = [0.0]
    if frame_label == "end":
        offsets.extend([-0.01, -0.05, -0.1])
    for offset in offsets:
        candidate_ts = max(0.0, timestamp + offset)
        destination.unlink(missing_ok=True)
        try:
            await extract_frame_at_timestamp(source_video, candidate_ts, destination)
        except Exception as exc:
            logger.warning(
                "Failed to extract %s frame for reproduce segment at %.3fs: %s",
                frame_label,
                candidate_ts,
                exc,
            )
            continue
        if destination.is_file() and destination.stat().st_size > 0:
            return str(destination)
    return current_path


async def _detect_segments_with_pyscenedetect(
    job_id: str,
    video_path: Path,
    video_duration: float,
) -> list[VideoInsightSegment]:
    if SceneManager is None or ContentDetector is None or open_video is None:
        raise RuntimeError(
            "PySceneDetect is unavailable. Install pyscenedetect to enable OpenCV segmentation."
        )

    def _run_detection() -> list[tuple[float, float]]:
        video = open_video(str(video_path))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=20.0, min_scene_len=12))
        try:
            manager.detect_scenes(video)
        finally:
            closer = getattr(video, "close", None)
            if callable(closer):
                closer()
        ranges: list[tuple[float, float]] = []
        for start_tc, end_tc in manager.get_scene_list():
            start_seconds = max(0.0, start_tc.get_seconds())
            end_seconds = max(start_seconds, end_tc.get_seconds())
            ranges.append((start_seconds, end_seconds))
        return ranges

    try:
        raw_ranges = await asyncio.to_thread(_run_detection)
    except Exception as exc:
        logger.exception("PySceneDetect failed for reproduce job %s: %s", job_id, exc)
        raise RuntimeError("PySceneDetect failed to analyze the uploaded video.") from exc

    normalized = _split_ranges_to_max_duration(
        raw_ranges,
        video_duration,
        MAX_SCENE_DURATION_SECONDS,
    )
    if not normalized:
        fallback_duration = max(video_duration, 5.0)
        normalized = [(0.0, fallback_duration)]
    segments: list[VideoInsightSegment] = []
    for index, (start_seconds, end_seconds) in enumerate(normalized, start=1):
        segments.append(
            VideoInsightSegment(
                segment_id=f"opencv-{index:03d}",
                label=f"Segment {index}",
                start=f"{start_seconds:.3f}s",
                end=f"{end_seconds:.3f}s",
                summary=None,
                tags=["opencv"],
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
        )
    return segments


def _split_ranges_to_max_duration(
    ranges: Sequence[tuple[float, float]],
    video_duration: float,
    max_duration: float,
) -> list[tuple[float, float]]:
    if not ranges and video_duration > 0:
        return [(0.0, video_duration)]
    cleaned: list[tuple[float, float]] = []
    upper_bound = video_duration if video_duration > 0 else None
    for start, end in ranges:
        start_seconds = max(0.0, float(start))
        end_seconds = max(start_seconds, float(end))
        if upper_bound is not None:
            start_seconds = min(start_seconds, upper_bound)
            end_seconds = min(end_seconds, upper_bound)
        if end_seconds - start_seconds <= 0.1:
            continue
        current = start_seconds
        while current + 0.05 < end_seconds:
            next_end = min(end_seconds, current + max_duration)
            if next_end - current >= 0.1:
                cleaned.append((current, next_end))
            current = next_end
    if not cleaned and upper_bound:
        cleaned.append((0.0, upper_bound))
    return cleaned


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
    dest_dir.mkdir(parents=True, exist_ok=True)
    for segment in segments:
        existing_path = Path(segment.original_video_path or "")
        if existing_path.is_file():
            continue
        start = max(0.0, segment.start_seconds)
        duration = max(0.5, segment.end_seconds - segment.start_seconds)
        output = dest_dir / f"segment_{segment.order:03d}.mp4"
        if output.is_file() and output.stat().st_size > 0:
            segment.original_video_path = str(output)
            await VIDEO_REPRODUCE_STORE.update_segment_attributes(
                job_id,
                segment.order,
                original_video_path=str(output),
            )
            continue
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
        segment.original_video_path = str(output)
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(
            job_id,
            segment.order,
            original_video_path=str(output),
        )


async def _describe_segments_with_frames(
    job_id: str,
    segments: Sequence[VideoReproduceSegment],
    frame_dir: Path,
    sample_count: int,
    prompts_dir: Path,
) -> None:
    if sample_count <= 0:
        return
    if not GPT5_REFINEMENT_DEPLOYMENT:
        logger.info("Skipping GPT-5 segment descriptions; deployment not configured.")
        return
    client = azure_text_client()
    if client is None:
        logger.info("Skipping GPT-5 segment descriptions; Azure OpenAI client unavailable.")
        return
    frame_dir.mkdir(parents=True, exist_ok=True)
    description_dir = prompts_dir / "descriptions"
    description_dir.mkdir(parents=True, exist_ok=True)
    for segment in segments:
        if not segment.original_video_path:
            continue
        description_path = description_dir / f"segment_{segment.order:03d}.txt"
        cached_description = _read_cached_text(description_path)
        if cached_description:
            if cached_description != segment.description:
                segment.description = cached_description
                await VIDEO_REPRODUCE_STORE.update_segment_attributes(
                    job_id,
                    segment.order,
                    description=cached_description,
                )
            continue
        frame_paths = await _sample_segment_preview_frames(segment, frame_dir, sample_count)
        if not frame_paths:
            continue
        summary = await _summarize_segment_frames(segment, frame_paths, client, GPT5_REFINEMENT_DEPLOYMENT)
        if not summary:
            continue
        cleaned = summary.strip()
        if not cleaned:
            continue
        description_path.write_text(cleaned, encoding="utf-8")
        segment.description = cleaned
        await VIDEO_REPRODUCE_STORE.update_segment_attributes(
            job_id,
            segment.order,
            description=cleaned,
        )


async def _sample_segment_preview_frames(
    segment: VideoReproduceSegment,
    frame_dir: Path,
    sample_count: int,
) -> list[Path]:
    clip_path = Path(segment.original_video_path or "")
    if not clip_path.is_file():
        return []
    duration = max(0.5, float(segment.duration_seconds or 0.0))
    spacing = duration / (sample_count + 1)
    captured: list[Path] = []
    for index in range(sample_count):
        timestamp = max(0.0, spacing * (index + 1))
        frame_path = frame_dir / f"segment_{segment.order:03d}_sample_{index + 1}.jpg"
        if frame_path.is_file() and frame_path.stat().st_size > 0:
            captured.append(frame_path)
            continue
        try:
            await extract_frame_at_timestamp(clip_path, timestamp, frame_path)
        except Exception as exc:
            logger.warning(
                "Failed to capture preview frame %s for segment %s at %.2fs: %s",
                frame_path.name,
                segment.order,
                timestamp,
                exc,
            )
            frame_path.unlink(missing_ok=True)
            continue
        if frame_path.is_file() and frame_path.stat().st_size > 0:
            captured.append(frame_path)
    return captured


async def _summarize_segment_frames(
    segment: VideoReproduceSegment,
    frame_paths: Sequence[Path],
    client,
    deployment: str,
) -> str | None:
    image_payloads = []
    for path in frame_paths:
        try:
            image_payloads.append(_encode_image_as_data_url(path))
        except Exception as exc:
            logger.warning("Failed to encode preview frame %s for segment %s: %s", path, segment.order, exc)
    if not image_payloads:
        return None
    user_text = (
        f"You are seeing {len(image_payloads)} evenly spaced frames from Segment {segment.order} "
        f"lasting approximately {segment.duration_seconds:.2f} seconds. "
        "Describe the visual content, motion, and mood across the entire clip in 2-3 sentences."
    )
    user_content = [{"type": "input_text", "text": user_text}]
    user_content.extend({"type": "input_image", "image_url": url} for url in image_payloads)

    def _call() -> str:
        response = client.responses.create(
            model=deployment,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": SEGMENT_DESCRIPTION_SYSTEM_PROMPT}]},
                {"role": "user", "content": user_content},
            ],
            max_output_tokens=800,
        )
        return extract_response_text(response)

    try:
        summary = await asyncio.to_thread(_call)
    except Exception as exc:
        logger.warning("GPT-5 failed to describe reproduce segment %s: %s", segment.order, exc)
        return None
    cleaned = summary.strip()
    return cleaned or None


def _encode_image_as_data_url(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64}"


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
    reused = 0
    for segment in segments:
        if await _reuse_completed_segment(job_id, segment):
            reused += 1
            await VIDEO_REPRODUCE_STORE.add_message(
                job_id,
                "running",
                f"Segment {segment.order} already rendered; using cached clip.",
            )
            continue
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
    if not tasks and reused:
        await VIDEO_REPRODUCE_STORE.update_progress(
            job_id,
            0.85,
            f"Reused {reused} cached Kling segment(s); no new renders required.",
        )
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


async def _reuse_completed_segment(job_id: str, segment: VideoReproduceSegment) -> bool:
    version = _pick_cached_version(segment)
    if version is None:
        return False
    segment.select_version(version)
    segment.status = "completed"
    version.status = "completed"
    version.error = None
    await VIDEO_REPRODUCE_STORE.select_segment_version(job_id, segment.order, version.version_id)
    await VIDEO_REPRODUCE_STORE.update_segment_attributes(
        job_id,
        segment.order,
        status="completed",
    )
    await VIDEO_REPRODUCE_STORE.update_segment_version(
        job_id,
        segment.order,
        version.version_id,
        status="completed",
        error=None,
    )
    return True


def _pick_cached_version(segment: VideoReproduceSegment) -> ReproduceSegmentVersion | None:
    completed_versions = [
        version
        for version in segment.versions
        if _artifact_exists(version.video_path) and version.status in ("completed", "running")
    ]
    if not completed_versions:
        return None
    if segment.selected_version_id:
        try:
            selected = segment.get_version(segment.selected_version_id)
        except KeyError:
            selected = None
        else:
            if selected.status == "completed" and _artifact_exists(selected.video_path):
                return selected
    return completed_versions[-1]


def _artifact_exists(path: str | None) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return candidate.is_file() and candidate.stat().st_size > 0


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
        adjusted_path = await _ensure_segment_duration(
            job_dir,
            segment,
            Path(version.video_path),
        )
        scene_paths.append(adjusted_path)
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

    audio_source = Path(state.video_path) if state.video_path else None
    if audio_source and audio_source.is_file():
        audio_path = await _maybe_extract_original_audio(audio_source, job_dir)
        if audio_path is not None:
            await _merge_video_with_audio(final_video, audio_path)
            audio_path.unlink(missing_ok=True)

    return final_video


async def _ensure_segment_duration(
    job_dir: Path,
    segment: VideoReproduceSegment,
    video_path: Path,
) -> Path:
    if not video_path.is_file():
        return video_path
    target_duration = max(0.1, float(segment.duration_seconds or 0.0))
    actual_duration = await _probe_clip_duration(video_path)
    if actual_duration <= 0.0 or abs(actual_duration - target_duration) <= 0.05:
        return video_path
    speed_factor = target_duration / actual_duration
    destination_dir = job_dir / "retimed"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{video_path.stem}_retimed.mp4"
    filter_expr = f"setpts={speed_factor:.6f}*PTS"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        filter_expr,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        str(destination),
    ]
    try:
        await run_subprocess(cmd)
    except RuntimeError:
        logger.warning(
            "Unable to retime reproduce segment %s to %.2fs; using original clip.",
            segment.order,
            target_duration,
        )
        destination.unlink(missing_ok=True)
        return video_path
    return destination


async def _probe_clip_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        stdout, _ = await run_subprocess(cmd)
    except RuntimeError:
        logger.warning("Unable to measure duration for %s", video_path)
        return 0.0
    try:
        return max(0.0, float(stdout.strip()))
    except (TypeError, ValueError):
        return 0.0


async def _maybe_extract_original_audio(source_video: Path, job_dir: Path) -> Path | None:
    audio_path = job_dir / "tmp" / f"source_audio_{uuid.uuid4().hex}.aac"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_video),
        "-vn",
        "-acodec",
        "copy",
        str(audio_path),
    ]
    try:
        await run_subprocess(cmd)
    except RuntimeError:
        logger.warning("Unable to extract audio from %s", source_video)
        audio_path.unlink(missing_ok=True)
        return None
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        audio_path.unlink(missing_ok=True)
        return None
    return audio_path


async def _merge_video_with_audio(video_path: Path, audio_path: Path) -> None:
    merged_path = video_path.with_suffix(".with-audio.tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        str(merged_path),
    ]
    try:
        await run_subprocess(cmd)
    except RuntimeError:
        logger.warning("Unable to merge source audio into final reproduce video.")
        merged_path.unlink(missing_ok=True)
        return
    merged_path.replace(video_path)


def _read_cached_text(path: Path) -> str | None:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    cleaned = contents.strip()
    return cleaned or None


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
