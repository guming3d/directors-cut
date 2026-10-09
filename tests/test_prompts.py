import textwrap

import pytest

from backend import config, prompts
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


class _RecordingResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_text": self.output_text})()


class _RecordingClient:
    def __init__(self, output_text: str = ""):
        self.responses = _RecordingResponses(output_text)


@pytest.mark.asyncio
async def test_refine_text_with_llm_raises_on_client_failure(monkeypatch):
    class _FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("upstream timeout")

    class _FailingClient:
        responses = _FailingResponses()

    monkeypatch.setattr(prompts, "azure_text_client", lambda: _FailingClient())

    with pytest.raises(RuntimeError, match="refinement failed"):
        await prompts.refine_text_with_llm("hello world", mode="single")


@pytest.mark.asyncio
async def test_refine_text_with_llm_uses_reasoning_deployment_with_medium_effort(monkeypatch):
    client = _RecordingClient("Refined prompt")
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.refine_text_with_llm("hello world", mode="single")

    call = client.responses.calls[0]
    assert (call["model"], call["reasoning"]) == (
        config.REASONING_DEPLOYMENT,
        {"effort": config.REASONING_EFFORT},
    )


@pytest.mark.asyncio
async def test_refine_text_with_llm_reserves_reasoning_token_headroom(monkeypatch):
    client = _RecordingClient("Refined prompt")
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.refine_text_with_llm("hello world", mode="single")

    assert client.responses.calls[0]["max_output_tokens"] > 2000


@pytest.mark.asyncio
async def test_ensure_scene_headers_skips_llm_when_headers_exist(monkeypatch):
    def _fail():
        raise AssertionError("LLM must not be called when scene headers already exist")

    monkeypatch.setattr(prompts, "azure_text_client", _fail)

    script, has_headers, used_llm = await prompts.ensure_scene_headers(
        "**Scene 1 (Intro)**\nA quick intro shot.", use_refinement=False
    )

    assert (has_headers, used_llm) == (True, False)


@pytest.mark.asyncio
async def test_ensure_scene_headers_inserts_separators_without_refine_toggle(monkeypatch):
    client = _RecordingClient(
        "**Scene 1 (Sunrise)**\nA city wakes up.\n\n**Scene 2 (Rush hour)**\nCommuters flood the station."
    )
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    script, has_headers, used_llm = await prompts.ensure_scene_headers(
        "A city wakes up. Commuters flood the station.", use_refinement=False, scene_duration=4
    )

    assert [draft.title for draft in parse_script(script)] == ["Sunrise", "Rush hour"]


@pytest.mark.asyncio
async def test_split_script_into_scenes_sends_duration_hint(monkeypatch):
    client = _RecordingClient("**Scene 1 (Only)**\nText.")
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.split_script_into_scenes("Text.", scene_duration=4)

    system_text = client.responses.calls[0]["input"][0]["content"][0]["text"]
    assert "4-second" in system_text


@pytest.mark.asyncio
async def test_ensure_scene_headers_reports_missing_when_split_returns_no_headers(monkeypatch):
    monkeypatch.setattr(prompts, "azure_text_client", lambda: _RecordingClient("still no headers"))

    _, has_headers, used_llm = await prompts.ensure_scene_headers(
        "A script without separators.", use_refinement=False
    )

    assert (has_headers, used_llm) == (False, True)


@pytest.mark.asyncio
async def test_ensure_scene_headers_splits_after_refinement_drops_headers(monkeypatch):
    client = _RecordingClient("refined but header-less")
    monkeypatch.setattr(prompts, "azure_text_client", lambda: client)

    await prompts.ensure_scene_headers("A script.", use_refinement=True)

    assert len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_split_script_into_scenes_raises_without_client(monkeypatch):
    monkeypatch.setattr(prompts, "azure_text_client", lambda: None)

    with pytest.raises(RuntimeError, match="no scene headers"):
        await prompts.split_script_into_scenes("A script.")
