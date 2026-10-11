"""Tests for character extraction and reference image generation."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import characters
from backend.characters import (
    extract_characters,
    generate_character_ref_image,
    prepare_character_refs,
)
from backend.models import CharacterReference


SAMPLE_SCRIPT = """
**Scene 1 (Meeting)**
A young woman with long red hair and a green jacket meets an elderly man
with white beard and a brown coat at a coffee shop.

**Scene 2 (Chase)**
The young woman with long red hair sprints through the market while the
elderly man with white beard follows on a bicycle.

**Scene 3 (Resolution)**
They both sit on a park bench. The young woman hands the elderly man a letter.
"""

SAMPLE_EXTRACTION_RESPONSE = json.dumps([
    {
        "name": "young woman",
        "description": "Female, early 20s, long red hair, fair skin, slim build, wearing a green jacket and dark jeans",
    },
    {
        "name": "elderly man",
        "description": "Male, late 60s, white beard, weathered skin, stocky build, wearing a brown wool coat and flat cap",
    },
])


@pytest.mark.asyncio
async def test_extract_characters_parses_json(monkeypatch):
    """Verify that valid JSON from the LLM is correctly parsed into CharacterReference objects."""

    class _FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(text=SAMPLE_EXTRACTION_RESPONSE)])])

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(characters, "azure_text_client", lambda: _FakeClient())
    monkeypatch.setattr(characters, "extract_response_text", lambda r: SAMPLE_EXTRACTION_RESPONSE)

    result = await extract_characters(SAMPLE_SCRIPT)
    assert len(result) == 2
    assert result[0].name == "young woman"
    assert "red hair" in result[0].description
    assert result[1].name == "elderly man"


@pytest.mark.asyncio
async def test_extract_characters_returns_empty_on_no_client(monkeypatch):
    """When no text client is available, extraction returns an empty list."""
    monkeypatch.setattr(characters, "azure_text_client", lambda: None)

    result = await extract_characters(SAMPLE_SCRIPT)
    assert result == []


@pytest.mark.asyncio
async def test_extract_characters_returns_empty_on_bad_json(monkeypatch):
    """When the LLM returns unparseable output, extraction returns empty list."""

    class _FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[])

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(characters, "azure_text_client", lambda: _FakeClient())
    monkeypatch.setattr(characters, "extract_response_text", lambda r: "not valid json {{{")

    result = await extract_characters(SAMPLE_SCRIPT)
    assert result == []


@pytest.mark.asyncio
async def test_extract_characters_returns_empty_on_exception(monkeypatch):
    """When the LLM call raises, extraction returns empty list."""

    class _FakeResponses:
        def create(self, **kwargs):
            raise RuntimeError("API down")

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(characters, "azure_text_client", lambda: _FakeClient())

    result = await extract_characters(SAMPLE_SCRIPT)
    assert result == []


@pytest.mark.asyncio
async def test_extract_characters_limits_to_max(monkeypatch):
    """Extraction caps to MAX_CHARACTERS even if LLM returns more."""
    many_chars = [{"name": f"char_{i}", "description": f"description {i}"} for i in range(20)]

    class _FakeResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[])

    class _FakeClient:
        responses = _FakeResponses()

    monkeypatch.setattr(characters, "azure_text_client", lambda: _FakeClient())
    monkeypatch.setattr(characters, "extract_response_text", lambda r: json.dumps(many_chars))

    result = await extract_characters("some script")
    assert len(result) == characters.MAX_CHARACTERS


@pytest.mark.asyncio
async def test_generate_character_ref_image_skips_existing(tmp_path):
    """When the target image already exists, generation is skipped."""
    output_dir = tmp_path / "refs"
    output_dir.mkdir()
    existing = output_dir / "hero_ref.png"
    existing.write_bytes(b"\x89PNG fake")

    char = CharacterReference(name="hero", description="a brave hero")
    result = await generate_character_ref_image(char, "720x1280", output_dir)

    assert result == str(existing)
    assert char.image_path == str(existing)


@pytest.mark.asyncio
async def test_generate_character_ref_image_returns_none_on_no_client(tmp_path, monkeypatch):
    """When no image client is available, returns None."""
    monkeypatch.setattr(characters, "azure_image_client", lambda: None)

    char = CharacterReference(name="hero", description="a brave hero")
    result = await generate_character_ref_image(char, "720x1280", tmp_path / "refs")
    assert result is None


@pytest.mark.asyncio
async def test_generate_character_ref_image_creates_file(tmp_path, monkeypatch):
    """When the image client succeeds, a PNG file is created."""
    import base64

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    fake_b64 = base64.b64encode(fake_png).decode()

    class _FakeImages:
        def generate(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(b64_json=fake_b64)])

    class _FakeClient:
        images = _FakeImages()

    monkeypatch.setattr(characters, "azure_image_client", lambda: _FakeClient())
    monkeypatch.setattr(characters, "trim_image_bytes", lambda raw, res: raw)

    output_dir = tmp_path / "refs"
    char = CharacterReference(name="test hero", description="a hero with a cape")
    result = await generate_character_ref_image(char, "720x1280", output_dir)

    assert result is not None
    assert Path(result).is_file()
    assert char.image_path == result


@pytest.mark.asyncio
async def test_prepare_character_refs_end_to_end(tmp_path, monkeypatch):
    """End-to-end test: extraction + image generation together."""
    import base64

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    fake_b64 = base64.b64encode(fake_png).decode()

    class _FakeTextResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output=[])

    class _FakeTextClient:
        responses = _FakeTextResponses()

    class _FakeImages:
        def generate(self, **kwargs):
            return SimpleNamespace(data=[SimpleNamespace(b64_json=fake_b64)])

    class _FakeImageClient:
        images = _FakeImages()

    monkeypatch.setattr(characters, "azure_text_client", lambda: _FakeTextClient())
    monkeypatch.setattr(characters, "extract_response_text", lambda r: SAMPLE_EXTRACTION_RESPONSE)
    monkeypatch.setattr(characters, "azure_image_client", lambda: _FakeImageClient())
    monkeypatch.setattr(characters, "trim_image_bytes", lambda raw, res: raw)

    output_dir = tmp_path / "char_refs"
    result = await prepare_character_refs(SAMPLE_SCRIPT, "720x1280", output_dir)

    assert len(result) == 2
    ref_files = list(output_dir.glob("*_ref.png"))
    assert len(ref_files) == 2


@pytest.mark.asyncio
async def test_prepare_character_refs_empty_on_extraction_failure(tmp_path, monkeypatch):
    """If extraction itself fails hard, prepare returns empty list."""
    monkeypatch.setattr(characters, "azure_text_client", lambda: None)

    result = await prepare_character_refs("some script", "720x1280", tmp_path / "refs")
    assert result == []


@pytest.mark.asyncio
async def test_extract_characters_reads_the_scenes_each_character_appears_in(monkeypatch):
    response = json.dumps([
        {"name": "protagonist", "description": "woman, about 30", "scenes": [2, 3, "4", "x"]},
        {"name": "elder", "description": "woman, about 60"},
    ])
    monkeypatch.setattr(characters, "azure_text_client", lambda: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: None)))
    monkeypatch.setattr(characters, "extract_response_text", lambda r: response)

    result = await extract_characters(SAMPLE_SCRIPT)

    assert [c.scene_orders for c in result] == [[2, 3, 4], []]


def test_scene_header_numbers_are_mapped_to_scene_positions():
    refs = [CharacterReference(name="a", description="d", scene_orders=[3, 5, 9])]

    characters.map_scene_numbers(refs, {3: 1, 5: 2})

    assert refs[0].scene_orders == [1, 2]


def _fake_image_client(generate):
    return SimpleNamespace(images=SimpleNamespace(generate=generate))


@pytest.mark.asyncio
async def test_reference_images_are_generated_one_at_a_time(tmp_path, monkeypatch):
    import base64
    import threading
    import time

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def generate(**kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())])

    monkeypatch.setattr(characters, "azure_text_client", lambda: object())
    monkeypatch.setattr(characters, "extract_response_text", lambda r: SAMPLE_EXTRACTION_RESPONSE)
    monkeypatch.setattr(characters, "azure_image_client", lambda: _fake_image_client(generate))
    monkeypatch.setattr(characters, "trim_image_bytes", lambda raw, res: raw)

    async def fake_extract(script_text):
        return [CharacterReference(name="one", description="d"), CharacterReference(name="two", description="d")]

    monkeypatch.setattr(characters, "extract_characters", fake_extract)

    await prepare_character_refs(SAMPLE_SCRIPT, "720x1280", tmp_path / "refs")

    assert state["peak"] == 1


@pytest.mark.asyncio
async def test_rate_limited_reference_image_is_retried(tmp_path, monkeypatch):
    import base64

    import httpx
    import openai

    from backend import image_utils

    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
            raise openai.RateLimitError("rate limited", response=response, body=None)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode())])

    monkeypatch.setattr(characters, "azure_image_client", lambda: _fake_image_client(generate))
    monkeypatch.setattr(characters, "trim_image_bytes", lambda raw, res: raw)
    monkeypatch.setattr(image_utils.time, "sleep", lambda seconds: None)

    char = CharacterReference(name="hero", description="d")
    result = await generate_character_ref_image(char, "720x1280", tmp_path / "refs")

    assert result is not None and len(calls) == 2
