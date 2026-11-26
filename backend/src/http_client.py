"""Centralized async HTTP client with strict timeouts, 429 handling, and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class HttpRequestError(RuntimeError):
    """Raised when an HTTP request fails after exhausting all retries."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ResilientHttpClient:
    """Shared ``httpx.AsyncClient`` wrapper built for flaky public crypto APIs.

    Handles HTTP 429 rate-limit responses (respecting bounded ``Retry-After``),
    transient 5xx errors, and network timeouts with exponential backoff + jitter.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        connect_timeout: float = 4.0,
        max_retries: int = 3,
        base_backoff_seconds: float = 0.6,
        max_backoff_seconds: float = 8.0,
        user_agent: str = "MeteoroTraderSolana/1.0 (+https://localhost)",
    ) -> None:
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._timeout = httpx.Timeout(
            timeout=timeout,
            connect=connect_timeout,
            read=timeout,
            write=5.0,
            pool=4.0,
        )
        self._limits = httpx.Limits(
            max_connections=30,
            max_keepalive_connections=15,
            keepalive_expiry=30.0,
        )
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                limits=self._limits,
                headers=self._headers,
                follow_redirects=True,
            )
        return self._client

    async def __aenter__(self) -> "ResilientHttpClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool cleanly."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _compute_backoff(self, attempt: int, retry_after_header: str | None = None) -> float:
        if retry_after_header:
            try:
                parsed = float(retry_after_header.strip())
                if parsed > 0:
                    return min(parsed, self.max_backoff_seconds)
            except ValueError:
                pass
        exp = self.base_backoff_seconds * (2**attempt)
        jitter = random.uniform(0.0, 0.25 * exp)
        return min(exp + jitter, self.max_backoff_seconds)

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Execute an HTTP request and parse JSON with retries and backoff."""
        client = await self._ensure_client()
        last_error: Exception | None = None
        last_status: int | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=timeout or self._timeout,
                )
                last_status = response.status_code

                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < self.max_retries:
                        wait_s = self._compute_backoff(
                            attempt, response.headers.get("Retry-After")
                        )
                        logger.warning(
                            "HTTP %s on %s (attempt %d/%d); backing off %.2fs",
                            response.status_code,
                            url,
                            attempt + 1,
                            self.max_retries + 1,
                            wait_s,
                        )
                        await asyncio.sleep(wait_s)
                        continue
                    raise HttpRequestError(
                        f"HTTP {response.status_code} after {self.max_retries + 1} attempts for {url}",
                        status_code=response.status_code,
                        url=url,
                    )

                if response.status_code >= 400:
                    raise HttpRequestError(
                        f"HTTP {response.status_code} for {url}: {response.text[:200]}",
                        status_code=response.status_code,
                        url=url,
                    )

                return response.json()

            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait_s = self._compute_backoff(attempt)
                    logger.warning(
                        "Network error (%s) on %s (attempt %d/%d); backing off %.2fs",
                        type(exc).__name__,
                        url,
                        attempt + 1,
                        self.max_retries + 1,
                        wait_s,
                    )
                    await asyncio.sleep(wait_s)
                    continue
                raise HttpRequestError(
                    f"Network failure after {self.max_retries + 1} attempts for {url}: {exc}",
                    status_code=last_status,
                    url=url,
                ) from exc
            except ValueError as exc:
                raise HttpRequestError(
                    f"Invalid JSON payload received from {url}: {exc}",
                    status_code=last_status,
                    url=url,
                ) from exc

        raise HttpRequestError(
            f"Request failed for {url}: {last_error}",
            status_code=last_status,
            url=url,
        )

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Perform a resilient GET request returning decoded JSON."""
        return await self.request_json(
            "GET", url, params=params, headers=headers, timeout=timeout
        )

    async def get_json_safe(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        default: Any = None,
    ) -> Any:
        """Perform a GET request returning ``default`` instead of raising on error."""
        try:
            return await self.get_json(
                url, params=params, headers=headers, timeout=timeout
            )
        except HttpRequestError as exc:
            logger.warning("Safe GET failed for %s: %s", url, exc)
            return default

    async def post_json(
        self,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Perform a resilient POST request returning decoded JSON."""
        return await self.request_json(
            "POST",
            url,
            params=params,
            json_body=json_body,
            headers=headers,
            timeout=timeout,
        )
