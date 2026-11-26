"""Phase 3 Verification Suite: Mute Judge (OpenRouter JSON) & Deterministic Gate."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.config import Settings
from src.database import Database, utc_now_iso
from src.gate import DeterministicGate
from src.judge import JUDGE_TIMEOUT_OR_ERROR, JudgeVerdict, MuteJudge
from src.models import TokenDossier


def _make_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        openrouter_api_key="test-openrouter-key",
        **overrides,
    )


def _mock_openrouter_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _seed_lead(
    db: Database,
    *,
    mint: str,
    symbol: str,
    market_cap: float,
    liquidity_usd: float,
    price_usd: float,
) -> dict[str, Any]:
    return await db.create_lead(
        mint_address=mint,
        symbol=symbol,
        name=f"{symbol} Token",
        pair_address=f"Pair{symbol}11111111111111111111111111111111",
        current_stage="WHALE",
        market_cap=market_cap,
        liquidity_usd=liquidity_usd,
        main_pool_share=1.0,
        holders_count=420,
        price_usd=price_usd,
    )


def _healthy_80k_dossier(lead_id: int, mint: str, symbol: str) -> TokenDossier:
    return TokenDossier(
        lead_id=lead_id,
        mint_address=mint,
        symbol=symbol,
        market_cap_usd=80_000.0,
        liquidity_usd=35_000.0,
        main_pool_share=1.0,
        price_usd=0.00008,
        age_minutes=28,
        holders_count=420,
        top10_holders_pct=14.2,
        dev_wallet_pct=0.0,
        lp_locked_pct=100.0,
        volume_5m_usd=18_500.0,
        volume_1h_usd=92_000.0,
        unique_makers_5m=84,
        vol_to_liquidity_ratio_1h=2.63,
        avg_tx_size_5m_usd=112.0,
        buys_5m=118,
        sells_5m=47,
        buy_sell_ratio_5m=2.51,
        price_change_5m_pct=14.5,
        price_change_1h_pct=42.0,
        whale_confirm=True,
        whale_wallets_count=2,
        planned_entry_sol=0.25,
        planned_entry_usd=18.75,
        planned_entry_pct_of_liquidity=0.0536,
    )


async def test_scenario_1_healthy_80k_mc_passes_all_gates_and_fixed_size(
    tmp_path: Path,
) -> None:
    """Scenario 1: Healthy $80K MC dossier passes Early Zone thresholds with fixed SOL sizing."""
    db = Database(db_path=tmp_path / "phase3_s1.db")
    await db.initialize()
    settings = _make_settings()

    mint = "Healthy80kMint11111111111111111111111111111"
    lead = await _seed_lead(
        db,
        mint=mint,
        symbol="HLTH",
        market_cap=80_000.0,
        liquidity_usd=35_000.0,
        price_usd=0.00008,
    )
    dossier = _healthy_80k_dossier(lead["id"], mint, "HLTH")

    captured_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_payloads.append(body)
        verdict_json = {
            "organic": "yes",
            "organic_prob": 0.85,
            "late": "no",
            "late_prob": 0.14,
            "exit_risk": 2,
            "manipulation_trap": "no",
            "trap_prob": 0.10,
            "action": "buy",
            "action_prob": 0.82,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(verdict_json)}}]},
        )

    async with _mock_openrouter_client(handler) as client:
        judge = MuteJudge(settings=settings, http_client=client)
        judge_result = await judge.evaluate(dossier)

    assert len(captured_payloads) == 1
    req_body = captured_payloads[0]
    assert req_body["temperature"] == 0.0
    assert req_body["response_format"]["type"] == "json_schema"
    assert req_body["response_format"]["json_schema"]["strict"] is True

    assert judge_result.is_fallback is False
    assert judge_result.verdict.action == "buy"

    gate = DeterministicGate(db=db, settings=settings)
    decision = await gate.evaluate(
        lead=lead,
        dossier=dossier,
        judge_result=judge_result,
        sol_usd_price=75.0,
    )

    assert decision.approved is True
    assert decision.decision == "APPROVE"
    assert decision.kill_reason is None
    assert decision.zone == "EARLY"
    assert decision.approved_size_sol == pytest.approx(0.25)
    assert decision.lead_stage == "GATE"
    assert decision.entry_liquidity_pct is not None and decision.entry_liquidity_pct < 1.0

    # Verify DB persistence in leads and judge_verdicts
    updated_lead = await db.get_lead(lead["id"])
    assert updated_lead is not None
    assert updated_lead["current_stage"] == "GATE"
    assert updated_lead["kill_reason"] is None

    verdicts = await db.get_judge_verdicts_for_lead(lead["id"])
    assert len(verdicts) == 1
    assert verdicts[0]["gate_passed"] == 1
    assert verdicts[0]["action"] == "buy"
    assert verdicts[0]["price_at_verdict"] == pytest.approx(0.00008)

    # Fixed position size invariant: 0.99 confidence enters with EXACT SAME SOL as 0.76
    lead2 = await _seed_lead(
        db,
        mint="Healthy80kHighConf111111111111111111111111",
        symbol="HCONF",
        market_cap=90_000.0,
        liquidity_usd=40_000.0,
        price_usd=0.00009,
    )
    high_conf_verdict = JudgeVerdict(
        organic="yes",
        organic_prob=0.98,
        late="no",
        late_prob=0.05,
        exit_risk=1,
        manipulation_trap="no",
        trap_prob=0.02,
        action="buy",
        action_prob=0.99,
    )
    decision2 = await gate.evaluate(
        lead=lead2,
        dossier=_healthy_80k_dossier(lead2["id"], lead2["mint_address"], "HCONF"),
        judge_result=high_conf_verdict,
        sol_usd_price=75.0,
    )
    assert decision2.approved is True
    assert decision2.approved_size_sol == decision.approved_size_sol == pytest.approx(0.25)


async def test_scenario_2_fake_volume_and_late_dossier_rejected_with_kill_reason(
    tmp_path: Path,
) -> None:
    """Scenario 2: Wash-traded / late dossier is killed by Judge and Gate with exact kill_reason."""
    db = Database(db_path=tmp_path / "phase3_s2.db")
    await db.initialize()
    settings = _make_settings()

    # Sub-case A: Judge identifies manipulation trap + late peak and outputs action="pass"
    mint_a = "WashTrapMintA11111111111111111111111111111"
    lead_a = await _seed_lead(
        db,
        mint=mint_a,
        symbol="WASH",
        market_cap=110_000.0,
        liquidity_usd=25_000.0,
        price_usd=0.00011,
    )
    wash_dossier = TokenDossier(
        lead_id=lead_a["id"],
        mint_address=mint_a,
        symbol="WASH",
        market_cap_usd=110_000.0,
        liquidity_usd=25_000.0,
        price_usd=0.00011,
        volume_5m_usd=95_000.0,
        volume_1h_usd=480_000.0,
        unique_makers_5m=6,
        vol_to_liquidity_ratio_1h=19.2,
        avg_tx_size_5m_usd=1500.0,
        price_change_1h_pct=940.0,
        whale_confirm=False,
    )

    def handler_pass(request: httpx.Request) -> httpx.Response:
        verdict_json = {
            "organic": "no",
            "organic_prob": 0.12,
            "late": "yes",
            "late_prob": 0.91,
            "exit_risk": 4,
            "manipulation_trap": "yes",
            "trap_prob": 0.94,
            "action": "pass",
            "action_prob": 0.95,
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(verdict_json)}}]},
        )

    async with _mock_openrouter_client(handler_pass) as client:
        judge = MuteJudge(settings=settings, http_client=client)
        judge_res_a = await judge.evaluate(wash_dossier)

    gate = DeterministicGate(db=db, settings=settings)
    dec_a = await gate.evaluate(
        lead=lead_a, dossier=wash_dossier, judge_result=judge_res_a, sol_usd_price=75.0
    )

    assert dec_a.approved is False
    assert dec_a.decision == "REJECT"
    assert dec_a.kill_reason == "JUDGE_PASS"
    assert dec_a.lead_stage == "REJECTED"

    persisted_a = await db.get_lead(lead_a["id"])
    assert persisted_a is not None
    assert persisted_a["current_stage"] == "REJECTED"
    assert persisted_a["kill_reason"] == "JUDGE_PASS"

    verdicts_a = await db.get_judge_verdicts_for_lead(lead_a["id"])
    assert len(verdicts_a) == 1
    assert verdicts_a[0]["gate_passed"] == 0
    assert verdicts_a[0]["kill_reason"] == "JUDGE_PASS"

    # Sub-case B: Judge attempts "buy" on $85K Early Zone token, but trap_prob=0.35 (>0.20)
    # -> Deterministic Gate MUST override and kill with MANIPULATION_TRAP.
    mint_b = "TrapBorderlineB11111111111111111111111111"
    lead_b = await _seed_lead(
        db,
        mint=mint_b,
        symbol="TRAPB",
        market_cap=85_000.0,
        liquidity_usd=28_000.0,
        price_usd=0.000085,
    )
    overridden_verdict = JudgeVerdict(
        organic="yes",
        organic_prob=0.80,
        late="no",
        late_prob=0.20,
        exit_risk=2,
        manipulation_trap="no",
        trap_prob=0.35,  # Exceeds Early Zone trap_prob cap of 0.20
        action="buy",
        action_prob=0.82,
    )
    dec_b = await gate.evaluate(
        lead=lead_b,
        dossier=_healthy_80k_dossier(lead_b["id"], mint_b, "TRAPB"),
        judge_result=overridden_verdict,
        sol_usd_price=75.0,
    )
    assert dec_b.approved is False
    assert dec_b.kill_reason == "MANIPULATION_TRAP"
    persisted_b = await db.get_lead(lead_b["id"])
    assert persisted_b is not None
    assert persisted_b["current_stage"] == "REJECTED"
    assert persisted_b["kill_reason"] == "MANIPULATION_TRAP"


async def test_scenario_3_max_concurrent_trades_blocks_fourth_trade(
    tmp_path: Path,
) -> None:
    """Scenario 3: When 3 trades are already OPEN, the 4th is blocked with MAX_CONCURRENT_TRADES."""
    db = Database(db_path=tmp_path / "phase3_s3.db")
    await db.initialize()
    settings = _make_settings()
    now = utc_now_iso()

    # Seed 3 active TUTORIAL trades (OPEN, TP1_HIT, RISK_FREE_RUNNER)
    async with db.connect() as conn:
        for idx, status in enumerate(("OPEN", "TP1_HIT", "RISK_FREE_RUNNER"), start=1):
            await conn.execute(
                """
                INSERT INTO trades (
                    mint_address, symbol, mode, status,
                    invested_sol, invested_usd, entry_price_usd, effective_entry_price_usd,
                    tokens_initial, tokens_remaining, current_price_usd, highest_price_seen, opened_at
                ) VALUES (?, ?, 'TUTORIAL', ?, 0.25, 18.75, 0.001, 0.001015, 18472.0, 18472.0, 0.001, 0.001, ?)
                """,
                (f"OpenMint{idx}11111111111111111111111111111111", f"OPEN{idx}", status, now),
            )
        await conn.commit()

    assert await db.count_open_trades(mode="TUTORIAL") == 3

    fourth_mint = "FourthMint1111111111111111111111111111111"
    fourth_lead = await _seed_lead(
        db,
        mint=fourth_mint,
        symbol="FOURTH",
        market_cap=220_000.0,
        liquidity_usd=60_000.0,
        price_usd=0.00022,
    )
    clean_verdict = JudgeVerdict(
        organic="yes",
        organic_prob=0.90,
        late="no",
        late_prob=0.10,
        exit_risk=1,
        manipulation_trap="no",
        trap_prob=0.05,
        action="buy",
        action_prob=0.92,
    )

    gate = DeterministicGate(db=db, settings=settings)
    decision = await gate.evaluate(
        lead=fourth_lead,
        dossier=_healthy_80k_dossier(fourth_lead["id"], fourth_mint, "FOURTH").model_copy(
            update={"market_cap_usd": 220_000.0, "liquidity_usd": 60_000.0}
        ),
        judge_result=clean_verdict,
        sol_usd_price=75.0,
    )

    assert decision.approved is False
    assert decision.decision == "REJECT"
    assert decision.kill_reason == "MAX_CONCURRENT_TRADES"
    assert decision.lead_stage == "REJECTED"

    persisted = await db.get_lead(fourth_lead["id"])
    assert persisted is not None
    assert persisted["current_stage"] == "REJECTED"
    assert persisted["kill_reason"] == "MAX_CONCURRENT_TRADES"

    verdicts = await db.get_judge_verdicts_for_lead(fourth_lead["id"])
    assert len(verdicts) == 1
    assert verdicts[0]["gate_passed"] == 0
    assert verdicts[0]["kill_reason"] == "MAX_CONCURRENT_TRADES"


async def test_judge_fail_closed_on_timeout_and_prose_plus_gate_edge_cases(
    tmp_path: Path,
) -> None:
    """Verify timeout/prose fail-closed, single-watch rule, liquidity cap, circuit breaker, & missing."""
    # 1. Golden Rule: None coerces literally to "missing"
    dossier = TokenDossier(
        mint_address="MissingMint1111111111111111111111111111111",
        symbol="MISS",
        market_cap_usd=200_000.0,
        liquidity_usd=50_000.0,
        price_usd=0.0002,
        volume_5m_usd=None,
        unique_makers_5m=None,
    )
    assert dossier.volume_5m_usd == "missing"
    assert dossier.unique_makers_5m == "missing"
    assert dossier.numeric_or_none("volume_5m_usd") is None

    settings = _make_settings()

    # 2. Timeout -> automatic pass + JUDGE_TIMEOUT_OR_ERROR
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Simulated OpenRouter timeout", request=request)

    async with _mock_openrouter_client(timeout_handler) as client:
        judge = MuteJudge(settings=settings, http_client=client)
        timeout_res = await judge.evaluate(dossier)
    assert timeout_res.is_fallback is True
    assert timeout_res.verdict.action == "pass"
    assert timeout_res.error_reason == JUDGE_TIMEOUT_OR_ERROR

    # 3. Free-text prose instead of JSON -> automatic pass + JUDGE_TIMEOUT_OR_ERROR
    def prose_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Sure! Here is my analysis: buy it!"}}]},
        )

    async with _mock_openrouter_client(prose_handler) as client:
        judge = MuteJudge(settings=settings, http_client=client)
        prose_res = await judge.evaluate(dossier)
    assert prose_res.is_fallback is True
    assert prose_res.verdict.action == "pass"
    assert prose_res.error_reason == JUDGE_TIMEOUT_OR_ERROR

    # 4. Single watch -> recheck in 4 min; second watch -> SECOND_WATCH_KILL
    db = Database(db_path=tmp_path / "phase3_edges.db")
    await db.initialize()
    gate = DeterministicGate(db=db, settings=settings)

    watch_lead = await _seed_lead(
        db,
        mint="WatchMint111111111111111111111111111111111",
        symbol="WTCH",
        market_cap=200_000.0,
        liquidity_usd=50_000.0,
        price_usd=0.0002,
    )
    watch_verdict = JudgeVerdict(
        organic="yes",
        organic_prob=0.72,
        late="yes",
        late_prob=0.55,
        exit_risk=2,
        manipulation_trap="no",
        trap_prob=0.15,
        action="watch",
        action_prob=0.68,
    )
    t0 = datetime(2026, 11, 24, 0, 0, 0, tzinfo=timezone.utc)
    w1 = await gate.evaluate(
        lead=watch_lead, dossier=dossier, judge_result=watch_verdict, sol_usd_price=75.0, now=t0
    )
    assert w1.decision == "WATCH"
    assert w1.approved is False
    assert w1.recheck_at == (t0 + timedelta(minutes=4)).isoformat()

    w2 = await gate.evaluate(
        lead=watch_lead,
        dossier=dossier,
        judge_result=watch_verdict,
        sol_usd_price=75.0,
        now=t0 + timedelta(minutes=4),
    )
    assert w2.decision == "REJECT"
    assert w2.kill_reason == "SECOND_WATCH_KILL"
    assert w2.lead_stage == "REJECTED"

    # 5. Entry > 1.0% of pool liquidity -> ENTRY_EXCEEDS_LIQUIDITY_CAP
    # 0.25 SOL * $100 = $25.00; pool = $2,000 -> 1.25% > 1.0%
    thin_lead = await _seed_lead(
        db,
        mint="ThinPoolMint111111111111111111111111111111",
        symbol="THIN",
        market_cap=200_000.0,
        liquidity_usd=2_000.0,
        price_usd=0.0002,
    )
    thin_dossier = dossier.model_copy(
        update={"mint_address": thin_lead["mint_address"], "symbol": "THIN", "liquidity_usd": 2_000.0}
    )
    thin_dec = await gate.evaluate(
        lead=thin_lead,
        dossier=thin_dossier,
        judge_result=JudgeVerdict(
            organic="yes",
            organic_prob=0.90,
            late="no",
            late_prob=0.10,
            exit_risk=2,
            manipulation_trap="no",
            trap_prob=0.05,
            action="buy",
            action_prob=0.90,
        ),
        sol_usd_price=100.0,
    )
    assert thin_dec.approved is False
    assert thin_dec.kill_reason == "ENTRY_EXCEEDS_LIQUIDITY_CAP"

    # 6. Daily loss >= 10% pauses bot and rejects with DAILY_LOSS_LIMIT
    await db.update_bot_state(daily_pnl_sol=-0.50)  # -0.50 / 5.0 bankroll = 10.0%
    cb_lead = await _seed_lead(
        db,
        mint="CircuitMint1111111111111111111111111111111",
        symbol="CBRK",
        market_cap=200_000.0,
        liquidity_usd=50_000.0,
        price_usd=0.0002,
    )
    cb_dec = await gate.evaluate(
        lead=cb_lead,
        dossier=dossier,
        judge_result=JudgeVerdict(
            organic="yes",
            organic_prob=0.90,
            late="no",
            late_prob=0.10,
            exit_risk=1,
            manipulation_trap="no",
            trap_prob=0.05,
            action="buy",
            action_prob=0.90,
        ),
        sol_usd_price=75.0,
    )
    assert cb_dec.approved is False
    assert cb_dec.kill_reason == "DAILY_LOSS_LIMIT"
    state_after = await db.get_bot_state()
    assert state_after["is_paused"] == 1
