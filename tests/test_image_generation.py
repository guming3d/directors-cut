from openai import OpenAI

from backend import config
from backend.image_generation import AzureImageGenerator, get_image_generator


def test_legacy_gpt_image_1_value_maps_to_gpt_image_2_5():
    assert config.normalize_image_generator("gpt-image-1") == "gpt-image-2.5"


def test_unknown_image_generator_falls_back_to_default():
    assert config.normalize_image_generator("dall-e-2") == "gpt-image-2.5"


def test_get_image_generator_accepts_legacy_name():
    assert isinstance(get_image_generator("gpt-image-1"), AzureImageGenerator)


def test_image_client_is_none_without_foundry_key(monkeypatch):
    monkeypatch.setattr(config, "FOUNDRY_IMAGE_API_KEY", None)
    monkeypatch.setattr(config, "_IMAGE_CLIENT", None)

    assert config.azure_image_client() is None


def test_image_client_targets_foundry_v1_endpoint(monkeypatch):
    monkeypatch.setattr(config, "FOUNDRY_IMAGE_API_KEY", "test-key")
    monkeypatch.setattr(config, "FOUNDRY_IMAGE_ENDPOINT", "https://example.services.ai.azure.com/openai/v1")
    monkeypatch.setattr(config, "_IMAGE_CLIENT", None)

    client = config.azure_image_client()

    assert isinstance(client, OpenAI)
    assert str(client.base_url).rstrip("/") == "https://example.services.ai.azure.com/openai/v1"


def test_image_client_disables_sdk_retries(monkeypatch):
    monkeypatch.setattr(config, "FOUNDRY_IMAGE_API_KEY", "test-key")
    monkeypatch.setattr(config, "_IMAGE_CLIENT", None)

    assert config.azure_image_client().max_retries == 0
