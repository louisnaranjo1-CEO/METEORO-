"""Unit tests for Phase 0 configuration and safety locks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import PROJECT_ROOT, Settings, TradingMode


def test_env_example_loads_cleanly() -> None:
    """Ensure `.env.example` parses with zero errors and matches Phase 0 defaults."""
    env_example = PROJECT_ROOT / ".env.example"
    assert env_example.exists(), ".env.example must exist at project root"

    cfg = Settings(_env_file=str(env_example))
    assert cfg.trading_mode == TradingMode.TUTORIAL
    assert cfg.enable_live_execution is False
    assert cfg.is_live_mode is False
    assert cfg.tutorial_initial_bankroll_sol == 5.0
    assert cfg.trade_size_sol == 0.25
    assert cfg.sim_dex_fee_pct == 0.25
    assert cfg.sim_priority_fee_sol == 0.0005
    assert cfg.sim_jito_tip_sol == 0.002
    assert cfg.sim_buy_slippage_pct == 1.5
    assert cfg.sim_tp_slippage_pct == 2.0
    assert cfg.sim_sl_slippage_pct == 8.0
    assert cfg.min_market_cap_usd == 50_000.0
    assert cfg.max_market_cap_usd == 1_500_000.0
    assert cfg.min_liquidity_usd == 12_000.0
    assert cfg.min_holders == 150
    assert cfg.max_top10_holders_pct == 25.0
    assert cfg.max_dev_wallet_pct == 4.0
    assert cfg.main_pool_liquidity_share_min == 0.80
    assert cfg.database_path == "data/desk.db"
    assert cfg.judge_temperature == 0.0
    assert cfg.max_concurrent_trades == 3
    assert cfg.initial_stop_loss_pct == 22.0
    assert cfg.liquidity_panic_drop_pct == 25.0
    assert cfg.resolved_smart_wallets_path.exists()


def test_live_mode_dual_lock_blocks_without_flag() -> None:
    """LIVE mode must reject startup if ENABLE_LIVE_EXECUTION is False."""
    with pytest.raises(ValidationError, match="ENABLE_LIVE_EXECUTION"):
        Settings(
            _env_file=None,
            trading_mode=TradingMode.LIVE,
            enable_live_execution=False,
            live_wallet_private_key="dummy_base58_key_for_test_only",
        )


def test_live_mode_dual_lock_blocks_without_private_key() -> None:
    """LIVE mode must reject startup if LIVE_WALLET_PRIVATE_KEY is empty."""
    with pytest.raises(ValidationError, match="LIVE_WALLET_PRIVATE_KEY"):
        Settings(
            _env_file=None,
            trading_mode=TradingMode.LIVE,
            enable_live_execution=True,
            live_wallet_private_key="   ",
        )


def test_live_mode_passes_when_dual_lock_and_key_provided() -> None:
    """LIVE mode activates cleanly only when all 3 lock conditions are met."""
    cfg = Settings(
        _env_file=None,
        trading_mode=TradingMode.LIVE,
        enable_live_execution=True,
        live_wallet_private_key="5TestDedicatedHotWalletKeyBase58String",
    )
    assert cfg.is_live_mode is True
