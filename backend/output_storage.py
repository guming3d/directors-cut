"""Mirror the output folder to Azure Blob Storage so cloud jobs survive redeploys.

With ``OUTPUT_STORAGE=local`` (the default, used for local runs) every function
here is a no-op and the app reads and writes ``output/`` only.

With ``OUTPUT_STORAGE=blob`` the container's ``output/`` folder is a working
copy of a private blob container, addressed by path relative to ``output/``:

- Startup: the job database and prompt overrides are downloaded before the
  stores load (``restore_startup_files``); failure stops startup, because
  starting empty would later overwrite the stored database.
- Writes: ``SyncLoop`` uploads changed database files every few seconds and
  other changed files every ``OUTPUT_SYNC_INTERVAL_SECONDS``.
- Reads: media is downloaded on first use (``ensure_local`` / ``ensure_tree``).
- Deletes: ``forget_tree`` removes a deleted job's blobs.

Access uses ``DefaultAzureCredential`` (the backend's managed identity), so no
storage keys are involved.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Iterable, Optional, Protocol

from .config import (
    AZURE_STORAGE_ACCOUNT_URL,
    OUTPUT_BLOB_CONTAINER,
    OUTPUT_DIR,
    OUTPUT_STORAGE,
    OUTPUT_SYNC_INTERVAL_SECONDS,
    logger,
)

# Small files the app needs before serving any request.
STARTUP_PREFIXES = ("db/", "prompt_overrides/")
# Scratch space and the process log are not job data.
SKIPPED_PREFIXES = ("tmp/",)
SKIPPED_NAMES = {"video_workflow.log"}
DB_PREFIX = "db/"


class BlobStore(Protocol):
    def upload(self, key: str, path: Path) -> None: ...

    def download(self, key: str, dest: Path) -> bool: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def delete_prefix(self, prefix: str) -> int: ...


class AzureBlobStore:
    """Blob container client authenticated with the managed identity."""

    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        self._container = service.get_container_client(container)

    def upload(self, key: str, path: Path) -> None:
        with path.open("rb") as data:
            self._container.upload_blob(key, data, overwrite=True)

    def download(self, key: str, dest: Path) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        blob = self._container.get_blob_client(key)
        partial = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with partial.open("wb") as handle:
                blob.download_blob().readinto(handle)
        except ResourceNotFoundError:
            partial.unlink(missing_ok=True)
            return False
        partial.replace(dest)
        return True

    def list_keys(self, prefix: str) -> list[str]:
        return [blob.name for blob in self._container.list_blobs(name_starts_with=prefix)]

    def delete_prefix(self, prefix: str) -> int:
        names = self.list_keys(prefix)
        for name in names:
            self._container.delete_blob(name)
        return len(names)


_STORE: Optional[BlobStore] = None


def blob_store() -> Optional[BlobStore]:
    """The configured blob store, or None when output stays local."""

    global _STORE
    if OUTPUT_STORAGE != "blob":
        return None
    if _STORE is None:
        if not AZURE_STORAGE_ACCOUNT_URL:
            raise RuntimeError("OUTPUT_STORAGE=blob requires AZURE_STORAGE_ACCOUNT_URL.")
        _STORE = AzureBlobStore(AZURE_STORAGE_ACCOUNT_URL, OUTPUT_BLOB_CONTAINER)
    return _STORE


def key_for(path: str | Path, root: Path | None = None) -> Optional[str]:
    """Blob key for a path under the output folder, or None if it is outside it."""

    base = (root or OUTPUT_DIR).resolve()
    try:
        relative = Path(path).resolve().relative_to(base)
    except ValueError:
        return None
    key = relative.as_posix()
    return None if key in ("", ".") else key


def _is_synced(key: str) -> bool:
    return not (
        key.startswith(SKIPPED_PREFIXES)
        or key in SKIPPED_NAMES
        or key.endswith(".part")
    )


def restore_startup_files(root: Path | None = None) -> int:
    """Download the job database and prompt overrides before the stores load."""

    store = blob_store()
    if store is None:
        return 0
    base = root or OUTPUT_DIR
    count = 0
    try:
        for prefix in STARTUP_PREFIXES:
            for key in store.list_keys(prefix):
                if store.download(key, base / key):
                    SYNC.mark_synced(base / key)
                    count += 1
    except Exception as exc:
        # Starting with an empty database would overwrite the stored one on the
        # next sync, so refuse to start instead.
        raise RuntimeError(f"Cannot restore output data from blob storage: {exc}") from exc
    logger.warning("Restored %d database file(s) from blob storage.", count)
    return count


async def ensure_local(path: str | Path | None) -> bool:
    """Make sure a file under output/ exists locally, downloading it if needed."""

    if not path:
        return False
    local = Path(path)
    if local.is_file():
        return True
    store = blob_store()
    key = key_for(local)
    if store is None or key is None:
        return False
    found = await asyncio.to_thread(store.download, key, local)
    if found:
        SYNC.mark_synced(local)
    return found


async def ensure_tree(directory: str | Path | None) -> int:
    """Download any files missing under a job folder, e.g. before recomposing."""

    store = blob_store()
    if store is None or not directory:
        return 0
    prefix = key_for(directory)
    if prefix is None:
        return 0
    count = 0
    for key in await asyncio.to_thread(store.list_keys, prefix.rstrip("/") + "/"):
        local = OUTPUT_DIR / key
        if not local.is_file() and await asyncio.to_thread(store.download, key, local):
            SYNC.mark_synced(local)
            count += 1
    return count


def forget_tree(directory: str | Path) -> int:
    """Delete a removed job's blobs so they are not restored or billed."""

    store = blob_store()
    prefix = key_for(directory)
    if store is None or prefix is None:
        return 0
    try:
        return store.delete_prefix(prefix.rstrip("/") + "/")
    except Exception as exc:  # pragma: no cover - best effort cleanup
        logger.warning("Could not delete blobs under %s: %s", prefix, exc)
        return 0


