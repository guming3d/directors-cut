import asyncio
import sys
import types

import pytest

from backend import config


@pytest.fixture(autouse=True)
def _reset_video_client(monkeypatch):
    monkeypatch.setattr(config, "_VIDEO_CLIENT", None)
    monkeypatch.setenv("ENDPOINT_URL", "https://example.openai.azure.com")


def test_video_client_in_entra_mode_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(config, "AZURE_OPENAI_AUTH", "entra")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    async def _token() -> str:
        return "entra-token"

    monkeypatch.setattr(config, "entra_token_provider", lambda: _token)

    assert config.video_client() is not None


def test_video_client_in_key_mode_requires_api_key(monkeypatch):
    monkeypatch.setattr(config, "AZURE_OPENAI_AUTH", "key")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AZURE_OPENAI_AUTH=entra"):
        config.video_client()


def test_entra_token_provider_requests_cognitive_services_scope(monkeypatch):
    requested: list[str] = []

    def _get_bearer_token_provider(credential, scope):
        requested.append(scope)
        return lambda: "entra-token"

    fake_identity = types.SimpleNamespace(
        DefaultAzureCredential=lambda: object(),
        get_bearer_token_provider=_get_bearer_token_provider,
    )
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity)

    token = asyncio.run(config.entra_token_provider()())

    assert (token, requested) == ("entra-token", ["https://cognitiveservices.azure.com/.default"])
