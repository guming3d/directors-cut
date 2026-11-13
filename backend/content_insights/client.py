"""Async Azure Content Understanding client used by video insight jobs."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Optional

import httpx
from azure.core.exceptions import ClientAuthenticationError

from ..config import (
    AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID,
    AZURE_CONTENT_UNDERSTANDING_API_KEY,
    AZURE_CONTENT_UNDERSTANDING_API_VERSION,
    AZURE_CONTENT_UNDERSTANDING_AUTH,
    AZURE_CONTENT_UNDERSTANDING_ENDPOINT,
    CONTENT_UNDERSTANDING_POLL_INTERVAL,
    CONTENT_UNDERSTANDING_POLL_TIMEOUT,
    SUPPORTED_CONTENT_UNDERSTANDING_AUTH_MODES,
    entra_token_provider,
    logger,
)


class ContentUnderstandingError(RuntimeError):
    """Raised when Azure Content Understanding configuration or calls fail."""


class AzureContentUnderstandingClient:
    """Thin wrapper around the Azure Content Understanding REST API.

    ``auth="entra"`` sends a Microsoft Entra ID bearer token from
    ``token_provider`` (default: DefaultAzureCredential); ``auth="key"`` sends
    the resource API key.
    """

    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        auth: Optional[str] = None,
        api_key: Optional[str] = None,
        token_provider: Optional[Callable[[], Awaitable[str]]] = None,
        analyzer_id: Optional[str] = None,
        poll_interval: Optional[float] = None,
        poll_timeout: Optional[float] = None,
        user_agent: str = "sora2-workflow/insights",
    ) -> None:
        configured_endpoint = (endpoint or AZURE_CONTENT_UNDERSTANDING_ENDPOINT or "").rstrip("/")
        configured_auth = (auth or AZURE_CONTENT_UNDERSTANDING_AUTH).strip().lower()
        configured_api_key = api_key if api_key is not None else AZURE_CONTENT_UNDERSTANDING_API_KEY
        configured_api_version = api_version or AZURE_CONTENT_UNDERSTANDING_API_VERSION
        configured_analyzer = analyzer_id or AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID
        if configured_auth not in SUPPORTED_CONTENT_UNDERSTANDING_AUTH_MODES:
            raise ContentUnderstandingError(
                f"Unsupported Azure Content Understanding auth mode '{configured_auth}'. "
                "Set AZURE_CONTENT_UNDERSTANDING_AUTH to 'entra' or 'key'."
            )
        if not configured_endpoint:
            raise ContentUnderstandingError(
                "Configure AZURE_CONTENT_UNDERSTANDING_ENDPOINT before running insights.",
            )
        if configured_auth == "key" and not configured_api_key:
            raise ContentUnderstandingError(
                "AZURE_CONTENT_UNDERSTANDING_AUTH=key needs AZURE_CONTENT_UNDERSTANDING_API_KEY. "
                "Set the key, or use AZURE_CONTENT_UNDERSTANDING_AUTH=entra."
            )
        if not configured_api_version:
            raise ContentUnderstandingError("Azure Content Understanding API version is missing.")
        self._endpoint = configured_endpoint
        self._auth = configured_auth
        self._api_key = configured_api_key
        self._token_provider = (
            (token_provider or entra_token_provider()) if configured_auth == "entra" else None
        )
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
                raise ContentUnderstandingError(self._auth_rejected_message(status)) from exc
            if status == 404:
                raise ContentUnderstandingError(
                    f"Azure Content Understanding returned HTTP 404 for analyzer '{self._analyzer_id}'. "
                    "Check AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID and AZURE_CONTENT_UNDERSTANDING_API_VERSION."
                ) from exc
            raise ContentUnderstandingError(
                f"Azure Content Understanding returned HTTP {status}: {exc.response.text[:300]}"
            ) from exc

    def _auth_rejected_message(self, status: int) -> str:
        if self._auth == "key":
            return (
                f"Azure Content Understanding rejected the request (HTTP {status}). "
                "Check AZURE_CONTENT_UNDERSTANDING_API_KEY and that key-based access is enabled on the resource, "
                "or use AZURE_CONTENT_UNDERSTANDING_AUTH=entra."
            )
        return (
            f"Azure Content Understanding rejected the Microsoft Entra ID token (HTTP {status}). "
            "Grant the signed-in identity (your az login account locally, the managed identity in Azure) "
            "the 'Cognitive Services User' role on the resource; new role assignments can take a few minutes to apply."
        )

    @property
    def _analyze_binary_url(self) -> str:
        # GA API: raw bytes go to :analyzeBinary; :analyze takes a JSON body of input URLs.
        return (
            f"{self._endpoint}/contentunderstanding/analyzers/{self._analyzer_id}:analyzeBinary"
            f"?api-version={self._api_version}"
        )

    async def _begin_analyze(self, payload: bytes, content_type: str) -> str:
        url = self._analyze_binary_url
        headers = await self._headers() | {"Content-Type": content_type}
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
        url = self._analyze_binary_url
        # An explicit length sends a plain upload rather than chunked encoding.
        headers = await self._headers() | {
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
                    response = await client.get(operation_url, headers=await self._headers())
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

    async def _headers(self) -> dict[str, str]:
        headers = {"x-ms-useragent": self._user_agent}
        if self._auth == "key":
            headers["Ocp-Apim-Subscription-Key"] = self._api_key
            return headers
        try:
            token = await self._token_provider()
        except ClientAuthenticationError as exc:
            raise ContentUnderstandingError(
                "Could not get a Microsoft Entra ID token for Azure Content Understanding. "
                "Run `az login` locally (in Azure, give the app a managed identity), "
                "or use AZURE_CONTENT_UNDERSTANDING_AUTH=key."
            ) from exc
        headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    async def _aiter_file_chunks(path: Path, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """Stream a file for httpx.AsyncClient, which rejects sync iterators."""

        with path.open("rb") as handle:
            while True:
                chunk = await asyncio.to_thread(handle.read, chunk_size)
                if not chunk:
                    break
                yield chunk
