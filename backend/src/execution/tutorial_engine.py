"""Realistic Solana Tutorial Execution Engine (virtual SOL bankroll + real friction)."""

from __future__ import annotations

import logging
from typing import Any

from src.config import Settings, get_settings
from src.database import Database, utc_now_iso
from src.execution.base import (
    ExecutionEngine,
    ExecutionError,
    ExecutionReceipt,
    InsufficientBalanceError,
)
from src.price_oracle import SolPriceOracle

logger = logging.getLogger(__name__)


class TutorialEngine(ExecutionEngine):
    """Simulates Solana DEX swaps against SQLite ``tutorial_available_sol`` with realistic fees.

    Every buy and sell deducts:
    - Solana Priority Fee (`SIM_PRIORITY_FEE_SOL`, default 0.0005 SOL)
    - Jito Tip (`SIM_JITO_TIP_SOL`, default 0.002 SOL)
    - DEX Swap Fee (`SIM_DEX_FEE_PCT`, default 0.25%)
    - Asymmetric Slippage:
      * Buy: `SIM_BUY_SLIPPAGE_PCT` (1.5%)
      * Take Profit / Trailing: `SIM_TP_SLIPPAGE_PCT` (2.0%)
      * Emergency SL / Panic / Stagnation: `SIM_SL_SLIPPAGE_PCT` (8.0%)
    """

    def __init__(
        self,
        db: Database,
        *,
        price_oracle: SolPriceOracle | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.price_oracle = price_oracle

    async def _resolve_sol_usd(self, sol_usd_price: float | None) -> float:
        if sol_usd_price is not None and sol_usd_price > 0:
            return float(sol_usd_price)
        if self.price_oracle is not None:
            return await self.price_oracle.get_sol_usd_price()
        return float(self.settings.fallback_sol_usd_price)

    async def execute_buy(
        self,
        lead: dict[str, Any],
        amount_sol: float,
        *,
        spot_price_usd: float | None = None,
        sol_usd_price: float | None = None,
    ) -> ExecutionReceipt:
        """Execute a simulated SOL -> Token swap and persist the trade & BUY event."""
        if amount_sol <= 0:
            raise ExecutionError("amount_sol must be positive.")

        entry_price_usd = (
            float(spot_price_usd)
            if spot_price_usd is not None
            else float(lead.get("price_usd") or 0.0)
        )
        if entry_price_usd <= 0:
            raise ExecutionError(
                f"Cannot buy {lead.get('symbol', '?')}: missing positive spot price_usd."
            )

        sol_usd = await self._resolve_sol_usd(sol_usd_price)

        priority_fee = float(self.settings.sim_priority_fee_sol)
        jito_tip = float(self.settings.sim_jito_tip_sol)
        tx_fee_sol = round(priority_fee + jito_tip, 9)
        total_debit_sol = round(amount_sol + tx_fee_sol, 9)

        bot_state = await self.db.get_bot_state()
        available_sol = float(bot_state["tutorial_available_sol"])
        if available_sol + 1e-9 < total_debit_sol:
            raise InsufficientBalanceError(
                f"Insufficient tutorial SOL: available={available_sol:.6f}, "
                f"required={total_debit_sol:.6f} (amount={amount_sol:.6f} + fees={tx_fee_sol:.6f})."
            )

        # Apply DEX fee and buy slippage
        dex_fee_rate = float(self.settings.sim_dex_fee_pct) / 100.0
        buy_slip_rate = float(self.settings.sim_buy_slippage_pct) / 100.0

        dex_fee_sol = round(amount_sol * dex_fee_rate, 9)
        net_swap_sol = amount_sol - dex_fee_sol
        effective_entry_price_usd = entry_price_usd * (1.0 + buy_slip_rate)
        tokens_initial = (net_swap_sol * sol_usd) / effective_entry_price_usd
        buy_slippage_sol = round(
            net_swap_sol * (buy_slip_rate / (1.0 + buy_slip_rate)), 9
        )
        buy_fees_sol = round(tx_fee_sol + dex_fee_sol, 9)

        # Initial mark-to-market at spot price reflects entry friction accurately
        spot_value_sol = (tokens_initial * entry_price_usd) / sol_usd
        initial_pnl_sol = round(spot_value_sol - total_debit_sol, 9)
        initial_pnl_usd = round(initial_pnl_sol * sol_usd, 4)
        initial_pnl_pct = round((initial_pnl_sol / total_debit_sol) * 100.0, 2)
        invested_usd = round(total_debit_sol * sol_usd, 4)

        new_available_sol = round(max(0.0, available_sol - total_debit_sol), 9)
        await self.db.update_bot_state(tutorial_available_sol=new_available_sol)

        trade = await self.db.create_trade(
            lead_id=lead.get("id"),
            mint_address=str(lead["mint_address"]),
            symbol=str(lead["symbol"]),
            mode="TUTORIAL",
            invested_sol=total_debit_sol,
            invested_usd=invested_usd,
            entry_price_usd=entry_price_usd,
            effective_entry_price_usd=effective_entry_price_usd,
            tokens_initial=tokens_initial,
            tokens_remaining=tokens_initial,
            fees_paid_sol=buy_fees_sol,
            slippage_cost_sol=buy_slippage_sol,
            realized_sol=0.0,
            current_price_usd=entry_price_usd,
            highest_price_seen=entry_price_usd,
            peak_volume_5m=0.0,
            pnl_sol=initial_pnl_sol,
            pnl_usd=initial_pnl_usd,
            pnl_pct=initial_pnl_pct,
            capital_recovered=False,
        )

        tx_sig = f"TUT-BUY-{trade['id']}"
        event = await self.db.record_trade_event(
            trade_id=trade["id"],
            event_type="BUY",
            sol_amount=total_debit_sol,
            tokens_amount=tokens_initial,
            price_usd=entry_price_usd,
            effective_price_usd=effective_entry_price_usd,
            fee_sol=buy_fees_sol,
            slippage_sol=buy_slippage_sol,
            tx_signature=tx_sig,
            notes=f"Swap={amount_sol:.6f} SOL | TxFee={tx_fee_sol:.6f} | DexFee={dex_fee_sol:.6f}",
        )

        if lead.get("id") is not None:
            await self.db.update_lead(int(lead["id"]), current_stage="OPEN")

        logger.info(
            "[TUTORIAL BUY] %s | spent=%.6f SOL ($%.2f) | tokens=%.4f | eff_px=$%.8f | avail=%.6f SOL",
            trade["symbol"],
            total_debit_sol,
            invested_usd,
            tokens_initial,
            effective_entry_price_usd,
            new_available_sol,
        )

        return ExecutionReceipt(
            trade=trade,
            event=event,
            mode="TUTORIAL",
            side="BUY",
            sol_amount=total_debit_sol,
            tokens_amount=tokens_initial,
            spot_price_usd=entry_price_usd,
            effective_price_usd=effective_entry_price_usd,
            fee_sol=buy_fees_sol,
            slippage_sol=buy_slippage_sol,
            tx_signature=tx_sig,
        )

    async def execute_sell(
        self,
        trade: dict[str, Any],
        share_pct: float,
        reason: str,
        is_emergency: bool = False,
        *,
        spot_price_usd: float | None = None,
        sol_usd_price: float | None = None,
    ) -> ExecutionReceipt:
        """Execute a partial or full simulated Token -> SOL sell and credit virtual SOL."""
        if not (0.0 < share_pct <= 100.0):
            raise ExecutionError(f"share_pct must be in (0, 100], got {share_pct}.")

        fresh_trade = await self.db.get_trade(int(trade["id"]))
        if fresh_trade is None:
            raise ExecutionError(f"Trade id={trade['id']} not found.")
        if fresh_trade["status"] == "CLOSED":
            raise ExecutionError(f"Trade id={fresh_trade['id']} is already CLOSED.")

        tokens_remaining = float(fresh_trade["tokens_remaining"])
        if tokens_remaining <= 0:
            raise ExecutionError(f"Trade id={fresh_trade['id']} has zero tokens remaining.")

        spot_px = (
            float(spot_price_usd)
            if spot_price_usd is not None
            else float(fresh_trade["current_price_usd"])
        )
        if spot_px <= 0:
            raise ExecutionError(
                f"Cannot sell {fresh_trade['symbol']}: spot price must be positive."
            )

        sol_usd = await self._resolve_sol_usd(sol_usd_price)

        if share_pct >= 99.9999:
            tokens_to_sell = tokens_remaining
            tokens_after = 0.0
            is_full_close = True
        else:
            tokens_to_sell = tokens_remaining * (share_pct / 100.0)
            tokens_after = max(0.0, tokens_remaining - tokens_to_sell)
            is_full_close = tokens_after <= 1e-12

        slip_pct = (
            float(self.settings.sim_sl_slippage_pct)
            if is_emergency
            else float(self.settings.sim_tp_slippage_pct)
        )
        slip_rate = slip_pct / 100.0
        dex_fee_rate = float(self.settings.sim_dex_fee_pct) / 100.0

        effective_sell_price_usd = spot_px * (1.0 - slip_rate)
        ideal_sol = (tokens_to_sell * spot_px) / sol_usd
        post_slip_sol = (tokens_to_sell * effective_sell_price_usd) / sol_usd

        sell_slippage_sol = round(ideal_sol - post_slip_sol, 9)
        sell_dex_fee_sol = round(post_slip_sol * dex_fee_rate, 9)
        sell_tx_fee_sol = round(
            float(self.settings.sim_priority_fee_sol)
            + float(self.settings.sim_jito_tip_sol),
            9,
        )
        sell_fees_sol = round(sell_dex_fee_sol + sell_tx_fee_sol, 9)
        net_sol_received = round(post_slip_sol - sell_dex_fee_sol - sell_tx_fee_sol, 9)

        # Credit proceeds to virtual tutorial balance
        bot_state = await self.db.get_bot_state()
        current_avail = float(bot_state["tutorial_available_sol"])
        new_available_sol = round(max(0.0, current_avail + net_sol_received), 9)

        invested_sol = float(fresh_trade["invested_sol"])
        new_realized_sol = round(float(fresh_trade["realized_sol"]) + net_sol_received, 9)
        new_fees_sol = round(float(fresh_trade["fees_paid_sol"]) + sell_fees_sol, 9)
        new_slippage_sol = round(
            float(fresh_trade["slippage_cost_sol"]) + sell_slippage_sol, 9
        )

        remaining_val_sol = (
            0.0 if is_full_close else (tokens_after * spot_px) / sol_usd
        )
        new_pnl_sol = round(new_realized_sol + remaining_val_sol - invested_sol, 9)
        new_pnl_usd = round(new_pnl_sol * sol_usd, 4)
        new_pnl_pct = (
            round((new_pnl_sol / invested_sol) * 100.0, 2) if invested_sol > 0 else 0.0
        )
        new_highest = max(float(fresh_trade["highest_price_seen"]), spot_px)
        capital_recovered = bool(fresh_trade["capital_recovered"]) or (
            new_realized_sol >= invested_sol - 1e-9
        )

        trade_updates: dict[str, Any] = {
            "tokens_remaining": tokens_after,
            "fees_paid_sol": new_fees_sol,
            "slippage_cost_sol": new_slippage_sol,
            "realized_sol": new_realized_sol,
            "current_price_usd": spot_px,
            "highest_price_seen": new_highest,
            "pnl_sol": new_pnl_sol,
            "pnl_usd": new_pnl_usd,
            "pnl_pct": new_pnl_pct,
            "capital_recovered": capital_recovered,
        }
        if is_full_close:
            trade_updates["status"] = "CLOSED"
            trade_updates["exit_reason"] = reason
            trade_updates["closed_at"] = utc_now_iso()

        updated_trade = await self.db.update_trade(fresh_trade["id"], **trade_updates)

        tx_sig = f"TUT-SELL-{fresh_trade['id']}-{reason}"
        event = await self.db.record_trade_event(
            trade_id=fresh_trade["id"],
            event_type=reason,
            sol_amount=net_sol_received,
            tokens_amount=tokens_to_sell,
            price_usd=spot_px,
            effective_price_usd=effective_sell_price_usd,
            fee_sol=sell_fees_sol,
            slippage_sol=sell_slippage_sol,
            tx_signature=tx_sig,
            notes=(
                f"share={share_pct:.2f}% | emergency={is_emergency} | "
                f"slip={slip_pct:.1f}%"
            ),
        )

        bot_updates: dict[str, Any] = {"tutorial_available_sol": new_available_sol}
        if is_full_close:
            bot_updates["daily_pnl_sol"] = round(
                float(bot_state["daily_pnl_sol"]) + new_pnl_sol, 9
            )
            if fresh_trade.get("lead_id") is not None:
                await self.db.update_lead(
                    int(fresh_trade["lead_id"]), current_stage="CLOSED"
                )
        await self.db.update_bot_state(**bot_updates)

        logger.info(
            "[TUTORIAL SELL %s] %s | share=%.1f%% | received=%.6f SOL | pnl=%.6f SOL (%.2f%%) | avail=%.6f SOL",
            reason,
            updated_trade["symbol"],
            share_pct,
            net_sol_received,
            new_pnl_sol,
            new_pnl_pct,
            new_available_sol,
        )

        return ExecutionReceipt(
            trade=updated_trade,
            event=event,
            mode="TUTORIAL",
            side="SELL",
            sol_amount=net_sol_received,
            tokens_amount=tokens_to_sell,
            spot_price_usd=spot_px,
            effective_price_usd=effective_sell_price_usd,
            fee_sol=sell_fees_sol,
            slippage_sol=sell_slippage_sol,
            tx_signature=tx_sig,
        )
