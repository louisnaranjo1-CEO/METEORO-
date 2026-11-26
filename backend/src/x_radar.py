"""Optional & isolated X (Twitter) Catalyst Radar (used strictly on RISK_FREE_RUNNER)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import Settings, get_settings
from src.database import Database

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class XMention:
    """Normalized X mention with author reach and notable-figure classification."""

    author_handle: str
    author_followers: int
    is_verified: bool
    is_notable_figure: bool
    notable_tag: str | None
    text: str
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "author_handle": self.author_handle,
            "author_followers": self.author_followers,
            "is_verified": self.is_verified,
            "is_notable_figure": self.is_notable_figure,
            "notable_tag": self.notable_tag,
            "text": self.text,
            "url": self.url,
        }


class XRadar:
    """Fetches and caches optional X mentions with strict 2.5s timeout and 3-min TTL.

    Disabled by default (`ENABLE_X_CONTEXT_ON_RUNNER=false`). Failures, timeouts, or
    missing credentials always degrade gracefully to an empty list without ever
    blocking or overriding volumetric exit decisions.
    """

    def __init__(
        self,
        db: Database,
        *,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._client = http_client
        self._last_fetch_monotonic: dict[str, float] = {}
        self._notable_handles, self._notable_keywords = self._load_notable_accounts()

    def _load_notable_accounts(self) -> tuple[set[str], tuple[str, ...]]:
        path = self.settings.resolved_notable_accounts_path
        if not path.exists():
            return set(), ()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Failed reading notable_accounts.json (%s): %s", path, exc)
            return set(), ()

        handles = {
            str(h).strip().lstrip("@").lower()
            for h in (data.get("handles") or [])
            if str(h).strip()
        }
        keywords = tuple(
            str(k).strip().lower()
            for k in (data.get("keywords") or [])
            if str(k).strip()
        )
        return handles, keywords

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.x_timeout_seconds),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _classify_author(
        self,
        *,
        handle: str,
        followers: int,
        is_verified: bool,
        bio: str,
        text: str,
    ) -> tuple[bool, str | None]:
        normalized_handle = handle.strip().lstrip("@").lower()
        combined_text = f"{bio} {text}".lower()

        if normalized_handle and normalized_handle in self._notable_handles:
            return True, "Figura Pública Configurada"
        if any(kw in combined_text for kw in self._notable_keywords):
            return True, "Posible Figura Pública"
        if followers > int(self.settings.x_notable_min_followers):
            return True, "Alto Alcance"
        if is_verified:
            return True, "Cuenta Verificada"
        return False, None

    def _normalize_raw_tweets(self, payload: Any) -> list[dict[str, Any]]:
        """Normalize TwitterAPI.io or X API v2 response shapes into ``XMention`` dicts."""
        if not isinstance(payload, dict):
            return []

        raw_tweets = payload.get("tweets") or payload.get("data") or []
        if not isinstance(raw_tweets, list):
            return []

        # X API v2 includes.users lookup map
        users_by_id: dict[str, dict[str, Any]] = {}
        includes = payload.get("includes")
        if isinstance(includes, dict) and isinstance(includes.get("users"), list):
            for u in includes["users"]:
                if isinstance(u, dict) and u.get("id"):
                    users_by_id[str(u["id"])] = u

        normalized: list[dict[str, Any]] = []
        for item in raw_tweets:
            if not isinstance(item, dict):
                continue

            author = item.get("author")
            if not isinstance(author, dict):
                author = users_by_id.get(str(item.get("author_id") or ""), {})

            handle = str(
                author.get("userName")
                or author.get("username")
                or author.get("screen_name")
                or item.get("author_handle")
                or ""
            ).lstrip("@")

            metrics = author.get("public_metrics") if isinstance(author.get("public_metrics"), dict) else {}
            try:
                followers = int(
                    author.get("followers")
                    or author.get("followers_count")
                    or metrics.get("followers_count")
                    or 0
                )
            except (TypeError, ValueError):
                followers = 0

            is_verified = bool(
                author.get("isBlueVerified")
                or author.get("isVerified")
                or author.get("verified")
                or False
            )
            bio = str(author.get("description") or author.get("bio") or "")
            text = str(item.get("text") or item.get("full_text") or "")
            url = item.get("url")
            if not url and item.get("id") and handle:
                url = f"https://x.com/{handle}/status/{item['id']}"

            is_notable, notable_tag = self._classify_author(
                handle=handle,
                followers=followers,
                is_verified=is_verified,
                bio=bio,
                text=text,
            )

            normalized.append(
                XMention(
                    author_handle=handle or "unknown",
                    author_followers=followers,
                    is_verified=is_verified,
                    is_notable_figure=is_notable,
                    notable_tag=notable_tag,
                    text=text[:500],
                    url=str(url) if url else None,
                ).to_dict()
            )
        return normalized

    async def fetch_mentions(
        self,
        mint_address: str,
        symbol: str,
    ) -> list[dict[str, Any]]:
        """Fetch recent X mentions for a token if enabled, respecting 3-min cache & 2.5s timeout."""
        if not self.settings.enable_x_context_on_runner:
            return []

        api_url = self.settings.x_api_url.strip()
        if not api_url:
            return []

        ttl = int(self.settings.x_cache_ttl_seconds)
        now_mono = time.monotonic()
        last_fetch = self._last_fetch_monotonic.get(mint_address)
        if last_fetch is not None and (now_mono - last_fetch) < ttl:
            return await self.db.get_cached_x_mentions(mint_address, max_age_seconds=ttl)

        cached = await self.db.get_cached_x_mentions(mint_address, max_age_seconds=ttl)
        if cached:
            self._last_fetch_monotonic[mint_address] = now_mono
            return cached

        query = f"${symbol} OR {mint_address}"
        headers: dict[str, str] = {}
        api_key = (self.settings.x_api_key or self.settings.x_bearer_token).strip()
        if api_key:
            headers["X-API-Key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            client = await self._get_client()
            response = await client.get(
                api_url,
                params={"query": query, "queryType": "Latest"},
                headers=headers,
                timeout=self.settings.x_timeout_seconds,
            )
            self._last_fetch_monotonic[mint_address] = time.monotonic()
            if response.status_code >= 400:
                logger.warning(
                    "XRadar HTTP %s for %s: %s",
                    response.status_code,
                    symbol,
                    response.text[:160],
                )
                return []

            mentions = self._normalize_raw_tweets(response.json())
            if mentions:
                await self.db.save_x_mentions(mint_address, query, mentions)
            return mentions

        except (httpx.TimeoutException, httpx.HTTPError, ValueError) as exc:
            self._last_fetch_monotonic[mint_address] = time.monotonic()
            logger.warning("XRadar failed safely for %s (%s): %s", symbol, mint_address[:8], exc)
            return []

    async def has_strong_catalyst(
        self,
        mint_address: str,
        symbol: str,
        *,
        min_organic_mentions: int = 5,
    ) -> bool:
        """Return True only when X Radar finds notable-figure or strong organic chatter."""
        mentions = await self.fetch_mentions(mint_address, symbol)
        if not mentions:
            return False
        if any(bool(m.get("is_notable_figure")) for m in mentions):
            return True
        distinct_handles = {
            str(m.get("author_handle") or "").lower()
            for m in mentions
            if m.get("author_handle")
        }
        return len(distinct_handles) >= min_organic_mentions
