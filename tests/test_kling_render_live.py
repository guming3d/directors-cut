"""Live integration test for render_kling_clip using real Kling credentials.

Run this test only when KLING_ACCESS_KEY and KLING_SECRET_KEY are configured in
.env so it can hit the actual Kling API and download a short video clip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sora_workflow.kling import render_kling_clip  # noqa: E402


REQUIRED_ENV_VARS = ("KLING_ACCESS_KEY", "KLING_SECRET_KEY")


def _truthy_env(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


RUN_KLING_LIVE_TESTS = _truthy_env("RUN_KLING_LIVE_TESTS")


def _has_kling_credentials() -> bool:
    return all(os.getenv(var) for var in REQUIRED_ENV_VARS)


@pytest.mark.skipif(
    not (RUN_KLING_LIVE_TESTS and _has_kling_credentials()),
    reason="Set RUN_KLING_LIVE_TESTS=1 and provide KLING_ACCESS_KEY/KLING_SECRET_KEY to exercise the live Kling API.",
)
@pytest.mark.asyncio
async def test_render_kling_clip_live_round_trip(tmp_path):
    """Exercise render_kling_clip end-to-end against the Kling API."""

    output_path = tmp_path / "kling_live_test.mp4"
    prompt_text = (
        "A cinematic close-up of a small glass orb suspended over water with subtle "
        "ripples and volumetric light rays, 16mm film look, slow push-in."
    )

    result = await render_kling_clip(
        prompt_text=prompt_text,
        duration_seconds=5,
        resolution="720x1280",
        output_path=output_path,
        external_task_id="pytest-kling-live",
    )

    assert output_path.is_file(), "render_kling_clip should download the resulting MP4"
    assert output_path.stat().st_size > 0, "rendered clip should have non-zero size"
    assert result.task_id, "Kling response should include a task_id"
    assert result.video_url.startswith("http"), "Kling response should contain a video URL"
