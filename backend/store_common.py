"""Shared helpers for store serialization and persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .utils import utc_now

_UNSET = object()


def _serialize_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat()


def _deserialize_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _serialize_progress_message(message) -> dict[str, Any]:
    return {
        "timestamp": _serialize_datetime(message.timestamp),
        "stage": message.stage,
        "text": message.text,
    }


def _deserialize_progress_message(payload: dict[str, Any]):
    from .models import ProgressMessage

    return ProgressMessage(
        timestamp=_deserialize_datetime(payload.get("timestamp")) or utc_now(),
        stage=payload.get("stage", "queued"),
        text=payload.get("text", ""),
    )


__all__ = [
    "_UNSET",
    "_deserialize_datetime",
    "_deserialize_progress_message",
    "_serialize_datetime",
    "_serialize_progress_message",
]
