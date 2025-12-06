import textwrap

import pytest

from backend import prompts
from backend.prompts import build_prompt_text, parse_script


def test_parse_script_extracts_scenes():
    script = textwrap.dedent(
        """
        **Scene 1 (Intro)**
        A quick intro shot.

        **Scene 2 (Beach)**
        A sunny shoreline.
        """
    )
    drafts = parse_script(script)

    assert len(drafts) == 2
    assert drafts[0].title == "Intro"
    assert drafts[0].description_lines == ["A quick intro shot."]
    assert drafts[1].scene_number == 2


def test_build_prompt_text_includes_orientation():
    prompt = build_prompt_text("Intro", "A short line.", 4, "720x1280")
    assert prompt.startswith("Intro")
    assert "A short line." in prompt
    assert "Scene title" not in prompt
    assert "Clip duration" not in prompt
    assert "Output resolution" not in prompt


def test_update_workflow_template_text_round_trip(tmp_path, monkeypatch):
    template_path = tmp_path / "sora_prompt_generation.txt"
    template_path.write_text("Original template body\n", encoding="utf-8")

    monkeypatch.setattr(
        prompts,
        "WORKFLOW_TEMPLATE_FILES",
        {"sora2": template_path, "kling": template_path},
    )
    prompts._read_prompt_file.cache_clear()
    prompts._workflow_template_for_provider.cache_clear()

    current = prompts.get_workflow_template_text("sora2")
    assert "Original template body" in current

    updated = prompts.update_workflow_template_text("sora2", "Updated template body")
    assert updated == "Updated template body"
    assert template_path.read_text(encoding="utf-8").strip() == "Updated template body"

    refreshed = prompts.get_workflow_template_text("sora2")
    assert refreshed == "Updated template body"

    prompts._read_prompt_file.cache_clear()
    prompts._workflow_template_for_provider.cache_clear()


def test_update_workflow_template_text_rejects_empty(tmp_path, monkeypatch):
    template_path = tmp_path / "sora_prompt_generation.txt"
    template_path.write_text("content\n", encoding="utf-8")
    monkeypatch.setattr(
        prompts,
        "WORKFLOW_TEMPLATE_FILES",
        {"sora2": template_path, "kling": template_path},
    )
    prompts._read_prompt_file.cache_clear()
    prompts._workflow_template_for_provider.cache_clear()

    with pytest.raises(ValueError):
        prompts.update_workflow_template_text("sora2", "   ")

    prompts._read_prompt_file.cache_clear()
    prompts._workflow_template_for_provider.cache_clear()
