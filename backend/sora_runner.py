"""Shared retry helpers for Sora subprocess invocations."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional, Sequence

from .provider_limits import provider_slot
from .config import (
    SORA_RETRY_BASE_DELAY_SECONDS,
    SORA_RETRY_JITTER_SECONDS,
    SORA_RETRY_MAX_DELAY_SECONDS,
    logger,
)
from .provider_errors import (
    ProviderFailure,
    classify_sora_failure,
    format_sora_failure_message,
    format_sora_retry_message,
)
from .utils import SubprocessExecutionError, run_subprocess

RetryCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class SoraSubprocessResult:
    stdout: str
    stderr: str
    attempts: int


class SoraExecutionError(RuntimeError):
    """Raised when a Sora subprocess invocation fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        failure: ProviderFailure,
        attempts: int,
        log_path: Path | None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.attempts = attempts
        self.log_path = log_path


def _retry_delay_seconds(attempt: int, retry_after_seconds: float | None) -> float:
    attempt_index = max(1, attempt)
    exponential = SORA_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt_index - 1))
    delay = min(exponential, SORA_RETRY_MAX_DELAY_SECONDS)
    if retry_after_seconds is not None:
        delay = max(delay, min(retry_after_seconds, SORA_RETRY_MAX_DELAY_SECONDS))
    if SORA_RETRY_JITTER_SECONDS > 0:
        delay = min(
            SORA_RETRY_MAX_DELAY_SECONDS,
            delay + random.uniform(0.0, SORA_RETRY_JITTER_SECONDS),
        )
    return delay


def _append_attempt_log(
    *,
    log_path: Path | None,
    attempt: int,
    outcome: str,
    failure: ProviderFailure | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    if not log_path:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"=== Attempt {attempt} | {timestamp} ===",
        f"Outcome: {outcome}",
    ]
    if failure:
        lines.append(f"Category: {failure.category}")
        lines.append(f"Reason: {failure.reason}")
        lines.append(f"Retryable: {failure.retryable}")
        if failure.retry_after_seconds is not None:
            lines.append(f"Retry-After: {failure.retry_after_seconds:.2f}s")
    if stderr:
        lines.append("STDERR:")
        lines.append(stderr.strip())
    if stdout:
        lines.append("STDOUT:")
        lines.append(stdout.strip())
    lines.append("")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def run_sora_subprocess_with_retry(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    operation_label: str,
    moderation_attempts: int,
    transient_attempts: int,
    log_path: Path | None = None,
    on_retry: RetryCallback | None = None,
) -> SoraSubprocessResult:
    moderation_limit = max(1, int(moderation_attempts))
    transient_limit = max(1, int(transient_attempts))
    moderation_failures = 0
    transient_failures = 0
    attempt = 0

    while True:
        attempt += 1
        stdout = ""
        stderr = ""
        try:
            # Held per attempt so a job waiting out a retry delay frees its slot.
            async with provider_slot("sora2"):
                stdout, stderr = await run_subprocess(cmd, cwd=cwd, env=dict(env) if env else None)
            _append_attempt_log(
                log_path=log_path,
                attempt=attempt,
                outcome="success",
                stdout=stdout,
                stderr=stderr,
            )
            return SoraSubprocessResult(stdout=stdout, stderr=stderr, attempts=attempt)
        except asyncio.CancelledError:  # pragma: no cover - propagation only
            raise
        except Exception as exc:
            if isinstance(exc, SubprocessExecutionError):
                stdout = exc.stdout
                stderr = exc.stderr
            failure = classify_sora_failure(str(exc), stderr, stdout)
            _append_attempt_log(
                log_path=log_path,
                attempt=attempt,
                outcome="failed",
                failure=failure,
                stdout=stdout,
                stderr=stderr,
            )

            should_retry = False
            failure_attempt = 1
            max_attempts = 1
            if failure.category == "moderation":
                moderation_failures += 1
                failure_attempt = moderation_failures
                max_attempts = moderation_limit
                should_retry = moderation_failures < moderation_limit
            elif failure.retryable:
                transient_failures += 1
                failure_attempt = transient_failures
                max_attempts = transient_limit
                should_retry = transient_failures < transient_limit

            if not should_retry:
                message = format_sora_failure_message(
                    operation_label,
                    failure,
                    attempts=attempt,
                )
                logger.error(
                    "Sora operation failed operation=%s category=%s attempts=%s detail=%s",
                    operation_label,
                    failure.category,
                    attempt,
                    failure.excerpt,
                )
                raise SoraExecutionError(
                    message,
                    failure=failure,
                    attempts=attempt,
                    log_path=log_path,
                ) from exc

            delay = _retry_delay_seconds(failure_attempt, failure.retry_after_seconds)
            retry_message = format_sora_retry_message(
                operation_label,
                failure,
                attempt=failure_attempt,
                max_attempts=max_attempts,
                delay_seconds=delay,
            )
            if on_retry:
                await on_retry(retry_message)
            logger.warning(
                "Sora operation retrying operation=%s category=%s attempt=%s/%s delay=%.1fs detail=%s",
                operation_label,
                failure.category,
                failure_attempt,
                max_attempts,
                delay,
                failure.excerpt,
            )
            await asyncio.sleep(delay)


__all__ = [
    "SoraExecutionError",
    "SoraSubprocessResult",
    "run_sora_subprocess_with_retry",
]
