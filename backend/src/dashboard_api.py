"""Phase 5: Solana Judge Desk Command Center REST API (FastAPI).

Thin, read-mostly API over SQLite plus a handful of essential controls. Protected by
``DASHBOARD_PIN`` (header ``X-Dashboard-Pin`` or cookie ``dashboard_pin``).
"""

from __future__ import annotations

import asyncio
from collections import deque
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field

from src.config import (
    PROJECT_ROOT,
    Settings,
    TradingMode,
    apply_strategy_profile,
    get_settings,
)
from src.database import Database, utc_now_iso
from src.execution.base import ExecutionEngine, ExecutionError
from src.execution.live_engine import LiveEngine
from src.execution.tutorial_engine import TutorialEngine
from src.http_client import ResilientHttpClient
from src.price_oracle import SolPriceOracle
from src.risk import HardRiskFilter, LP_LOCKED_MIN_PCT, _is_authority_active
from src.search import DEXSCREENER_SOLANA_TOKENS_URL, extract_solana_pairs
from src.security import install_secret_redaction
from src.strategies import (
    apply_params_to_settings,
    delete_strategy,
    load_strategies,
    save_strategies_store,
    snapshot_current_params,
    upsert_strategy,
)
from src.sunday_audit import SundayAuditor
from src.wallet_manager import (
    add_withdrawal_wallet,
    configure_live_wallet_in_env,
    fetch_live_wallet_balance_sol,
    generate_dedicated_trading_wallet,
    get_public_address_from_settings,
    list_withdrawal_wallets,
    remove_withdrawal_wallet,
    withdraw_sol_to_whitelisted,
)

logger = logging.getLogger(__name__)

PIN_HEADER = "X-Dashboard-Pin"
PIN_COOKIE = "dashboard_pin"
DASHBOARD_DIST = PROJECT_ROOT / "dashboard" / "dist"

# Tutorial simulation fields editable from the dashboard (persisted in runtime_config)
TUTORIAL_OVERRIDE_FIELDS: dict[str, type] = {
    "tutorial_initial_bankroll_sol": float,
    "sim_dex_fee_pct": float,
    "sim_priority_fee_sol": float,
    "sim_jito_tip_sol": float,
    "sim_buy_slippage_pct": float,
    "sim_tp_slippage_pct": float,
    "sim_sl_slippage_pct": float,
}

RADAR_LANES: dict[str, tuple[str, ...]] = {
    "SEARCH": ("SEARCH",),
    "RISK_WHALE": ("RISK", "WHALE"),
    "JUDGE": ("JUDGE", "GATE", "PENDING_APPROVAL"),
    "POSITION": ("OPEN", "RISK_FREE_RUNNER"),
}

RISK_KILLS = {
    "BLACKLISTED_DEV", "FRAGMENTED_LIQUIDITY", "MINT_OR_FREEZE_ACTIVE", "LP_NOT_LOCKED",
    "TOP10_CONCENTRATION", "DEV_WALLET_HIGH", "LOW_HOLDERS", "DEXSCREENER_UNAVAILABLE",
    "RUGCHECK_UNAVAILABLE",
}
JUDGE_KILLS = {"JUDGE_PASS", "JUDGE_TIMEOUT_OR_ERROR", "SECOND_WATCH_KILL"}
APPROVAL_KILLS = {"APPROVAL_TIMEOUT", "MANUAL_REJECT"}


def death_phase(kill_reason: str | None) -> str:
    """Infer the pipeline phase where a rejected lead died from its kill reason."""
    if not kill_reason:
        return "UNKNOWN"
    if kill_reason in RISK_KILLS:
        return "RISK"
    if kill_reason in JUDGE_KILLS:
        return "JUDGE"
    if kill_reason in APPROVAL_KILLS:
        return "APPROVAL"
    return "GATE"


# ----------------------------------------------------------------------
# Request Models
# ----------------------------------------------------------------------
class LoginBody(BaseModel):
    pin: str


class PauseBody(BaseModel):
    paused: bool | None = None


class BlacklistBody(BaseModel):
    dev_wallet: str = Field(min_length=20, max_length=64)
    reason: str = Field(default="MANUAL", max_length=120)
    mint_address: str | None = Field(default=None, max_length=64)


class SmartWalletBody(BaseModel):
    address: str = Field(min_length=20, max_length=64)
    label: str = Field(default="", max_length=60)


class TutorialConfigBody(BaseModel):
    tutorial_initial_bankroll_sol: float | None = Field(default=None, gt=0, le=10_000)
    trade_size_sol: float | None = Field(default=None, gt=0, le=1_000)
    sim_dex_fee_pct: float | None = Field(default=None, ge=0, le=10)
    sim_priority_fee_sol: float | None = Field(default=None, ge=0, le=0.1)
    sim_jito_tip_sol: float | None = Field(default=None, ge=0, le=0.1)
    sim_buy_slippage_pct: float | None = Field(default=None, ge=0, le=50)
    sim_tp_slippage_pct: float | None = Field(default=None, ge=0, le=50)
    sim_sl_slippage_pct: float | None = Field(default=None, ge=0, le=90)
    semi_auto_mode: bool | None = None
    max_concurrent_trades: int | None = Field(default=None, ge=1, le=20)
    strategy_profile: Literal["CONSERVADOR", "AGRESIVO"] | None = None


class TutorialResetBody(BaseModel):
    initial_sol: float | None = Field(default=None, gt=0, le=10_000)


class ModeSwitchBody(BaseModel):
    mode: Literal["TUTORIAL", "LIVE"]


class StrategySaveBody(BaseModel):
    id: str | None = Field(default=None, max_length=60)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    params: dict[str, Any] = Field(default_factory=dict)


class StrategyApplyBody(BaseModel):
    id: str = Field(min_length=1, max_length=60)


class WalletConfigureBody(BaseModel):
    private_key: str | None = Field(default=None, max_length=512)
    enable_live_execution: bool | None = None


class WithdrawalWhitelistBody(BaseModel):
    address: str = Field(min_length=20, max_length=64)
    label: str = Field(default="", max_length=60)


class WithdrawSolBody(BaseModel):
    destination: str = Field(min_length=20, max_length=64)
    amount_sol: float = Field(gt=0.0, le=1000.0)


