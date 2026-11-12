from __future__ import annotations

import json

import httpx
import pytest
from PIL import Image

from backend import config, fal, provider_limits
from backend.fal import FalAPIError
from backend.models import SingleVideoPayload, StartWorkflowPayload

ENDPOINT = "https://foundry.example.services.ai.azure.com"
BASE = f"{ENDPOINT}/providers/falai/v1"


@pytest.fixture(autouse=True)
def _fal_settings(monkeypatch):
    monkeypatch.setattr(fal, "FOUNDRY_FAL_ENDPOINT", ENDPOINT)
    monkeypatch.setattr(fal, "FOUNDRY_FAL_AUTH", "key")
    monkeypatch.setattr(fal, "FOUNDRY_FAL_API_KEY", "foundry-test-key")
    monkeypatch.setattr(fal, "FAL_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(fal, "FAL_GENERATE_AUDIO", True)
    real_sleep = fal.asyncio.sleep
    monkeypatch.setattr(fal.asyncio, "sleep", lambda seconds: real_sleep(0))


class _FakeFoundry:
    """providers/falai double: submit, then the queued status sequence, result, asset."""

    def __init__(self, statuses, submit_codes=(200,), result=None, result_code=200, asset_host=ENDPOINT):
        self.statuses = list(statuses)
        self.submit_codes = list(submit_codes)
        self.video_url = f"{asset_host}/providers/falai/v1/assets/req-1/asset-1"
        self.result = result if result is not None else {"video": {"url": self.video_url}}
        self.result_code = result_code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST":
            code = self.submit_codes.pop(0)
            if code != 200:
                return httpx.Response(code, text="busy")
            return httpx.Response(200, json={"request_id": "req-1", "status": "IN_QUEUE"})
        if url == self.video_url:
            return httpx.Response(200, content=b"fake-mp4", headers={"Content-Type": "video/mp4"})
        if url.endswith("/status"):
            return httpx.Response(200, json={"request_id": "req-1", "status": self.statuses.pop(0)})
        return httpx.Response(self.result_code, json=self.result)

    def submits(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def fake_foundry(monkeypatch):
    def _install(*args, **kwargs) -> _FakeFoundry:
        foundry = _FakeFoundry(*args, **kwargs)
        real_client = httpx.AsyncClient

        def _client(*client_args, **client_kwargs):
            client_kwargs["transport"] = httpx.MockTransport(foundry.handler)
            return real_client(*client_args, **client_kwargs)

        monkeypatch.setattr(fal.httpx, "AsyncClient", _client)
        return foundry

    return _install


def _render(provider, tmp_path, **kwargs):
    return fal.render_fal_clip(
        provider=provider,
        prompt_text="A paper boat drifts.",
        duration_seconds=kwargs.pop("duration_seconds", 4),
        resolution=kwargs.pop("resolution", "720x1280"),
        output_path=tmp_path / "clip.mp4",
        **kwargs,
    )


def _start_image(tmp_path):
    path = tmp_path / "start.png"
    Image.new("RGB", (72, 128), "red").save(path)
    return str(path)


# Request shapes ------------------------------------------------------------


def test_kling_payload_sends_duration_as_string_and_ratio_without_image():
    payload = fal.build_fal_payload(
        config.FAL_MODELS["fal-kling-v3-pro"], prompt_text="x", duration_seconds=4, resolution="720x1280"
    )

    assert (payload["model"], payload["duration"], payload["aspect_ratio"]) == ("Fal-Kling-V3-Pro", "4", "9:16")


def test_kling_payload_takes_frame_from_start_image():
    payload = fal.build_fal_payload(
        config.FAL_MODELS["fal-kling-v3-pro"],
        prompt_text="x",
        duration_seconds=4,
        resolution="720x1280",
        image_url="data:image/jpeg;base64,AA",
    )

    assert (payload["start_image_url"], "aspect_ratio" in payload) == ("data:image/jpeg;base64,AA", False)


def test_ltx_payload_maps_1080_landscape_to_its_enums():
    payload = fal.build_fal_payload(
        config.FAL_MODELS["fal-ltx-2.5-pro"], prompt_text="x", duration_seconds=6, resolution="1920x1080"
    )

    assert (payload["duration"], payload["resolution"], payload["aspect_ratio"]) == (6, "1080p", "16:9")


def test_minimax_payload_uses_768p_and_requires_prompt_expansion():
    payload = fal.build_fal_payload(
        config.FAL_MODELS["fal-minimax-h3-max-turbo"], prompt_text="x", duration_seconds=4, resolution="720x1280"
    )

    assert (payload["resolution"], payload["prompt_expansion_mode"], payload["aspect_ratio"]) == (
        "768P",
        config.FAL_MINIMAX_PROMPT_EXPANSION,
        "9:16",
    )


def test_minimax_payload_has_no_audio_flag():
    payload = fal.build_fal_payload(
        config.FAL_MODELS["fal-minimax-h3-max"], prompt_text="x", duration_seconds=4, resolution="720x1280"
    )

    assert "generate_audio" not in payload


def test_image_data_uri_is_jpeg(tmp_path):
    assert fal.image_data_uri(_start_image(tmp_path)).startswith("data:image/jpeg;base64,")


# Render lifecycle ----------------------------------------------------------


@pytest.mark.asyncio
async def test_render_downloads_video_after_completion(tmp_path, fake_foundry):
    fake_foundry(["IN_QUEUE", "IN_PROGRESS", "COMPLETED"])

    await _render("fal-minimax-h3-max", tmp_path)

    assert (tmp_path / "clip.mp4").read_bytes() == b"fake-mp4"


@pytest.mark.asyncio
async def test_render_submits_text_to_video_without_image(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"])

    await _render("fal-ltx-2.5-pro", tmp_path, duration_seconds=6)

    assert str(foundry.submits()[0].url) == f"{BASE}/lightricks/ltx-2.5/text-to-video/pro"


@pytest.mark.asyncio
async def test_render_submits_image_to_video_with_start_image(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"])

    await _render("fal-kling-v3-pro", tmp_path, primary_image_path=_start_image(tmp_path))

    body = json.loads(foundry.submits()[0].content)
    assert (str(foundry.submits()[0].url), body["start_image_url"][:23]) == (
        f"{BASE}/kling/v3/pro/image-to-video",
        "data:image/jpeg;base64,",
    )


@pytest.mark.asyncio
async def test_render_polls_the_models_queue_route(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"])

    await _render("fal-ltx-2.5-pro", tmp_path, duration_seconds=6)

    status_urls = [str(r.url) for r in foundry.requests if str(r.url).endswith("/status")]
    assert status_urls == [f"{BASE}/lightricks/ltx-2.5/requests/req-1/status"]


@pytest.mark.asyncio
async def test_render_sends_credentials_to_the_foundry_asset(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"])

    await _render("fal-minimax-h3-max", tmp_path)

    download = next(r for r in foundry.requests if str(r.url) == foundry.video_url)
    assert download.headers.get("api-key") == "foundry-test-key"


@pytest.mark.asyncio
async def test_render_never_sends_credentials_to_another_host(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"], asset_host="https://cdn.example.com")

    await _render("fal-minimax-h3-max", tmp_path)

    download = next(r for r in foundry.requests if str(r.url) == foundry.video_url)
    assert "api-key" not in download.headers


@pytest.mark.asyncio
async def test_render_retries_rate_limited_submits(tmp_path, fake_foundry):
    foundry = fake_foundry(["COMPLETED"], submit_codes=(429, 429, 200))

    result = await _render("fal-minimax-h3-max", tmp_path)

    assert (result.request_id, len(foundry.submits())) == ("req-1", 3)


@pytest.mark.asyncio
async def test_render_does_not_retry_submit_on_server_error(tmp_path, fake_foundry):
    foundry = fake_foundry([], submit_codes=(503, 200))

    with pytest.raises(FalAPIError, match="may already be running"):
        await _render("fal-minimax-h3-max", tmp_path)
    assert len(foundry.submits()) == 1


@pytest.mark.asyncio
async def test_render_reports_the_models_error_when_the_result_fails(tmp_path, fake_foundry):
    fake_foundry(["COMPLETED"], result={"detail": "content policy violation"}, result_code=422)

    with pytest.raises(FalAPIError, match="content policy violation"):
        await _render("fal-minimax-h3-max", tmp_path)


@pytest.mark.asyncio
async def test_render_rejects_durations_the_model_does_not_offer(tmp_path, fake_foundry):
    foundry = fake_foundry([])

    with pytest.raises(FalAPIError, match="6, 8, 10"):
        await _render("fal-ltx-2.5-pro", tmp_path, duration_seconds=4)
    assert foundry.requests == []


@pytest.mark.asyncio
async def test_render_requires_an_endpoint(tmp_path, monkeypatch, fake_foundry):
    monkeypatch.setattr(fal, "FOUNDRY_FAL_ENDPOINT", "")
    fake_foundry([])

    with pytest.raises(FalAPIError, match="FOUNDRY_FAL_ENDPOINT"):
        await _render("fal-minimax-h3-max", tmp_path)


def test_write_logs_omit_the_inline_image(tmp_path):
    spec = config.FAL_MODELS["fal-kling-v3-pro"]
    result = fal.FalRenderResult(
        provider="fal-kling-v3-pro",
        request_id="req-1",
        endpoint=f"{BASE}/kling/v3/pro/image-to-video",
        request_payload=fal._loggable_payload({"start_image_url": "data:image/jpeg;base64," + "A" * 500}, spec),
        final_response={},
        video_url="https://x/asset",
    )

    log_path, _ = fal.write_fal_logs(result, tmp_path / "clip.mp4", ".client.log")

    assert "base64" not in log_path.read_text(encoding="utf-8")


# App integration -----------------------------------------------------------


@pytest.mark.parametrize("provider", config.FAL_VIDEO_PROVIDERS)
def test_fal_providers_are_selectable(provider):
    assert config.normalize_video_provider(provider) == provider


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", config.FAL_VIDEO_PROVIDERS)
async def test_each_fal_deployment_has_its_own_slot(provider):
    assert provider_limits.provider_slot(provider)._value == config.FAL_MAX_CONCURRENT_TASKS


@pytest.mark.parametrize("provider", ["fal-kling-v3-pro", "fal-minimax-h3-max", "fal-minimax-h3-max-turbo"])
def test_fal_models_start_at_four_seconds(provider):
    assert config.allowed_durations_for_provider(provider)[0] == 4


def test_ltx_starts_at_its_six_second_minimum():
    assert config.allowed_durations_for_provider("fal-ltx-2.5-pro")[0] == 6


def test_workflow_payload_rejects_four_second_ltx_scenes():
    with pytest.raises(ValueError):
        StartWorkflowPayload(script="**Scene 1 (T)**\nText", video_provider="fal-ltx-2.5-pro", scene_duration=4)


def test_single_video_payload_accepts_four_second_minimax_clip():
    payload = SingleVideoPayload(prompt="A cat.", video_provider="fal-minimax-h3-max", duration=4)

    assert payload.duration == 4


def test_durations_env_keeps_only_values_the_model_accepts(monkeypatch):
    monkeypatch.setenv("FAL_TEST_DURATIONS", "4, 6, 99")

    assert config._durations_env("FAL_TEST_DURATIONS", (6,), (6, 8, 10)) == (6,)


def test_durations_env_falls_back_on_bad_input(monkeypatch):
    monkeypatch.setenv("FAL_TEST_DURATIONS", "four")

    assert config._durations_env("FAL_TEST_DURATIONS", (4, 8), (4, 8)) == (4, 8)
