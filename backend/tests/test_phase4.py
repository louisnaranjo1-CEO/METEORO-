"""Phase 4 Verification Suite: TutorialEngine, XRadar, VolumetricExitManager, SundayAudit, & Recovery."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.config import Settings
from src.database import Database
from src.execution.base import InsufficientBalanceError
from src.execution.tutorial_engine import TutorialEngine
from src.exit_manager import MarketSnapshot, VolumetricExitManager
from src.gate import GateDecision
from src.judge import JudgeVerdict
from src.orchestrator import TradingOrchestrator
from src.sunday_audit import SundayAuditor
from src.telegram_bot import TelegramNotifier
from src.x_radar import XRadar


def _make_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,
        tutorial_initial_bankroll_sol=5.0,
        trade_size_sol=0.25,
        sim_priority_fee_sol=0.0005,
        sim_jito_tip_sol=0.002,
        sim_dex_fee_pct=0.25,
        sim_buy_slippage_pct=1.5,
        sim_tp_slippage_pct=2.0,
        sim_sl_slippage_pct=8.0,
        **overrides,
    )


async def _seed_lead(
    db: Database,
    *,
    mint: str,
    symbol: str,
    price_usd: float = 0.001,
    liquidity_usd: float = 40_000.0,
    market_cap: float = 120_000.0,
    dev_wallet: str | None = None,
) -> dict[str, Any]:
    return await db.create_lead(
        mint_address=mint,
        symbol=symbol,
        name=f"{symbol} Coin",
        pair_address=f"Pair{symbol}11111111111111111111111111111",
        current_stage="GATE",
        market_cap=market_cap,
        liquidity_usd=liquidity_usd,
        main_pool_share=1.0,
        holders_count=350,
        dev_wallet=dev_wallet,
        price_usd=price_usd,
    )


async def test_1_tutorial_engine_buy_deducts_exact_sol_fees_and_slippage(
    tmp_path: Path,
) -> None:
    """1. Buying 0.25 SOL deducts exact amount + priority + Jito tip + DEX fee + 1.5% buy slippage."""
    db = Database(db_path=tmp_path / "phase4_t1.db")
    await db.initialize()
    settings = _make_settings()
    engine = TutorialEngine(db=db, settings=settings)

    lead = await _seed_lead(
        db,
        mint="BuyMathMint1111111111111111111111111111111",
        symbol="MATH",
        price_usd=0.0010,
        liquidity_usd=50_000.0,
    )

    sol_usd = 100.0
    receipt = await engine.execute_buy(lead, 0.25, sol_usd_price=sol_usd)

    # Exact debit from virtual SOL bankroll: 0.25 + 0.0005 (priority) + 0.002 (Jito) = 0.2525 SOL
    bot_state = await db.get_bot_state()
    assert bot_state["tutorial_available_sol"] == pytest.approx(5.0 - 0.2525, abs=1e-9)

    trade = receipt.trade
    assert trade["mode"] == "TUTORIAL"
    assert trade["status"] == "OPEN"
    assert trade["invested_sol"] == pytest.approx(0.2525, abs=1e-9)
    assert trade["invested_usd"] == pytest.approx(25.25, abs=1e-4)

    # Effective entry price penalized by +1.5% buy slippage
    assert trade["entry_price_usd"] == pytest.approx(0.0010, abs=1e-9)
    assert trade["effective_entry_price_usd"] == pytest.approx(0.001015, abs=1e-9)

    # DEX fee = 0.25 * 0.25% = 0.000625; total buy fees = 0.0025 + 0.000625 = 0.003125 SOL
    expected_dex_fee = 0.25 * 0.0025
    expected_buy_fees = 0.0025 + expected_dex_fee
    net_swap_sol = 0.25 - expected_dex_fee
    expected_tokens = (net_swap_sol * sol_usd) / 0.001015
    expected_slip_sol = net_swap_sol * (0.015 / 1.015)

    assert trade["fees_paid_sol"] == pytest.approx(expected_buy_fees, abs=1e-9)
    assert trade["slippage_cost_sol"] == pytest.approx(expected_slip_sol, abs=1e-9)
    assert trade["tokens_initial"] == pytest.approx(expected_tokens, rel=1e-9)
    assert trade["tokens_remaining"] == pytest.approx(expected_tokens, rel=1e-9)
    assert trade["pnl_sol"] == pytest.approx(-(expected_buy_fees + expected_slip_sol), abs=1e-8)

    events = await db.get_trade_events(trade["id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "BUY"
    assert events[0]["sol_amount"] == pytest.approx(0.2525, abs=1e-9)

    # Insufficient balance guard
    await db.update_bot_state(tutorial_available_sol=0.251)
    with pytest.raises(InsufficientBalanceError):
        await engine.execute_buy(lead, 0.25, sol_usd_price=sol_usd)


async def test_2_lifecycle_tp1_45_runner_110_and_stagnation_25pct_vol_reconciles_bankroll(
    tmp_path: Path,
) -> None:
    """2. Simulate +45% TP1 -> +110% RISK_FREE_RUNNER -> Stagnation at 25% peak volume."""
    db = Database(db_path=tmp_path / "phase4_t2.db")
    await db.initialize()
    settings = _make_settings()
    engine = TutorialEngine(db=db, settings=settings)
    exit_mgr = VolumetricExitManager(db=db, engine=engine, settings=settings)

    mint = "RunnerMint11111111111111111111111111111111"
    lead = await _seed_lead(
        db,
        mint=mint,
        symbol="RUNR",
        price_usd=0.0010,
        liquidity_usd=40_000.0,
    )
    sol_usd = 100.0
    buy_receipt = await engine.execute_buy(lead, 0.25, sol_usd_price=sol_usd)
    trade = buy_receipt.trade
    t0 = datetime.fromisoformat(trade["opened_at"])

    # Step A (+2 min): +45% -> TP1 sells 50%, moves status to TP1_HIT
    res_tp1 = await exit_mgr.evaluate_trade(
        trade,
        MarketSnapshot(
            mint_address=mint,
            price_usd=0.00145,
            liquidity_usd=48_000.0,
            volume_5m_usd=25_000.0,
            buys_5m=60,
            sells_5m=22,
            observed_at=t0 + timedelta(minutes=2),
        ),
        sol_usd_price=sol_usd,
    )
    assert res_tp1.action == "TP1"
    assert res_tp1.new_status == "TP1_HIT"
    mid_trade = await db.get_trade(trade["id"])
    assert mid_trade is not None
    assert mid_trade["tokens_remaining"] == pytest.approx(trade["tokens_initial"] * 0.5, rel=1e-9)

    # Step B (+5 min): +110% (2.1x) -> recovers 100% of invested SOL and transitions to RISK_FREE_RUNNER
    res_2x = await exit_mgr.evaluate_trade(
        mid_trade,
        MarketSnapshot(
            mint_address=mint,
            price_usd=0.00210,
            liquidity_usd=65_000.0,
            volume_5m_usd=40_000.0,  # sets peak_volume_5m = 40,000
            buys_5m=110,
            sells_5m=35,
            observed_at=t0 + timedelta(minutes=5),
        ),
        sol_usd_price=sol_usd,
    )
    assert res_2x.action == "RISK_FREE_RUNNER"
    assert res_2x.new_status == "RISK_FREE_RUNNER"
    runner_trade = await db.get_trade(trade["id"])
    assert runner_trade is not None
    assert runner_trade["capital_recovered"] == 1
    assert float(runner_trade["realized_sol"]) >= float(runner_trade["invested_sol"])
    assert float(runner_trade["tokens_remaining"]) > 0.0
    assert float(runner_trade["peak_volume_5m"]) == pytest.approx(40_000.0)

    # Step C (+26 min -> >20m in runner): flat price (<8% band) + vol dropped to 25% of peak ($10K/$40K)
    res_stag = await exit_mgr.evaluate_trade(
        runner_trade,
        MarketSnapshot(
            mint_address=mint,
            price_usd=0.00208,
            liquidity_usd=62_000.0,
            volume_5m_usd=10_000.0,  # 25% of 40,000 peak (< 35% floor)
            buys_5m=12,
            sells_5m=11,
            observed_at=t0 + timedelta(minutes=26),
        ),
        sol_usd_price=sol_usd,
    )
    assert res_stag.action == "CLOSED"
    assert res_stag.reason == "STAGNATION_LOW_VOLUME"
    assert res_stag.new_status == "CLOSED"

    closed_trade = await db.get_trade(trade["id"])
    assert closed_trade is not None
    assert closed_trade["status"] == "CLOSED"
    assert closed_trade["exit_reason"] == "STAGNATION_LOW_VOLUME"
    assert float(closed_trade["tokens_remaining"]) == 0.0
    assert float(closed_trade["pnl_sol"]) > 0.0

    # Exact bankroll reconciliation to the decimal
    final_state = await db.get_bot_state()
    expected_avail = 5.0 + float(closed_trade["pnl_sol"])
    assert float(final_state["tutorial_available_sol"]) == pytest.approx(expected_avail, abs=1e-9)
    assert float(final_state["daily_pnl_sol"]) == pytest.approx(float(closed_trade["pnl_sol"]), abs=1e-9)

    events = await db.get_trade_events(trade["id"])
    assert [e["event_type"] for e in events] == ["BUY", "TP1", "TP2", "STAGNATION_LOW_VOLUME"]


async def test_3_liquidity_panic_35pct_drop_at_min_5_closes_and_blacklists_dev(
    tmp_path: Path,
) -> None:
    """3. Liquidity dropping 35% at minute 5 triggers LIQUIDITY_PANIC (8% slip) & blacklists dev."""
    db = Database(db_path=tmp_path / "phase4_t3.db")
    await db.initialize()
    settings = _make_settings()
    engine = TutorialEngine(db=db, settings=settings)
    exit_mgr = VolumetricExitManager(db=db, engine=engine, settings=settings)

    bad_dev = "RuggerDevWallet99999999999999999999999999"
    mint = "PanicMint11111111111111111111111111111111"
    lead = await _seed_lead(
        db,
        mint=mint,
        symbol="RUGP",
        price_usd=0.0010,
        liquidity_usd=40_000.0,
        dev_wallet=bad_dev,
    )
    sol_usd = 100.0
    buy_receipt = await engine.execute_buy(lead, 0.25, sol_usd_price=sol_usd)
    trade = buy_receipt.trade
    t0 = datetime.fromisoformat(trade["opened_at"])

    # Minute 5: liquidity drops 35% ($40,000 -> $26,000)
    res_panic = await exit_mgr.evaluate_trade(
        trade,
        MarketSnapshot(
            mint_address=mint,
            price_usd=0.00078,
            liquidity_usd=26_000.0,
            volume_5m_usd=32_000.0,
            buys_5m=10,
            sells_5m=85,
            observed_at=t0 + timedelta(minutes=5),
        ),
        sol_usd_price=sol_usd,
    )

    assert res_panic.action == "CLOSED"
    assert res_panic.reason == "LIQUIDITY_PANIC"
    assert res_panic.new_status == "CLOSED"
    assert res_panic.dev_blacklisted is True

    # Confirm dev added automatically to blacklisted_devs
    banned = await db.is_dev_blacklisted(bad_dev)
    assert banned is not None
    assert banned["offending_mint"] == mint

    # Confirm emergency 8% slippage was applied on the sell event
    events = await db.get_trade_events(trade["id"])
    assert events[-1]["event_type"] == "LIQUIDITY_PANIC"
    assert events[-1]["effective_price_usd"] == pytest.approx(0.00078 * 0.92, rel=1e-9)

    closed = await db.get_trade(trade["id"])
    assert closed is not None
    state = await db.get_bot_state()
    assert float(state["tutorial_available_sol"]) == pytest.approx(
        5.0 + float(closed["pnl_sol"]), abs=1e-9
    )


async def test_x_radar_trailing_boost_vs_unbreakable_volume_override_and_stop_loss(
    tmp_path: Path,
) -> None:
    """Verify Rule 1 (-22% SL), X Radar 3-min cache + 24% trailing boost, & SELL_RATIO_KILL override."""
    db = Database(db_path=tmp_path / "phase4_x_vol.db")
    await db.initialize()
    settings = _make_settings(
        enable_x_context_on_runner=True,
        x_api_url="https://mock-x.local/twitter/tweet/advanced_search",
        x_api_key="test-x-key",
    )

    # A. Rule 1: Initial Stop Loss -22%
    engine = TutorialEngine(db=db, settings=settings)
    lead_sl = await _seed_lead(
        db, mint="StopLossMint1111111111111111111111111111", symbol="SLX", price_usd=0.0010
    )
    buy_sl = await engine.execute_buy(lead_sl, 0.25, sol_usd_price=100.0)
    exit_mgr_plain = VolumetricExitManager(db=db, engine=engine, settings=settings)
    t0_sl = datetime.fromisoformat(buy_sl.trade["opened_at"])
    sl_res = await exit_mgr_plain.evaluate_trade(
        buy_sl.trade,
        MarketSnapshot(
            mint_address=lead_sl["mint_address"],
            price_usd=0.00077,  # -23% <= -22%
            liquidity_usd=38_000.0,
            volume_5m_usd=15_000.0,
            buys_5m=20,
            sells_5m=35,
            observed_at=t0_sl + timedelta(minutes=3),
        ),
        sol_usd_price=100.0,
    )
    assert sl_res.reason == "STOP_LOSS"
    assert (await db.get_bot_state())["daily_sl_count"] == 1

    # B. X Radar cache (3-min TTL) + trailing widening (18% -> 24%) + Volume Override (SELL_RATIO_KILL)
    api_calls = 0

    def x_handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        api_calls += 1
        return httpx.Response(
            200,
            json={
                "tweets": [
                    {
                        "id": "999",
                        "text": "Watching $XRUN closely!",
                        "author": {
                            "userName": "elonmusk",
                            "followers": 200_000_000,
                            "isBlueVerified": True,
                            "description": "Founder and CEO",
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(x_handler)) as x_client:
        x_radar = XRadar(db=db, http_client=x_client, settings=settings)
        exit_mgr_x = VolumetricExitManager(
            db=db, engine=engine, x_radar=x_radar, settings=settings
        )

        mint_x = "XRunnerMint11111111111111111111111111111"
        lead_x = await _seed_lead(
            db, mint=mint_x, symbol="XRUN", price_usd=0.0010, liquidity_usd=60_000.0
        )
        buy_x = await engine.execute_buy(lead_x, 0.25, sol_usd_price=100.0)
        tx0 = datetime.fromisoformat(buy_x.trade["opened_at"])

        # Push to +150% ($0.0025) -> RISK_FREE_RUNNER, peak_vol = 50,000
        r_runner = await exit_mgr_x.evaluate_trade(
            buy_x.trade,
            MarketSnapshot(
                mint_address=mint_x,
                price_usd=0.0025,
                liquidity_usd=80_000.0,
                volume_5m_usd=50_000.0,
                buys_5m=120,
                sells_5m=40,
                observed_at=tx0 + timedelta(minutes=3),
            ),
            sol_usd_price=100.0,
        )
        assert r_runner.new_status == "RISK_FREE_RUNNER"

        # Pullback -20% from peak ($0.0020) WITH healthy volume (>50% peak & buys >= sells)
        # Normal 18% trailing would close, but X catalyst widens trailing to 24% -> HOLD!
        t_runner = await db.get_trade(buy_x.trade["id"])
        assert t_runner is not None
        r_hold = await exit_mgr_x.evaluate_trade(
            t_runner,
            MarketSnapshot(
                mint_address=mint_x,
                price_usd=0.0020,  # -20% from 0.0025 peak
                liquidity_usd=78_000.0,
                volume_5m_usd=35_000.0,  # 70% of peak (>50%)
                buys_5m=65,
                sells_5m=50,
                observed_at=tx0 + timedelta(minutes=4),
            ),
            sol_usd_price=100.0,
        )
        assert r_hold.action == "HOLD"
        assert r_hold.x_trailing_widened is True
        assert api_calls == 1

        # Second call within 3 minutes hits cache (api_calls stays 1!)
        # Price is above trailing stop (-12% from peak), but sells/buys >= 3:1 for 2 cycles
        # -> MUST close via SELL_RATIO_KILL despite X hype!
        for cycle in (1, 2):
            r_sell = await exit_mgr_x.evaluate_trade(
                t_runner,
                MarketSnapshot(
                    mint_address=mint_x,
                    price_usd=0.0022,  # -12% from 0.0025 peak (safe from 18% trailing)
                    liquidity_usd=76_000.0,
                    volume_5m_usd=32_000.0,
                    buys_5m=10,
                    sells_5m=35,  # 3.5:1 sell pressure
                    observed_at=tx0 + timedelta(minutes=4, seconds=15 * cycle),
                ),
                sol_usd_price=100.0,
            )
            if cycle == 1:
                assert r_sell.action == "HOLD"
                assert r_sell.x_trailing_widened is False
            else:
                assert r_sell.action == "CLOSED"
                assert r_sell.reason == "SELL_RATIO_KILL"
        assert api_calls == 1


async def test_sunday_audit_crash_recovery_and_semi_auto_approval(
    tmp_path: Path,
) -> None:
    """Verify Sunday calibration buckets + PASS audit, Crash Recovery, and semi-auto 90s flow."""
    db = Database(db_path=tmp_path / "phase4_audit_recovery.db")
    await db.initialize()
    settings = _make_settings()
    engine = TutorialEngine(db=db, settings=settings)

    # 1. Seed Sunday calibration data: two closed BUY trades in different buckets + two PASS verdicts
    lead_win = await _seed_lead(
        db, mint="WinMint111111111111111111111111111111111", symbol="WIN", price_usd=0.001
    )
    buy_win = await engine.execute_buy(lead_win, 0.25, sol_usd_price=100.0)
    await engine.execute_sell(
        buy_win.trade, 100.0, "TRAILING", is_emergency=False, spot_price_usd=0.002, sol_usd_price=100.0
    )
    await db.record_judge_verdict(
        lead_id=lead_win["id"],
        dossier_json="{}",
        organic="yes",
        organic_prob=0.88,
        late="no",
        late_prob=0.10,
        exit_risk=2,
        manipulation_trap="no",
        trap_prob=0.05,
        action="buy",
        action_prob=0.84,  # bucket 0.80-0.89
        gate_passed=True,
        kill_reason=None,
        price_at_verdict=0.001,
    )

    lead_loss = await _seed_lead(
        db, mint="LossMint11111111111111111111111111111111", symbol="LOSS", price_usd=0.001
    )
    buy_loss = await engine.execute_buy(lead_loss, 0.25, sol_usd_price=100.0)
    await engine.execute_sell(
        buy_loss.trade, 100.0, "STOP_LOSS", is_emergency=True, spot_price_usd=0.00075, sol_usd_price=100.0
    )
    await db.record_judge_verdict(
        lead_id=lead_loss["id"],
        dossier_json="{}",
        organic="yes",
        organic_prob=0.76,
        late="no",
        late_prob=0.25,
        exit_risk=3,
        manipulation_trap="no",
        trap_prob=0.15,
        action="buy",
        action_prob=0.75,  # bucket 0.70-0.79
        gate_passed=True,
        kill_reason=None,
        price_at_verdict=0.001,
    )

    # Two PASS verdicts (1 collapsed >50%, 1 pumped)
    lead_p1 = await _seed_lead(db, mint="PassDead1111", symbol="PDEAD", price_usd=0.002)
    await db.record_judge_verdict(
        lead_id=lead_p1["id"],
        dossier_json="{}",
        organic="no",
        organic_prob=0.2,
        late="yes",
        late_prob=0.8,
        exit_risk=4,
        manipulation_trap="yes",
        trap_prob=0.8,
        action="pass",
        action_prob=0.9,
        gate_passed=False,
        kill_reason="JUDGE_PASS",
        price_at_verdict=0.002,
        price_24h_later=0.0004,  # dropped 80% (>= 50%)
    )
    lead_p2 = await _seed_lead(db, mint="PassAlive111", symbol="PALV", price_usd=0.002)
    await db.record_judge_verdict(
        lead_id=lead_p2["id"],
        dossier_json="{}",
        organic="no",
        organic_prob=0.3,
        late="yes",
        late_prob=0.7,
        exit_risk=3,
        manipulation_trap="no",
        trap_prob=0.3,
        action="pass",
        action_prob=0.75,
        gate_passed=False,
        kill_reason="JUDGE_PASS",
        price_at_verdict=0.002,
        price_24h_later=0.003,
    )

    auditor = SundayAuditor(db=db, settings=settings)
    try:
        report = await auditor.generate_weekly_report(days=7, mode="TUTORIAL")
    finally:
        await auditor.aclose()

    buckets = {b.label: b for b in report.buy_buckets}
    assert buckets["0.70-0.79"].win_rate_pct == pytest.approx(0.0)
    assert buckets["0.80-0.89"].win_rate_pct == pytest.approx(100.0)
    assert report.pass_audit.total_pass_verdicts == 2
    assert report.pass_audit.evaluated_24h_count == 2
    assert report.pass_audit.died_or_dropped_50pct_count == 1
    assert report.pass_audit.saved_rate_pct == pytest.approx(50.0)

    # 2. Crash Recovery + Semi-Auto Mode
    open_lead = await _seed_lead(
        db, mint="CrashMint1111111111111111111111111111111", symbol="CRSH", price_usd=0.001
    )
    open_receipt = await engine.execute_buy(open_lead, 0.25, sol_usd_price=100.0)

    sent_messages: list[str] = []

    def tg_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        sent_messages.append(body["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent_messages)}})

    tg_settings = _make_settings(telegram_bot_token="bot-token", telegram_chat_id="123456")
    async with httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)) as tg_client:
        notifier = TelegramNotifier(http_client=tg_client, settings=tg_settings)
        orch = TradingOrchestrator(db=db, settings=tg_settings, notifier=notifier)
        try:
            recovered = await orch.recover_open_positions()
            assert len(recovered) == 1
            assert int(recovered[0]["id"]) == int(open_receipt.trade["id"])
            assert int(open_receipt.trade["id"]) in orch.exit_manager._runtime

            # Semi-auto mode: approved lead stays in PENDING_APPROVAL until clicked or 90s timeout
            await db.update_bot_state(semi_auto_mode=1)
            semi_lead = await _seed_lead(
                db, mint="SemiMint11111111111111111111111111111111", symbol="SEMI", price_usd=0.001
            )
            t_now = datetime(2026, 11, 24, 1, 0, 0, tzinfo=timezone.utc)
            res_semi = await orch.handle_approved_lead(
                semi_lead,
                GateDecision(
                    approved=True,
                    decision="APPROVE",
                    kill_reason=None,
                    approved_size_sol=0.25,
                    recheck_at=None,
                    verdict_id=99,
                    lead_stage="PENDING_APPROVAL",
                    zone="EARLY",
                    entry_liquidity_pct=0.05,
                ),
                JudgeVerdict(
                    organic="yes",
                    organic_prob=0.9,
                    late="no",
                    late_prob=0.1,
                    exit_risk=1,
                    manipulation_trap="no",
                    trap_prob=0.05,
                    action="buy",
                    action_prob=0.9,
                ),
                now=t_now,
            )
            assert res_semi is None
            assert semi_lead["id"] in orch.pending_approvals
            assert (await db.get_lead(semi_lead["id"]))["current_stage"] == "PENDING_APPROVAL"

            # Expire after 91 seconds -> REJECTED (APPROVAL_TIMEOUT)
            expired = await orch.expire_pending_approvals(now=t_now + timedelta(seconds=91))
            assert expired == [semi_lead["id"]]
            assert (await db.get_lead(semi_lead["id"]))["kill_reason"] == "APPROVAL_TIMEOUT"
            assert any("[SEMI-AUTO]" in msg for msg in sent_messages)
        finally:
            await orch.aclose()
