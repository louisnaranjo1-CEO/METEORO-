"""Phase 1 (SEARCH): DexScreener Solana scanner with deduplication and SQLite persistence."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.config import Settings, get_settings
from src.database import Database
from src.http_client import HttpRequestError, ResilientHttpClient

logger = logging.getLogger(__name__)

DEXSCREENER_TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_TOKEN_BOOSTS_LATEST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_TOKEN_BOOSTS_TOP_URL = "https://api.dexscreener.com/token-boosts/top/v1"
DEXSCREENER_SOLANA_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/solana/{mints}"
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
GECKOTERMINAL_TRENDING_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
GECKOTERMINAL_NEW_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
GECKOTERMINAL_TOP_POOLS_URL = "https://api.geckoterminal.com/api/v2/networks/solana/pools"

# Stablecoins, WSOL, and major liquid staking tokens excluded from memecoin discovery
IGNORED_SOLANA_MINTS: frozenset[str] = frozenset(
    {
        "So11111111111111111111111111111111111111112",  # WSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",   # mSOL
        "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL
        "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1",   # bSOL
        "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",  # PYUSD
    }
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def extract_solana_pairs(payload: Any) -> list[dict[str, Any]]:
    """Normalize DexScreener pair responses (list or ``{'pairs': [...]}``) to Solana pairs."""
    raw_pairs: list[Any]
    if isinstance(payload, list):
        raw_pairs = payload
    elif isinstance(payload, dict) and isinstance(payload.get("pairs"), list):
        raw_pairs = payload["pairs"]
    else:
        return []

    return [
        p
        for p in raw_pairs
        if isinstance(p, dict) and p.get("chainId") == "solana"
    ]


ROTATING_SEARCH_QUERIES: tuple[str, ...] = (
    "pump",
    "raydium",
    "pumpswap",
    "meteora",
    "ai",
    "cat",
    "dog",
    "pepe",
    "meme",
    "agent",
    "frog",
    "moon",
    "sol",
    "bonk",
    "trump",
)


class SearchScanner:
    """Discovers active Solana token candidates on DexScreener and stores them in ``leads``."""

    def __init__(
        self,
        db: Database,
        http_client: ResilientHttpClient | None = None,
        *,
        settings: Settings | None = None,
        dedup_window_minutes: int = 60,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or ResilientHttpClient(timeout=10.0, max_retries=3)
        self.dedup_window_minutes = dedup_window_minutes
        self._query_cursor: int = 0
        self._gecko_page_cursor: int = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    @staticmethod
    def _extract_gecko_mints(payload: Any) -> list[str]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            return []
        out: list[str] = []
        for pool in payload["data"]:
            if not isinstance(pool, dict):
                continue
            rels = pool.get("relationships") or {}
            base_id = str(((rels.get("base_token") or {}).get("data") or {}).get("id") or "")
            if base_id.startswith("solana_"):
                mint = base_id.split("solana_", 1)[1].strip()
                if mint:
                    out.append(mint)
        return out

    async def discover_candidate_mints(self, *, max_mints: int = 120) -> list[str]:
        """Collect unique recent & active Solana token mints from DexScreener + GeckoTerminal."""
        ordered_mints: list[str] = []
        seen: set[str] = set()

        def _add(mint: str | None) -> None:
            if not mint or not isinstance(mint, str):
                return
            cleaned = mint.strip()
            if (
                cleaned
                and cleaned not in seen
                and cleaned not in IGNORED_SOLANA_MINTS
                and len(ordered_mints) < max_mints
            ):
                seen.add(cleaned)
                ordered_mints.append(cleaned)

        # 1. GeckoTerminal live Solana new + trending + active pools (rotating pages 1..6)
        gecko_page = (self._gecko_page_cursor % 6) + 1
        self._gecko_page_cursor += 1
        for gecko_url in (
            GECKOTERMINAL_NEW_POOLS_URL,
            GECKOTERMINAL_TRENDING_POOLS_URL,
            GECKOTERMINAL_TOP_POOLS_URL,
        ):
            if len(ordered_mints) >= max_mints:
                break
            g_payload = await self._http.get_json_safe(
                gecko_url, params={"page": gecko_page}, default={}
            )
            for mint in self._extract_gecko_mints(g_payload):
                _add(mint)

        # 2. DexScreener profiles & boosts
        discovery_urls = (
            DEXSCREENER_TOKEN_PROFILES_URL,
            DEXSCREENER_TOKEN_BOOSTS_LATEST_URL,
            DEXSCREENER_TOKEN_BOOSTS_TOP_URL,
        )
        for url in discovery_urls:
            payload = await self._http.get_json_safe(url, default=[])
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict) and item.get("chainId") == "solana":
                        _add(item.get("tokenAddress"))

        # 3. Rotate across Solana memecoin search terms every cycle so discovery stays fresh
        if len(ordered_mints) < max_mints:
            batch_queries = [
                ROTATING_SEARCH_QUERIES[(self._query_cursor + i) % len(ROTATING_SEARCH_QUERIES)]
                for i in range(4)
            ]
            self._query_cursor = (self._query_cursor + 4) % len(ROTATING_SEARCH_QUERIES)
            for query in batch_queries:
                if len(ordered_mints) >= max_mints:
                    break
                payload = await self._http.get_json_safe(
                    DEXSCREENER_SEARCH_URL, params={"q": query}, default={}
                )
                for pair in extract_solana_pairs(payload):
                    base = pair.get("baseToken") or {}
                    _add(base.get("address"))

        return ordered_mints

    async def fetch_pairs_for_mints(self, mints: list[str]) -> list[dict[str, Any]]:
        """Batch-fetch Solana pairs from DexScreener in chunks of 30 mints."""
        all_pairs: list[dict[str, Any]] = []
        chunk_size = 30
        for idx in range(0, len(mints), chunk_size):
            batch = mints[idx : idx + chunk_size]
            if not batch:
                continue
            url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=",".join(batch))
            try:
                payload = await self._http.get_json(url)
                all_pairs.extend(extract_solana_pairs(payload))
            except HttpRequestError as exc:
                logger.warning("DexScreener batch fetch failed for %d mints: %s", len(batch), exc)
        return all_pairs

    async def fetch_pairs_for_mint(self, mint_address: str) -> list[dict[str, Any]]:
        """Fetch all Solana pairs for a single token mint on DexScreener."""
        return await self.fetch_pairs_for_mints([mint_address])

    async def scan(self, *, max_candidates: int = 30) -> list[dict[str, Any]]:
        """Run one SEARCH cycle: discover, filter by MCap/Liq, deduplicate, and persist."""
        effective_dedup = (
            min(self.dedup_window_minutes, 15)
            if self.settings.strategy_profile == "AGRESIVO"
            else self.dedup_window_minutes
        )
        recent_mints = await self.db.get_recent_lead_mints(
            minutes=effective_dedup
        )
        discovered_mints = await self.discover_candidate_mints(max_mints=120)
        fresh_mints = [m for m in discovered_mints if m not in recent_mints]
        if not fresh_mints:
            logger.info("SEARCH: no fresh Solana mints outside %dm dedup window.", self.dedup_window_minutes)
            return []

        pairs = await self.fetch_pairs_for_mints(fresh_mints)
        pairs_by_mint: dict[str, list[dict[str, Any]]] = {}
        for pair in pairs:
            base_token = pair.get("baseToken") or {}
            mint = base_token.get("address")
            if not mint or mint in IGNORED_SOLANA_MINTS or mint in recent_mints:
                continue
            pairs_by_mint.setdefault(mint, []).append(pair)

        # Rank fresh momentum candidates first (young tokens + strong 5m buy flow)
        now_ms = time.time() * 1000.0
        ranked_candidates: list[tuple[float, str, dict[str, Any], float, float, float, float]] = []
        for mint, mint_pairs in pairs_by_mint.items():
            # Sort pairs by descending liquidity USD to pick the main pool
            scored_pairs = sorted(
                mint_pairs,
                key=lambda p: _safe_float((p.get("liquidity") or {}).get("usd")),
                reverse=True,
            )
            main_pair = scored_pairs[0]
            main_liq = _safe_float((main_pair.get("liquidity") or {}).get("usd"))
            total_liq = sum(
                _safe_float((p.get("liquidity") or {}).get("usd")) for p in scored_pairs
            )
            main_pool_share = (main_liq / total_liq) if total_liq > 0 else 0.0

            market_cap = _safe_float(
                main_pair.get("marketCap")
                if main_pair.get("marketCap") is not None
                else main_pair.get("fdv")
            )
            price_usd = _safe_float(main_pair.get("priceUsd"))

            if market_cap < self.settings.min_market_cap_usd:
                continue
            if market_cap > self.settings.max_market_cap_usd:
                continue
            if main_liq < self.settings.min_liquidity_usd:
                continue

            created_ms = _safe_float(main_pair.get("pairCreatedAt"), default=0.0)
            age_hours = ((now_ms - created_ms) / 3_600_000.0) if created_ms > 0 else None
            txns_obj = main_pair.get("txns") if isinstance(main_pair.get("txns"), dict) else {}
            tx5 = txns_obj.get("m5") if isinstance(txns_obj.get("m5"), dict) else {}
            tx1h = txns_obj.get("h1") if isinstance(txns_obj.get("h1"), dict) else {}
            buys_5m = int(_safe_float(tx5.get("buys")))
            sells_5m = int(_safe_float(tx5.get("sells")))
            buys_1h = int(_safe_float(tx1h.get("buys")))
            sells_1h = int(_safe_float(tx1h.get("sells")))

            # Skip dead/bleeding legacy tokens (>48h old with weak or heavy-sell 1h flow)
            if (
                age_hours is not None
                and age_hours > 48.0
                and tx1h
                and (buys_1h < 10 or (sells_1h > 0 and (buys_1h / sells_1h) < 0.6))
            ):
                continue

            vol_obj = main_pair.get("volume") if isinstance(main_pair.get("volume"), dict) else {}
            pc_obj = main_pair.get("priceChange") if isinstance(main_pair.get("priceChange"), dict) else {}
            vol_5m = _safe_float(vol_obj.get("m5"))
            vol_1h = _safe_float(vol_obj.get("h1"))
            pc_5m = _safe_float(pc_obj.get("m5"))
            pc_1h = _safe_float(pc_obj.get("h1"))

            # Skip falling knives in AGRESIVO wave-scalper mode
            if (
                self.settings.strategy_profile == "AGRESIVO"
                and pc_obj
                and pc_5m < -15.0
            ):
                continue

            # When targeting $100K organic breakouts ($100K–$220K window), require green 5m candle + buyer dominance
            is_100k_breakout_mode = (
                self.settings.min_market_cap_usd >= 95_000.0
                and self.settings.max_market_cap_usd <= 300_000.0
            )
            if is_100k_breakout_mode and tx5:
                if pc_obj and pc_5m <= 0.0:
                    continue
                if sells_5m > 0 and (buys_5m / sells_5m) < 1.15:
                    continue
                if vol_5m > 0 and vol_5m < 3_000.0:
                    continue

            momentum_score = 0.0
            if age_hours is not None:
                if age_hours <= 6.0:
                    momentum_score += 40.0
                elif age_hours <= 24.0:
                    momentum_score += 20.0
                elif age_hours > 72.0:
                    momentum_score -= 15.0
            # Wave detection: rising 5m + 1h price + buy dominance + accelerating 5m volume
            if pc_5m > 0:
                momentum_score += min(28.0, pc_5m * 1.2)
            if pc_1h > 0:
                momentum_score += min(15.0, pc_1h * 0.25)
            if buys_5m > sells_5m:
                momentum_score += min(32.0, (buys_5m - sells_5m) * 1.6)
            if vol_5m > 0:
                momentum_score += min(20.0, vol_5m / 1000.0)
                avg_5m_in_1h = vol_1h / 12.0 if vol_1h > 0 else 0.0
                if avg_5m_in_1h > 0:
                    accel = vol_5m / avg_5m_in_1h
                    if accel >= 1.5:
                        momentum_score += min(35.0, accel * 10.0)

            ranked_candidates.append(
                (momentum_score, mint, main_pair, main_liq, main_pool_share, market_cap, price_usd)
            )

        ranked_candidates.sort(key=lambda item: item[0], reverse=True)

        created_leads: list[dict[str, Any]] = []
        for _score, mint, main_pair, main_liq, main_pool_share, market_cap, price_usd in ranked_candidates:
            if len(created_leads) >= max_candidates:
                break

            base_token = main_pair.get("baseToken") or {}
            symbol = str(base_token.get("symbol") or "UNKNOWN").strip()[:32]
            name = str(base_token.get("name") or symbol).strip()[:100]
            pair_address = str(main_pair.get("pairAddress") or "").strip()
            if not pair_address:
                continue

            lead = await self.db.create_lead(
                mint_address=mint,
                symbol=symbol,
                name=name,
                pair_address=pair_address,
                chain="solana",
                current_stage="SEARCH",
                market_cap=round(market_cap, 2),
                liquidity_usd=round(main_liq, 2),
                main_pool_share=round(main_pool_share, 4),
                price_usd=price_usd if price_usd > 0 else None,
            )
            recent_mints.add(mint)
            created_leads.append(lead)

        logger.info(
            "SEARCH cycle finished: discovered=%d fresh=%d persisted=%d",
            len(discovered_mints),
            len(fresh_mints),
            len(created_leads),
        )
        return created_leads
