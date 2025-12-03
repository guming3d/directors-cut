from __future__ import annotations

import sys
import types

import httpx
import pytest


class _StubExecutor:
    def __init__(self, *_, **__):
        pass


class _StubWorkflowContext:
    def __class_getitem__(cls, _item):
        return cls


def _stub_handler(func):
    return func


class _StubWorkflowBuilder:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def set_start_executor(self, *_args, **_kwargs):
        return self

    def add_edge(self, *_args, **_kwargs):
        return self

    def build(self):
        return object()


if "agent_framework" not in sys.modules:
    sys.modules["agent_framework"] = types.SimpleNamespace(
        Executor=_StubExecutor,
        WorkflowBuilder=_StubWorkflowBuilder,
        WorkflowContext=_StubWorkflowContext,
        handler=_stub_handler,
    )

from backend import kling


def test_calculate_retry_delay_honors_retry_after(monkeypatch):
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_BASE_DELAY_SECONDS", 1.0)
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_MAX_DELAY_SECONDS", 8.0)
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_JITTER_SECONDS", 0.0)

    delay = kling._calculate_retry_delay_seconds(2, "5")
    assert delay == 5.0

    capped = kling._calculate_retry_delay_seconds(4, "15")
    assert capped == 8.0


@pytest.mark.asyncio
async def test_create_kling_task_retries_on_429(monkeypatch):
    monkeypatch.setattr(kling, "KLING_CREATE_MAX_RETRIES", 3)
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_BASE_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_MAX_DELAY_SECONDS", 1.0)
    monkeypatch.setattr(kling, "KLING_CREATE_RETRY_JITTER_SECONDS", 0.0)

    sleep_calls: list[float] = []

    async def fake_sleep(value: float) -> None:
        sleep_calls.append(value)

    monkeypatch.setattr(kling.asyncio, "sleep", fake_sleep)

    attempts = {"count": 0}

    class StubClient:
        async def post(self, path: str, json: dict[str, object]) -> httpx.Response:
            attempts["count"] += 1
            request = httpx.Request("POST", f"https://example.com{path}")
            if attempts["count"] < 3:
                return httpx.Response(
                    429,
                    request=request,
                    headers={"Retry-After": "0.5"},
                    json={"code": 1303},
                )
            return httpx.Response(200, request=request, json={"code": 0})

    client = StubClient()

    response = await kling._create_kling_task_with_retry(
        client,
        endpoint="image2video",
        request_payload={"prompt": "test"},
    )

    assert response.status_code == 200
    assert attempts["count"] == 3
    assert sleep_calls == [0.5, 0.5]
