"""Phase 8 (EXIT): Volumetric Exit Manager with capital recovery, LP panic, stagnation, & X context."""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import Settings, get_settings
from src.database import Database, utc_now_iso
from src.execution.base import ExecutionEngine
from src.http_client import HttpRequestError, ResilientHttpClient
from src.price_oracle import SolPriceOracle
from src.search import DEXSCREENER_SOLANA_TOKENS_URL, extract_solana_pairs
from src.telegram_bot import TelegramNotifier
from src.x_radar import XRadar

logger = logging.getLogger(__name__)


def _parse_iso(ts: str | None) -> datetime:
    if not ts:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class MarketSnapshot:
    """Point-in-time DexScreener metrics used by the volumetric exit rules."""

    mint_address: str
    price_usd: float
    liquidity_usd: float
    volume_5m_usd: float
    buys_5m: int
    sells_5m: int
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class TradeRuntimeState:
    """In-memory sliding-window state tracked per active position."""

    entry_liquidity_usd: float
    liquidity_history: deque[tuple[datetime, float]] = field(default_factory=deque)
    price_window: deque[tuple[datetime, float]] = field(default_factory=deque)
    consecutive_sell_ratio_cycles: int = 0
    runner_entered_at: datetime | None = None
    last_volume_5m_usd: float | None = None
    last_buys_5m: int | None = None
    last_sells_5m: int | None = None
    last_liquidity_usd: float | None = None


@dataclass(slots=True)
class ExitActionResult:
    """Structured result emitted by ``VolumetricExitManager.evaluate_trade``."""

    trade_id: int
    mint_address: str
    symbol: str
    action: str  # HOLD | TP1 | RISK_FREE_RUNNER | CLOSED
    reason: str | None
    new_status: str
    realized_sol: float
    pnl_sol: float
    pnl_usd: float
    dev_blacklisted: bool = False
    x_trailing_widened: bool = False


