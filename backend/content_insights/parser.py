"""Utilities for normalizing Azure Content Understanding results."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from ..models import VideoInsightKeyFrame, VideoInsightSegment

KEYFRAME_KEYS = ("keyFrames", "keyframes", "KeyFrames")
SEGMENT_KEYS = ("segments", "videoSegments", "temporalSegments", "Segments")
MAX_ITEMS = 100
ISO_DURATION_PATTERN = re.compile(
    r"^PT(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$",
    re.IGNORECASE,
)


def summarize_content_understanding_result(
    payload: dict[str, Any],
) -> tuple[list[VideoInsightKeyFrame], list[VideoInsightSegment]]:
    return extract_key_frames(payload), extract_segments(payload)


def extract_key_frames(payload: dict[str, Any]) -> list[VideoInsightKeyFrame]:
    frames: list[VideoInsightKeyFrame] = []
    seen_ids: set[str] = set()
    for collection in _find_lists(payload, KEYFRAME_KEYS):
        for entry in collection:
            if not isinstance(entry, dict):
                continue
            entry = _normalize_schema_dict(entry)
            frame_id = _ensure_str(entry.get("id") or entry.get("frameId"))
            if frame_id and frame_id in seen_ids:
                continue
            timestamp = _stringify_timestamp(
                entry.get("timestamp")
                or entry.get("timeOffset")
                or entry.get("offset")
            )
            description = _first_non_empty(
                entry.get("text"),
                entry.get("description"),
                entry.get("caption"),
            )
            confidence = _to_float(entry.get("confidence") or entry.get("probability"))
            tags = _collect_tags(entry)
            frame = VideoInsightKeyFrame(
                frame_id=frame_id,
                timestamp=timestamp,
                description=description,
                confidence=confidence,
                tags=tags,
            )
            frames.append(frame)
            if frame_id:
                seen_ids.add(frame_id)
            if len(frames) >= MAX_ITEMS:
                return frames
    return frames


def extract_segments(payload: dict[str, Any]) -> list[VideoInsightSegment]:
    segments: list[VideoInsightSegment] = []
    seen_ids: set[str] = set()
    for collection in _find_lists(payload, SEGMENT_KEYS):
        for entry in collection:
            if not isinstance(entry, dict):
                continue
            entry = _normalize_schema_dict(entry)
            segment_id = _ensure_str(
                _get_field(entry, "segment_id", "segmentId", "SegmentId", "id")
            )
            if segment_id and segment_id in seen_ids:
                continue
            label = _first_non_empty(
                entry.get("label"),
                entry.get("name"),
                entry.get("title"),
            )
            start = _stringify_timestamp(
                _get_field(entry, "start", "startTime", "offset", "Start", "StartTime")
            )
            end = _stringify_timestamp(
                _get_field(entry, "end", "endTime", "duration", "End", "EndTime")
            )
            start_ms = _to_float(
                _get_field(entry, "starttimems", "start_time_ms", "startOffsetMs", "StartTimeMs")
            )
            end_ms = _to_float(
                _get_field(entry, "endtimems", "end_time_ms", "endOffsetMs", "EndTimeMs")
            )
            start_seconds = _timestamp_to_seconds(start)
            end_seconds = _timestamp_to_seconds(end)
            if start_seconds is None and start_ms is not None:
                start_seconds = max(start_ms / 1000.0, 0.0)
                if start is None:
                    start = _stringify_timestamp(start_seconds)
            if end_seconds is None and end_ms is not None:
                end_seconds = max(end_ms / 1000.0, 0.0)
                if end is None:
                    end = _stringify_timestamp(end_seconds)
            summary = _first_non_empty(
                _get_field(entry, "summary", "Summary"),
                _get_field(entry, "text", "Text"),
                _get_field(entry, "description", "Description"),
                _get_field(entry, "transcript", "Transcript"),
            )
            tags = _collect_tags(entry)
            segments.append(
                VideoInsightSegment(
                    segment_id=segment_id,
                    label=label,
                    start=start,
                    end=end,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    summary=summary,
                    tags=tags,
                )
            )
            if segment_id:
                seen_ids.add(segment_id)
            if len(segments) >= MAX_ITEMS:
                return segments
    return segments


def _maybe_extract_list(value: Any) -> Optional[list[Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for candidate in ("valueArray", "values", "items", "value"):
            nested = value.get(candidate)
            if isinstance(nested, list):
                return nested
    return None


def _flatten_schema_value(value: Any) -> Any:
    if isinstance(value, dict):
        for candidate in (
            "valueString",
            "valueInteger",
            "valueFloat",
            "valueNumber",
            "valueBoolean",
            "valueDate",
            "valueDateTime",
            "content",
            "value",
        ):
            if candidate in value:
                return _flatten_schema_value(value[candidate])
        if "valueArray" in value and isinstance(value["valueArray"], list):
            return [_flatten_schema_value(item) for item in value["valueArray"]]
        if "valueObject" in value and isinstance(value["valueObject"], dict):
            return {
                key: _flatten_schema_value(inner)
                for key, inner in value["valueObject"].items()
            }
        return {key: _flatten_schema_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_flatten_schema_value(item) for item in value]
    return value


def _normalize_schema_dict(entry: dict[str, Any]) -> dict[str, Any]:
    if "valueObject" in entry and isinstance(entry["valueObject"], dict):
        entry = entry["valueObject"]
    normalized: dict[str, Any] = {}
    for key, value in entry.items():
        if key == "type":
            continue
        normalized[key] = _flatten_schema_value(value)
    return normalized


def _get_field(entry: dict[str, Any], *names: str) -> Any:
    if not isinstance(entry, dict):
        return None
    for name in names:
        if name in entry and entry[name] is not None:
            return entry[name]
    lowered = {key.lower(): value for key, value in entry.items()}
    for name in names:
        lowered_value = lowered.get(name.lower())
        if lowered_value is not None:
            return lowered_value
    return None


def _find_lists(payload: Any, keys: Iterable[str]) -> list[list[Any]]:
    matches: list[list[Any]] = []
    key_set = {key.lower(): key for key in keys}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized = key.lower()
                potential_list = _maybe_extract_list(value)
                if normalized in key_set and potential_list is not None:
                    matches.append(potential_list)
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return matches


def _stringify_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = max(0.0, float(value))
        base = f"{seconds:.2f}".rstrip("0").rstrip(".")
        if not base:
            base = "0"
        return f"{base}s"
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, dict):
        for key in ("text", "value", "timestamp", "start", "offset", "seconds"):
            if key in value:
                result = _stringify_timestamp(value[key])
                if result:
                    return result
    return None


def _collect_tags(entry: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in ("tags", "labels", "categories"):
        value = entry.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned:
                        tags.append(cleaned)
                elif isinstance(item, dict):
                    label = _first_non_empty(
                        item.get("name"),
                        item.get("text"),
                        item.get("label"),
                        item.get("category"),
                    )
                    if label:
                        tags.append(label)
    return tags


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ensure_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    try:
        return str(value)
    except Exception:
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_to_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    iso_match = ISO_DURATION_PATTERN.match(text)
    if iso_match:
        total = 0.0
        hours = iso_match.group("hours")
        minutes = iso_match.group("minutes")
        seconds = iso_match.group("seconds")
        if hours:
            total += float(hours) * 3600
        if minutes:
            total += float(minutes) * 60
        if seconds:
            total += float(seconds)
        return max(total, 0.0)
    if ":" in text:
        parts = text.split(":")
        try:
            values = [float(part) for part in parts]
        except ValueError:
            pass
        else:
            total = 0.0
            multiplier = 1.0
            for part in reversed(values):
                total += part * multiplier
                multiplier *= 60.0
            return max(total, 0.0)
    lowered = text.lower()
    for suffix in ("seconds", "second", "secs", "sec", "s"):
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            stripped = lowered[: -len(suffix)].strip()
            try:
                return max(float(stripped), 0.0)
            except ValueError:
                break
    try:
        return max(float(text), 0.0)
    except ValueError:
        return None
