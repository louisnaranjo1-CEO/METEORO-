"""Phase 6 (GATE): 100% deterministic decision gate enforcing risk caps & MCap rules."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from src.config import Settings, get_settings
from src.database import Database
from src.judge import JUDGE_TIMEOUT_OR_ERROR, JudgeResult, JudgeVerdict
from src.models import TokenDossier
from src.price_oracle import SolPriceOracle

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GateDecision:
    """Deterministic outcome from evaluating a Mute Judge verdict against risk rules."""

    approved: bool
    decision: Literal["APPROVE", "WATCH", "REJECT"]
    kill_reason: str | None
    approved_size_sol: float | None
    recheck_at: str | None
    verdict_id: int
    lead_stage: str
    zone: Literal["EARLY", "CONFIRMED", "OUT_OF_RANGE"]
    entry_liquidity_pct: float | None


class DeterministicGate:
    """Pure Python rule engine guarding capital, MCap thresholds, and fixed trade sizing."""

    def __init__(
        self,
        db: Database,
        *,
        settings: Settings | None = None,
        price_oracle: SolPriceOracle | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.price_oracle = price_oracle

    async def _resolve_sol_usd_price(self, explicit_price: float | None) -> float:
        if explicit_price is not None and explicit_price > 0:
            return float(explicit_price)
        if self.price_oracle is not None:
            return await self.price_oracle.get_sol_usd_price()
        return float(self.settings.fallback_sol_usd_price)

    def _classify_zone(
        self, market_cap_usd: float | None
    ) -> Literal["EARLY", "CONFIRMED", "OUT_OF_RANGE"]:
        if market_cap_usd is None:
            return "OUT_OF_RANGE"
        if (
            self.settings.min_market_cap_usd
            <= market_cap_usd
            < self.settings.low_mcap_threshold_usd
        ):
            return "EARLY"
        if (
            self.settings.low_mcap_threshold_usd
            <= market_cap_usd
            <= self.settings.max_market_cap_usd
        ):
            return "CONFIRMED"
        return "OUT_OF_RANGE"

    def _check_verdict_against_zone(
        self,
        verdict: JudgeVerdict,
        zone: Literal["EARLY", "CONFIRMED", "OUT_OF_RANGE"],
    ) -> str | None:
        if zone == "OUT_OF_RANGE":
            return "MARKET_CAP_OUT_OF_RANGE"

        is_aggressive = self.settings.strategy_profile == "AGRESIVO"

        if zone == "EARLY":
            if verdict.action_prob < self.settings.gate_min_action_prob_low_mcap:
                return "EARLY_ACTION_PROB_LOW"
            if (
                verdict.organic != "yes"
                or verdict.organic_prob < self.settings.gate_min_organic_prob_low_mcap
            ):
                return "EARLY_NOT_ORGANIC"
            if (
                verdict.late != "no"
                and (not is_aggressive or verdict.late_prob > self.settings.gate_max_late_prob_low_mcap)
            ):
                return "LATE_ENTRY"
            if (
                verdict.manipulation_trap != "no"
                or verdict.trap_prob > self.settings.gate_max_manipulation_prob_low_mcap
            ):
                return "MANIPULATION_TRAP"
            if verdict.exit_risk > self.settings.gate_max_exit_risk:
                return "EXIT_RISK_HIGH"
            return None

        # CONFIRMED zone ($150K to $1.5M)
        if verdict.action_prob < self.settings.gate_min_action_prob:
            return "ACTION_PROB_LOW"
        if (
            verdict.organic != "yes"
            or verdict.organic_prob < self.settings.gate_min_organic_prob
        ):
            return "NOT_ORGANIC"
        if (
            verdict.late != "no"
            and (not is_aggressive or verdict.late_prob > self.settings.gate_max_late_prob)
        ):
            return "LATE_ENTRY"
        if verdict.manipulation_trap != "no":
            return "MANIPULATION_TRAP"
        if verdict.exit_risk > self.settings.gate_max_exit_risk:
            return "EXIT_RISK_HIGH"
        return None

    async def evaluate(
        self,
        *,
        lead: dict[str, Any],
        dossier: TokenDossier,
        judge_result: JudgeResult | JudgeVerdict,
        sol_usd_price: float | None = None,
        now: datetime | None = None,
    ) -> GateDecision:
        """Evaluate ``judge_result`` against deterministic risk rules and persist verdict."""
        if isinstance(judge_result, JudgeVerdict):
            normalized = JudgeResult(
                verdict=judge_result,
                is_fallback=False,
                error_reason=None,
                model_used=self.settings.judge_model,
                latency_ms=0.0,
            )
        else:
            normalized = judge_result

        verdict = normalized.verdict
        now_utc = now or datetime.now(timezone.utc)

        bot_state = await self.db.get_bot_state()
        trading_mode = str(bot_state["trading_mode"])
        open_trades = await self.db.count_open_trades(mode=trading_mode)

        sol_price = await self._resolve_sol_usd_price(sol_usd_price)
        fixed_size_sol = float(bot_state["trade_size_sol"])
        entry_usd = fixed_size_sol * sol_price

        market_cap_usd = dossier.numeric_or_none("market_cap_usd")
        if market_cap_usd is None and lead.get("market_cap") is not None:
            market_cap_usd = float(lead["market_cap"])

        liquidity_usd = dossier.numeric_or_none("liquidity_usd")
        if liquidity_usd is None and lead.get("liquidity_usd") is not None:
            liquidity_usd = float(lead["liquidity_usd"])

        price_at_verdict = dossier.numeric_or_none("price_usd")
        if price_at_verdict is None and lead.get("price_usd") is not None:
            price_at_verdict = float(lead["price_usd"])

        entry_liquidity_pct = (
            round((entry_usd / liquidity_usd) * 100.0, 4)
            if liquidity_usd is not None and liquidity_usd > 0
            else None
        )
        zone = self._classify_zone(market_cap_usd)

        # --------------------------------------------------------------
        # 1. Global Risk Pre-Checks (order matters)
        # --------------------------------------------------------------
        decision: Literal["APPROVE", "WATCH", "REJECT"]
        kill_reason: str | None = None
        approved = False
        approved_size_sol: float | None = None
        recheck_at: str | None = None

        daily_sl_count = int(bot_state["daily_sl_count"])
        daily_pnl_sol = float(bot_state["daily_pnl_sol"])
        daily_loss_sol = max(0.0, -daily_pnl_sol)
        bankroll_sol = float(bot_state["tutorial_bankroll_sol"])
        daily_loss_pct = (
            (daily_loss_sol / bankroll_sol) * 100.0 if bankroll_sol > 0 else 0.0
        )

        if bool(bot_state["is_paused"]):
            decision = "REJECT"
            kill_reason = "BOT_PAUSED"
        elif open_trades >= int(bot_state["max_concurrent_trades"]):
            decision = "REJECT"
            kill_reason = "MAX_CONCURRENT_TRADES"
        elif (
            daily_sl_count >= self.settings.max_consecutive_sl
            or daily_loss_pct >= self.settings.max_daily_loss_pct
            or daily_loss_sol >= self.settings.max_daily_loss_sol
        ):
            await self.db.update_bot_state(is_paused=1)
            decision = "REJECT"
            kill_reason = "DAILY_LOSS_LIMIT"
        elif (
            entry_liquidity_pct is None
            or entry_liquidity_pct > self.settings.max_entry_liquidity_pct
        ):
            decision = "REJECT"
            kill_reason = "ENTRY_EXCEEDS_LIQUIDITY_CAP"

        # --------------------------------------------------------------
        # 2. Mute Judge Failure / Pass / Single Watch Rule
        # --------------------------------------------------------------
        elif normalized.is_fallback or normalized.error_reason == JUDGE_TIMEOUT_OR_ERROR:
            decision = "REJECT"
            kill_reason = JUDGE_TIMEOUT_OR_ERROR
        elif verdict.action == "pass":
            decision = "REJECT"
            kill_reason = "JUDGE_PASS"
        elif verdict.action == "watch":
            prior_watches = await self.db.count_watch_verdicts_for_lead(lead["id"])
            if prior_watches >= 1:
                decision = "REJECT"
                kill_reason = "SECOND_WATCH_KILL"
            else:
                decision = "WATCH"
                recheck_at = (
                    now_utc + timedelta(minutes=self.settings.watch_recheck_minutes)
                ).isoformat()

        # --------------------------------------------------------------
        # 3. Zone-Specific Thresholds (action == "buy")
        # --------------------------------------------------------------
        else:
            zone_kill = self._check_verdict_against_zone(verdict, zone)
            if zone_kill is not None:
                decision = "REJECT"
                kill_reason = zone_kill
            else:
                decision = "APPROVE"
                approved = True
                # FIXED SIZE RULE: Judge confidence NEVER scales position size.
                approved_size_sol = fixed_size_sol

        # --------------------------------------------------------------
        # 4. Persist Verdict (ALL verdicts saved for Sunday calibration)
        # --------------------------------------------------------------
        verdict_row = await self.db.record_judge_verdict(
            lead_id=lead["id"],
            dossier_json=dossier.to_prompt_json(),
            organic=verdict.organic,
            organic_prob=verdict.organic_prob,
            late=verdict.late,
            late_prob=verdict.late_prob,
            exit_risk=verdict.exit_risk,
            manipulation_trap=verdict.manipulation_trap,
            trap_prob=verdict.trap_prob,
            action=verdict.action,
            action_prob=verdict.action_prob,
            gate_passed=approved,
            kill_reason=kill_reason,
            price_at_verdict=price_at_verdict,
        )

        # --------------------------------------------------------------
        # 5. Update Lead Lifecycle Stage
        # --------------------------------------------------------------
        if decision == "APPROVE":
            lead_stage = (
                "PENDING_APPROVAL" if bool(bot_state["semi_auto_mode"]) else "GATE"
            )
            await self.db.update_lead(lead["id"], current_stage=lead_stage)
        elif decision == "WATCH":
            lead_stage = "JUDGE"
            await self.db.update_lead(lead["id"], current_stage=lead_stage)
        else:
            lead_stage = "REJECTED"
            await self.db.update_lead(
                lead["id"], current_stage=lead_stage, kill_reason=kill_reason
            )

        logger.info(
            "Gate[%s | %s]: decision=%s zone=%s action=%s(%.2f) kill=%s liq_impact=%s%%",
            lead["symbol"],
            lead["mint_address"][:8],
            decision,
            zone,
            verdict.action,
            verdict.action_prob,
            kill_reason,
            entry_liquidity_pct,
        )

        return GateDecision(
            approved=approved,
            decision=decision,
            kill_reason=kill_reason,
            approved_size_sol=approved_size_sol,
            recheck_at=recheck_at,
            verdict_id=int(verdict_row["id"]),
            lead_stage=lead_stage,
            zone=zone,
            entry_liquidity_pct=entry_liquidity_pct,
        )
