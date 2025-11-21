import textwrap

from sora_workflow.prompts import build_prompt_text, parse_script


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
    assert "720x1280" in prompt
    assert "portrait orientation" in prompt
