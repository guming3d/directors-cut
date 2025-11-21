"""Shared pipeline helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import SceneRender


def summarize_manifest(
    renders: list[SceneRender],
    final_video: Path,
    total_seconds: int,
    resolution: str,
) -> dict[str, Any]:
    return {
        "final_video": str(final_video),
        "assigned_seconds": total_seconds,
        "resolution": resolution,
        "scenes": [
            {
                "order": render.order,
                "label": render.label,
                "duration_seconds": render.duration_seconds,
                "prompt_path": render.prompt_path,
                "prompt_text": render.prompt_text,
                "video_path": render.video_path,
                "sora_log_path": render.log_path,
                "client_capture": render.client_log_path,
                "version_id": render.version_id,
                "version_label": render.version_label,
                "version_source": render.version_source,
                "video_id": render.video_id,
            }
            for render in renders
        ],
    }


__all__ = ["summarize_manifest"]
