from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend import provider_limits, seedance, sora_runner
from backend.seedance import SeedanceAPIError


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["sora2", "kling", "jimeng", "seedance"])
async def test_each_provider_slot_allows_its_configured_concurrency(provider):
    slot = provider_limits.provider_slot(provider)

    assert slot._value == provider_limits.PROVIDER_CONCURRENCY[provider]


@pytest.mark.asyncio
async def test_parallel_sora_jobs_never_exceed_the_sora_cap(monkeypatch):
    state = {"now": 0, "peak": 0}

    async def _fake_run(cmd, cwd=None, env=None):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return "ok", ""

    monkeypatch.setattr(sora_runner, "run_subprocess", _fake_run)

    await asyncio.gather(*(
        sora_runner.run_sora_subprocess_with_retry(
            ["sora"], operation_label=f"Scene {i}", moderation_attempts=1, transient_attempts=1
        )
        for i in range(5)
    ))

    assert state["peak"] == provider_limits.PROVIDER_CONCURRENCY["sora2"]


def _ark(monkeypatch, create_statuses):
    creates: list[int] = []
    statuses = list(create_statuses)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            status = statuses.pop(0)
            creates.append(status)
            if status != 200:
                return httpx.Response(status, text="busy")
            return httpx.Response(200, json={"id": "cgt-1"})
        if str(request.url).endswith(".mp4"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "succeeded", "content": {"video_url": "https://cdn.example/v.mp4"}})

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(seedance.httpx, "AsyncClient", _client)
    monkeypatch.setattr(seedance, "ARK_API_KEY", "k")
    monkeypatch.setattr(seedance, "SEEDANCE_POLL_INTERVAL_SECONDS", 0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(seedance.asyncio, "sleep", lambda seconds: real_sleep(0))
    return creates


@pytest.mark.asyncio
async def test_seedance_create_retries_rate_limited_requests(tmp_path, monkeypatch):
    creates = _ark(monkeypatch, [429, 429, 200])

    result = await seedance.render_seedance_clip(
        prompt_text="x", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
    )

    assert (result.task_id, creates) == ("cgt-1", [429, 429, 200])


@pytest.mark.asyncio
async def test_seedance_create_does_not_retry_server_errors(tmp_path, monkeypatch):
    creates = _ark(monkeypatch, [503, 200])

    with pytest.raises(SeedanceAPIError, match="may already exist"):
        await seedance.render_seedance_clip(
            prompt_text="x", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
        )
    assert creates == [503]
