from __future__ import annotations

import json

import httpx
import pytest

from backend import seedance
from backend.seedance import SeedanceAPIError

TASKS_URL = "https://ark.example.com/api/v3/contents/generations/tasks"
VIDEO_URL = "https://cdn.example.com/out.mp4"


@pytest.fixture(autouse=True)
def _seedance_settings(monkeypatch):
    monkeypatch.setattr(seedance, "ARK_API_KEY", "ark-test-key")
    monkeypatch.setattr(seedance, "SEEDANCE_API_URL", TASKS_URL)
    monkeypatch.setattr(seedance, "SEEDANCE_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(seedance, "SEEDANCE_IMAGE_ROLE", "first_frame")
    monkeypatch.setattr(seedance, "SEEDANCE_RESOLUTION", None)


class _FakeArk:
    """Ark task API double: one create, then the queued status sequence."""

    def __init__(self, statuses: list[dict], create_status: int = 200):
        self.statuses = list(statuses)
        self.create_status = create_status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            if self.create_status != 200:
                return httpx.Response(self.create_status, text="upstream error")
            return httpx.Response(200, json={"id": "cgt-123"})
        if str(request.url) == VIDEO_URL:
            return httpx.Response(200, content=b"fake-mp4-bytes", headers={"Content-Type": "video/mp4"})
        return httpx.Response(200, json=self.statuses.pop(0))

    @property
    def creates(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def fake_ark(monkeypatch):
    def _install(statuses: list[dict], create_status: int = 200) -> _FakeArk:
        ark = _FakeArk(statuses, create_status)
        real_client = httpx.AsyncClient

        def _client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(ark.handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(seedance.httpx, "AsyncClient", _client)
        return ark

    return _install


SUCCEEDED = {"id": "cgt-123", "status": "succeeded", "content": {"video_url": VIDEO_URL}}


def test_payload_uses_model_ratio_and_duration():
    payload = seedance.build_seedance_payload(
        prompt_text="A cat jumps.", duration_seconds=4, resolution="720x1280"
    )

    assert (payload["model"], payload["ratio"], payload["duration"]) == (
        seedance.SEEDANCE_MODEL,
        "9:16",
        4,
    )


def test_payload_attaches_image_with_configured_role():
    payload = seedance.build_seedance_payload(
        prompt_text="A cat jumps.",
        duration_seconds=4,
        resolution="1280x720",
        image_url="https://pub.example.com/a.png",
    )

    assert payload["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://pub.example.com/a.png"},
        "role": "first_frame",
    }


def test_payload_marks_reference_image_as_opening_frame_in_prompt(monkeypatch):
    monkeypatch.setattr(seedance, "SEEDANCE_IMAGE_ROLE", "reference_image")

    payload = seedance.build_seedance_payload(
        prompt_text="A cat jumps.",
        duration_seconds=4,
        resolution="1280x720",
        image_url="https://pub.example.com/a.png",
    )

    assert payload["content"][0]["text"].startswith("The video opens on the composition of Image 1.")


def test_payload_omits_resolution_unless_configured():
    payload = seedance.build_seedance_payload(prompt_text="x", duration_seconds=4, resolution="720x1280")

    assert "resolution" not in payload


@pytest.mark.asyncio
async def test_render_downloads_video_after_success(tmp_path, fake_ark):
    fake_ark([{"status": "queued"}, {"status": "running"}, SUCCEEDED])
    output = tmp_path / "scene_01.mp4"

    result = await seedance.render_seedance_clip(
        prompt_text="A cat jumps.", duration_seconds=4, resolution="720x1280", output_path=output
    )

    assert (result.task_id, output.read_bytes()) == ("cgt-123", b"fake-mp4-bytes")


@pytest.mark.asyncio
async def test_render_never_sends_ark_key_to_media_host(tmp_path, fake_ark):
    ark = fake_ark([SUCCEEDED])

    await seedance.render_seedance_clip(
        prompt_text="x", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
    )

    download = next(r for r in ark.requests if str(r.url) == VIDEO_URL)
    assert "authorization" not in download.headers


@pytest.mark.asyncio
async def test_render_sends_payload_to_ark(tmp_path, fake_ark):
    ark = fake_ark([SUCCEEDED])

    await seedance.render_seedance_clip(
        prompt_text="A cat jumps.", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
    )

    body = json.loads(ark.creates[0].content)
    assert (body["duration"], body["content"][0]["text"]) == (4, "A cat jumps.")


@pytest.mark.asyncio
async def test_render_reports_provider_error_on_failed_task(tmp_path, fake_ark):
    fake_ark([{"status": "failed", "error": {"code": "OutputVideoSensitive", "message": "blocked by moderation"}}])

    with pytest.raises(SeedanceAPIError, match="blocked by moderation"):
        await seedance.render_seedance_clip(
            prompt_text="x", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
        )


@pytest.mark.asyncio
async def test_render_does_not_retry_create_on_server_error(tmp_path, fake_ark):
    ark = fake_ark([], create_status=503)

    with pytest.raises(SeedanceAPIError, match="may already exist"):
        await seedance.render_seedance_clip(
            prompt_text="x", duration_seconds=4, resolution="720x1280", output_path=tmp_path / "a.mp4"
        )
    assert len(ark.creates) == 1


@pytest.mark.asyncio
async def test_render_rejects_unsupported_duration_before_any_request(tmp_path, fake_ark):
    ark = fake_ark([])

    with pytest.raises(SeedanceAPIError, match="durations"):
        await seedance.render_seedance_clip(
            prompt_text="x", duration_seconds=7, resolution="720x1280", output_path=tmp_path / "a.mp4"
        )
    assert ark.requests == []


@pytest.mark.asyncio
async def test_render_requires_ark_key_before_uploading_reference(tmp_path, monkeypatch, fake_ark):
    monkeypatch.setattr(seedance, "ARK_API_KEY", None)
    uploads: list[str] = []

    async def _upload(path, prefix=None):
        uploads.append(path)
        return "https://pub.example.com/a.png"

    monkeypatch.setattr(seedance, "upload_public_file_async", _upload)

    with pytest.raises(SeedanceAPIError, match="ARK_API_KEY"):
        await seedance.render_seedance_clip(
            prompt_text="x",
            duration_seconds=4,
            resolution="720x1280",
            output_path=tmp_path / "a.mp4",
            primary_image_path="frame.png",
        )
    assert uploads == []


@pytest.mark.asyncio
async def test_render_uploads_reference_and_sends_public_url(tmp_path, monkeypatch, fake_ark):
    ark = fake_ark([SUCCEEDED])

    async def _upload(path, prefix=None):
        return "https://pub.example.com/frame-abc.png"

    monkeypatch.setattr(seedance, "upload_public_file_async", _upload)

    await seedance.render_seedance_clip(
        prompt_text="x",
        duration_seconds=4,
        resolution="720x1280",
        output_path=tmp_path / "a.mp4",
        primary_image_path="frame.png",
    )

    body = json.loads(ark.creates[0].content)
    assert body["content"][1]["image_url"]["url"] == "https://pub.example.com/frame-abc.png"


def test_write_logs_records_task_and_video_url(tmp_path):
    result = seedance.SeedanceRenderResult(
        task_id="cgt-1", endpoint=TASKS_URL, request_payload={}, final_response={}, video_url=VIDEO_URL
    )

    log_path, _ = seedance.write_seedance_logs(result, tmp_path / "scene_01.mp4", ".client.log")

    logged = json.loads(log_path.read_text(encoding="utf-8"))
    assert (logged["provider"], logged["task_id"], logged["video_url"]) == ("seedance", "cgt-1", VIDEO_URL)
