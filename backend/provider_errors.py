"""Helpers for classifying provider failures into actionable categories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_RETRY_AFTER_PATTERNS = (
    re.compile(r"retry[-_\s]?after[^0-9]*(?P<seconds>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"try again in[^0-9]*(?P<seconds>\d+(?:\.\d+)?)", re.IGNORECASE),
)


@dataclass(frozen=True)
class ProviderFailure:
    category: str
    reason: str
    retryable: bool
    excerpt: str
    retry_after_seconds: Optional[float] = None


def _truncate(value: str, limit: int = 220) -> str:
    cleaned = " ".join((value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


_ERROR_LINE_PATTERN = re.compile(r"(error|exception)\b", re.IGNORECASE)


def _error_excerpt(text: str) -> str:
    """Prefer the provider's last error line over the leading command text.

    Subprocess failures start with the full command line, so a plain prefix
    hides the actual provider message at the end of the traceback.
    """

    for line in reversed((text or "").splitlines()):
        stripped = line.strip()
        if _ERROR_LINE_PATTERN.search(stripped) and not stripped.startswith(("File ", "raise ", "Traceback")):
            return _truncate(stripped)
    return _truncate(text)


def _extract_retry_after_seconds(text: str) -> Optional[float]:
    for pattern in _RETRY_AFTER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return max(0.0, float(match.group("seconds")))
        except (TypeError, ValueError):
            continue
    return None


def classify_sora_failure(*parts: str) -> ProviderFailure:
    combined = "\n".join(part for part in parts if part)
    lowered = combined.lower()
    excerpt = _error_excerpt(combined)
    retry_after_seconds = _extract_retry_after_seconds(combined)

    moderation_markers = (
        "moderation_blocked",
        "blocked by our moderation system",
        "content safety",
        "content policy",
        "content filter",
        "policy_violation",
        "safety system",
        "responsible ai policy",
    )
    if any(marker in lowered for marker in moderation_markers):
        return ProviderFailure(
            category="moderation",
            reason="request blocked by content safety policy",
            retryable=True,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    throttling_markers = (
        "429",
        "rate limit",
        "too many requests",
        "throttle",
        "quota exceeded",
        "insufficient_quota",
    )
    if any(marker in lowered for marker in throttling_markers):
        return ProviderFailure(
            category="throttled",
            reason="provider rate limit reached",
            retryable=True,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    timeout_markers = (
        "timed out",
        "timeout",
        "deadline exceeded",
    )
    if any(marker in lowered for marker in timeout_markers):
        return ProviderFailure(
            category="timeout",
            reason="request timed out while waiting for provider",
            retryable=True,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    if "authenticationtypedisabled" in lowered or "key based authentication is disabled" in lowered:
        return ProviderFailure(
            category="key_auth_disabled",
            reason="API key sign-in is disabled on this Azure resource",
            retryable=False,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    auth_markers = (
        "unauthorized",
        "forbidden",
        "invalid api key",
        "authentication",
        "permission denied",
        "status: 401",
        "status: 403",
        "status=401",
        "status=403",
    )
    if any(marker in lowered for marker in auth_markers):
        return ProviderFailure(
            category="auth",
            reason="authentication or authorization failed",
            retryable=False,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    configuration_markers = (
        "configure endpoint",
        "configure azure_openai_api_key",
        "set azure_openai_api_key",
        "set openai_api_key",
        "before running",
        "video resolution is empty",
    )
    if any(marker in lowered for marker in configuration_markers):
        return ProviderFailure(
            category="configuration",
            reason="provider configuration is missing or invalid",
            retryable=False,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    transient_markers = (
        "connection reset",
        "connection refused",
        "name or service not known",
        "temporary failure in name resolution",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "status: 500",
        "status=500",
        "status: 502",
        "status=502",
        "status: 503",
        "status=503",
        "status: 504",
        "status=504",
    )
    if any(marker in lowered for marker in transient_markers):
        return ProviderFailure(
            category="transient",
            reason="temporary network or upstream service failure",
            retryable=True,
            excerpt=excerpt,
            retry_after_seconds=retry_after_seconds,
        )

    return ProviderFailure(
        category="unknown",
        reason="provider returned an unexpected error",
        retryable=False,
        excerpt=excerpt,
        retry_after_seconds=retry_after_seconds,
    )


def format_sora_failure_message(
    operation_label: str,
    failure: ProviderFailure,
    *,
    attempts: int,
) -> str:
    label = operation_label.strip() or "Sora request"
    guidance_map = {
        "moderation": "Adjust prompt wording or reference images and retry.",
        "throttled": "Retry later or lower concurrent render jobs.",
        "timeout": "Retry the request; the provider did not finish in time.",
        "transient": "Retry shortly; this looks like a temporary provider/network issue.",
        "auth": "Check Azure endpoint, API key, and deployment permissions.",
        "key_auth_disabled": "Set AZURE_OPENAI_AUTH=entra to sign in with Microsoft Entra ID instead of the API key.",
        "configuration": "Check required Sora environment configuration.",
        "unknown": "Review client logs for provider response details.",
    }
    guidance = guidance_map.get(failure.category, guidance_map["unknown"])
    return (
        f"{label} failed after {attempts} attempt(s) [{failure.category}]: "
        f"{failure.reason}. {guidance} Provider detail: {failure.excerpt}"
    )


def format_sora_retry_message(
    operation_label: str,
    failure: ProviderFailure,
    *,
    attempt: int,
    max_attempts: int,
    delay_seconds: float,
) -> str:
    label = operation_label.strip() or "Sora request"
    return (
        f"{label} hit {failure.category} error ({attempt}/{max_attempts}); "
        f"retrying in {delay_seconds:.1f}s."
    )


__all__ = [
    "ProviderFailure",
    "classify_sora_failure",
    "format_sora_failure_message",
    "format_sora_retry_message",
]
