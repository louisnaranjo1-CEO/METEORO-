"""Phase 2 (RISK): Hard contract, liquidity concentration, and dev-blacklist filter."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.config import Settings, get_settings
from src.database import Database
from src.http_client import HttpRequestError, ResilientHttpClient
from src.search import DEXSCREENER_SOLANA_TOKENS_URL, extract_solana_pairs

logger = logging.getLogger(__name__)

RUGCHECK_REPORT_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"

BURN_OR_SYSTEM_ADDRESSES: frozenset[str] = frozenset(
    {
        "11111111111111111111111111111111",
        "1nc1nerator11111111111111111111111111111111",
        "Dead111111111111111111111111111111111111111",
    }
)

POOL_ACCOUNT_KEYWORDS: tuple[str, ...] = (
    "raydium",
    "orca",
    "meteora",
    "pump",
    "pumpswap",
    "amm",
    "pool",
    "liquidity",
    "locker",
    "burn",
    "vault",
    "authority",
    "market",
    "bonding",
)

LP_LOCKED_MIN_PCT: float = 85.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_authority_active(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"none", "null", "0"}


@dataclass(slots=True)
class RiskEvaluationResult:
    """Outcome of the Phase 2 Hard Risk Filter for a single lead."""

    lead_id: int
    mint_address: str
    symbol: str
    passed: bool
    stage: str
    kill_reason: str | None = None
    liquidity_usd: float | None = None
    main_pool_share: float | None = None
    dev_wallet: str | None = None
    holders_count: int | None = None
    top10_pct: float | None = None
    dev_pct: float | None = None
    lp_locked_pct: float | None = None
    top_holder_owners: tuple[str, ...] = ()
    mutable_metadata: bool | None = None
    transfer_fee_pct: float | None = None
    rugcheck_score: float | None = None
    rugcheck_insider_flag: bool = False
    insiders_detected: int | None = None
    insider_networks_count: int | None = None
    creator_tokens_count: int | None = None


class HardRiskFilter:
    """Evaluates leads against blacklist, pool fragmentation, and RugCheck hard rules."""

    def __init__(
        self,
        db: Database,
        http_client: ResilientHttpClient | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or ResilientHttpClient(timeout=12.0, max_retries=3)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _fetch_solana_pairs(self, mint_address: str) -> list[dict[str, Any]]:
        url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint_address)
        payload = await self._http.get_json(url)
        return extract_solana_pairs(payload)

    async def _fetch_rugcheck_report(self, mint_address: str) -> dict[str, Any]:
        url = RUGCHECK_REPORT_URL.format(mint=mint_address)
        payload = await self._http.get_json(url)
        if not isinstance(payload, dict):
            raise HttpRequestError(f"Unexpected RugCheck payload type for {mint_address}")
        return payload

    @staticmethod
    def _compute_main_pool_metrics(
        pairs: list[dict[str, Any]],
        mint_address: str,
    ) -> tuple[float, float]:
        """Return ``(main_pool_liquidity_usd, main_pool_share)`` across Solana pairs."""
        matching_liqs: list[float] = []
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            base_addr = (pair.get("baseToken") or {}).get("address")
            quote_addr = (pair.get("quoteToken") or {}).get("address")
            if base_addr != mint_address and quote_addr != mint_address:
                continue
            liq = _safe_float((pair.get("liquidity") or {}).get("usd"))
            if liq > 0:
                matching_liqs.append(liq)

        if not matching_liqs:
            return 0.0, 0.0

        main_liq = max(matching_liqs)
        total_liq = sum(matching_liqs)
        share = (main_liq / total_liq) if total_liq > 0 else 0.0
        return round(main_liq, 2), round(share, 4)

    @staticmethod
    def _extract_excluded_pool_addresses(
        report: dict[str, Any],
        pair_address: str | None,
    ) -> set[str]:
        """Identify AMM pools, LP vaults, lockers, and burn addresses to exclude from Top 10."""
        excluded: set[str] = set(BURN_OR_SYSTEM_ADDRESSES)
        if pair_address:
            excluded.add(pair_address)

        known_accounts = report.get("knownAccounts")
        if isinstance(known_accounts, dict):
            for addr, meta in known_accounts.items():
                if not isinstance(meta, dict):
                    continue
                acc_type = str(meta.get("type") or "").upper()
                acc_name = str(meta.get("name") or "").lower()
                if acc_type in {"AMM", "LOCKER", "BURN", "MARKET", "VAULT"} or any(
                    kw in acc_name for kw in POOL_ACCOUNT_KEYWORDS
                ):
                    excluded.add(str(addr))

        markets = report.get("markets")
        if isinstance(markets, list):
            for market in markets:
                if not isinstance(market, dict):
                    continue
                for key in ("pubkey", "liquidityA", "liquidityB", "mintLP"):
                    val = market.get(key)
                    if isinstance(val, str) and val:
                        excluded.add(val)
                lp = market.get("lp")
                if isinstance(lp, dict):
                    for key in ("lpMint", "lpVault", "base", "quote", "tokenAccountA", "tokenAccountB"):
                        val = lp.get(key)
                        if isinstance(val, str) and val:
                            excluded.add(val)

        return excluded

    @staticmethod
    def _extract_lp_locked_pct(
        report: dict[str, Any],
        pair_address: str | None,
    ) -> float:
        """Compute locked/burned LP percentage from RugCheck market entries."""
        markets = report.get("markets")
        if not isinstance(markets, list) or not markets:
            return 0.0

        # Prefer the market matching pair_address if present
        candidates: list[tuple[float, float]] = []  # (weight_usd, locked_pct)
        for market in markets:
            if not isinstance(market, dict):
                continue
            lp = market.get("lp")
            if not isinstance(lp, dict):
                continue
            locked_pct = _safe_float(lp.get("lpLockedPct"), default=-1.0)
            if locked_pct < 0.0:
                continue
            if pair_address and market.get("pubkey") == pair_address:
                return round(locked_pct, 2)
            weight = _safe_float(lp.get("quoteUSD")) + _safe_float(lp.get("baseUSD"))
            if weight <= 0:
                weight = _safe_float(lp.get("lpLockedUSD"))
            candidates.append((weight, locked_pct))

        if not candidates:
            return 0.0

        # Pick locked_pct of the largest-liquidity market reported by RugCheck
        candidates.sort(key=lambda item: item[0], reverse=True)
        return round(candidates[0][1], 2)

    @staticmethod
    def _compute_top10_pct(
        report: dict[str, Any],
        excluded_addresses: set[str],
    ) -> float:
        """Sum percentage held by top 10 non-pool/non-AMM holders."""
        top_holders = report.get("topHolders")
        if not isinstance(top_holders, list):
            return 0.0

        filtered_pcts: list[float] = []
        for holder in top_holders:
            if not isinstance(holder, dict):
                continue
            address = str(holder.get("address") or "")
            owner = str(holder.get("owner") or "")
            if address in excluded_addresses or owner in excluded_addresses:
                continue
            pct = _safe_float(holder.get("pct"))
            if pct > 0:
                filtered_pcts.append(pct)

        filtered_pcts.sort(reverse=True)
        return round(sum(filtered_pcts[:10]), 4)

    @staticmethod
    def _compute_dev_wallet_pct(
        report: dict[str, Any],
        dev_wallet: str | None,
    ) -> float:
        """Determine percentage of total supply currently controlled by ``dev_wallet``."""
        pct_from_holders = 0.0
        if dev_wallet and isinstance(report.get("topHolders"), list):
            for holder in report["topHolders"]:
                if not isinstance(holder, dict):
                    continue
                if holder.get("owner") == dev_wallet or holder.get("address") == dev_wallet:
                    pct_from_holders += _safe_float(holder.get("pct"))

        pct_from_creator_balance = 0.0
        creator_balance = _safe_float(report.get("creatorBalance"), default=0.0)
        token_info = report.get("token") if isinstance(report.get("token"), dict) else {}
        total_supply = _safe_float(token_info.get("supply"), default=0.0)
        if creator_balance > 0 and total_supply > 0:
            pct_from_creator_balance = (creator_balance / total_supply) * 100.0

        return round(max(pct_from_holders, pct_from_creator_balance), 4)

    async def _reject(
        self,
        lead: dict[str, Any],
        reason: str,
        *,
        liquidity_usd: float | None = None,
        main_pool_share: float | None = None,
        dev_wallet: str | None = None,
        holders_count: int | None = None,
        top10_pct: float | None = None,
        dev_pct: float | None = None,
        lp_locked_pct: float | None = None,
    ) -> RiskEvaluationResult:
        await self.db.update_lead(
            lead["id"],
            current_stage="REJECTED",
            kill_reason=reason,
            liquidity_usd=liquidity_usd,
            main_pool_share=main_pool_share,
            dev_wallet=dev_wallet,
            holders_count=holders_count,
        )
        return RiskEvaluationResult(
            lead_id=lead["id"],
            mint_address=lead["mint_address"],
            symbol=lead["symbol"],
            passed=False,
            stage="REJECTED",
            kill_reason=reason,
            liquidity_usd=liquidity_usd if liquidity_usd is not None else lead.get("liquidity_usd"),
            main_pool_share=main_pool_share if main_pool_share is not None else lead.get("main_pool_share"),
            dev_wallet=dev_wallet or lead.get("dev_wallet"),
            holders_count=holders_count if holders_count is not None else lead.get("holders_count"),
            top10_pct=top10_pct,
            dev_pct=dev_pct,
            lp_locked_pct=lp_locked_pct,
        )

    async def evaluate_lead(
        self,
        lead: dict[str, Any],
        *,
        dex_pairs_override: list[dict[str, Any]] | None = None,
        rugcheck_report_override: dict[str, Any] | None = None,
    ) -> RiskEvaluationResult:
        """Run all Phase 2 Hard Risk checks against a lead and persist outcome in SQLite."""
        mint = str(lead["mint_address"])
        pair_address = lead.get("pair_address")

        # --------------------------------------------------------------
        # 1. BLACKLIST CHECK FIRST (zero wasted external calls if known)
        # --------------------------------------------------------------
        known_dev = lead.get("dev_wallet")
        if known_dev and await self.db.is_dev_blacklisted(known_dev):
            return await self._reject(lead, "BLACKLISTED_DEV", dev_wallet=known_dev)

        # --------------------------------------------------------------
        # 2. SINGLE PRIMARY POOL / FRAGMENTED LIQUIDITY CHECK
        # --------------------------------------------------------------
        try:
            pairs = (
                dex_pairs_override
                if dex_pairs_override is not None
                else await self._fetch_solana_pairs(mint)
            )
        except HttpRequestError as exc:
            logger.warning("DexScreener pairs lookup failed for %s: %s", mint, exc)
            return await self._reject(lead, "DEXSCREENER_UNAVAILABLE")

        main_liq, main_pool_share = self._compute_main_pool_metrics(pairs, mint)
        if (
            main_pool_share < self.settings.main_pool_liquidity_share_min
            or main_liq < self.settings.min_liquidity_usd
        ):
            return await self._reject(
                lead,
                "FRAGMENTED_LIQUIDITY",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
            )

        # --------------------------------------------------------------
        # 3. RUGCHECK CONTRACT & HOLDER AUDIT
        # --------------------------------------------------------------
        try:
            report = (
                rugcheck_report_override
                if rugcheck_report_override is not None
                else await self._fetch_rugcheck_report(mint)
            )
        except HttpRequestError as exc:
            logger.warning("RugCheck lookup failed for %s: %s", mint, exc)
            return await self._reject(
                lead,
                "RUGCHECK_UNAVAILABLE",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
            )

        dev_wallet = report.get("creator") or known_dev
        if isinstance(dev_wallet, str):
            dev_wallet = dev_wallet.strip() or None
        else:
            dev_wallet = None

        holders_count = _safe_int(report.get("totalHolders"))

        # Re-check blacklist immediately once dev_wallet is discovered via RugCheck
        if dev_wallet and await self.db.is_dev_blacklisted(dev_wallet):
            return await self._reject(
                lead,
                "BLACKLISTED_DEV",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
            )

        # 3a. Mint / Freeze Authority active
        token_meta = report.get("token") if isinstance(report.get("token"), dict) else {}
        mint_auth = report.get("mintAuthority") or token_meta.get("mintAuthority")
        freeze_auth = report.get("freezeAuthority") or token_meta.get("freezeAuthority")
        if (self.settings.require_mint_revoked and _is_authority_active(mint_auth)) or (
            self.settings.require_freeze_revoked and _is_authority_active(freeze_auth)
        ):
            return await self._reject(
                lead,
                "MINT_OR_FREEZE_ACTIVE",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
            )

        # 3a-bis. Rugged flag or hidden Token-2022 transfer fee
        if bool(report.get("rugged")):
            return await self._reject(
                lead,
                "RUGCHECK_RUGGED",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
            )

        tf_obj = report.get("transferFee") if isinstance(report.get("transferFee"), dict) else {}
        transfer_fee_pct = _safe_float(tf_obj.get("pct"), default=0.0)
        if transfer_fee_pct > 0.0:
            return await self._reject(
                lead,
                "HIDDEN_TRANSFER_FEE",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
            )

        # 3b. LP Locked or Burned
        lp_locked_pct = self._extract_lp_locked_pct(report, pair_address)
        if self.settings.require_lp_locked_or_burned and lp_locked_pct < LP_LOCKED_MIN_PCT:
            return await self._reject(
                lead,
                "LP_NOT_LOCKED",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
                lp_locked_pct=lp_locked_pct,
            )

        # 3c. Top 10 Holders Concentration (excluding pool/AMM/locker)
        excluded_addrs = self._extract_excluded_pool_addresses(report, pair_address)
        top10_pct = self._compute_top10_pct(report, excluded_addrs)
        if top10_pct > self.settings.max_top10_holders_pct:
            return await self._reject(
                lead,
                "TOP10_CONCENTRATION",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
                top10_pct=top10_pct,
                lp_locked_pct=lp_locked_pct,
            )

        # 3d. Dev Wallet > 4.0%
        dev_pct = self._compute_dev_wallet_pct(report, dev_wallet)
        if dev_pct > self.settings.max_dev_wallet_pct:
            return await self._reject(
                lead,
                "DEV_WALLET_HIGH",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
                top10_pct=top10_pct,
                dev_pct=dev_pct,
                lp_locked_pct=lp_locked_pct,
            )

        # 3e. Minimum Holders (< 150 when data available)
        if holders_count is not None and holders_count < self.settings.min_holders:
            return await self._reject(
                lead,
                "LOW_HOLDERS",
                liquidity_usd=main_liq,
                main_pool_share=main_pool_share,
                dev_wallet=dev_wallet,
                holders_count=holders_count,
                top10_pct=top10_pct,
                dev_pct=dev_pct,
                lp_locked_pct=lp_locked_pct,
            )

        # --------------------------------------------------------------
        # PASSED ALL HARD RISK CHECKS -> Advance to stage "RISK"
        # --------------------------------------------------------------
        await self.db.update_lead(
            lead["id"],
            current_stage="RISK",
            liquidity_usd=main_liq,
            main_pool_share=main_pool_share,
            dev_wallet=dev_wallet,
            holders_count=holders_count,
        )
        top_holder_owners: list[str] = []
        raw_holders = report.get("topHolders")
        if isinstance(raw_holders, list):
            for h in raw_holders:
                if not isinstance(h, dict):
                    continue
                for field_name in ("owner", "address"):
                    val = str(h.get(field_name) or "").strip()
                    if val and val not in excluded_addrs and val not in top_holder_owners:
                        top_holder_owners.append(val)

        token_meta = report.get("tokenMeta") if isinstance(report.get("tokenMeta"), dict) else {}
        mutable_meta = bool(token_meta["mutable"]) if "mutable" in token_meta else None
        rugcheck_score = _safe_float(report.get("score_normalised"), default=-1.0)
        insiders_detected = _safe_int(report.get("graphInsidersDetected"))
        insider_nets = report.get("insiderNetworks")
        insider_networks_count = len(insider_nets) if isinstance(insider_nets, list) else None
        risks_raw = report.get("risks") if isinstance(report.get("risks"), list) else []
        rugcheck_insider_flag = any(
            isinstance(r, dict)
            and any(
                kw in f"{r.get('name', '')} {r.get('description', '')}".lower()
                for kw in ("insider", "bundle", "sniper", "copycat")
            )
            for r in risks_raw
        )
        creator_tokens = report.get("creatorTokens")
        creator_tokens_count = len(creator_tokens) if isinstance(creator_tokens, list) else None

        return RiskEvaluationResult(
            lead_id=lead["id"],
            mint_address=mint,
            symbol=lead["symbol"],
            passed=True,
            stage="RISK",
            kill_reason=None,
            liquidity_usd=main_liq,
            main_pool_share=main_pool_share,
            dev_wallet=dev_wallet,
            holders_count=holders_count,
            top10_pct=top10_pct,
            dev_pct=dev_pct,
            lp_locked_pct=lp_locked_pct,
            top_holder_owners=tuple(top_holder_owners),
            mutable_metadata=mutable_meta,
            transfer_fee_pct=transfer_fee_pct,
            rugcheck_score=rugcheck_score if rugcheck_score >= 0 else None,
            rugcheck_insider_flag=rugcheck_insider_flag,
            insiders_detected=insiders_detected,
            insider_networks_count=insider_networks_count,
            creator_tokens_count=creator_tokens_count,
        )
