"""Publish local reference files to a public Cloudflare R2 bucket.

Seedance only accepts media by URL, so scene start frames are uploaded here
first. Object keys embed a content hash and uploads never overwrite, so
re-running a scene reuses the existing object.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import quote

from . import config
from .config import logger


class R2UploadError(RuntimeError):
    """Raised when a reference file cannot be published to R2."""


_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def _require_settings() -> None:
    missing = [
        name
        for name in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_PUBLIC_BASE_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
        if not getattr(config, name)
    ]
    if missing:
        raise R2UploadError(
            "Seedance reference images must be hosted on Cloudflare R2; configure " + ", ".join(missing) + "."
        )
    if not config.R2_PUBLIC_BASE_URL.startswith("https://"):
        raise R2UploadError("R2_PUBLIC_BASE_URL must be the bucket's public https:// origin.")
    if config.R2_PUBLIC_BASE_URL.endswith(".r2.cloudflarestorage.com"):
        raise R2UploadError("R2_PUBLIC_BASE_URL points at the private S3 API; use the r2.dev or custom public domain.")


def object_key_for(path: Path, digest: str, prefix: str) -> str:
    name = f"{path.stem}-{digest[:12]}{path.suffix.lower()}"
    return f"{prefix}/{name}" if prefix else name


def public_url_for(key: str) -> str:
    return f"{config.R2_PUBLIC_BASE_URL}/{quote(key, safe='/')}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _s3_client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise R2UploadError("Install boto3>=1.36 to upload Seedance reference images to R2.") from exc

    jurisdiction = f".{config.R2_JURISDICTION}" if config.R2_JURISDICTION else ""
    return boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}{jurisdiction}.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        config=Config(
            signature_version="s3v4",
            connect_timeout=15,
            read_timeout=120,
            retries={"mode": "standard", "total_max_attempts": 3},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def upload_public_file(path: str | Path, prefix: str | None = None) -> str:
    """Upload ``path`` to R2 and return its public HTTPS URL."""

    source = Path(path)
    if not source.is_file() or source.stat().st_size == 0:
        raise R2UploadError(f"Reference file is missing or empty: {source}")
    _require_settings()

    digest = _sha256(source)
    key = object_key_for(source, digest, config.R2_UPLOAD_PREFIX if prefix is None else prefix.strip("/"))
    content_type = (
        _CONTENT_TYPES.get(source.suffix.lower())
        or mimetypes.guess_type(source.name)[0]
        or "application/octet-stream"
    )

    from botocore.exceptions import BotoCoreError, ClientError

    client = _s3_client()
    try:
        with source.open("rb") as body:
            client.put_object(
                Bucket=config.R2_BUCKET,
                Key=key,
                Body=body,
                ContentType=content_type,
                CacheControl="public, max-age=3600",
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", "Unknown"))
        if code not in ("PreconditionFailed", "412"):
            raise R2UploadError(f"R2 upload of {source.name} failed ({code}).") from None
        # Same content hash already published; reuse it.
    except BotoCoreError as exc:
        raise R2UploadError(f"R2 upload of {source.name} failed ({type(exc).__name__}).") from None
    finally:
        client.close()

    url = public_url_for(key)
    logger.info("Published Seedance reference %s -> %s", source.name, url)
    return url


async def upload_public_file_async(path: str | Path, prefix: str | None = None) -> str:
    return await asyncio.to_thread(upload_public_file, path, prefix)


__all__ = [
    "R2UploadError",
    "object_key_for",
    "public_url_for",
    "upload_public_file",
    "upload_public_file_async",
]
