from __future__ import annotations

import asyncio

import httpx
import pytest

from backend import jimeng
from backend.jimeng import JimengAPIError


class _FakeJimeng:
    """Volcengine double: tracks tasks in flight and can reject submits with 429."""

    def __init__(self, rejections: int = 0):
        self.rejections = rejections
        self.submits = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.polls: dict[str, int] = {}

    async def invoke(self, client, action, body):
        if action == "CVSync2AsyncSubmitTask":
            self.submits += 1
            if self.rejections:
                self.rejections -= 1
                request = httpx.Request("POST", "https://visual.volcengineapi.com")
                raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
            task_id = f"task-{self.submits}"
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.polls[task_id] = 0
            return {"code": 10000, "data": {"task_id": task_id}}
        task_id = body["task_id"]
        self.polls[task_id] += 1
        if self.polls[task_id] < 3:
            await asyncio.sleep(0)
            return {"code": 10000, "data": {"status": "generating"}}
        self.in_flight -= 1
        return {"code": 10000, "data": {"status": "done", "video_url": "https://cdn.example/v.mp4"}}


@pytest.fixture
def fake(monkeypatch):
    def _install(rejections: int = 0) -> _FakeJimeng:
        double = _FakeJimeng(rejections)
        monkeypatch.setattr(jimeng, "_invoke_action", double.invoke)
        monkeypatch.setattr(jimeng, "JIMENG_POLL_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(jimeng, "JIMENG_SUBMIT_RETRY_BASE_DELAY_SECONDS", 0)

        async def _download(client, url, dest):
            dest.write_bytes(b"mp4")

        monkeypatch.setattr(jimeng, "_download_video", _download)
        return double

    return _install


def _render(tmp_path, name):
    return jimeng.render_jimeng_clip(
        prompt_text="A cat stretches.", duration_seconds=5, resolution="1280x720", output_path=tmp_path / name
    )


@pytest.mark.asyncio
async def test_parallel_scenes_queue_for_the_single_jimeng_task_slot(tmp_path, fake):
    double = fake()

    await asyncio.gather(_render(tmp_path, "a.mp4"), _render(tmp_path, "b.mp4"))

    assert (double.submits, double.max_in_flight) == (2, 1)


@pytest.mark.asyncio
async def test_submit_retries_after_429_without_extra_tasks(tmp_path, fake):
    double = fake(rejections=2)

    result = await _render(tmp_path, "a.mp4")

    assert (result.task_id, double.submits) == ("task-3", 3)


@pytest.mark.asyncio
async def test_persistent_429_raises_readable_error(tmp_path, fake, monkeypatch):
    fake(rejections=99)
    monkeypatch.setattr(jimeng, "JIMENG_SUBMIT_MAX_RETRIES", 2)

    with pytest.raises(JimengAPIError, match="concurrent task limit after 3 attempts"):
        await _render(tmp_path, "a.mp4")