# ----------------------------------------------------------------------
# Shared Application State
# ----------------------------------------------------------------------
class DashboardContext:
    """Holds DB, settings, price oracle, execution engine, and optional orchestrator."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: Database,
        price_oracle: Any,
        engine: ExecutionEngine,
        orchestrator: Any | None,
        live_engine_available: bool,
    ) -> None:
        self.settings = settings
        self.db = db
        self.price_oracle = price_oracle
        self.engine = engine
        self.orchestrator = orchestrator
        self.live_engine_available = live_engine_available
        self.trade_lock = asyncio.Lock()
        self._token_live_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._token_price_history: dict[str, deque[dict[str, Any]]] = {}
        self._token_image_cache: dict[str, str | None] = {}
        self._ohlcv_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._wallet_live_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._token_whales_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._batch_price_cache: tuple[float, dict[str, float]] = (0.0, {})
        self._token_http = ResilientHttpClient(timeout=6.0, max_retries=1)

    async def fetch_live_prices_batch(self, mints: list[str]) -> dict[str, float]:
        """Sub-second batch mark-to-market Solana spot prices for open positions (cached 0.8s)."""
        unique = [m for m in dict.fromkeys(mints) if m]
        if not unique:
            return {}
        now_mono = time.monotonic()
        cached_ts, cached_map = self._batch_price_cache
        if (now_mono - cached_ts) < 0.8 and all(m in cached_map for m in unique):
            return cached_map

        http = getattr(self.orchestrator, "http", None) or self._token_http
        prices: dict[str, float] = dict(cached_map)
        for idx in range(0, len(unique), 30):
            batch = unique[idx : idx + 30]
            url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=",".join(batch))
            payload = await http.get_json_safe(url, default=[])
            best_by_mint: dict[str, tuple[float, float]] = {}
            for p in extract_solana_pairs(payload):
                addr = str((p.get("baseToken") or {}).get("address") or "")
                try:
                    px = float(p.get("priceUsd") or 0.0)
                    liq = float((p.get("liquidity") or {}).get("usd") or 0.0)
                except (TypeError, ValueError):
                    continue
                if addr and px > 0:
                    prev = best_by_mint.get(addr)
                    if prev is None or liq > prev[0]:
                        best_by_mint[addr] = (liq, px)
            for addr, (_liq, px) in best_by_mint.items():
                prices[addr] = px
        self._batch_price_cache = (now_mono, prices)
        return prices

    async def fetch_token_rugcheck_bundle(
        self,
        mint_address: str,
        pair_address: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch RugCheck report once (cached 20s) for both Smart Wallet matches & Security Audit."""
        now_mono = time.monotonic()
        cached = self._token_whales_cache.get(mint_address)
        if cached and (now_mono - cached[0]) < 20.0:
            bundle = cached[1]
            return bundle["smart_wallets"], bundle["security_audit"]

        wallets = _read_smart_wallets(self.settings.resolved_smart_wallets_path)
        by_addr = {w["address"]: w for w in wallets if w.get("address")}
        matched: list[dict[str, Any]] = []
        checked_holders = 0

        http = getattr(self.orchestrator, "http", None) or self._token_http
        url = f"https://api.rugcheck.xyz/v1/tokens/{mint_address}/report"
        report = await http.get_json_safe(url, default={})
        has_report = isinstance(report, dict) and bool(report.get("mint") or report.get("token"))

        if has_report and by_addr:
            top_holders = report.get("topHolders") or []
            seen_addrs: set[str] = set()
            if isinstance(top_holders, list):
                checked_holders = len(top_holders)
                for h in top_holders:
                    if not isinstance(h, dict):
                        continue
                    for field_name in ("owner", "address"):
                        addr = str(h.get(field_name) or "").strip()
                        if addr and addr in by_addr and addr not in seen_addrs:
                            seen_addrs.add(addr)
                            w = dict(by_addr[addr])
                            try:
                                w["pct_held"] = round(float(h.get("pct") or 0.0), 3)
                            except (TypeError, ValueError):
                                w["pct_held"] = None
                            matched.append(w)

        security_audit: dict[str, Any] = {
            "available": False,
            "thresholds": {
                "lp_locked_min_pct": LP_LOCKED_MIN_PCT,
                "max_top10_pct": self.settings.max_top10_holders_pct,
                "max_dev_pct": self.settings.max_dev_wallet_pct,
                "main_pool_min_pct": round(self.settings.main_pool_liquidity_share_min * 100.0, 1),
                "min_holders": self.settings.min_holders,
                "min_liquidity_usd": self.settings.min_liquidity_usd,
            },
        }
        if has_report:
            token_info = report.get("token") if isinstance(report.get("token"), dict) else {}
            mint_auth = token_info.get("mintAuthority") or report.get("mintAuthority")
            freeze_auth = token_info.get("freezeAuthority") or report.get("freezeAuthority")
            dev_wallet = str(report.get("creator") or "").strip() or None
            excluded = HardRiskFilter._extract_excluded_pool_addresses(report, pair_address)
            lp_locked_pct = HardRiskFilter._extract_lp_locked_pct(report, pair_address)
            top10_pct = HardRiskFilter._compute_top10_pct(report, excluded)
            dev_pct = HardRiskFilter._compute_dev_wallet_pct(report, dev_wallet)
            token_meta = report.get("tokenMeta") if isinstance(report.get("tokenMeta"), dict) else {}
            tf_obj = report.get("transferFee") if isinstance(report.get("transferFee"), dict) else {}
            try:
                tf_pct = float(tf_obj.get("pct") or 0.0)
            except (TypeError, ValueError):
                tf_pct = 0.0
            risks_raw = report.get("risks") if isinstance(report.get("risks"), list) else []
            risks = [
                {
                    "name": str(r.get("name") or ""),
                    "level": str(r.get("level") or "warn"),
                    "value": str(r.get("value") or ""),
                    "description": str(r.get("description") or "")[:160],
                }
                for r in risks_raw[:8]
                if isinstance(r, dict)
            ]
            insider_nets = report.get("insiderNetworks")
            creator_tokens = report.get("creatorTokens")
            rugcheck_insider_flag = any(
                any(
                    kw in f"{r.get('name', '')} {r.get('description', '')}".lower()
                    for kw in ("insider", "bundle", "sniper", "copycat")
                )
                for r in risks
            )
            security_audit = {
                "available": True,
                "mint_revoked": not _is_authority_active(mint_auth),
                "freeze_revoked": not _is_authority_active(freeze_auth),
                "lp_locked_pct": lp_locked_pct,
                "top10_pct": round(top10_pct, 2),
                "dev_pct": round(dev_pct, 2),
                "mutable_metadata": bool(token_meta.get("mutable")) if "mutable" in token_meta else None,
                "transfer_fee_pct": round(tf_pct, 3),
                "rugged": bool(report.get("rugged")),
                "rugcheck_score": report.get("score_normalised"),
                "rugcheck_insider_flag": rugcheck_insider_flag,
                "insiders_detected": report.get("graphInsidersDetected"),
                "insider_networks_count": len(insider_nets) if isinstance(insider_nets, list) else 0,
                "creator_tokens_count": len(creator_tokens) if isinstance(creator_tokens, list) else None,
                "risks": risks,
                "thresholds": security_audit["thresholds"],
            }

        smart_wallets = {
            "count": len(matched),
            "total_tracked": len(by_addr),
            "checked_holders": checked_holders,
            "wallets": matched,
        }
        self._token_whales_cache[mint_address] = (
            now_mono,
            {"smart_wallets": smart_wallets, "security_audit": security_audit},
        )
        return smart_wallets, security_audit

    async def fetch_token_ohlcv(
        self,
        pair_address: str | None,
        timeframe: str = "1m",
        live_price: float | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch real Solana OHLCV Japanese candles from GeckoTerminal and merge live tick."""
        if not pair_address:
            return []
        agg_map = {"1m": 1, "5m": 5, "15m": 15}
        agg = agg_map.get(timeframe, 1)
        tf_key = f"{agg}m"
        cache_key = (pair_address, tf_key)
        now_mono = time.monotonic()

        candles: list[dict[str, Any]]
        cached = self._ohlcv_cache.get(cache_key)
        if cached and (now_mono - cached[0]) < 4.0:
            candles = [dict(c) for c in cached[1]]
        else:
            http = getattr(self.orchestrator, "http", None) or self._token_http
            url = (
                f"https://api.geckoterminal.com/api/v2/networks/solana/pools/"
                f"{pair_address}/ohlcv/minute"
            )
            payload = await http.get_json_safe(
                url, params={"aggregate": agg, "limit": 80}, default={}
            )
            raw_list = (
                ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")
                if isinstance(payload, dict)
                else []
            )
            parsed: list[dict[str, Any]] = []
            for row in raw_list or []:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                try:
                    parsed.append(
                        {
                            "ts": int(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )
                except (TypeError, ValueError):
                    continue
            parsed.sort(key=lambda c: c["ts"])
            if parsed:
                self._ohlcv_cache[cache_key] = (now_mono, parsed)
                candles = [dict(c) for c in parsed]
            elif cached:
                candles = [dict(c) for c in cached[1]]
            else:
                candles = []

        # Live 0.25s tick merge into the rightmost active candle
        if candles and live_price and live_price > 0:
            bucket_sec = agg * 60
            now_bucket = int(time.time() // bucket_sec) * bucket_sec
            last = candles[-1]
            if now_bucket > last["ts"]:
                candles.append(
                    {
                        "ts": now_bucket,
                        "open": last["close"],
                        "high": max(last["close"], live_price),
                        "low": min(last["close"], live_price),
                        "close": live_price,
                        "volume": 0.0,
                    }
                )
            else:
                last["close"] = live_price
                if live_price > last["high"]:
                    last["high"] = live_price
                if live_price < last["low"]:
                    last["low"] = live_price
        return candles

    async def fetch_wallet_live_rpc(self, address: str) -> dict[str, Any]:
        """Fetch live SOL balance & recent signatures from Solana RPC (cached 15s)."""
        now_mono = time.monotonic()
        cached = self._wallet_live_cache.get(address)
        if cached and (now_mono - cached[0]) < 15.0:
            return cached[1]

        rpc_url = self.settings.solana_rpc_url or "https://api.mainnet-beta.solana.com"
        sol_balance: float | None = None
        recent_txs: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                bal_resp, sig_resp = await asyncio.gather(
                    client.post(
                        rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getBalance",
                            "params": [address],
                        },
                    ),
                    client.post(
                        rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "getSignaturesForAddress",
                            "params": [address, {"limit": 8}],
                        },
                    ),
                    return_exceptions=True,
                )
                if isinstance(bal_resp, httpx.Response) and bal_resp.status_code == 200:
                    val = (bal_resp.json().get("result") or {}).get("value")
                    if isinstance(val, (int, float)):
                        sol_balance = round(float(val) / 1_000_000_000.0, 4)
                if isinstance(sig_resp, httpx.Response) and sig_resp.status_code == 200:
                    sigs = sig_resp.json().get("result") or []
                    for s in sigs:
                        if isinstance(s, dict) and s.get("signature"):
                            recent_txs.append(
                                {
                                    "signature": str(s["signature"]),
                                    "block_time": s.get("blockTime"),
                                    "err": s.get("err") is not None,
                                    "solscan_tx_url": f"https://solscan.io/tx/{s['signature']}",
                                }
                            )
        except Exception as exc:
            logger.debug("Solana RPC wallet lookup failed for %s: %s", address[:8], exc)

        result = {
            "live_sol_balance": sol_balance,
            "recent_txs": recent_txs,
            "fetched_at": utc_now_iso(),
        }
        self._wallet_live_cache[address] = (now_mono, result)
        return result

    async def resolve_token_images(self, mints: list[str]) -> dict[str, str | None]:
        """Batch-resolve token icon URLs from DexScreener and cache in memory."""
        missing = [m for m in dict.fromkeys(mints) if m and m not in self._token_image_cache]
        if missing:
            http = getattr(self.orchestrator, "http", None) or self._token_http
            for idx in range(0, len(missing), 30):
                batch = missing[idx : idx + 30]
                url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=",".join(batch))
                payload = await http.get_json_safe(url, timeout=4.0, default=None)
                found: set[str] = set()
                for pair in extract_solana_pairs(payload):
                    mint = (pair.get("baseToken") or {}).get("address")
                    info = pair.get("info") if isinstance(pair.get("info"), dict) else {}
                    img = info.get("imageUrl")
                    if mint and img and not self._token_image_cache.get(mint):
                        self._token_image_cache[mint] = str(img)
                        found.add(mint)
                for m in batch:
                    if m not in self._token_image_cache:
                        self._token_image_cache[m] = None
        return {m: self._token_image_cache.get(m) for m in mints}

    @property
    def exit_manager(self) -> Any | None:
        return getattr(self.orchestrator, "exit_manager", None) if self.orchestrator else None

    async def fetch_live_token_market(self, mint_address: str, pair_address: str | None = None) -> dict[str, Any] | None:
        """Fetch live DexScreener market details cached 1.5s to support 0.25s UI polling."""
        now_mono = time.monotonic()
        cached = self._token_live_cache.get(mint_address)
        if cached is not None and (now_mono - cached[0]) < 1.5:
            return cached[1]

        url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint_address)
        http = getattr(self.orchestrator, "http", None) or self._token_http
        payload = await http.get_json_safe(url, timeout=5.0, default=None)
        pairs = extract_solana_pairs(payload)
        if not pairs:
            return cached[1] if cached else None

        total_liq = sum(float((p.get("liquidity") or {}).get("usd") or 0.0) for p in pairs)
        chosen = None
        if pair_address:
            chosen = next((p for p in pairs if p.get("pairAddress") == pair_address), None)
        if chosen is None:
            pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0), reverse=True)
            chosen = pairs[0]

        liq_usd = float((chosen.get("liquidity") or {}).get("usd") or 0.0)
        try:
            price_usd = float(chosen.get("priceUsd") or 0.0)
        except (TypeError, ValueError):
            price_usd = 0.0
        mcap = chosen.get("marketCap") if chosen.get("marketCap") is not None else chosen.get("fdv")
        market_cap = float(mcap) if mcap is not None else None
        fdv = float(chosen["fdv"]) if chosen.get("fdv") is not None else None

        vol = chosen.get("volume") or {}
        pc = chosen.get("priceChange") or {}
        txns = chosen.get("txns") or {}
        info = chosen.get("info") if isinstance(chosen.get("info"), dict) else {}
        websites = [
            {"label": str(w.get("label") or "Web"), "url": str(w["url"])}
            for w in (info.get("websites") or [])
            if isinstance(w, dict) and w.get("url")
        ]
        socials = [
            {"type": str(s.get("type") or "social").lower(), "url": str(s["url"])}
            for s in (info.get("socials") or [])
            if isinstance(s, dict) and s.get("url")
        ]

        live = {
            "price_usd": price_usd if price_usd > 0 else None,
            "market_cap": market_cap,
            "fdv": fdv,
            "liquidity_usd": liq_usd,
            "total_liquidity_usd": round(total_liq, 2),
            "main_pool_share": round(liq_usd / total_liq, 4) if total_liq > 0 else None,
            "dex_id": chosen.get("dexId"),
            "pair_address": chosen.get("pairAddress"),
            "pair_url": chosen.get("url"),
            "pair_created_at": chosen.get("pairCreatedAt"),
            "image_url": info.get("imageUrl"),
            "header_url": info.get("header"),
            "websites": websites,
            "socials": socials,
            "price_change": {
                "m5": pc.get("m5"),
                "h1": pc.get("h1"),
                "h6": pc.get("h6"),
                "h24": pc.get("h24"),
            },
            "volume": {
                "m5": vol.get("m5"),
                "h1": vol.get("h1"),
                "h6": vol.get("h6"),
                "h24": vol.get("h24"),
            },
            "txns": {
                "m5": txns.get("m5") or {"buys": 0, "sells": 0},
                "h1": txns.get("h1") or {"buys": 0, "sells": 0},
                "h6": txns.get("h6") or {"buys": 0, "sells": 0},
                "h24": txns.get("h24") or {"buys": 0, "sells": 0},
            },
            "fetched_at": utc_now_iso(),
        }
        self._token_live_cache[mint_address] = (now_mono, live)

        if price_usd > 0:
            hist = self._token_price_history.setdefault(mint_address, deque(maxlen=80))
            hist.append(
                {
                    "ts": live["fetched_at"],
                    "price_usd": price_usd,
                    "market_cap": market_cap,
                    "liquidity_usd": liq_usd,
                    "vol_5m": float(vol.get("m5") or 0.0),
                }
            )
        return live

    async def sol_usd(self) -> float:
        try:
            return float(await self.price_oracle.get_sol_usd_price())
        except Exception:  # pragma: no cover - oracle already fails safe
            return float(self.settings.fallback_sol_usd_price)

    async def apply_runtime_overrides(self) -> None:
        overrides = await self.db.get_runtime_config()
        if "active_strategy_params" in overrides:
            try:
                saved_params = json.loads(str(overrides["active_strategy_params"]))
                if isinstance(saved_params, dict):
                    bot_upd = apply_params_to_settings(self.settings, saved_params)
                    if bot_upd:
                        await self.db.update_bot_state(**bot_upd)
            except (TypeError, ValueError):
                pass
        if "strategy_profile" in overrides:
            apply_strategy_profile(self.settings, str(overrides["strategy_profile"]))
        if "max_concurrent_trades" in overrides:
            try:
                mct = max(1, min(20, int(float(overrides["max_concurrent_trades"]))))
                self.settings.max_concurrent_trades = mct
                await self.db.update_bot_state(max_concurrent_trades=mct)
            except (TypeError, ValueError):
                pass
        for key, caster in TUTORIAL_OVERRIDE_FIELDS.items():
            if key in overrides:
                try:
                    setattr(self.settings, key, caster(overrides[key]))
                except (TypeError, ValueError):
                    logger.warning("Ignoring invalid runtime override %s=%r", key, overrides[key])


def get_ctx(request: Request) -> DashboardContext:
    return request.app.state.ctx


def require_pin(request: Request) -> None:
    """Dependency enforcing DASHBOARD_PIN when configured (constant-time compare)."""
    ctx: DashboardContext = request.app.state.ctx
    expected = ctx.settings.dashboard_pin.strip()
    if not expected:
        return
    provided = request.headers.get(PIN_HEADER) or request.cookies.get(PIN_COOKIE) or ""
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail="PIN_REQUIRED")


# ----------------------------------------------------------------------
# Smart Wallets JSON helpers
# ----------------------------------------------------------------------
def _read_smart_wallets(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw = payload.get("wallets") if isinstance(payload, dict) else []
    out: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, str) and item.strip():
            out.append({"address": item.strip(), "label": "", "added_at": None})
        elif isinstance(item, dict) and item.get("address"):
            clean = dict(item)
            clean["address"] = str(item["address"]).strip()
            clean["label"] = str(item.get("label") or "")
            clean["added_at"] = item.get("added_at")
            out.append(clean)
    return out


def _write_smart_wallets(path: Path, wallets: list[dict[str, Any]]) -> None:
    payload: dict[str, Any] = {
        "version": 1,
        "description": "Curated Solana smart-money / whale wallets tracked by WHALE phase.",
        "wallets": wallets,
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = {**existing, "wallets": wallets}
        except (OSError, ValueError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------
# Serialization helpers
# ----------------------------------------------------------------------
def _verdict_view(v: dict[str, Any] | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "organic": v.get("organic"),
        "organic_prob": v.get("organic_prob"),
        "late": v.get("late"),
        "late_prob": v.get("late_prob"),
        "exit_risk": v.get("exit_risk"),
        "manipulation_trap": v.get("manipulation_trap"),
        "trap_prob": v.get("trap_prob"),
        "action": v.get("action"),
        "action_prob": v.get("action_prob"),
        "gate_passed": bool(v.get("gate_passed")),
        "kill_reason": v.get("kill_reason"),
        "created_at": v.get("created_at"),
    }


def _pending_expiry(ctx: DashboardContext, lead: dict[str, Any]) -> str | None:
    if lead.get("current_stage") != "PENDING_APPROVAL":
        return None
    if ctx.orchestrator is not None:
        pending = ctx.orchestrator.pending_approvals.get(int(lead["id"]))
        if pending is not None:
            return pending.expires_at.isoformat()
    try:
        updated = datetime.fromisoformat(str(lead["updated_at"]))
    except (TypeError, ValueError):
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (updated + timedelta(seconds=int(ctx.settings.semi_auto_approval_timeout_seconds))).isoformat()


async def _close_trade_now(
    ctx: DashboardContext,
    trade: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Close 100% of a trade immediately using the live spot price when available."""
    spot = None
    em = ctx.exit_manager
    if em is not None:
        try:
            snap = await em.fetch_market_snapshot(
                str(trade["mint_address"]), pair_address=trade.get("lead_pair_address")
            )
            if snap is not None:
                spot = snap.price_usd
        except Exception as exc:  # pragma: no cover - network guard
            logger.warning("Manual exit snapshot failed for %s: %s", trade["symbol"], exc)
    receipt = await ctx.engine.execute_sell(
        trade,
        100.0,
        reason,
        is_emergency=True,
        spot_price_usd=spot,
        sol_usd_price=await ctx.sol_usd(),
    )
    if em is not None:
        em.forget_trade(int(trade["id"]))
    notifier = getattr(ctx.orchestrator, "notifier", None) if ctx.orchestrator else None
    if notifier is not None:
        await notifier.notify_trade_closed(receipt.trade)
    return receipt.trade


# ----------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------
public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_pin)])


