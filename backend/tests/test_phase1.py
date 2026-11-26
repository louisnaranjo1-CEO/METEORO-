"""Phase 1 Verification Suite: SQLite WAL, SOL/USD Oracle, Blacklist, Fragmented LP, Live SEARCH+RISK."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from src.config import get_settings
from src.database import Database, utc_now_iso
from src.http_client import ResilientHttpClient
from src.price_oracle import SolPriceOracle
from src.risk import HardRiskFilter
from src.search import SearchScanner


async def test_sol_usd_oracle_live_and_cached() -> None:
    """1. Verify the SOL/USD price oracle returns a valid positive live price and caches it."""
    async with ResilientHttpClient(timeout=10.0, max_retries=2) as http:
        oracle = SolPriceOracle(http_client=http, cache_ttl_seconds=30.0)
        quote1 = await oracle.get_quote(force_refresh=True)
        quote2 = await oracle.get_quote(force_refresh=False)

        assert quote1.price_usd > 0.0
        assert 5.0 <= quote1.price_usd <= 50_000.0
        assert quote1.fetched_at_monotonic == quote2.fetched_at_monotonic
        converted_usd = await oracle.sol_to_usd(1.0)
        assert converted_usd == pytest.approx(quote1.price_usd, rel=1e-4)

        print(
            f"\n[ORACLE] SOL/USD = ${quote1.price_usd:.2f} "
            f"(source={quote1.source}, fallback={quote1.is_fallback})"
        )


async def test_blacklist_dev_rejects_immediately_and_survives_tutorial_reset(
    tmp_path: Path,
) -> None:
    """2. Verify blacklist_dev rejects instantly with BLACKLISTED_DEV and survives tutorial reset."""
    db = Database(db_path=tmp_path / "test_blacklist.db")
    await db.initialize()

    # Confirm WAL mode is active
    async with db.connect() as conn:
        cursor = await conn.execute("PRAGMA journal_mode;")
        mode_row = await cursor.fetchone()
        assert str(mode_row[0]).lower() == "wal"

    malicious_dev = "BadDevWallet1111111111111111111111111111111"
    await db.blacklist_dev(
        malicious_dev,
        reason="SERIAL_RUGGER",
        mint_address="RugMint11111111111111111111111111111111111",
    )

    lead = await db.create_lead(
        mint_address="NewTokenFromBadDev111111111111111111111111",
        symbol="RUGX",
        name="RugX Token",
        pair_address="PairRugX111111111111111111111111111111111",
        market_cap=120_000.0,
        liquidity_usd=25_000.0,
        main_pool_share=1.0,
        dev_wallet=malicious_dev,
        price_usd=0.0012,
    )

    risk_filter = HardRiskFilter(db=db)
    try:
        # No network call should even be attempted because dev_wallet is already known & blacklisted
        result = await risk_filter.evaluate_lead(lead)
    finally:
        await risk_filter.aclose()

    assert result.passed is False
    assert result.stage == "REJECTED"
    assert result.kill_reason == "BLACKLISTED_DEV"

    persisted = await db.get_lead(lead["id"])
    assert persisted is not None
    assert persisted["current_stage"] == "REJECTED"
    assert persisted["kill_reason"] == "BLACKLISTED_DEV"

    # Also verify reset_tutorial_account cleans TUTORIAL trades only, keeping LIVE & blacklisted_devs
    now = utc_now_iso()
    async with db.connect() as conn:
        await conn.execute(
            """
            INSERT INTO trades (
                lead_id, mint_address, symbol, mode, status,
                invested_sol, invested_usd, entry_price_usd, effective_entry_price_usd,
                tokens_initial, tokens_remaining, current_price_usd, highest_price_seen, opened_at
            ) VALUES (?, ?, ?, 'TUTORIAL', 'OPEN', 0.25, 45.0, 0.01, 0.01015, 4433.0, 4433.0, 0.01, 0.01, ?)
            """,
            (lead["id"], "TutMint111", "TUT", now),
        )
        await conn.execute(
            """
            INSERT INTO trades (
                lead_id, mint_address, symbol, mode, status,
                invested_sol, invested_usd, entry_price_usd, effective_entry_price_usd,
                tokens_initial, tokens_remaining, current_price_usd, highest_price_seen, opened_at
            ) VALUES (?, ?, ?, 'LIVE', 'OPEN', 0.25, 45.0, 0.01, 0.01015, 4433.0, 4433.0, 0.01, 0.01, ?)
            """,
            (lead["id"], "LiveMint111", "LIVE", now),
        )
        await conn.commit()

    state = await db.reset_tutorial_account(10.0)
    assert state["tutorial_bankroll_sol"] == 10.0
    assert state["tutorial_available_sol"] == 10.0
    assert await db.is_dev_blacklisted(malicious_dev) is not None

    async with db.connect() as conn:
        cur_tut = await conn.execute("SELECT COUNT(*) FROM trades WHERE mode = 'TUTORIAL'")
        cur_live = await conn.execute("SELECT COUNT(*) FROM trades WHERE mode = 'LIVE'")
        assert (await cur_tut.fetchone())[0] == 0
        assert (await cur_live.fetchone())[0] == 1


async def test_fragmented_liquidity_filter_with_50_pct_main_pool(tmp_path: Path) -> None:
    """3. Mock DexScreener pairs where main pool holds only 50% -> must reject FRAGMENTED_LIQUIDITY."""
    db = Database(db_path=tmp_path / "test_fragmented.db")
    await db.initialize()

    mint = "FragMint1111111111111111111111111111111111"
    lead = await db.create_lead(
        mint_address=mint,
        symbol="FRAG",
        name="Fragmented Coin",
        pair_address="PairA111111111111111111111111111111111111",
        market_cap=200_000.0,
        liquidity_usd=20_000.0,
        price_usd=0.002,
    )

    mock_pairs = [
        {
            "chainId": "solana",
            "pairAddress": "PairA111111111111111111111111111111111111",
            "baseToken": {"address": mint, "symbol": "FRAG"},
            "quoteToken": {"address": "So11111111111111111111111111111111111111112", "symbol": "SOL"},
            "liquidity": {"usd": 20_000.0},
        },
        {
            "chainId": "solana",
            "pairAddress": "PairB222222222222222222222222222222222222",
            "baseToken": {"address": mint, "symbol": "FRAG"},
            "quoteToken": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC"},
            "liquidity": {"usd": 20_000.0},
        },
    ]

    risk_filter = HardRiskFilter(db=db)
    try:
        result = await risk_filter.evaluate_lead(lead, dex_pairs_override=mock_pairs)
    finally:
        await risk_filter.aclose()

    assert result.passed is False
    assert result.stage == "REJECTED"
    assert result.kill_reason == "FRAGMENTED_LIQUIDITY"
    assert result.main_pool_share == pytest.approx(0.50, abs=1e-4)

    persisted = await db.get_lead(lead["id"])
    assert persisted is not None
    assert persisted["current_stage"] == "REJECTED"
    assert persisted["kill_reason"] == "FRAGMENTED_LIQUIDITY"
    assert persisted["main_pool_share"] == pytest.approx(0.50, abs=1e-4)


async def test_live_search_and_risk_cycle_into_desk_db() -> None:
    """4. Run a real SEARCH + RISK cycle against DexScreener and RugCheck into backend/data/desk.db."""
    settings = get_settings()
    db = Database(settings=settings)
    await db.initialize()
    assert db.db_path.name == "desk.db"

    async with ResilientHttpClient(timeout=12.0, max_retries=3) as http:
        scanner = SearchScanner(db=db, http_client=http, settings=settings)
        risk_filter = HardRiskFilter(db=db, http_client=http, settings=settings)

        leads = await scanner.scan(max_candidates=12)

        # If desk.db already has recent leads from a re-run within 60 minutes, evaluate pending SEARCH leads too
        if not leads:
            leads = await db.list_leads(stage="SEARCH", limit=12)

        results = []
        for lead in leads:
            res = await risk_filter.evaluate_lead(lead)
            results.append(res)

    rejections = Counter(r.kill_reason for r in results if not r.passed)
    survivors = [r for r in results if r.passed]

    print("\n" + "=" * 72)
    print("PHASE 1 LIVE CYCLE SUMMARY (DexScreener SEARCH + RugCheck RISK)")
    print("=" * 72)
    print(f"Database path      : {db.db_path}")
    print(f"Detected / audited : {len(results)}")
    print(f"Rejected total     : {len(results) - len(survivors)}")
    if rejections:
        print("Rejections by reason:")
        for reason, count in rejections.most_common():
            print(f"  - {reason}: {count}")
    print(f"Survivors (RISK)   : {len(survivors)}")
    for s in survivors:
        print(
            f"  * {s.symbol} ({s.mint_address[:8]}...) | "
            f"Liq=${(s.liquidity_usd or 0):,.0f} | "
            f"MainPool={(s.main_pool_share or 0)*100:.1f}% | "
            f"Top10={(s.top10_pct or 0):.2f}% | "
            f"Dev={(s.dev_pct or 0):.2f}% | "
            f"LP_Lock={(s.lp_locked_pct or 0):.1f}% | "
            f"Holders={s.holders_count}"
        )
    print("=" * 72)

    assert db.db_path.exists()
    # Ensure either new leads were discovered & audited, or desk.db already holds audited leads
    total_in_db = await db.list_leads(limit=50)
    assert len(total_in_db) > 0, "Expected at least one Solana token candidate persisted in desk.db"
