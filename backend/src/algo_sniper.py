"""Pure Mathematical Algorithmic Sniper Scorer (0ms LLM bypass).

Replaces OpenRouter LLM calls when ``settings.use_llm_judge == False`` so
entries execute with sub-millisecond decision latency, zero API cost, and
strict anti-top-buying / pullback-dip mathematical discipline.
"""

from __future__ import annotations

import time
from typing import Any

from src.config import Settings
from src.judge import JudgeResult, JudgeVerdict
from src.models import TokenDossier


def _num(dossier: TokenDossier, field: str) -> float | None:
    return dossier.numeric_or_none(field)


def evaluate_algorithmic_sniper(
    dossier: TokenDossier,
    settings: Settings,
) -> JudgeResult:
    """Deterministically score a TokenDossier in <1ms without any LLM call."""
    started = time.perf_counter()

    mcap = _num(dossier, "market_cap_usd") or 0.0
    liq = _num(dossier, "liquidity_usd") or 0.0
    liq_mc = _num(dossier, "liq_to_mcap_pct")
    if liq_mc is None and mcap > 0 and liq > 0:
        liq_mc = (liq / mcap) * 100.0
    age_min = _num(dossier, "age_minutes")
    holders = _num(dossier, "holders_count") or 0.0
    top10 = _num(dossier, "top10_holders_pct")
    dev_pct = _num(dossier, "dev_wallet_pct")
    lp_locked = _num(dossier, "lp_locked_pct")
    vol_5m = _num(dossier, "volume_5m_usd") or 0.0
    vol_1h = _num(dossier, "volume_1h_usd") or 0.0
    vol_accel = _num(dossier, "volume_accel_5m_vs_1h")
    buys_5m = _num(dossier, "buys_5m") or 0.0
    sells_5m = _num(dossier, "sells_5m") or 0.0
    bs_5m = _num(dossier, "buy_sell_ratio_5m")
    if bs_5m is None and buys_5m > 0:
        bs_5m = buys_5m / max(1.0, sells_5m)
    bs_1h = _num(dossier, "buy_sell_ratio_1h")
    pc_5m = _num(dossier, "price_change_5m_pct")
    pc_1h = _num(dossier, "price_change_1h_pct")
    pc_6h = _num(dossier, "price_change_6h_pct")
    entry_liq_pct = _num(dossier, "planned_entry_pct_of_liquidity")
    whale_confirm = getattr(dossier, "whale_confirm", False) is True
    insider_flag = getattr(dossier, "rugcheck_insider_flag", False) is True
    creator_tokens = _num(dossier, "creator_tokens_count")

    # ------------------------------------------------------------------
    # 1. EXIT RISK (1 = deep & clean, 5 = illiquid trap)
    # ------------------------------------------------------------------
    exit_risk = 2
    if liq_mc is not None:
        if liq_mc >= 14.0:
            exit_risk = 1
        elif liq_mc >= 10.0:
            exit_risk = 2
        elif liq_mc >= 7.5:
            exit_risk = 3
        elif liq_mc >= 5.0:
            exit_risk = 4
        else:
            exit_risk = 5
    if entry_liq_pct is not None and entry_liq_pct > 1.0:
        exit_risk = max(exit_risk, 4)

    # ------------------------------------------------------------------
    # 2. MANIPULATION TRAP CHECK (Pure On-Chain Math)
    # ------------------------------------------------------------------
    trap_score = 0.12
    if insider_flag:
        trap_score += 0.55
    if top10 is not None and top10 > settings.max_top10_holders_pct:
        trap_score += 0.45
    if dev_pct is not None and dev_pct > settings.max_dev_wallet_pct:
        trap_score += 0.45
    if lp_locked is not None and lp_locked < 85.0:
        trap_score += 0.50
    if creator_tokens is not None and creator_tokens > 5:
        trap_score += 0.30
    if vol_5m > 0 and (buys_5m + sells_5m) > 0:
        avg_tx = vol_5m / (buys_5m + sells_5m)
        # Suspicious wash-bot pattern: huge 5m vol with tiny tx count or massive identical bots
        if (buys_5m + sells_5m) < 12 and vol_5m > 25_000:
            trap_score += 0.40
        if avg_tx > 2500 and holders < 150:
            trap_score += 0.25
    trap_prob = round(min(0.99, max(0.05, trap_score)), 3)
    manipulation_trap = "yes" if trap_prob >= 0.50 else "no"

    # ------------------------------------------------------------------
    # 3. ANTI-TOP-BUYING ("NO ENTRAR EN LA PUNTA")
    # ------------------------------------------------------------------
    max_pc5 = float(settings.sniper_max_entry_pc5m_pct)
    min_pc5 = float(settings.sniper_min_entry_pc5m_pct)
    max_pc1h = float(settings.sniper_max_entry_pc1h_pct)
    max_age = float(settings.sniper_max_age_minutes)

    late_score = 0.20
    hard_top_reject = False
    if pc_5m is not None:
        if pc_5m > max_pc5:
            # Parabolic 5m candle tip -> DO NOT CHASE THE TOP
            late_score = max(late_score, 0.88)
            hard_top_reject = True
        elif pc_5m > max_pc5 * 0.75:
            late_score += 0.25
        elif min_pc5 <= pc_5m <= 12.0:
            # Sweet-spot healthy pullback / early breakout continuation
            late_score -= 0.08
    if pc_1h is not None:
        if pc_1h > max_pc1h:
            # Already ran >140% in 1h -> exit liquidity risk
            late_score = max(late_score, 0.85)
            hard_top_reject = True
        elif pc_1h > max_pc1h * 0.75:
            late_score += 0.20
    if age_min is not None:
        if age_min > max_age:
            late_score = max(late_score, 0.78)
        elif age_min < 4.5:
            # Brand-new 1-4m coin spiking vertically = high instant-dump risk
            if pc_5m is not None and pc_5m > 18.0:
                late_score = max(late_score, 0.85)
                hard_top_reject = True
    if pc_6h is not None and pc_6h < -35.0:
        late_score = max(late_score, 0.80)

    late_prob = round(min(0.99, max(0.05, late_score)), 3)
    late = "yes" if late_prob >= 0.55 else "no"

    # ------------------------------------------------------------------
    # 4. ORGANIC MOMENTUM & FLOW QUALITY
    # ------------------------------------------------------------------
    org_score = 0.35
    if bs_5m is not None:
        if bs_5m >= 1.45:
            org_score += 0.24
        elif bs_5m >= 1.20:
            org_score += 0.16
        elif bs_5m >= 1.05:
            org_score += 0.08
        elif bs_5m < 0.85:
            org_score -= 0.20
    if bs_1h is not None:
        if bs_1h >= 1.25:
            org_score += 0.12
        elif bs_1h < 0.90:
            org_score -= 0.18
    if vol_5m >= 15_000:
        org_score += 0.14
    elif vol_5m >= 5_000:
        org_score += 0.09
    elif vol_5m < 2_000:
        org_score -= 0.18
    if vol_accel is not None and vol_accel >= 1.3:
        org_score += 0.08
    if holders >= 250:
        org_score += 0.08
    elif holders >= 100:
        org_score += 0.04
    if whale_confirm:
        org_score += 0.15

    organic_prob = round(min(0.98, max(0.05, org_score)), 3)
    organic = "yes" if organic_prob >= 0.52 else "no"

    # ------------------------------------------------------------------
    # 5. FINAL ACTION DECISION (BUY / WATCH / PASS)
    # ------------------------------------------------------------------
    falling_knife = pc_5m is not None and pc_5m < min_pc5
    if (
        manipulation_trap == "yes"
        or exit_risk > settings.gate_max_exit_risk
        or hard_top_reject
        or falling_knife
        or vol_5m < 2_500.0
    ):
        action = "pass"
        action_prob = 0.85
    elif organic == "yes" and late == "no" and (bs_5m is None or bs_5m >= 1.15):
        # Composite BUY confidence from organic flow, low trap risk, and depth
        buy_conf = 0.45 + (organic_prob * 0.35) + ((1.0 - late_prob) * 0.15) + (0.08 if whale_confirm else 0.0)
        if pc_5m is not None and -6.0 <= pc_5m <= 14.0:
            buy_conf += 0.06  # Dip/pullback or clean non-extended entry bonus
        action = "buy"
        action_prob = round(min(0.96, max(0.50, buy_conf)), 3)
    elif organic == "yes" and late_prob < 0.72:
        action = "watch"
        action_prob = 0.55
    else:
        action = "pass"
        action_prob = 0.75

    verdict = JudgeVerdict(
        organic=organic,
        organic_prob=organic_prob,
        late=late,
        late_prob=late_prob,
        exit_risk=exit_risk,
        manipulation_trap=manipulation_trap,
        trap_prob=trap_prob,
        action=action,
        action_prob=action_prob,
    )
    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    return JudgeResult(
        verdict=verdict,
        is_fallback=False,
        error_reason=None,
        model_used="ALGO_SNIPER_V1 (0ms)",
        latency_ms=latency_ms,
        raw_content=None,
    )
