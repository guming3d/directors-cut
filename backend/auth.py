"""Authentication helpers for protected API routes."""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass
from typing import Mapping

_FALSE_VALUES = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class AuthSettings:
    required: bool
    username: str | None
    password: str | None
    bearer_token: str | None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def auth_settings() -> AuthSettings:
    """Load authentication settings from environment variables."""

    required = _env_flag("SORA_WORKFLOW_REQUIRE_AUTH", True)
    username = (os.getenv("SORA_WORKFLOW_USERNAME") or "").strip() or None
    password_raw = os.getenv("SORA_WORKFLOW_PASSWORD")
    password = password_raw if password_raw else None
    bearer_token = (os.getenv("SORA_WORKFLOW_API_TOKEN") or "").strip() or None
    return AuthSettings(
        required=required,
        username=username,
        password=password,
        bearer_token=bearer_token,
    )


def validate_auth_configuration(settings: AuthSettings | None = None) -> None:
    current = settings or auth_settings()
    if not current.required:
        return
    basic_configured = bool(current.username and current.password)
    token_configured = bool(current.bearer_token)
    if basic_configured or token_configured:
        return
    raise RuntimeError(
        "Authentication is required but not configured. Set SORA_WORKFLOW_API_TOKEN or "
        "set both SORA_WORKFLOW_USERNAME and SORA_WORKFLOW_PASSWORD, or disable auth by "
        "setting SORA_WORKFLOW_REQUIRE_AUTH=0 for local development only."
    )


def auth_challenge_header(settings: AuthSettings | None = None) -> str:
    current = settings or auth_settings()
    if current.username and current.password and current.bearer_token:
        return 'Basic realm="sora-workflow", Bearer'
    if current.username and current.password:
        return 'Basic realm="sora-workflow"'
    return "Bearer"


def _decode_basic_credentials(encoded: str) -> tuple[str, str] | None:
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def _constant_time_match(actual: str, expected: str) -> bool:
    return secrets.compare_digest(actual, expected)


def authorize_request(
    headers: Mapping[str, str],
    settings: AuthSettings | None = None,
) -> bool:
    """Validate request headers against configured auth settings."""

    current = settings or auth_settings()
    if not current.required:
        return True

    # Support x-api-key for automation clients that cannot set Authorization.
    api_key = (headers.get("x-api-key") or "").strip()
    if current.bearer_token and api_key and _constant_time_match(api_key, current.bearer_token):
        return True

    authorization = (headers.get("authorization") or "").strip()
    if not authorization:
        return False

    scheme, _, value = authorization.partition(" ")
    if not scheme or not value:
        return False
    normalized = scheme.lower()

    if normalized == "bearer" and current.bearer_token:
        return _constant_time_match(value.strip(), current.bearer_token)

    if normalized != "basic" or not (current.username and current.password):
        return False

    credentials = _decode_basic_credentials(value.strip())
    if credentials is None:
        return False
    username, password = credentials
    return _constant_time_match(username, current.username) and _constant_time_match(
        password, current.password
    )


__all__ = [
    "AuthSettings",
    "auth_challenge_header",
    "auth_settings",
    "authorize_request",
    "validate_auth_configuration",
]
