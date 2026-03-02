from __future__ import annotations

from pathlib import Path

import pytest

from backend.provider_errors import classify_sora_failure
from backend.sora_runner import SoraExecutionError, run_sora_subprocess_with_retry
from backend.utils import SubprocessExecutionError


def test_classify_sora_failure_detects_moderation() -> None:
    failure = classify_sora_failure(
        "Video generation failed. Status: failed. Error: blocked by our moderation system."
    )
    assert failure.category == "moderation"
    assert failure.retryable is True


def test_classify_sora_failure_detects_throttle_retry_after() -> None:
    failure = classify_sora_failure("HTTP 429 Too Many Requests. Retry-After: 3")
    assert failure.category == "throttled"
    assert failure.retryable is True
    assert failure.retry_after_seconds == 3.0


@pytest.mark.asyncio
async def test_run_sora_subprocess_retries_on_throttle(monkeypatch, tmp_path: Path) -> None:
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    async def fake_run_subprocess(cmd, cwd=None, env=None):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise SubprocessExecutionError(
                cmd,
                1,
                "",
                "HTTP 429 Too Many Requests. Retry-After: 0.2",
            )
        return "ok", ""

    async def fake_sleep(value: float) -> None:
        sleep_calls.append(value)

    monkeypatch.setattr("backend.sora_runner.run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("backend.sora_runner.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_BASE_DELAY_SECONDS", 0.1)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_MAX_DELAY_SECONDS", 0.2)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_JITTER_SECONDS", 0.0)

    log_path = tmp_path / "sora.client.log"
    result = await run_sora_subprocess_with_retry(
        ["python", "dummy.py"],
        operation_label="Scene 1 render",
        moderation_attempts=1,
        transient_attempts=3,
        log_path=log_path,
    )

    assert result.attempts == 3
    assert attempts["count"] == 3
    assert sleep_calls == [0.2, 0.2]
    assert log_path.is_file()
    contents = log_path.read_text(encoding="utf-8")
    assert "Category: throttled" in contents
    assert "Outcome: success" in contents


@pytest.mark.asyncio
async def test_run_sora_subprocess_raises_actionable_moderation_error(monkeypatch, tmp_path: Path) -> None:
    async def fake_run_subprocess(cmd, cwd=None, env=None):
        raise SubprocessExecutionError(
            cmd,
            1,
            "",
            "blocked by our moderation system",
        )

    async def fake_sleep(_value: float) -> None:
        return None

    monkeypatch.setattr("backend.sora_runner.run_subprocess", fake_run_subprocess)
    monkeypatch.setattr("backend.sora_runner.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_BASE_DELAY_SECONDS", 0.1)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_MAX_DELAY_SECONDS", 0.1)
    monkeypatch.setattr("backend.sora_runner.SORA_RETRY_JITTER_SECONDS", 0.0)

    with pytest.raises(SoraExecutionError) as exc_info:
        await run_sora_subprocess_with_retry(
            ["python", "dummy.py"],
            operation_label="Scene 2 render",
            moderation_attempts=2,
            transient_attempts=1,
            log_path=tmp_path / "moderation.log",
        )

    exc = exc_info.value
    assert exc.failure.category == "moderation"
    assert "content safety" in str(exc)
