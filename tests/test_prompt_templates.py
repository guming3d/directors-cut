import re
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from backend import first_frames, image_utils, prompts
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
        "segment_description", "framing_note", "reference_note",
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


def test_landscape_framing_note_gives_the_smaller_top_trim():
    note = image_utils.framing_note("1280x720")

    assert "top 4% and bottom 12%" in note


def test_landscape_trim_keeps_more_of_the_top_than_a_centered_crop():
    canvas = Image.new("RGB", (1536, 1024), "blue")
    canvas.paste(Image.new("RGB", (1536, 60), "red"), (0, 0))
    buffer = BytesIO()
    canvas.save(buffer, format="PNG")

    trimmed = Image.open(BytesIO(image_utils.trim_image_bytes(buffer.getvalue(), "1280x720")))

    assert trimmed.getpixel((640, 2)) == (255, 0, 0)


def test_portrait_framing_note_names_the_trimmed_sides():
    note = image_utils.framing_note("720x1280")

    assert "left 8% and right 8%" in note


def test_first_frame_prompt_forbids_lettering_on_props():
    prompt = first_frames._fallback_image_prompt(_plan(), "1280x720", video_provider="kling")

    assert "signs, jars, labels" in prompt


def test_first_frame_prompt_includes_the_framing_note():
    prompt = first_frames._fallback_image_prompt(_plan(), "1280x720", video_provider="kling")

    assert image_utils.framing_note("1280x720") in prompt


def test_3d_style_rules_out_2d_rendering():
    assert "not 2D" in prompts.get_visual_style_description("3d_animation")


@pytest.mark.asyncio
async def test_first_frame_prompt_names_each_attached_reference(monkeypatch):
    client = _RecordingClient(output_text="An image prompt.")
    monkeypatch.setattr(first_frames, "azure_text_client", lambda: client)

    await first_frames.build_first_frame_prompt(_plan(), "1280x720", reference_names=["Mango", "Pip"])

    assert "Image 1: Mango; Image 2: Pip" in client.sent_text()


@pytest.mark.asyncio
async def test_first_frame_prompt_says_when_no_reference_is_attached(monkeypatch):
    client = _RecordingClient(output_text="An image prompt.")
    monkeypatch.setattr(first_frames, "azure_text_client", lambda: client)

    await first_frames.build_first_frame_prompt(_plan(), "1280x720")

    assert "No character reference images are attached" in client.sent_text()


@pytest.mark.asyncio
async def test_first_frame_image_prompt_is_saved_beside_the_image(tmp_path, monkeypatch):
    async def fake_prompt(*args, **kwargs):
        return "Saved image prompt."

    async def fake_image(*args, **kwargs):
        return b"png"

    monkeypatch.setattr(first_frames, "build_first_frame_prompt", fake_prompt)
    monkeypatch.setattr(first_frames, "_generate_image_bytes", fake_image)
    monkeypatch.setattr(first_frames, "trim_image_bytes", lambda data, resolution: data)
    request = type("R", (), {"first_frame_dir": str(tmp_path), "output_dir": str(tmp_path),
                             "resolution": "1280x720", "visual_style": "3d_animation",
                             "video_provider": "kling"})()

    await first_frames.generate_first_frame_image(_plan(), request)

    assert (tmp_path / "scene_01_first_frame_prompt.txt").read_text(encoding="utf-8") == "Saved image prompt."


def test_kling_profile_reports_generated_sound_by_default():
    assert "speech" in prompts.PROVIDER_PROFILES["kling"].audio


def test_blob_mode_saves_template_edits_as_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "PROMPT_OVERRIDE_DIR", tmp_path / "prompt_overrides")
    prompts._read_prompt_file.cache_clear()
    prompts._workflow_template_for_provider.cache_clear()
    shipped = prompts.WORKFLOW_TEMPLATE_FILES["kling"].read_text(encoding="utf-8")
    try:
        prompts.update_workflow_template_text("kling", "Edited in the dashboard")

        assert (
            prompts.get_workflow_template_text("kling"),
            prompts.WORKFLOW_TEMPLATE_FILES["kling"].read_text(encoding="utf-8"),
        ) == ("Edited in the dashboard", shipped)
    finally:
        prompts._read_prompt_file.cache_clear()
        prompts._workflow_template_for_provider.cache_clear()


def test_local_mode_has_no_prompt_override_dir():
    assert prompts.PROMPT_OVERRIDE_DIR is None
