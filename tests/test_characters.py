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
