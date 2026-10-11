from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from backend import config, r2_storage
from backend.r2_storage import R2UploadError


@pytest.fixture
def r2_settings(monkeypatch):
    monkeypatch.setattr(config, "R2_ACCOUNT_ID", "0" * 32)
    monkeypatch.setattr(config, "R2_BUCKET", "media")
    monkeypatch.setattr(config, "R2_PUBLIC_BASE_URL", "https://pub-test.r2.dev")
    monkeypatch.setattr(config, "R2_ACCESS_KEY_ID", "access")
    monkeypatch.setattr(config, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(config, "R2_UPLOAD_PREFIX", "seedance-refs")


class _FakeS3:
    def __init__(self, error_code: str | None = None):
        self.error_code = error_code
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append({key: value for key, value in kwargs.items() if key != "Body"})
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code}}, "PutObject")

    def close(self):
        pass


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "scene_01_first_frame.png"
    path.write_bytes(b"\x89PNG fake image bytes")
    return path


def test_upload_returns_public_url_under_prefix(r2_settings, monkeypatch, frame):
    monkeypatch.setattr(r2_storage, "_s3_client", lambda: _FakeS3())

    url = r2_storage.upload_public_file(frame)

    assert url.startswith("https://pub-test.r2.dev/seedance-refs/scene_01_first_frame-")


def test_upload_never_overwrites_existing_objects(r2_settings, monkeypatch, frame):
    s3 = _FakeS3()
    monkeypatch.setattr(r2_storage, "_s3_client", lambda: s3)

    r2_storage.upload_public_file(frame)

    assert s3.puts[0]["IfNoneMatch"] == "*"


def test_upload_reuses_object_when_same_content_exists(r2_settings, monkeypatch, frame):
    monkeypatch.setattr(r2_storage, "_s3_client", lambda: _FakeS3("PreconditionFailed"))

    assert r2_storage.upload_public_file(frame).endswith(".png")


def test_upload_surfaces_access_errors(r2_settings, monkeypatch, frame):
    monkeypatch.setattr(r2_storage, "_s3_client", lambda: _FakeS3("AccessDenied"))

    with pytest.raises(R2UploadError, match="AccessDenied"):
        r2_storage.upload_public_file(frame)


def test_upload_names_missing_settings(monkeypatch, frame):
    monkeypatch.setattr(config, "R2_BUCKET", None)

    with pytest.raises(R2UploadError, match="R2_BUCKET"):
        r2_storage.upload_public_file(frame)


def test_upload_rejects_private_s3_endpoint_as_public_url(r2_settings, monkeypatch, frame):
    monkeypatch.setattr(config, "R2_PUBLIC_BASE_URL", "https://abc.r2.cloudflarestorage.com")

    with pytest.raises(R2UploadError, match="private S3 API"):
        r2_storage.upload_public_file(frame)


def test_object_key_is_stable_for_same_content(tmp_path):
    path = tmp_path / "Frame.PNG"

    assert r2_storage.object_key_for(path, "a" * 64, "refs") == "refs/Frame-aaaaaaaaaaaa.png"


def test_upload_connection_errors_keep_their_cause(r2_settings, monkeypatch, frame):
    from botocore.exceptions import SSLError

    class _BrokenS3(_FakeS3):
        def put_object(self, **kwargs):
            raise SSLError(endpoint_url="https://r2.test", error="EOF occurred in violation of protocol")

    monkeypatch.setattr(r2_storage, "_s3_client", lambda: _BrokenS3())

    with pytest.raises(R2UploadError, match="EOF occurred in violation of protocol"):
        r2_storage.upload_public_file(frame)
