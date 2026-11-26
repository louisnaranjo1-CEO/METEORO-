"""Live SOL/USD price oracle with multi-provider fallback and 30-second in-memory cache."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass

from src.config import Settings, get_settings
from src.http_client import HttpRequestError, ResilientHttpClient

logger = logging.getLogger(__name__)

WSOL_MINT: str = "So11111111111111111111111111111111111111112"
STABLE_QUOTES: frozenset[str] = frozenset({"USDC", "USDT"})

DEXSCREENER_SOL_URL: str = f"https://api.dexscreener.com/latest/dex/tokens/{WSOL_MINT}"
JUPITER_PRICE_URLS: tuple[str, ...] = (
    f"https://lite-api.jup.ag/price/v2?ids={WSOL_MINT}",
    f"https://api.jup.ag/price/v2?ids={WSOL_MINT}",
)
COINGECKO_SOL_URL: str = (
    "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
)


@dataclass(slots=True)
class SolPriceQuote:
    """Resolved SOL/USD quote with provenance metadata."""

    price_usd: float
    source: str
    fetched_at_monotonic: float
    is_fallback: bool = False


class SolPriceOracle:
    """Fetches and caches live SOL/USD price across DexScreener, Jupiter, and CoinGecko."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        cache_ttl_seconds: float = 30.0,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or ResilientHttpClient(timeout=8.0, max_retries=2)
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached: SolPriceQuote | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    @staticmethod
    def _is_valid_price(value: float | None) -> bool:
        return (
            value is not None
            and math.isfinite(value)
            and 1.0 <= value <= 100_000.0
        )

    async def _fetch_from_dexscreener(self) -> float | None:
        data = await self._http.get_json(DEXSCREENER_SOL_URL, timeout=6.0)
        pairs = data.get("pairs") if isinstance(data, dict) else None
        if not isinstance(pairs, list):
            return None

        best_price: float | None = None
        best_liq: float = -1.0
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            if pair.get("chainId") != "solana":
                continue
            base_addr = (pair.get("baseToken") or {}).get("address")
            quote_sym = ((pair.get("quoteToken") or {}).get("symbol") or "").upper()
            if base_addr != WSOL_MINT or quote_sym not in STABLE_QUOTES:
                continue
            liq = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            try:
                price = float(pair.get("priceUsd") or 0.0)
            except (TypeError, ValueError):
                continue
            if self._is_valid_price(price) and liq > best_liq:
                best_liq = liq
                best_price = price
        return best_price

    async def _fetch_from_jupiter(self) -> float | None:
        for url in JUPITER_PRICE_URLS:
            try:
                data = await self._http.get_json(url, timeout=5.0)
            except HttpRequestError:
                continue
            entry = (data.get("data") or {}).get(WSOL_MINT) if isinstance(data, dict) else None
            if isinstance(entry, dict) and entry.get("price") is not None:
                try:
                    price = float(entry["price"])
                except (TypeError, ValueError):
                    continue
                if self._is_valid_price(price):
                    return price
        return None

    async def _fetch_from_coingecko(self) -> float | None:
        data = await self._http.get_json(COINGECKO_SOL_URL, timeout=6.0)
        sol_obj = data.get("solana") if isinstance(data, dict) else None
        if isinstance(sol_obj, dict) and sol_obj.get("usd") is not None:
            try:
                price = float(sol_obj["usd"])
            except (TypeError, ValueError):
                return None
            if self._is_valid_price(price):
                return price
        return None

    async def get_quote(self, *, force_refresh: bool = False) -> SolPriceQuote:
        """Return a cached or freshly fetched ``SolPriceQuote``."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._cached is not None
            and (now - self._cached.fetched_at_monotonic) < self.cache_ttl_seconds
        ):
            return self._cached

        async with self._lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._cached is not None
                and (now - self._cached.fetched_at_monotonic) < self.cache_ttl_seconds
            ):
                return self._cached

            providers = (
                ("dexscreener", self._fetch_from_dexscreener),
                ("jupiter", self._fetch_from_jupiter),
                ("coingecko", self._fetch_from_coingecko),
            )
            for name, fetcher in providers:
                try:
                    price = await fetcher()
                    if self._is_valid_price(price):
                        quote = SolPriceQuote(
                            price_usd=round(float(price), 4),
                            source=name,
                            fetched_at_monotonic=time.monotonic(),
                            is_fallback=False,
                        )
                        self._cached = quote
                        return quote
                except HttpRequestError as exc:
                    logger.warning("SOL oracle provider %s failed: %s", name, exc)
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.warning("Unexpected error in SOL oracle %s: %s", name, exc)

            # All live providers failed -> return stale cached quote or config fallback
            if self._cached is not None:
                logger.warning(
                    "All SOL price providers failed; reusing last cached price $%.2f (%s)",
                    self._cached.price_usd,
                    self._cached.source,
                )
                return SolPriceQuote(
                    price_usd=self._cached.price_usd,
                    source=f"{self._cached.source}_stale",
                    fetched_at_monotonic=self._cached.fetched_at_monotonic,
                    is_fallback=True,
                )

            fallback_price = float(self.settings.fallback_sol_usd_price)
            logger.error(
                "All SOL price providers failed and no cache exists; using fallback $%.2f",
                fallback_price,
            )
            quote = SolPriceQuote(
                price_usd=fallback_price,
                source="config_fallback",
                fetched_at_monotonic=time.monotonic(),
                is_fallback=True,
            )
            self._cached = quote
            return quote

    async def get_sol_usd_price(self, *, force_refresh: bool = False) -> float:
        """Convenience wrapper returning the numeric SOL/USD price."""
        quote = await self.get_quote(force_refresh=force_refresh)
        return quote.price_usd

    async def sol_to_usd(self, amount_sol: float) -> float:
        """Convert SOL amount into USD using the current oracle price."""
        price = await self.get_sol_usd_price()
        return round(amount_sol * price, 4)

    async def usd_to_sol(self, amount_usd: float) -> float:
        """Convert USD amount into SOL using the current oracle price."""
        price = await self.get_sol_usd_price()
        return round(amount_usd / price, 9)
