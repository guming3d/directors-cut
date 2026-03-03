import base64

from fastapi.testclient import TestClient

from backend.api import fastapi_app


def _basic_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def test_api_requires_auth_for_protected_routes(monkeypatch):
    monkeypatch.setenv("SORA_WORKFLOW_REQUIRE_AUTH", "1")
    monkeypatch.setenv("SORA_WORKFLOW_USERNAME", "reviewer")
    monkeypatch.setenv("SORA_WORKFLOW_PASSWORD", "secret-pass")
    monkeypatch.delenv("SORA_WORKFLOW_API_TOKEN", raising=False)

    with TestClient(fastapi_app) as client:
        unauthorized = client.get("/api/providers/active")
        assert unauthorized.status_code == 401
        assert "WWW-Authenticate" in unauthorized.headers

        authorized = client.get(
            "/api/providers/active",
            headers={"Authorization": _basic_header("reviewer", "secret-pass")},
        )
        assert authorized.status_code == 200
        body = authorized.json()
        assert "active" in body


def test_auth_session_endpoint_validates_credentials(monkeypatch):
    monkeypatch.setenv("SORA_WORKFLOW_REQUIRE_AUTH", "1")
    monkeypatch.setenv("SORA_WORKFLOW_USERNAME", "reviewer")
    monkeypatch.setenv("SORA_WORKFLOW_PASSWORD", "secret-pass")
    monkeypatch.delenv("SORA_WORKFLOW_API_TOKEN", raising=False)

    with TestClient(fastapi_app) as client:
        denied = client.get("/api/auth/session")
        assert denied.status_code == 401

        ok = client.get(
            "/api/auth/session",
            headers={"Authorization": _basic_header("reviewer", "secret-pass")},
        )
        assert ok.status_code == 200
        payload = ok.json()
        assert payload["authenticated"] is True
        assert payload["required"] is True
