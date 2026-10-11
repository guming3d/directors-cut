from __future__ import annotations

from pathlib import Path

import pytest

from backend import output_storage as storage


class FakeBlobStore:
    """In-memory blob container keyed by path relative to output/."""

    def __init__(self, blobs: dict[str, bytes] | None = None, fail: bool = False):
        self.blobs = dict(blobs or {})
        self.uploads: list[str] = []
        self.fail = fail

    def upload(self, key: str, path: Path) -> None:
        self.uploads.append(key)
        self.blobs[key] = path.read_bytes()

    def download(self, key: str, dest: Path) -> bool:
        if self.fail:
            raise ConnectionError("storage unreachable")
        if key not in self.blobs:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.blobs[key])
        return True

    def list_keys(self, prefix: str) -> list[str]:
        if self.fail:
            raise ConnectionError("storage unreachable")
        return [key for key in self.blobs if key.startswith(prefix)]

    def delete_prefix(self, prefix: str) -> int:
        doomed = [key for key in self.blobs if key.startswith(prefix)]
        for key in doomed:
            del self.blobs[key]
        return len(doomed)


@pytest.fixture
def output(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(storage, "SYNC", storage.SyncLoop(tmp_path))
    return tmp_path


@pytest.fixture
def blob(monkeypatch):
    def _install(**kwargs) -> FakeBlobStore:
        fake = FakeBlobStore(**kwargs)
        monkeypatch.setattr(storage, "blob_store", lambda: fake)
        return fake

    return _install


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_local_mode_uses_no_blob_store():
    assert storage.blob_store() is None


def test_sync_uploads_new_files(output, blob):
    fake = blob()
    _write(output / "job-1" / "scenes" / "scene_01.mp4")

    storage.SYNC.sync_once(fake)

    assert fake.uploads == ["job-1/scenes/scene_01.mp4"]


def test_sync_skips_unchanged_files(output, blob):
    fake = blob()
    _write(output / "db" / "workflow_jobs.json", b"{}")
    storage.SYNC.sync_once(fake)

    storage.SYNC.sync_once(fake)

    assert fake.uploads == ["db/workflow_jobs.json"]


def test_sync_reuploads_changed_files(output, blob):
    fake = blob()
    db = _write(output / "db" / "workflow_jobs.json", b"{}")
    storage.SYNC.sync_once(fake)

    db.write_bytes(b'{"workflow_jobs": {}}')
    storage.SYNC.sync_once(fake)

    assert fake.blobs["db/workflow_jobs.json"] == b'{"workflow_jobs": {}}'


def test_sync_skips_scratch_and_partial_files(output, blob):
    fake = blob()
    _write(output / "tmp" / "concat.txt")
    _write(output / "job-1" / "scene.mp4.part")
    _write(output / "video_workflow.log")

    storage.SYNC.sync_once(fake)

    assert fake.uploads == []


def test_database_only_sync_ignores_media(output, blob):
    fake = blob()
    _write(output / "db" / "workflow_jobs.json")
    _write(output / "job-1" / "scene.mp4")

    storage.SYNC.sync_once(fake, database_only=True)

    assert fake.uploads == ["db/workflow_jobs.json"]


def test_restore_downloads_database_and_prompt_overrides(output, blob):
    blob(blobs={"db/workflow_jobs.json": b"{}", "prompt_overrides/kling.txt": b"t", "job-1/scene.mp4": b"v"})

    storage.restore_startup_files(output)

    assert sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()) == [
        "db/workflow_jobs.json",
        "prompt_overrides/kling.txt",
    ]


def test_restored_files_are_not_uploaded_again(output, blob):
    fake = blob(blobs={"db/workflow_jobs.json": b"{}"})
    storage.restore_startup_files(output)

    storage.SYNC.sync_once(fake)

    assert fake.uploads == []


def test_restore_failure_stops_startup(output, blob):
    blob(fail=True)

    with pytest.raises(RuntimeError, match="Cannot restore output data"):
        storage.restore_startup_files(output)


@pytest.mark.asyncio
async def test_ensure_local_downloads_missing_media_once(output, blob):
    fake = blob(blobs={"job-1/scene.mp4": b"video"})
    target = output / "job-1" / "scene.mp4"

    assert await storage.ensure_local(target)
    storage.SYNC.sync_once(fake)

    assert (target.read_bytes(), fake.uploads) == (b"video", [])


@pytest.mark.asyncio
async def test_ensure_local_reports_missing_blob(output, blob):
    blob()

    assert not await storage.ensure_local(output / "job-1" / "gone.mp4")


@pytest.mark.asyncio
async def test_ensure_tree_fetches_a_whole_job_folder(output, blob):
    blob(blobs={"job-1/a.mp4": b"a", "job-1/first_frames/b.png": b"b", "job-2/c.mp4": b"c"})

    count = await storage.ensure_tree(output / "job-1")

    assert (count, (output / "job-2").exists()) == (2, False)


def test_forget_tree_deletes_only_that_jobs_blobs(output, blob):
    fake = blob(blobs={"job-1/a.mp4": b"a", "job-10/b.mp4": b"b"})

    storage.forget_tree(output / "job-1")

    assert list(fake.blobs) == ["job-10/b.mp4"]


def test_paths_outside_output_have_no_key(output, tmp_path_factory):
    assert storage.key_for(tmp_path_factory.mktemp("elsewhere") / "x.png") is None


def test_static_request_fetches_missing_media_from_blob(monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    from backend import config
    from backend.api import fastapi_app

    fake = FakeBlobStore(blobs={"zz-static-test/frame.png": b"png-bytes"})
    monkeypatch.setattr(storage, "blob_store", lambda: fake)
    try:
        # No `with`: the app's startup hooks do not run against the real job database.
        response = TestClient(fastapi_app).get("/static/zz-static-test/frame.png")

        assert (response.status_code, response.content) == (200, b"png-bytes")
    finally:
        shutil.rmtree(config.OUTPUT_DIR / "zz-static-test", ignore_errors=True)