class VolumetricExitManager:
    """Monitors active trades and enforces the 5 volumetric & risk exit rules."""

    def __init__(
        self,
        db: Database,
        engine: ExecutionEngine,
        *,
        http_client: ResilientHttpClient | None = None,
        price_oracle: SolPriceOracle | None = None,
        x_radar: XRadar | None = None,
        notifier: TelegramNotifier | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.engine = engine
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or ResilientHttpClient(timeout=8.0, max_retries=2)
        self.price_oracle = price_oracle
        self.x_radar = x_radar
        self.notifier = notifier
        self._runtime: dict[int, TradeRuntimeState] = {}

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    def forget_trade(self, trade_id: int) -> None:
        """Drop in-memory runtime tracking for a trade closed outside the exit loop."""
        self._runtime.pop(int(trade_id), None)

    def runtime_snapshot(self, trade_id: int) -> dict[str, Any] | None:
        """Return last observed volume/flow metrics for dashboard health indicators."""
        rt = self._runtime.get(int(trade_id))
        if rt is None:
            return None
        return {
            "volume_5m_usd": rt.last_volume_5m_usd,
            "buys_5m": rt.last_buys_5m,
            "sells_5m": rt.last_sells_5m,
            "liquidity_usd": rt.last_liquidity_usd,
            "entry_liquidity_usd": rt.entry_liquidity_usd,
        }

    def register_recovered_trades(self, trades: list[dict[str, Any]]) -> int:
        """Seed runtime sliding windows for trades recovered from SQLite after restart."""
        registered = 0
        for trade in trades:
            trade_id = int(trade["id"])
            if trade_id in self._runtime:
                continue
            opened_at = _parse_iso(trade.get("opened_at"))
            entry_liq = float(trade.get("lead_liquidity_usd") or 0.0)
            curr_price = float(trade.get("current_price_usd") or trade.get("entry_price_usd") or 0.0)
            runtime = TradeRuntimeState(entry_liquidity_usd=entry_liq)
            if entry_liq > 0:
                runtime.liquidity_history.append((opened_at, entry_liq))
            if curr_price > 0:
                runtime.price_window.append((opened_at, curr_price))
            if trade.get("status") == "RISK_FREE_RUNNER":
                runtime.runner_entered_at = opened_at
            self._runtime[trade_id] = runtime
            registered += 1
        return registered

    def _ensure_runtime(
        self,
        trade: dict[str, Any],
        snapshot: MarketSnapshot,
    ) -> TradeRuntimeState:
        trade_id = int(trade["id"])
        runtime = self._runtime.get(trade_id)
        if runtime is None:
            opened_at = _parse_iso(trade.get("opened_at"))
            entry_liq = float(trade.get("lead_liquidity_usd") or snapshot.liquidity_usd or 0.0)
            runtime = TradeRuntimeState(entry_liquidity_usd=entry_liq)
            if entry_liq > 0:
                runtime.liquidity_history.append((opened_at, entry_liq))
            entry_px = float(trade.get("entry_price_usd") or snapshot.price_usd)
            if entry_px > 0:
                runtime.price_window.append((opened_at, entry_px))
            if trade.get("status") == "RISK_FREE_RUNNER":
                runtime.runner_entered_at = opened_at
            self._runtime[trade_id] = runtime
        elif runtime.entry_liquidity_usd <= 0 and snapshot.liquidity_usd > 0:
            runtime.entry_liquidity_usd = snapshot.liquidity_usd
        return runtime

    @staticmethod
    def _update_window(
        window: deque[tuple[datetime, float]],
        now: datetime,
        value: float,
        max_age_seconds: int,
    ) -> None:
        cutoff = now - timedelta(seconds=max_age_seconds)
        # Preserve the latest sample at or just before cutoff as the window anchor
        while len(window) >= 2 and window[0][0] < cutoff and window[1][0] <= cutoff:
            window.popleft()
        window.append((now, value))

    async def _resolve_sol_usd(self, sol_usd_price: float | None) -> float:
        if sol_usd_price is not None and sol_usd_price > 0:
            return float(sol_usd_price)
        if self.price_oracle is not None:
            return await self.price_oracle.get_sol_usd_price()
        return float(self.settings.fallback_sol_usd_price)

    async def fetch_market_snapshot(
        self,
        mint_address: str,
        pair_address: str | None = None,
    ) -> MarketSnapshot | None:
        """Fetch live price, liquidity, 5m volume, and 5m buys/sells from DexScreener."""
        url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint_address)
        try:
            payload = await self._http.get_json(url, timeout=6.0)
        except HttpRequestError as exc:
            logger.warning("Snapshot fetch failed for %s: %s", mint_address[:8], exc)
            return None

        pairs = extract_solana_pairs(payload)
        if not pairs:
            return None

        chosen: dict[str, Any] | None = None
        if pair_address:
            for pair in pairs:
                if pair.get("pairAddress") == pair_address:
                    chosen = pair
                    break
        if chosen is None:
            pairs.sort(
                key=lambda p: _safe_float((p.get("liquidity") or {}).get("usd")),
                reverse=True,
            )
            chosen = pairs[0]

        price_usd = _safe_float(chosen.get("priceUsd"))
        if price_usd <= 0:
            return None

        liquidity_usd = _safe_float((chosen.get("liquidity") or {}).get("usd"))
        volume_5m = _safe_float((chosen.get("volume") or {}).get("m5"))
        txns_m5 = (chosen.get("txns") or {}).get("m5") or {}
        buys_5m = _safe_int(txns_m5.get("buys"))
        sells_5m = _safe_int(txns_m5.get("sells"))

        return MarketSnapshot(
            mint_address=mint_address,
            price_usd=price_usd,
            liquidity_usd=liquidity_usd,
            volume_5m_usd=volume_5m,
            buys_5m=buys_5m,
            sells_5m=sells_5m,
            observed_at=datetime.now(timezone.utc),
        )

    async def _close_full(
        self,
        trade: dict[str, Any],
        *,
        reason: str,
        is_emergency: bool,
        snapshot: MarketSnapshot,
        sol_usd: float,
        increment_sl_if_loss: bool = False,
        blacklist_on_early_panic: bool = False,
    ) -> ExitActionResult:
        receipt = await self.engine.execute_sell(
            trade,
            100.0,
            reason,
            is_emergency=is_emergency,
            spot_price_usd=snapshot.price_usd,
            sol_usd_price=sol_usd,
        )
        closed_trade = receipt.trade
        pnl_sol = float(closed_trade["pnl_sol"])
        pnl_usd = float(closed_trade["pnl_usd"])

        if increment_sl_if_loss and pnl_sol < 0:
            state = await self.db.get_bot_state()
            await self.db.update_bot_state(
                daily_sl_count=int(state["daily_sl_count"]) + 1
            )

        dev_blacklisted = False
        if blacklist_on_early_panic:
            dev_wallet = trade.get("lead_dev_wallet")
            opened_at = _parse_iso(trade.get("opened_at"))
            age = snapshot.observed_at - opened_at
            max_age = timedelta(
                minutes=int(self.settings.liquidity_panic_blacklist_max_age_minutes)
            )
            if dev_wallet and age <= max_age:
                await self.db.blacklist_dev(
                    str(dev_wallet),
                    reason=f"LIQUIDITY_PANIC_{int(age.total_seconds() // 60)}m",
                    mint_address=str(trade["mint_address"]),
                )
                dev_blacklisted = True

        self._runtime.pop(int(trade["id"]), None)

        if self.notifier is not None:
            await self.notifier.notify_trade_closed(closed_trade)

        return ExitActionResult(
            trade_id=int(closed_trade["id"]),
            mint_address=str(closed_trade["mint_address"]),
            symbol=str(closed_trade["symbol"]),
            action="CLOSED",
            reason=reason,
            new_status="CLOSED",
            realized_sol=float(closed_trade["realized_sol"]),
            pnl_sol=pnl_sol,
            pnl_usd=pnl_usd,
            dev_blacklisted=dev_blacklisted,
        )

    async def evaluate_trade(
        self,
        trade: dict[str, Any],
        snapshot: MarketSnapshot,
        *,
        sol_usd_price: float | None = None,
    ) -> ExitActionResult:
        """Evaluate all 5 volumetric & risk exit rules on a single open trade."""
        fresh = await self.db.get_trade(int(trade["id"]))
        if fresh is None:
            raise LookupError(f"Trade id={trade['id']} not found.")
        if fresh["status"] == "CLOSED":
            return ExitActionResult(
                trade_id=int(fresh["id"]),
                mint_address=str(fresh["mint_address"]),
                symbol=str(fresh["symbol"]),
                action="HOLD",
                reason=fresh.get("exit_reason"),
                new_status="CLOSED",
                realized_sol=float(fresh["realized_sol"]),
                pnl_sol=float(fresh["pnl_sol"]),
                pnl_usd=float(fresh["pnl_usd"]),
            )

        sol_usd = await self._resolve_sol_usd(sol_usd_price)
        runtime = self._ensure_runtime(fresh, snapshot)
        runtime.last_volume_5m_usd = snapshot.volume_5m_usd
        runtime.last_buys_5m = snapshot.buys_5m
        runtime.last_sells_5m = snapshot.sells_5m
        runtime.last_liquidity_usd = snapshot.liquidity_usd
        now = snapshot.observed_at
        opened_at = _parse_iso(fresh.get("opened_at"))
        trade_age = now - opened_at

        new_highest = max(float(fresh["highest_price_seen"]), snapshot.price_usd)
        new_peak_vol_5m = max(float(fresh["peak_volume_5m"]), snapshot.volume_5m_usd)

        # Persist peak price and peak 5m volume before rule checks
        fresh = await self.db.update_trade(
            fresh["id"],
            current_price_usd=snapshot.price_usd,
            highest_price_seen=new_highest,
            peak_volume_5m=new_peak_vol_5m,
        )

        # Track sliding liquidity & price windows
        self._update_window(
            runtime.liquidity_history,
            now,
            snapshot.liquidity_usd,
            int(self.settings.liquidity_panic_window_seconds),
        )
        max_price_window_secs = max(
            int(self.settings.stagnation_window_minutes),
            int(self.settings.stagnation_runner_window_minutes),
        ) * 60
        self._update_window(runtime.price_window, now, snapshot.price_usd, max_price_window_secs)

        entry_price = float(fresh["entry_price_usd"])
        effective_entry = float(fresh.get("effective_entry_price_usd") or entry_price)
        price_change_pct = round(
            ((snapshot.price_usd - entry_price) / entry_price) * 100.0, 6
        )
        net_gain_pct = (
            round(((snapshot.price_usd - effective_entry) / effective_entry) * 100.0, 6)
            if effective_entry > 0
            else price_change_pct
        )
        peak_gain_pct = (
            round(((new_highest - effective_entry) / effective_entry) * 100.0, 6)
            if effective_entry > 0
            else 0.0
        )
        pullback_from_peak_pct = (
            round(((new_highest - snapshot.price_usd) / new_highest) * 100.0, 6)
            if new_highest > 0
            else 0.0
        )
        status = str(fresh["status"])
        no_sl_mode = bool(self.settings.disable_hard_stop_loss)
        positive_only = bool(self.settings.only_close_positive)
        be_floor_pct = float(self.settings.smart_be_floor_pct)

        # Estimate exact net SOL received if selling 100% right now (accounting for fees + slippage)
        def _net_positive_if_closed_now(is_emergency: bool) -> bool:
            if bool(fresh.get("capital_recovered")):
                return True
            tokens_rem = float(fresh["tokens_remaining"])
            if tokens_rem <= 0 or sol_usd <= 0:
                return float(fresh["realized_sol"]) > float(fresh["invested_sol"])
            slip_pct = (
                float(self.settings.sim_sl_slippage_pct)
                if is_emergency
                else float(self.settings.sim_tp_slippage_pct)
            )
            dex_fee_rate = float(self.settings.sim_dex_fee_pct) / 100.0
            fixed_fee = float(self.settings.sim_fixed_tx_fee_sol)
            gross_sol = (tokens_rem * snapshot.price_usd) / sol_usd
            net_sale_sol = max(
                0.0,
                gross_sol * (1.0 - slip_pct / 100.0) * (1.0 - dex_fee_rate) - fixed_fee,
            )
            projected_total_sol = float(fresh["realized_sol"]) + net_sale_sol
            return projected_total_sol > float(fresh["invested_sol"])

        # Strict positive-only guard: never close automatically if net SOL would be <= invested
        can_close_positive = (
            bool(fresh.get("capital_recovered"))
            or (net_gain_pct >= be_floor_pct and _net_positive_if_closed_now(False))
        )

        # --------------------------------------------------------------
        # RULE 2 — LIQUIDITY PANIC (>25% drop from entry or 2-min peak)
        # (In positive-only mode, only closes if position is already green/BE)
        # --------------------------------------------------------------
        liq_drop_limit = float(self.settings.liquidity_panic_drop_pct)
        entry_liq = runtime.entry_liquidity_usd
        recent_peak_liq = max(
            (liq for _, liq in runtime.liquidity_history),
            default=snapshot.liquidity_usd,
        )
        drop_from_entry_pct = (
            round(((entry_liq - snapshot.liquidity_usd) / entry_liq) * 100.0, 6)
            if entry_liq > 0
            else 0.0
        )
        drop_from_peak_pct = (
            round(((recent_peak_liq - snapshot.liquidity_usd) / recent_peak_liq) * 100.0, 6)
            if recent_peak_liq > 0
            else 0.0
        )
        if (
            snapshot.liquidity_usd >= 0
            and (drop_from_entry_pct > liq_drop_limit or drop_from_peak_pct > liq_drop_limit)
            and (not positive_only or can_close_positive)
        ):
            logger.warning(
                "LIQUIDITY_PANIC on %s: liq=$%.0f (entry=$%.0f, 2m_peak=$%.0f, drop=%.1f%%)",
                fresh["symbol"],
                snapshot.liquidity_usd,
                entry_liq,
                recent_peak_liq,
                max(drop_from_entry_pct, drop_from_peak_pct),
            )
            return await self._close_full(
                fresh,
                reason="LIQUIDITY_PANIC",
                is_emergency=True,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=True,
                blacklist_on_early_panic=True,
            )

        # --------------------------------------------------------------
        # RULE 1 — STOP LOSS (disabled in Wave Scalper No-SL mode) + SMART BREAKEVEN
        # --------------------------------------------------------------
        if (
            not no_sl_mode
            and not positive_only
            and status == "OPEN"
            and price_change_pct <= -float(self.settings.initial_stop_loss_pct)
        ):
            return await self._close_full(
                fresh,
                reason="STOP_LOSS",
                is_emergency=True,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=True,
            )

        # Smart Breakeven Shield: once wave rises >= +10%, never let it round-trip negative
        if (
            (no_sl_mode or positive_only)
            and status in {"OPEN", "TP1_HIT"}
            and peak_gain_pct >= float(self.settings.smart_be_arm_pct)
            and net_gain_pct <= be_floor_pct
            and _net_positive_if_closed_now(False)
        ):
            logger.info(
                "SMART_BREAKEVEN on %s: peak=+%.1f%% -> now=+%.1f%% (locking BE/positive)",
                fresh["symbol"],
                peak_gain_pct,
                net_gain_pct,
            )
            return await self._close_full(
                fresh,
                reason="SMART_BREAKEVEN",
                is_emergency=False,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=False,
            )

        if (
            status == "TP1_HIT"
            and price_change_pct <= float(self.settings.tp1_breakeven_sl_pct)
            and (not positive_only or _net_positive_if_closed_now(False))
        ):
            return await self._close_full(
                fresh,
                reason="BREAKEVEN_STOP",
                is_emergency=not positive_only,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=not positive_only,
            )

        # --------------------------------------------------------------
        # RULE 1B — WICK HARVEST (MECHA HUNTER): lock wave profit on sharp rejection
        # --------------------------------------------------------------
        if (
            (no_sl_mode or positive_only)
            and status in {"OPEN", "TP1_HIT"}
            and peak_gain_pct >= float(self.settings.wick_harvest_arm_pct)
            and pullback_from_peak_pct >= float(self.settings.wick_pullback_exit_pct)
            and net_gain_pct >= be_floor_pct
            and _net_positive_if_closed_now(False)
        ):
            logger.info(
                "WICK_PROFIT_EXIT on %s: peak=+%.1f%% pullback=-%.1f%% net=+%.1f%%",
                fresh["symbol"],
                peak_gain_pct,
                pullback_from_peak_pct,
                net_gain_pct,
            )
            return await self._close_full(
                fresh,
                reason="WICK_PROFIT_EXIT",
                is_emergency=False,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=False,
            )

        # --------------------------------------------------------------
        # RULE 1C — SLOW WAVE POSITIVE EXIT (+10% min after stagnation window)
        # --------------------------------------------------------------
        if (
            positive_only
            and status == "OPEN"
            and trade_age >= timedelta(minutes=int(self.settings.stagnation_window_minutes))
            and net_gain_pct >= 10.0
            and _net_positive_if_closed_now(False)
        ):
            logger.info(
                "SLOW_WAVE_POSITIVE_EXIT on %s: age=%dm net=+%.1f%% (locking >=+10%%)",
                fresh["symbol"],
                int(trade_age.total_seconds() // 60),
                net_gain_pct,
            )
            return await self._close_full(
                fresh,
                reason="SLOW_WAVE_POSITIVE_EXIT",
                is_emergency=False,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=False,
            )

        # --------------------------------------------------------------
        # RULE 3A — FULL 100% AUTOMATIC CLOSE ON TP TARGET (e.g. 1.5x = +50%)
        # --------------------------------------------------------------
        tp_sell_pct = float(self.settings.tp1_sell_pct)
        tp_gain_metric = net_gain_pct if positive_only else price_change_pct
        if (
            status == "OPEN"
            and tp_sell_pct >= 99.9
            and tp_gain_metric >= float(self.settings.tp1_trigger_pct)
            and (not positive_only or _net_positive_if_closed_now(False))
        ):
            logger.info(
                "TP_FULL_TARGET (100%%) hit on %s: gain=+%.2f%% >= +%.2f%% (%.2fx)",
                fresh["symbol"],
                tp_gain_metric,
                self.settings.tp1_trigger_pct,
                1.0 + float(self.settings.tp1_trigger_pct) / 100.0,
            )
            return await self._close_full(
                fresh,
                reason="TP_FULL_TARGET",
                is_emergency=False,
                snapshot=snapshot,
                sol_usd=sol_usd,
                increment_sl_if_loss=False,
            )

        # --------------------------------------------------------------
        # RULE 3 — TP1 PARTIAL & FULL CAPITAL RECOVERY (2x / +100% -> RISK_FREE_RUNNER)
        # --------------------------------------------------------------
        if (
            status in {"OPEN", "TP1_HIT"}
            and not bool(fresh["capital_recovered"])
            and price_change_pct >= float(self.settings.free_risk_trigger_pct)
        ):
            invested_sol = float(fresh["invested_sol"])
            realized_sol = float(fresh["realized_sol"])
            sol_shortfall = invested_sol - realized_sol

            if sol_shortfall > 1e-9:
                tokens_rem = float(fresh["tokens_remaining"])
                rem_val_sol = (tokens_rem * snapshot.price_usd) / sol_usd
                tp_slip_rate = float(self.settings.sim_tp_slippage_pct) / 100.0
                dex_fee_rate = float(self.settings.sim_dex_fee_pct) / 100.0
                sell_tx_fee = (
                    float(self.settings.sim_priority_fee_sol)
                    + float(self.settings.sim_jito_tip_sol)
                )
                net_factor = (1.0 - tp_slip_rate) * (1.0 - dex_fee_rate)
                denom = rem_val_sol * net_factor
                if denom <= 0 or (sol_shortfall + sell_tx_fee) >= denom:
                    share_pct = 100.0
                else:
                    fraction = (sol_shortfall + sell_tx_fee) / denom
                    share_pct = min(100.0, max(1.0, math.ceil(fraction * 10000.0) / 100.0))

                receipt = await self.engine.execute_sell(
                    fresh,
                    share_pct,
                    "TP2",
                    is_emergency=False,
                    spot_price_usd=snapshot.price_usd,
                    sol_usd_price=sol_usd,
                )
                fresh = receipt.trade
                if fresh["status"] == "CLOSED":
                    self._runtime.pop(int(fresh["id"]), None)
                    if self.notifier is not None:
                        await self.notifier.notify_trade_closed(fresh)
                    return ExitActionResult(
                        trade_id=int(fresh["id"]),
                        mint_address=str(fresh["mint_address"]),
                        symbol=str(fresh["symbol"]),
                        action="CLOSED",
                        reason="TP2",
                        new_status="CLOSED",
                        realized_sol=float(fresh["realized_sol"]),
                        pnl_sol=float(fresh["pnl_sol"]),
                        pnl_usd=float(fresh["pnl_usd"]),
                    )

            fresh = await self.db.update_trade(
                fresh["id"],
                status="RISK_FREE_RUNNER",
                capital_recovered=True,
            )
            if fresh.get("lead_id") is not None:
                await self.db.update_lead(
                    int(fresh["lead_id"]), current_stage="RISK_FREE_RUNNER"
                )
            runtime.runner_entered_at = now
            runtime.price_window = deque([(now, snapshot.price_usd)])

            if self.notifier is not None:
                await self.notifier.notify_capital_recovered(fresh)

            return ExitActionResult(
                trade_id=int(fresh["id"]),
                mint_address=str(fresh["mint_address"]),
                symbol=str(fresh["symbol"]),
                action="RISK_FREE_RUNNER",
                reason="CAPITAL_RECOVERED_2X",
                new_status="RISK_FREE_RUNNER",
                realized_sol=float(fresh["realized_sol"]),
                pnl_sol=float(fresh["pnl_sol"]),
                pnl_usd=float(fresh["pnl_usd"]),
            )

        if (
            status == "OPEN"
            and tp_gain_metric >= float(self.settings.tp1_trigger_pct)
            and (not positive_only or _net_positive_if_closed_now(False))
        ):
            receipt = await self.engine.execute_sell(
                fresh,
                tp_sell_pct,
                "TP1",
                is_emergency=False,
                spot_price_usd=snapshot.price_usd,
                sol_usd_price=sol_usd,
            )
            if receipt.trade["status"] == "CLOSED":
                fresh = receipt.trade
                self._runtime.pop(int(fresh["id"]), None)
                if fresh.get("lead_id") is not None:
                    await self.db.update_lead(int(fresh["lead_id"]), current_stage="CLOSED")
                if self.notifier is not None:
                    await self.notifier.notify_trade_closed(fresh)
                return ExitActionResult(
                    trade_id=int(fresh["id"]),
                    mint_address=str(fresh["mint_address"]),
                    symbol=str(fresh["symbol"]),
                    action="CLOSED",
                    reason="TP_FULL_TARGET",
                    new_status="CLOSED",
                    realized_sol=float(fresh["realized_sol"]),
                    pnl_sol=float(fresh["pnl_sol"]),
                    pnl_usd=float(fresh["pnl_usd"]),
                )
            fresh = await self.db.update_trade(receipt.trade["id"], status="TP1_HIT")
            if self.notifier is not None:
                await self.notifier.notify_tp1_hit(fresh)
            return ExitActionResult(
                trade_id=int(fresh["id"]),
                mint_address=str(fresh["mint_address"]),
                symbol=str(fresh["symbol"]),
                action="TP1",
                reason="TP1",
                new_status="TP1_HIT",
                realized_sol=float(fresh["realized_sol"]),
                pnl_sol=float(fresh["pnl_sol"]),
                pnl_usd=float(fresh["pnl_usd"]),
            )

        # --------------------------------------------------------------
        # RULE 4 — VOLUMETRIC EXIT & STAGNATION (VOLUME ALWAYS OVERRIDES X)
        # --------------------------------------------------------------
        if status in {"TP1_HIT", "RISK_FREE_RUNNER"}:
            if snapshot.buys_5m > 0:
                sell_buy_ratio = snapshot.sells_5m / snapshot.buys_5m
            elif snapshot.sells_5m >= 3:
                sell_buy_ratio = float("inf")
            else:
                sell_buy_ratio = 0.0

            if sell_buy_ratio >= float(self.settings.sell_ratio_kill_multiplier):
                runtime.consecutive_sell_ratio_cycles += 1
            else:
                runtime.consecutive_sell_ratio_cycles = 0

            if (
                runtime.consecutive_sell_ratio_cycles >= int(
                    self.settings.sell_ratio_kill_consecutive_cycles
                )
                and (not positive_only or can_close_positive)
            ):
                return await self._close_full(
                    fresh,
                    reason="SELL_RATIO_KILL",
                    is_emergency=True,
                    snapshot=snapshot,
                    sol_usd=sol_usd,
                )

        if status == "RISK_FREE_RUNNER":
            stag_window_min = int(self.settings.stagnation_runner_window_minutes)
            stag_elapsed = now - (runtime.runner_entered_at or opened_at)
        else:
            stag_window_min = int(self.settings.stagnation_window_minutes)
            stag_elapsed = trade_age

        if stag_elapsed >= timedelta(minutes=stag_window_min) and new_peak_vol_5m > 0:
            window_cutoff = now - timedelta(minutes=stag_window_min)
            # Include latest anchor sample <= window_cutoff plus samples within window
            relevant_prices: list[float] = []
            last_anchor: float | None = None
            for ts, px in runtime.price_window:
                if ts <= window_cutoff:
                    last_anchor = px
                else:
                    relevant_prices.append(px)
            if last_anchor is not None:
                relevant_prices.insert(0, last_anchor)
            if not relevant_prices:
                relevant_prices = [snapshot.price_usd]

            min_px = min(relevant_prices)
            max_px = max(relevant_prices)
            band_pct = round(((max_px - min_px) / min_px) * 100.0, 6) if min_px > 0 else 0.0
            vol_ratio_pct = round((snapshot.volume_5m_usd / new_peak_vol_5m) * 100.0, 6)

            if (
                band_pct < float(self.settings.stagnation_price_band_pct)
                and vol_ratio_pct < float(self.settings.stagnation_volume_floor_pct)
                and (not positive_only or can_close_positive)
            ):
                logger.info(
                    "STAGNATION_LOW_VOLUME on %s: band=%.2f%% (<%.1f%%), vol=%.1f%% of peak (<%.1f%%)",
                    fresh["symbol"],
                    band_pct,
                    self.settings.stagnation_price_band_pct,
                    vol_ratio_pct,
                    self.settings.stagnation_volume_floor_pct,
                )
                return await self._close_full(
                    fresh,
                    reason="STAGNATION_LOW_VOLUME",
                    is_emergency=True,
                    snapshot=snapshot,
                    sol_usd=sol_usd,
                )

        # --------------------------------------------------------------
        # RULE 5 — DYNAMIC TRAILING STOP + OPTIONAL X CONTEXT (RUNNER ONLY)
        # --------------------------------------------------------------
        x_trailing_widened = False
        if status == "RISK_FREE_RUNNER" and new_highest > 0:
            trailing_pct = float(self.settings.trailing_stop_pct)

            healthy_volume_for_x = (
                new_peak_vol_5m > 0
                and snapshot.volume_5m_usd > 0.50 * new_peak_vol_5m
                and snapshot.buys_5m >= snapshot.sells_5m
            )
            if (
                self.settings.enable_x_context_on_runner
                and self.x_radar is not None
                and healthy_volume_for_x
            ):
                if await self.x_radar.has_strong_catalyst(
                    str(fresh["mint_address"]), str(fresh["symbol"])
                ):
                    trailing_pct = float(self.settings.trailing_stop_x_boost_pct)
                    x_trailing_widened = True

            drawdown_from_peak_pct = round(
                ((new_highest - snapshot.price_usd) / new_highest) * 100.0, 6
            )
            if drawdown_from_peak_pct >= trailing_pct:
                return await self._close_full(
                    fresh,
                    reason="TRAILING",
                    is_emergency=False,
                    snapshot=snapshot,
                    sol_usd=sol_usd,
                )

        # --------------------------------------------------------------
        # HOLD — Refresh mark-to-market PnL in SQLite
        # --------------------------------------------------------------
        tokens_rem = float(fresh["tokens_remaining"])
        invested_sol = float(fresh["invested_sol"])
        realized_sol = float(fresh["realized_sol"])
        rem_val_sol = (tokens_rem * snapshot.price_usd) / sol_usd
        mtm_pnl_sol = round(realized_sol + rem_val_sol - invested_sol, 9)
        mtm_pnl_usd = round(mtm_pnl_sol * sol_usd, 4)
        mtm_pnl_pct = (
            round((mtm_pnl_sol / invested_sol) * 100.0, 2) if invested_sol > 0 else 0.0
        )

        fresh = await self.db.update_trade(
            fresh["id"],
            pnl_sol=mtm_pnl_sol,
            pnl_usd=mtm_pnl_usd,
            pnl_pct=mtm_pnl_pct,
        )
        return ExitActionResult(
            trade_id=int(fresh["id"]),
            mint_address=str(fresh["mint_address"]),
            symbol=str(fresh["symbol"]),
            action="HOLD",
            reason=None,
            new_status=str(fresh["status"]),
            realized_sol=realized_sol,
            pnl_sol=mtm_pnl_sol,
            pnl_usd=mtm_pnl_usd,
            x_trailing_widened=x_trailing_widened,
        )

    async def poll_all_open_trades(
        self,
        mode: str | None = None,
    ) -> list[ExitActionResult]:
        """Poll DexScreener and evaluate exit rules for every active position."""
        open_trades = await self.db.get_open_trades(mode=mode)
        if not open_trades:
            return []

        sol_usd = await self._resolve_sol_usd(None)
        results: list[ExitActionResult] = []
        for trade in open_trades:
            snapshot = await self.fetch_market_snapshot(
                str(trade["mint_address"]),
                pair_address=trade.get("lead_pair_address"),
            )
            if snapshot is None:
                continue
            res = await self.evaluate_trade(trade, snapshot, sol_usd_price=sol_usd)
            results.append(res)
        return results
