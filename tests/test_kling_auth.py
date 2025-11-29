import importlib
import os
import sys
from pathlib import Path

import jwt
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.config as config
import backend.kling as kling_module


def _reload_kling(monkeypatch, **env):
    keys = [
        "KLING_ACCESS_KEY",
        "KLING_SECRET_KEY",
        "KLING_TOKEN_TTL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    importlib.reload(config)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.KLING_ACCESS_KEY = os.getenv("KLING_ACCESS_KEY")
    config.KLING_SECRET_KEY = os.getenv("KLING_SECRET_KEY")
    try:
        config.KLING_TOKEN_TTL_SECONDS = max(300, int(os.getenv("KLING_TOKEN_TTL", "1800")))
    except ValueError:
        config.KLING_TOKEN_TTL_SECONDS = 1800
    importlib.reload(kling_module)
    return kling_module


def test_generate_kling_token_from_access_secret(monkeypatch):
    kling = _reload_kling(
        monkeypatch,
        KLING_ACCESS_KEY=config.KLING_ACCESS_KEY,
        KLING_SECRET_KEY=config.KLING_SECRET_KEY,
        KLING_TOKEN_TTL="600",
    )
    token, source, expires_at = kling.generate_kling_auth_token(force_refresh=True)
    assert source == "aksk"
    assert expires_at is not None

    payload = jwt.decode(token, config.KLING_SECRET_KEY, algorithms=["HS256"])
    assert payload["iss"] == config.KLING_ACCESS_KEY

    cached_token, cached_source, cached_exp = kling.generate_kling_auth_token()
    print(cached_token)
    assert cached_token == token
    assert cached_source == source
    assert cached_exp == expires_at


