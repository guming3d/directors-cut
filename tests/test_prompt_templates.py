import re
from pathlib import Path

import pytest

from backend import first_frames, prompts
from backend.models import ScenePlan

TEMPLATE_DIR = Path(prompts.PROMPT_TEMPLATE_DIR)
PROVIDERS = ("sora2", "kling", "jimeng", "seedance")


class _RecordingClient:
    def __init__(self, output_text="**Scene 1 (Only)**\nText."):
        self.calls: list[dict] = []
        outer = self

        class _Responses:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return type("R", (), {"output_text": output_text})()

        self.responses = _Responses()

    def sent_text(self) -> str:
        return " ".join(part["text"] for item in self.calls[0]["input"] for part in item["content"])


@pytest.mark.parametrize("provider", PROVIDERS)
def test_workflow_templates_do_not_hard_code_runtime_or_style(provider):
    text = prompts.get_workflow_template_text(provider)

    assert not re.search(r"\b(60|90|120) seconds\b|3D animation|live-action", text)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_workflow_templates_require_the_parser_header_format(provider):
    assert "**Scene 1 (Short scene title)**" in prompts.get_workflow_template_text(provider)


@pytest.mark.parametrize("name", sorted(prompts.STATIC_PROMPT_FILES))
def test_static_templates_have_no_unknown_placeholders(name):
    known = {
        "settings", "prompt", "duration_hint", "scene_label", "video_model", "resolution",
        "visual_style", "face_rule", "prompt_text", "character_name", "character_description",
        "segment_description",
    }
    placeholders = set(re.findall(r"\$(\w+)", prompts.STATIC_PROMPT_FILES[name].read_text(encoding="utf-8")))

    assert placeholders <= known


def test_settings_describe_clip_length_frame_and_audio():
    text = prompts.ProductionSettings(
        video_provider="jimeng", clip_seconds=5, resolution="720x1280", visual_style="2d_animation"
    ).describe()

    assert all(part in text for part in ("5-second clip", "9:16 portrait", "no sound", "800 characters"))


def test_settings_describe_start_image_when_used():
    text = prompts.ProductionSettings(video_provider="sora2", uses_start_image=True).describe()

    assert "generated first-frame image" in text


@pytest.mark.asyncio
async def test_workflow_refinement_sends_production_settings(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.refine_text_with_llm(
        "A cat stretches.",
        mode="workflow",
        settings=prompts.ProductionSettings(video_provider="seedance", clip_seconds=4, resolution="1280x720"),
    )

    assert "exactly one 4-second clip" in client.sent_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", PROVIDERS)
async def test_single_refinement_uses_the_providers_template(monkeypatch, provider):
    client = _RecordingClient("Refined.")
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.refine_text_with_llm(
        "A cat stretches.", mode="single", settings=prompts.ProductionSettings(video_provider=provider)
    )

    assert prompts.provider_profile(provider).label in client.sent_text()


def test_length_check_stops_over_limit_kling_prompt_before_rendering():
    with pytest.raises(RuntimeError, match="Scene 2"):
        prompts.check_prompt_lengths([("Scene 1", "ok"), ("Scene 2", "x" * 2600)], "kling")


def test_length_check_allows_long_sora_prompts():
    prompts.check_prompt_lengths([("Scene 1", "x" * 6000)], "sora2")


def _plan():
    return ScenePlan(order=1, label="Scene 1: Dawn", duration_seconds=4, description="d", prompt_path="p.txt", prompt_text="A woman waves.")


def test_sora_first_frame_prompt_keeps_human_faces_out():
    assert "no human face" in first_frames._fallback_image_prompt(_plan(), "720x1280", video_provider="sora2").lower()


def test_other_providers_first_frames_have_no_face_rule():
    assert "human face" not in first_frames._fallback_image_prompt(_plan(), "720x1280", video_provider="kling")


def test_kling_profile_reports_generated_sound_by_default():
    assert "speech" in prompts.PROVIDER_PROFILES["kling"].audio