class SyncLoop:
    """Upload new and changed files under output/ to blob storage."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or OUTPUT_DIR
        self._seen: dict[str, tuple[int, int]] = {}
        self._task: Optional[asyncio.Task] = None

    def _signature(self, path: Path) -> Optional[tuple[int, int]]:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def mark_synced(self, path: Path) -> None:
        key = key_for(path, self._root)
        signature = self._signature(path)
        if key and signature:
            self._seen[key] = signature

    def _walk(self, directory: Path) -> Iterable[Path]:
        for current, _dirs, files in os.walk(directory):
            for name in files:
                yield Path(current) / name

    def sync_once(self, store: BlobStore, *, database_only: bool = False) -> int:
        """Upload files whose size or modification time changed. Returns the count."""

        directory = self._root / DB_PREFIX if database_only else self._root
        if not directory.is_dir():
            return 0
        uploaded = 0
        for path in self._walk(directory):
            key = key_for(path, self._root)
            if key is None or not _is_synced(key):
                continue
            signature = self._signature(path)
            if signature is None or self._seen.get(key) == signature:
                continue
            try:
                store.upload(key, path)
            except Exception as exc:
                logger.warning("Blob upload failed for %s: %s", key, exc)
                continue
            self._seen[key] = signature
            uploaded += 1
        return uploaded

    async def _run(self, store: BlobStore) -> None:
        tick = 0
        full_every = max(1, int(OUTPUT_SYNC_INTERVAL_SECONDS // 3))
        while True:
            await asyncio.sleep(3)
            tick += 1
            await asyncio.to_thread(self.sync_once, store, database_only=tick % full_every != 0)

    def start(self) -> None:
        store = blob_store()
        if store is None or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(store))

    async def stop(self) -> None:
        """Stop the loop and flush everything once more."""

        store = blob_store()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if store is not None:
            await asyncio.to_thread(self.sync_once, store)


SYNC = SyncLoop()


__all__ = [
    "AzureBlobStore",
    "BlobStore",
    "SYNC",
    "SyncLoop",
    "blob_store",
    "ensure_local",
    "ensure_tree",
    "forget_tree",
    "key_for",
    "restore_startup_files",
]