@public.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    ctx = get_ctx(request)
    expected = ctx.settings.dashboard_pin.strip()
    if not expected:
        return {"pin_required": False, "authenticated": True}
    provided = request.headers.get(PIN_HEADER) or request.cookies.get(PIN_COOKIE) or ""
    ok = hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    return {"pin_required": True, "authenticated": ok}


@public.post("/auth/login")
async def auth_login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    ctx = get_ctx(request)
    expected = ctx.settings.dashboard_pin.strip()
    if expected and not hmac.compare_digest(body.pin.encode("utf-8"), expected.encode("utf-8")):
        await asyncio.sleep(0.6)  # slow down brute-force attempts
        raise HTTPException(status_code=401, detail="PIN_INVALID")
    response.set_cookie(
        PIN_COOKIE, body.pin, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 7
    )
    return {"ok": True}


@public.post("/auth/logout")
async def auth_logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(PIN_COOKIE)
    return {"ok": True}


@api.get("/hud")
async def get_hud(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    mode = str(state["trading_mode"])
    sol_usd = await ctx.sol_usd()
    open_trades = await ctx.db.get_open_trades(mode=mode)
    live_prices = await ctx.fetch_live_prices_batch(
        [str(t["mint_address"]) for t in open_trades]
    )

    open_value_sol = 0.0
    unrealized_sol = 0.0
    for t in open_trades:
        mint = str(t["mint_address"])
        px = live_prices.get(mint) or float(t.get("current_price_usd") or t.get("entry_price_usd") or 0.0)
        rem_sol = ((float(t["tokens_remaining"]) * px) / sol_usd) if (px > 0 and sol_usd > 0) else 0.0
        open_value_sol += rem_sol
        unrealized_sol += (float(t["realized_sol"]) + rem_sol) - float(t["invested_sol"])
    open_value_sol = round(open_value_sol, 6)
    unrealized_sol = round(unrealized_sol, 6)

    leads = await ctx.db.list_leads(stage="PENDING_APPROVAL", limit=50)

    available_sol = float(state["tutorial_available_sol"]) if mode == "TUTORIAL" else None
    equity_sol = round(available_sol + open_value_sol, 6) if available_sol is not None else None
    daily_pnl_sol = float(state["daily_pnl_sol"])
    total_live_pnl_sol = round(daily_pnl_sol + unrealized_sol, 6)
    live_locks_ok = bool(
        ctx.settings.enable_live_execution and ctx.settings.effective_solana_private_key
    )
    return {
        "mode": mode,
        "is_paused": bool(state["is_paused"]),
        "semi_auto_mode": bool(state["semi_auto_mode"]),
        "sol_usd": sol_usd,
        "bankroll_sol": float(state["tutorial_bankroll_sol"]),
        "available_sol": available_sol,
        "available_usd": round(available_sol * sol_usd, 2) if available_sol is not None else None,
        "open_value_sol": open_value_sol,
        "equity_sol": equity_sol,
        "equity_usd": round(equity_sol * sol_usd, 2) if equity_sol is not None else None,
        "daily_pnl_sol": daily_pnl_sol,
        "daily_pnl_usd": round(daily_pnl_sol * sol_usd, 2),
        "unrealized_pnl_sol": unrealized_sol,
        "unrealized_pnl_usd": round(unrealized_sol * sol_usd, 2),
        "total_live_pnl_sol": total_live_pnl_sol,
        "total_live_pnl_usd": round(total_live_pnl_sol * sol_usd, 2),
        "open_trades": len(open_trades),
        "max_concurrent_trades": int(state["max_concurrent_trades"]),
        "daily_sl_count": int(state["daily_sl_count"]),
        "max_consecutive_sl": int(ctx.settings.max_consecutive_sl),
        "pending_approvals": len(leads),
        "trade_size_sol": float(state["trade_size_sol"]),
        "live_locks_ok": live_locks_ok,
        "live_engine_available": ctx.live_engine_available,
        "bot_running": ctx.orchestrator is not None,
        "strategy_profile": ctx.settings.strategy_profile,
        "helius_connected": bool(ctx.settings.helius_api_key.strip()),
        "telegram_connected": bool(
            ctx.settings.telegram_bot_token.strip() and ctx.settings.telegram_chat_id.strip()
        ),
        "telegram_bot_ready": bool(ctx.settings.telegram_bot_token.strip()),
        "tp1_trigger_pct": ctx.settings.tp1_trigger_pct,
        "initial_stop_loss_pct": ctx.settings.initial_stop_loss_pct,
        "disable_hard_stop_loss": ctx.settings.disable_hard_stop_loss,
        "only_close_positive": ctx.settings.only_close_positive,
        "smart_be_arm_pct": ctx.settings.smart_be_arm_pct,
        "smart_be_floor_pct": ctx.settings.smart_be_floor_pct,
        "wick_harvest_arm_pct": ctx.settings.wick_harvest_arm_pct,
        "wick_pullback_exit_pct": ctx.settings.wick_pullback_exit_pct,
        "search_interval_seconds": ctx.settings.search_interval_seconds,
        "exit_poll_interval_seconds": ctx.settings.exit_poll_interval_seconds,
        "use_llm_judge": ctx.settings.use_llm_judge,
        "tutorial_config": {
            "tutorial_initial_bankroll_sol": ctx.settings.tutorial_initial_bankroll_sol,
            "trade_size_sol": float(state["trade_size_sol"]),
            "max_concurrent_trades": int(state["max_concurrent_trades"]),
            "sim_dex_fee_pct": ctx.settings.sim_dex_fee_pct,
            "sim_priority_fee_sol": ctx.settings.sim_priority_fee_sol,
            "sim_jito_tip_sol": ctx.settings.sim_jito_tip_sol,
            "sim_buy_slippage_pct": ctx.settings.sim_buy_slippage_pct,
            "sim_tp_slippage_pct": ctx.settings.sim_tp_slippage_pct,
            "sim_sl_slippage_pct": ctx.settings.sim_sl_slippage_pct,
            "semi_auto_mode": bool(state["semi_auto_mode"]),
            "strategy_profile": ctx.settings.strategy_profile,
        },
        "server_time": utc_now_iso(),
    }


@api.get("/radar")
async def get_radar(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    leads = await ctx.db.list_recent_leads(minutes=180, limit=250)
    # Always include active position leads even if older than 3h
    active_ids = {int(l["id"]) for l in leads}
    for stage in ("OPEN", "RISK_FREE_RUNNER", "PENDING_APPROVAL"):
        for l in await ctx.db.list_leads(stage=stage, limit=50):
            if int(l["id"]) not in active_ids:
                leads.append(l)
                active_ids.add(int(l["id"]))

    verdicts = await ctx.db.get_latest_verdicts([int(l["id"]) for l in leads])
    images = await ctx.resolve_token_images([str(l["mint_address"]) for l in leads[:60]])
    lanes: dict[str, list[dict[str, Any]]] = {k: [] for k in RADAR_LANES}
    for lead in leads:
        stage = str(lead["current_stage"])
        lane = next((k for k, stages in RADAR_LANES.items() if stage in stages), None)
        if lane is None:
            continue
        lanes[lane].append(
            {
                "id": int(lead["id"]),
                "mint_address": lead["mint_address"],
                "symbol": lead["symbol"],
                "name": lead["name"],
                "image_url": images.get(str(lead["mint_address"])),
                "stage": stage,
                "market_cap": lead.get("market_cap"),
                "liquidity_usd": lead.get("liquidity_usd"),
                "main_pool_share": lead.get("main_pool_share"),
                "holders_count": lead.get("holders_count"),
                "price_usd": lead.get("price_usd"),
                "updated_at": lead.get("updated_at"),
                "verdict": _verdict_view(verdicts.get(int(lead["id"]))),
                "approval_expires_at": _pending_expiry(ctx, lead),
            }
        )
    for k in lanes:
        lanes[k] = lanes[k][:40]
    return {"lanes": lanes, "server_time": utc_now_iso()}


@api.get("/positions")
async def get_positions(
    mode: Literal["TUTORIAL", "LIVE"] | None = None,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    active_mode = mode or str(state["trading_mode"])
    sol_usd = await ctx.sol_usd()
    open_trades = await ctx.db.get_open_trades(mode=active_mode)
    closed = await ctx.db.list_trades(mode=active_mode, status="CLOSED", limit=60)
    trade_images, live_prices = await asyncio.gather(
        ctx.resolve_token_images([str(t["mint_address"]) for t in open_trades + closed]),
        ctx.fetch_live_prices_batch([str(t["mint_address"]) for t in open_trades]),
    )
    tp_target_pct = float(ctx.settings.tp1_trigger_pct or 18.0)

    def view(t: dict[str, Any], events: list[dict[str, Any]], is_open_trade: bool) -> dict[str, Any]:
        rt = ctx.exit_manager.runtime_snapshot(int(t["id"])) if ctx.exit_manager else None
        invested_sol = float(t["invested_sol"])
        realized_sol = float(t["realized_sol"])
        tokens_rem = float(t["tokens_remaining"])
        entry_px = float(t.get("entry_price_usd") or 0.0)
        eff_px = float(t.get("effective_entry_price_usd") or entry_px)
        cur_px = (
            live_prices.get(str(t["mint_address"]))
            if is_open_trade
            else None
        ) or float(t.get("current_price_usd") or entry_px)

        if is_open_trade and cur_px > 0 and sol_usd > 0:
            rem_val_sol = (tokens_rem * cur_px) / sol_usd
            live_pnl_sol = round((realized_sol + rem_val_sol) - invested_sol, 6)
            live_pnl_pct = round((live_pnl_sol / invested_sol) * 100.0, 2) if invested_sol > 0 else 0.0
            live_val_sol = round(realized_sol + rem_val_sol, 6)
            spot_move_pct = round(((cur_px - eff_px) / eff_px) * 100.0, 2) if eff_px > 0 else 0.0
        else:
            live_pnl_sol = float(t["pnl_sol"])
            live_pnl_pct = float(t["pnl_pct"])
            live_val_sol = round(realized_sol, 6)
            spot_move_pct = live_pnl_pct

        tp_progress_pct = (
            min(1000.0, max(0.0, round((live_pnl_pct / tp_target_pct) * 100.0, 1)))
            if tp_target_pct > 0 and live_pnl_pct > 0
            else 0.0
        )
        return {
            **{k: t.get(k) for k in (
                "id", "lead_id", "mint_address", "symbol", "mode", "status", "invested_sol",
                "invested_usd", "entry_price_usd", "effective_entry_price_usd", "tokens_initial",
                "tokens_remaining", "fees_paid_sol", "slippage_cost_sol", "realized_sol",
                "highest_price_seen", "peak_volume_5m",
                "exit_reason", "opened_at", "closed_at",
            )},
            "current_price_usd": cur_px,
            "pnl_sol": live_pnl_sol,
            "pnl_usd": round(live_pnl_sol * sol_usd, 2),
            "pnl_pct": live_pnl_pct,
            "live_value_sol": live_val_sol,
            "live_value_usd": round(live_val_sol * sol_usd, 2),
            "spot_move_pct": spot_move_pct,
            "tp_target_pct": tp_target_pct,
            "tp_progress_pct": tp_progress_pct,
            "image_url": trade_images.get(str(t["mint_address"])),
            "capital_recovered": bool(t.get("capital_recovered")),
            "pnl_usd_live": round(live_pnl_sol * sol_usd, 2),
            "flow": rt,
            "events": [
                {k: e.get(k) for k in (
                    "id", "event_type", "sol_amount", "tokens_amount", "price_usd",
                    "effective_price_usd", "fee_sol", "slippage_sol", "tx_signature", "created_at",
                )}
                for e in events
            ],
        }

    open_views = [view(t, await ctx.db.get_trade_events(int(t["id"])), True) for t in open_trades]
    closed_views = [view(t, await ctx.db.get_trade_events(int(t["id"])), False) for t in closed]

    auditor = SundayAuditor(db=ctx.db, settings=ctx.settings)
    try:
        report = await auditor.generate_weekly_report(days=7, mode=active_mode)
    finally:
        await auditor.aclose()

    wins = sum(1 for t in closed if float(t["pnl_sol"]) > 0)
    return {
        "mode": active_mode,
        "sol_usd": sol_usd,
        "open": open_views,
        "closed": closed_views,
        "totals": {
            "closed_count": len(closed),
            "wins": wins,
            "win_rate_pct": round(wins / len(closed) * 100, 1) if closed else 0.0,
            "realized_pnl_sol": round(sum(float(t["pnl_sol"]) for t in closed), 9),
            "fees_sol": round(sum(float(t["fees_paid_sol"]) for t in closed + open_trades), 9),
            "slippage_sol": round(sum(float(t["slippage_cost_sol"]) for t in closed + open_trades), 9),
        },
        "calibration": {
            "buckets": [asdict(b) for b in report.buy_buckets],
            "pass_audit": asdict(report.pass_audit),
            "period_days": report.period_days,
        },
    }


@api.get("/rejected")
async def get_rejected(
    limit: int = 50,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    leads = await ctx.db.list_leads(stage="REJECTED", limit=max(1, min(limit, 200)))
    verdicts = await ctx.db.get_latest_verdicts([int(l["id"]) for l in leads])
    items = []
    for l in leads:
        v = verdicts.get(int(l["id"]))
        items.append(
            {
                "id": int(l["id"]),
                "mint_address": l["mint_address"],
                "symbol": l["symbol"],
                "market_cap": l.get("market_cap"),
                "liquidity_usd": l.get("liquidity_usd"),
                "dev_wallet": l.get("dev_wallet"),
                "kill_reason": l.get("kill_reason"),
                "phase": death_phase(l.get("kill_reason")),
                "verdict": _verdict_view(v),
                "updated_at": l.get("updated_at"),
            }
        )
    return {"items": items}


@api.get("/blacklist")
async def get_blacklist(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    return {"items": await ctx.db.list_blacklisted_devs()}


@api.post("/blacklist")
async def add_blacklist(body: BlacklistBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    row = await ctx.db.blacklist_dev(body.dev_wallet.strip(), body.reason.strip() or "MANUAL", body.mint_address)
    return {"ok": True, "item": row}


@api.delete("/blacklist/{wallet}")
async def remove_blacklist(wallet: str, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    if not await ctx.db.unblacklist_dev(wallet):
        raise HTTPException(status_code=404, detail="WALLET_NOT_BLACKLISTED")
    return {"ok": True}


@api.get("/smart-wallets")
async def get_smart_wallets(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    return {"items": _read_smart_wallets(ctx.settings.resolved_smart_wallets_path)}


@api.get("/smart-wallets/{address}")
async def get_smart_wallet_detail(
    address: str,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    addr = address.strip()
    wallets = _read_smart_wallets(ctx.settings.resolved_smart_wallets_path)
    wallet = next((w for w in wallets if w["address"] == addr), None)
    if wallet is None:
        raise HTTPException(status_code=404, detail="WALLET_NOT_TRACKED")
    live_rpc, sol_usd = await asyncio.gather(
        ctx.fetch_wallet_live_rpc(addr),
        ctx.sol_usd(),
    )
    tw = wallet.get("twitter_username")
    twitch = wallet.get("twitch")
    links = {
        "solscan": f"https://solscan.io/account/{addr}",
        "gmgn": f"https://gmgn.ai/sol/address/{addr}",
        "kolscan": f"https://kolscan.io/account/{addr}",
        "cielo": f"https://app.cielo.finance/profile/{addr}",
        "arkham": f"https://intel.arkm.com/explorer/address/{addr}",
        "twitter": f"https://x.com/{tw}" if tw else None,
        "twitch": f"https://twitch.tv/{twitch}" if twitch else None,
    }
    return {
        "wallet": wallet,
        "live": live_rpc,
        "sol_usd": sol_usd,
        "links": links,
    }


@api.post("/smart-wallets")
async def add_smart_wallet(body: SmartWalletBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    path = ctx.settings.resolved_smart_wallets_path
    wallets = _read_smart_wallets(path)
    address = body.address.strip()
    if any(w["address"] == address for w in wallets):
        raise HTTPException(status_code=409, detail="WALLET_ALREADY_TRACKED")
    wallets.append({"address": address, "label": body.label.strip(), "added_at": utc_now_iso()})
    _write_smart_wallets(path, wallets)
    return {"ok": True, "items": wallets}


@api.delete("/smart-wallets/{address}")
async def remove_smart_wallet(address: str, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    path = ctx.settings.resolved_smart_wallets_path
    wallets = _read_smart_wallets(path)
    remaining = [w for w in wallets if w["address"] != address]
    if len(remaining) == len(wallets):
        raise HTTPException(status_code=404, detail="WALLET_NOT_TRACKED")
    _write_smart_wallets(path, remaining)
    return {"ok": True, "items": remaining}


@api.get("/x-feed")
async def get_x_feed(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    open_trades = await ctx.db.get_open_trades(mode=str(state["trading_mode"]))
    flow_by_mint: dict[str, dict[str, Any]] = {}
    symbol_by_mint: dict[str, str] = {}
    status_by_mint: dict[str, str] = {}
    for t in open_trades:
        mint = str(t["mint_address"])
        symbol_by_mint[mint] = str(t["symbol"])
        status_by_mint[mint] = str(t["status"])
        rt = ctx.exit_manager.runtime_snapshot(int(t["id"])) if ctx.exit_manager else None
        flow_by_mint[mint] = {
            **(rt or {}),
            "peak_volume_5m": float(t["peak_volume_5m"] or 0.0),
        }

    mentions = await ctx.db.get_x_mentions_for_mints([], max_age_hours=24, limit=400)
    groups: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()
    for m in mentions:
        mint = str(m["mint_address"])
        dedup_key = (mint, str(m["author_handle"]), str(m["text"])[:80])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        g = groups.setdefault(
            mint,
            {
                "mint_address": mint,
                "symbol": symbol_by_mint.get(mint) or str(m.get("query", "")).split(" ")[0].lstrip("$"),
                "status": status_by_mint.get(mint),
                "flow": flow_by_mint.get(mint),
                "mentions": [],
                "notable_count": 0,
            },
        )
        g["mentions"].append(
            {
                "author_handle": m["author_handle"],
                "author_followers": int(m["author_followers"] or 0),
                "is_verified": bool(m["is_verified"]),
                "is_notable_figure": bool(m["is_notable_figure"]),
                "notable_tag": m.get("notable_tag"),
                "text": m["text"],
                "url": m.get("url"),
                "fetched_at": m["fetched_at"],
            }
        )
        if m["is_notable_figure"]:
            g["notable_count"] += 1

    ordered = sorted(
        groups.values(),
        key=lambda g: (g["status"] is not None, g["notable_count"], len(g["mentions"])),
        reverse=True,
    )
    return {
        "enabled": bool(ctx.settings.enable_x_context_on_runner),
        "groups": ordered,
        "active_without_mentions": [
            {"mint_address": mint, "symbol": symbol_by_mint[mint], "status": status_by_mint[mint], "flow": flow_by_mint[mint]}
            for mint in symbol_by_mint
            if mint not in groups
        ],
    }


@api.get("/token/{mint_address}/ohlcv")
async def get_token_ohlcv(
    mint_address: str,
    tf: Literal["1m", "5m", "15m"] = "1m",
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Return live OHLCV Japanese candlestick bars (1m / 5m / 15m) for a token."""
    mint = mint_address.strip()
    async with ctx.db.connect() as conn:
        lead_cur = await conn.execute(
            "SELECT pair_address, price_usd FROM leads WHERE mint_address = ? ORDER BY id DESC LIMIT 1",
            (mint,),
        )
        lead_row = await lead_cur.fetchone()
    pair_address = str(lead_row["pair_address"]) if lead_row and lead_row["pair_address"] else None
    live = await ctx.fetch_live_token_market(mint, pair_address=pair_address)
    resolved_pair = (live or {}).get("pair_address") or pair_address
    live_price = (live or {}).get("price_usd") or (float(lead_row["price_usd"]) if lead_row and lead_row["price_usd"] else None)
    candles = await ctx.fetch_token_ohlcv(resolved_pair, timeframe=tf, live_price=live_price)
    return {
        "mint_address": mint,
        "pair_address": resolved_pair,
        "tf": tf,
        "candles": candles,
        "live_price": live_price,
    }


@api.get("/token/{mint_address}")
async def get_token_detail(
    mint_address: str,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Return full real-time market, chart history, Judge dossier, trade, & X data for a token."""
    mint = mint_address.strip()
    async with ctx.db.connect() as conn:
        lead_cur = await conn.execute(
            "SELECT * FROM leads WHERE mint_address = ? ORDER BY id DESC LIMIT 1",
            (mint,),
        )
        lead_row = await lead_cur.fetchone()
        lead = dict(lead_row) if lead_row is not None else None

        trade_cur = await conn.execute(
            "SELECT * FROM trades WHERE mint_address = ? ORDER BY id DESC LIMIT 1",
            (mint,),
        )
        trade_row = await trade_cur.fetchone()
        trade = dict(trade_row) if trade_row is not None else None

    if lead is None and trade is None:
        raise HTTPException(status_code=404, detail="TOKEN_NOT_FOUND")

    pair_addr = lead.get("pair_address") if lead else None
    live, (smart_wallets_matched, security_audit) = await asyncio.gather(
        ctx.fetch_live_token_market(mint, pair_address=pair_addr),
        ctx.fetch_token_rugcheck_bundle(mint, pair_address=pair_addr),
    )

    verdict_raw: dict[str, Any] | None = None
    dossier: dict[str, Any] | None = None
    if lead is not None:
        verdicts = await ctx.db.get_judge_verdicts_for_lead(int(lead["id"]))
        if verdicts:
            verdict_raw = verdicts[-1]
            try:
                dossier = json.loads(str(verdict_raw.get("dossier_json") or "{}"))
            except ValueError:
                dossier = None

    dev_wallet = lead.get("dev_wallet") if lead else None
    dev_blacklisted = bool(await ctx.db.is_dev_blacklisted(dev_wallet)) if dev_wallet else False

    events: list[dict[str, Any]] = []
    if trade is not None:
        events = await ctx.db.get_trade_events(int(trade["id"]))

    x_mentions = await ctx.db.get_x_mentions_for_mints([mint], max_age_hours=24, limit=20)
    history = list(ctx._token_price_history.get(mint, []))
    if not history and lead and lead.get("price_usd"):
        history = [
            {
                "ts": lead.get("updated_at") or utc_now_iso(),
                "price_usd": float(lead["price_usd"]),
                "market_cap": lead.get("market_cap"),
                "liquidity_usd": lead.get("liquidity_usd"),
                "vol_5m": 0.0,
            }
        ]

    return {
        "mint_address": mint,
        "symbol": (lead or trade or {}).get("symbol", "UNKNOWN"),
        "name": (lead or {}).get("name") or (trade or {}).get("symbol", "UNKNOWN"),
        "lead": {
            "id": int(lead["id"]),
            "stage": lead["current_stage"],
            "market_cap": lead.get("market_cap"),
            "liquidity_usd": lead.get("liquidity_usd"),
            "main_pool_share": lead.get("main_pool_share"),
            "holders_count": lead.get("holders_count"),
            "dev_wallet": dev_wallet,
            "dev_blacklisted": dev_blacklisted,
            "price_usd": lead.get("price_usd"),
            "kill_reason": lead.get("kill_reason"),
            "approval_expires_at": _pending_expiry(ctx, lead),
            "created_at": lead.get("created_at"),
            "updated_at": lead.get("updated_at"),
        }
        if lead
        else None,
        "live": live,
        "security_audit": security_audit,
        "smart_wallets_matched": smart_wallets_matched,
        "price_history": history,
        "verdict": _verdict_view(verdict_raw),
        "dossier": dossier,
        "trade": {
            **{
                k: trade.get(k)
                for k in (
                    "id", "mode", "status", "invested_sol", "invested_usd",
                    "entry_price_usd", "effective_entry_price_usd", "tokens_initial",
                    "tokens_remaining", "fees_paid_sol", "slippage_cost_sol",
                    "realized_sol", "current_price_usd", "highest_price_seen",
                    "peak_volume_5m", "pnl_sol", "pnl_usd", "pnl_pct",
                    "exit_reason", "opened_at", "closed_at",
                )
            },
            "capital_recovered": bool(trade.get("capital_recovered")),
            "events": events,
        }
        if trade
        else None,
        "x_mentions": x_mentions,
        "server_time": utc_now_iso(),
    }


# ----------------------------- Controls -------------------------------
@api.post("/control/pause")
async def control_pause(body: PauseBody | None = None, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    target = (not bool(state["is_paused"])) if body is None or body.paused is None else body.paused
    updates: dict[str, Any] = {"is_paused": 1 if target else 0}
    breaker_reset = False
    if not target:
        # Respect explicit manual Resume: clear tripped daily circuit-breaker latch so bot stays running
        daily_sl = int(state.get("daily_sl_count") or 0)
        daily_pnl = float(state.get("daily_pnl_sol") or 0.0)
        bankroll = float(state.get("tutorial_bankroll_sol") or 1.0)
        loss_sol = max(0.0, -daily_pnl)
        loss_pct = (loss_sol / bankroll) * 100.0 if bankroll > 0 else 0.0
        if (
            daily_sl >= ctx.settings.max_consecutive_sl
            or loss_pct >= ctx.settings.max_daily_loss_pct
            or loss_sol >= ctx.settings.max_daily_loss_sol
        ):
            updates["daily_sl_count"] = 0
            updates["daily_pnl_sol"] = 0.0
            breaker_reset = True
    state = await ctx.db.update_bot_state(**updates)
    return {"ok": True, "is_paused": bool(state["is_paused"]), "breaker_reset": breaker_reset}


@api.post("/control/panic-close-all")
async def control_panic(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.update_bot_state(is_paused=1)
    closed: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    async with ctx.trade_lock:
        for trade in await ctx.db.get_open_trades(mode=str(state["trading_mode"])):
            try:
                t = await _close_trade_now(ctx, trade, "MANUAL_PANIC")
                closed.append({"id": t["id"], "symbol": t["symbol"], "pnl_sol": t["pnl_sol"]})
            except ExecutionError as exc:
                errors.append({"id": trade["id"], "symbol": trade["symbol"], "error": str(exc)})
    return {"ok": not errors, "closed": closed, "errors": errors, "is_paused": True}


@api.post("/control/exit-trade/{trade_id}")
async def control_exit_trade(trade_id: int, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    async with ctx.trade_lock:
        trade = await ctx.db.get_trade(trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="TRADE_NOT_FOUND")
        if trade["status"] == "CLOSED":
            raise HTTPException(status_code=409, detail="TRADE_ALREADY_CLOSED")
        try:
            closed = await _close_trade_now(ctx, trade, "MANUAL_EXIT")
        except ExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "trade": {"id": closed["id"], "pnl_sol": closed["pnl_sol"], "pnl_usd": closed["pnl_usd"]}}


@api.post("/control/approve/{lead_id}")
async def control_approve(lead_id: int, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    async with ctx.trade_lock:
        lead = await ctx.db.get_lead(lead_id)
        if lead is None:
            raise HTTPException(status_code=404, detail="LEAD_NOT_FOUND")
        if lead["current_stage"] != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail=f"LEAD_NOT_PENDING ({lead['current_stage']})")
        expires = _pending_expiry(ctx, lead)
        if expires and datetime.now(timezone.utc) >= datetime.fromisoformat(expires):
            await ctx.db.update_lead(lead_id, current_stage="REJECTED", kill_reason="APPROVAL_TIMEOUT")
            raise HTTPException(status_code=410, detail="APPROVAL_TIMEOUT")

        state = await ctx.db.get_bot_state()
        if await ctx.db.count_open_trades(mode=str(state["trading_mode"])) >= int(state["max_concurrent_trades"]):
            raise HTTPException(status_code=409, detail="MAX_CONCURRENT_TRADES")

        try:
            if ctx.orchestrator is not None and lead_id in ctx.orchestrator.pending_approvals:
                receipt = await ctx.orchestrator.approve_pending_lead(lead_id)
            else:
                receipt = await ctx.engine.execute_buy(lead, float(state["trade_size_sol"]), sol_usd_price=await ctx.sol_usd())
                if ctx.exit_manager is not None:
                    ctx.exit_manager.register_recovered_trades([receipt.trade])
        except ExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "trade_id": receipt.trade["id"]}


@api.post("/control/reject/{lead_id}")
async def control_reject(lead_id: int, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    lead = await ctx.db.get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="LEAD_NOT_FOUND")
    if lead["current_stage"] != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"LEAD_NOT_PENDING ({lead['current_stage']})")
    if ctx.orchestrator is not None:
        await ctx.orchestrator.reject_pending_lead(lead_id, "MANUAL_REJECT")
    else:
        await ctx.db.update_lead(lead_id, current_stage="REJECTED", kill_reason="MANUAL_REJECT")
    return {"ok": True}


# ----------------------------- Tutorial -------------------------------
@api.post("/tutorial/config")
async def tutorial_config(body: TutorialConfigBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    data = body.model_dump(exclude_none=True)

    bankroll_ref = data.get("tutorial_initial_bankroll_sol", float(state["tutorial_bankroll_sol"]))
    if "trade_size_sol" in data and data["trade_size_sol"] > bankroll_ref:
        raise HTTPException(status_code=422, detail="TRADE_SIZE_EXCEEDS_BANKROLL")

    overrides: dict[str, Any] = {k: v for k, v in data.items() if k in TUTORIAL_OVERRIDE_FIELDS}
    if "strategy_profile" in data:
        prof = apply_strategy_profile(ctx.settings, str(data["strategy_profile"]))
        overrides["strategy_profile"] = prof
    if "max_concurrent_trades" in data:
        mct = max(1, min(20, int(data["max_concurrent_trades"])))
        ctx.settings.max_concurrent_trades = mct
        overrides["max_concurrent_trades"] = mct
    if overrides:
        await ctx.db.set_runtime_config(overrides)
        for k, v in overrides.items():
            if k in TUTORIAL_OVERRIDE_FIELDS:
                setattr(ctx.settings, k, float(v))

    bot_updates: dict[str, Any] = {}
    if "tutorial_initial_bankroll_sol" in data:
        bot_updates["tutorial_bankroll_sol"] = float(data["tutorial_initial_bankroll_sol"])
    if "trade_size_sol" in data:
        bot_updates["trade_size_sol"] = float(data["trade_size_sol"])
    if "max_concurrent_trades" in data:
        bot_updates["max_concurrent_trades"] = max(1, min(20, int(data["max_concurrent_trades"])))
    if "semi_auto_mode" in data:
        bot_updates["semi_auto_mode"] = 1 if data["semi_auto_mode"] else 0
    if bot_updates:
        await ctx.db.update_bot_state(**bot_updates)
    return {"ok": True, "applied": data}


@api.post("/tutorial/reset")
async def tutorial_reset(body: TutorialResetBody | None = None, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    initial = (body.initial_sol if body and body.initial_sol else None) or float(ctx.settings.tutorial_initial_bankroll_sol)
    async with ctx.trade_lock:
        open_tut = await ctx.db.get_open_trades(mode="TUTORIAL")
        state = await ctx.db.reset_tutorial_account(initial)
        if ctx.exit_manager is not None:
            for t in open_tut:
                ctx.exit_manager.forget_trade(int(t["id"]))
        for t in open_tut:
            if t.get("lead_id") is not None:
                await ctx.db.update_lead(int(t["lead_id"]), current_stage="CLOSED", kill_reason="TUTORIAL_RESET")
    await ctx.db.set_runtime_config({"tutorial_initial_bankroll_sol": initial})
    ctx.settings.tutorial_initial_bankroll_sol = initial
    return {"ok": True, "tutorial_available_sol": state["tutorial_available_sol"]}


@api.post("/mode/switch")
async def mode_switch(body: ModeSwitchBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    current = str(state["trading_mode"])
    if body.mode == current:
        return {"ok": True, "mode": current}

    if body.mode == "LIVE":
        missing = []
        if not ctx.settings.enable_live_execution:
            missing.append("ENABLE_LIVE_EXECUTION=true")
        if not ctx.settings.effective_solana_private_key:
            missing.append("SOLANA_PRIVATE_KEY (or LIVE_WALLET_PRIVATE_KEY)")
        if missing:
            raise HTTPException(
                status_code=403,
                detail=(
                    "LIVE bloqueado por doble candado de seguridad. Configura en .env: "
                    + ", ".join(missing)
                    + " y reinicia el servidor."
                ),
            )
        if not ctx.live_engine_available:
            raise HTTPException(
                status_code=501,
                detail="LIVE_ENGINE_NOT_AVAILABLE: el motor Jupiter + Jito aún no está disponible.",
            )

    if await ctx.db.count_open_trades(mode=current) > 0:
        raise HTTPException(status_code=409, detail="CLOSE_OPEN_TRADES_BEFORE_SWITCHING_MODE")

    ctx.settings.trading_mode = TradingMode(body.mode)
    if body.mode == "LIVE":
        ctx.engine = LiveEngine(
            db=ctx.db, price_oracle=ctx.price_oracle, settings=ctx.settings
        )
    else:
        ctx.engine = TutorialEngine(
            db=ctx.db, price_oracle=ctx.price_oracle, settings=ctx.settings
        )
    if ctx.orchestrator is not None and hasattr(ctx.orchestrator, "set_active_engine"):
        ctx.orchestrator.set_active_engine(ctx.engine)

    await ctx.db.update_bot_state(trading_mode=body.mode, is_paused=1)
    return {"ok": True, "mode": body.mode, "is_paused": True}


# ----------------------------- Strategies -----------------------------
@api.get("/strategies")
async def get_strategies(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    state = await ctx.db.get_bot_state()
    store = load_strategies()
    return {
        "active_id": store.get("active_id", "PREDETERMINADA"),
        "strategies": store.get("strategies", []),
        "current_params": snapshot_current_params(ctx.settings, state),
    }


@api.post("/strategies/apply")
async def apply_strategy_route(body: StrategyApplyBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    store = load_strategies()
    target = next((s for s in store["strategies"] if s.get("id") == body.id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="STRATEGY_NOT_FOUND")
    store["active_id"] = target["id"]
    save_strategies_store(store)
    params = dict(target.get("params") or {})
    bot_upd = apply_params_to_settings(ctx.settings, params)
    if bot_upd:
        await ctx.db.update_bot_state(**bot_upd)
    await ctx.db.set_runtime_config(
        {
            "active_strategy_id": target["id"],
            "active_strategy_params": json.dumps(params),
            "strategy_profile": ctx.settings.strategy_profile,
        }
    )
    state = await ctx.db.get_bot_state()
    return {
        "ok": True,
        "active_id": target["id"],
        "strategy": target,
        "current_params": snapshot_current_params(ctx.settings, state),
    }


@api.post("/strategies/save")
async def save_strategy_route(body: StrategySaveBody, ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    entry, store = upsert_strategy(
        name=body.name,
        params=body.params,
        description=body.description,
        strategy_id=body.id,
    )
    params = dict(entry.get("params") or {})
    bot_upd = apply_params_to_settings(ctx.settings, params)
    if bot_upd:
        await ctx.db.update_bot_state(**bot_upd)
    await ctx.db.set_runtime_config(
        {
            "active_strategy_id": entry["id"],
            "active_strategy_params": json.dumps(params),
            "strategy_profile": ctx.settings.strategy_profile,
        }
    )
    state = await ctx.db.get_bot_state()
    return {
        "ok": True,
        "active_id": entry["id"],
        "strategy": entry,
        "strategies": store["strategies"],
        "current_params": snapshot_current_params(ctx.settings, state),
    }


@api.delete("/strategies/{strategy_id}")
async def delete_strategy_route(strategy_id: str) -> dict[str, Any]:
    try:
        store = delete_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="STRATEGY_NOT_FOUND") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="BUILTIN_STRATEGY_PROTECTED") from exc
    return {"ok": True, "active_id": store["active_id"], "strategies": store["strategies"]}


# ---------------------- Live Wallet & Withdrawals ---------------------
@api.get("/wallet/status")
async def get_wallet_status(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    pub = get_public_address_from_settings(ctx.settings)
    sol_usd = await ctx.sol_usd()
    bal = await fetch_live_wallet_balance_sol(ctx.settings, pub) if pub else None
    return {
        "configured": bool(pub),
        "public_address": pub,
        "solscan_url": f"https://solscan.io/account/{pub}" if pub else None,
        "sol_balance": bal,
        "usd_balance": round(bal * sol_usd, 2) if bal is not None else None,
        "sol_usd": sol_usd,
        "enable_live_execution": bool(ctx.settings.enable_live_execution),
        "trading_mode": str((await ctx.db.get_bot_state())["trading_mode"]),
        "dual_lock_ready": bool(pub and ctx.settings.enable_live_execution),
        "max_live_trade_sol": ctx.settings.max_live_trade_sol,
        "min_live_gas_reserve_sol": ctx.settings.min_live_gas_reserve_sol,
        "withdrawal_wallets": list_withdrawal_wallets(),
    }


@api.post("/wallet/configure")
async def configure_wallet_route(
    body: WalletConfigureBody,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        res = configure_live_wallet_in_env(
            ctx.settings,
            private_key_raw=body.private_key,
            enable_live_execution=body.enable_live_execution,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"INVALID_SOLANA_KEY: {exc}") from exc
    return {"ok": True, **res}


@api.post("/wallet/generate")
async def generate_wallet_route(ctx: DashboardContext = Depends(get_ctx)) -> dict[str, Any]:
    res = generate_dedicated_trading_wallet(ctx.settings)
    return {"ok": True, **res}


@api.get("/wallet/withdraw-whitelist")
async def get_withdrawal_whitelist() -> dict[str, Any]:
    return {"items": list_withdrawal_wallets()}


@api.post("/wallet/withdraw-whitelist")
async def add_withdrawal_whitelist_route(body: WithdrawalWhitelistBody) -> dict[str, Any]:
    try:
        items = add_withdrawal_wallet(body.address, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "items": items}


@api.delete("/wallet/withdraw-whitelist/{address}")
async def remove_withdrawal_whitelist_route(address: str) -> dict[str, Any]:
    try:
        items = remove_withdrawal_wallet(address)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="WALLET_NOT_FOUND") from exc
    return {"ok": True, "items": items}


@api.post("/wallet/withdraw")
async def withdraw_sol_route(
    body: WithdrawSolBody,
    ctx: DashboardContext = Depends(get_ctx),
) -> dict[str, Any]:
    async with ctx.trade_lock:
        try:
            res = await withdraw_sol_to_whitelisted(
                ctx.settings,
                body.destination,
                body.amount_sol,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="DESTINATION_NOT_WHITELISTED") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    notifier = getattr(ctx.orchestrator, "notifier", None) if ctx.orchestrator else None
    if notifier is not None:
        await notifier.send_message(
            f"💸 <b>RETIRO SOL ENVIADO</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Monto: <b>{res['amount_sol']:.4f} SOL</b>\n"
            f"🎯 Destino: <b>{res['destination_label']}</b>\n"
            f"🔗 <a href=\"{res['solscan_url']}\">Ver TX en Solscan</a>",
            parse_mode="HTML",
        )
    return res


# ----------------------------------------------------------------------
# App Factory
# ----------------------------------------------------------------------
def create_app(
    *,
    settings: Settings | None = None,
    db: Database | None = None,
    orchestrator: Any | None = None,
    price_oracle: Any | None = None,
    engine: ExecutionEngine | None = None,
    run_orchestrator: bool = False,
    serve_static: bool = True,
    live_engine_available: bool = True,
) -> FastAPI:
    """Build the Command Center app. ``run_orchestrator`` starts the trading bot in-process."""
    cfg = settings or get_settings()
    install_secret_redaction(cfg)
    database = db or (orchestrator.db if orchestrator is not None else Database(settings=cfg))

    if run_orchestrator and orchestrator is None:
        from src.orchestrator import TradingOrchestrator

        orchestrator = TradingOrchestrator(db=database, settings=cfg)

    oracle = price_oracle or (orchestrator.price_oracle if orchestrator is not None else SolPriceOracle(settings=cfg))
    default_engine = (
        LiveEngine(db=database, price_oracle=oracle, settings=cfg)
        if cfg.trading_mode == TradingMode.LIVE
        else TutorialEngine(db=database, price_oracle=oracle, settings=cfg)
    )
    exec_engine = engine or (orchestrator.engine if orchestrator is not None else default_engine)
    ctx = DashboardContext(
        settings=cfg,
        db=database,
        price_oracle=oracle,
        engine=exec_engine,
        orchestrator=orchestrator,
        live_engine_available=live_engine_available,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        await ctx.apply_runtime_overrides()
        bot_task: asyncio.Task[None] | None = None
        if run_orchestrator and orchestrator is not None:
            bot_task = asyncio.create_task(orchestrator.run_forever(), name="meteoro_orchestrator")
        try:
            yield
        finally:
            if bot_task is not None:
                orchestrator.stop()
                await asyncio.gather(bot_task, return_exceptions=True)

    app = FastAPI(title="METEORO TRADER — Solana Judge Desk", version="1.0.0", lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", PIN_HEADER],
    )
    app.include_router(public)
    app.include_router(api)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if serve_static and DASHBOARD_DIST.exists():
        assets = DASHBOARD_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (DASHBOARD_DIST / full_path).resolve()
            if full_path and candidate.is_file() and DASHBOARD_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(DASHBOARD_DIST / "index.html")

    return app
