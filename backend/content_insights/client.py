"""Async Azure Content Understanding client used by video insight jobs."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, AsyncIterator, Iterator, Optional

import httpx

from ..config import (
    AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID,
    AZURE_CONTENT_UNDERSTANDING_API_KEY,
    AZURE_CONTENT_UNDERSTANDING_API_VERSION,
    AZURE_CONTENT_UNDERSTANDING_ENDPOINT,
    CONTENT_UNDERSTANDING_POLL_INTERVAL,
    CONTENT_UNDERSTANDING_POLL_TIMEOUT,
    logger,
)


class ContentUnderstandingError(RuntimeError):
    """Raised when Azure Content Understanding configuration or calls fail."""


class AzureContentUnderstandingClient:
    """Thin wrapper around the Azure Content Understanding REST API."""

    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        api_key: Optional[str] = None,
        analyzer_id: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
        user_agent: str = "sora2-workflow/insights",
    ) -> None:
        configured_endpoint = (endpoint or AZURE_CONTENT_UNDERSTANDING_ENDPOINT or "").rstrip("/")
        configured_api_key = api_key or AZURE_CONTENT_UNDERSTANDING_API_KEY
        configured_api_version = api_version or AZURE_CONTENT_UNDERSTANDING_API_VERSION
        configured_analyzer = analyzer_id or AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID
        if not configured_endpoint or not configured_api_key:
            raise ContentUnderstandingError(
                "Azure Content Understanding endpoint and API key must be configured before running insights.",
            )
        if not configured_api_version:
            raise ContentUnderstandingError("Azure Content Understanding API version is missing.")
        self._endpoint = configured_endpoint
        self._api_key = configured_api_key
        self._api_version = configured_api_version
        self._analyzer_id = configured_analyzer
        self._poll_interval = poll_interval or CONTENT_UNDERSTANDING_POLL_INTERVAL
        self._poll_timeout = poll_timeout or CONTENT_UNDERSTANDING_POLL_TIMEOUT
        self._user_agent = user_agent
        self._timeout = max(self._poll_timeout + 30.0, 120.0)

    async def analyze_video(
        self,
        video_path: Path | str,
        *,
        content_type: str = "application/octet-stream",
        poll_interval: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(f"Video file not found: {path}")
        operation_url = await self._begin_analyze_file(path, content_type)
        return await self._poll_operation(
            operation_url,
            poll_interval or self._poll_interval,
            timeout or self._poll_timeout,
        )

    async def analyze_bytes(
        self,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
        poll_interval: Optional[float] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        operation_url = await self._begin_analyze(payload, content_type)
        return await self._poll_operation(
            operation_url,
            poll_interval or self._poll_interval,
            timeout or self._poll_timeout,
        )

    @contextlib.contextmanager
    def _readable_http_errors(self) -> Iterator[None]:
        """Turn transport and auth failures into messages that name the fix."""

        try:
            yield
        except httpx.ConnectError as exc:
            raise ContentUnderstandingError(
                f"Cannot reach Azure Content Understanding at {self._endpoint} ({exc}). "
                "Check AZURE_CONTENT_UNDERSTANDING_ENDPOINT; the resource may have been deleted or renamed."
            ) from exc
        except httpx.TimeoutException as exc:
            raise ContentUnderstandingError(
                f"Azure Content Understanding at {self._endpoint} did not respond in time."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise ContentUnderstandingError(
                    f"Azure Content Understanding rejected the request (HTTP {status}). "
                    "Check AZURE_CONTENT_UNDERSTANDING_API_KEY and that key-based access is enabled on the resource."
                ) from exc
            if status == 404:
                raise ContentUnderstandingError(
                    f"Azure Content Understanding returned HTTP 404 for analyzer '{self._analyzer_id}'. "
                    "Check AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID and AZURE_CONTENT_UNDERSTANDING_API_VERSION."
                ) from exc
            raise ContentUnderstandingError(
                f"Azure Content Understanding returned HTTP {status}: {exc.response.text[:300]}"
            ) from exc

    async def _begin_analyze(self, payload: bytes, content_type: str) -> str:
        url = (
            f"{self._endpoint}/contentunderstanding/analyzers/{self._analyzer_id}:analyze"
            f"?api-version={self._api_version}"
        )
        headers = self._base_headers | {"Content-Type": content_type}
        logger.debug("Submitting video insight request to %s", url)
        with self._readable_http_errors():
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=headers, content=payload)
                response.raise_for_status()
        operation_url = (
            response.headers.get("operation-location")
            or response.headers.get("Operation-Location")
            or ""
        )
        if not operation_url:
            raise ContentUnderstandingError("Azure Content Understanding response missing operation-location header.")
        logger.debug("Azure Content Understanding operation: %s", operation_url)
        return operation_url

    async def _begin_analyze_file(self, path: Path, content_type: str) -> str:
        url = (
            f"{self._endpoint}/contentunderstanding/analyzers/{self._analyzer_id}:analyze"
            f"?api-version={self._api_version}"
        )
        # An explicit length sends a plain upload rather than chunked encoding.
        headers = self._base_headers | {
            "Content-Type": content_type,
            "Content-Length": str(path.stat().st_size),
        }
        logger.debug("Submitting video insight request to %s", url)
        with self._readable_http_errors():
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    content=self._aiter_file_chunks(path),
                )
                response.raise_for_status()
        operation_url = (
            response.headers.get("operation-location")
            or response.headers.get("Operation-Location")
            or ""
        )
        if not operation_url:
            raise ContentUnderstandingError(
                "Azure Content Understanding response missing operation-location header."
            )
        logger.debug("Azure Content Understanding operation: %s", operation_url)
        return operation_url

    async def _poll_operation(self, operation_url: str, poll_interval: float, timeout: float) -> dict[str, Any]:
        start = asyncio.get_event_loop().time()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while True:
                elapsed = asyncio.get_event_loop().time() - start
                if elapsed > timeout:
                    raise ContentUnderstandingError(
                        f"Azure Content Understanding operation timed out after {timeout:.0f}s.",
                    )
                with self._readable_http_errors():
                    response = await client.get(operation_url, headers=self._base_headers)
                    response.raise_for_status()
                payload = response.json()
                status = str(payload.get("status", ""))
                if status.lower() == "succeeded":
                    logger.debug("Azure Content Understanding succeeded in %.2fs", elapsed)
                    return payload
                if status.lower() == "failed":
                    logger.error("Azure Content Understanding operation failed: %s", payload)
                    raise ContentUnderstandingError("Azure Content Understanding analysis failed.")
                await asyncio.sleep(max(0.1, poll_interval))

    @property
    def _base_headers(self) -> dict[str, str]:
        return {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "x-ms-useragent": self._user_agent,
        }

    @staticmethod
    async def _aiter_file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """Stream a file for httpx.AsyncClient, which rejects sync iterators."""

        with path.open("rb") as handle:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk
