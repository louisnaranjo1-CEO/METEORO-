"""Centralized typed configuration for METEORO TRADER SOLANA.

Loads and validates environment variables via ``pydantic-settings``, enforcing:
- Dual-lock safety before allowing ``LIVE`` trading execution.
- Strict Mute Judge determinism (``JUDGE_TEMPERATURE == 0.0``).
- Coherent market cap, risk, gate, and volumetric exit thresholds.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
PROJECT_ROOT: Path = BACKEND_DIR.parent


class TradingMode(str, Enum):
    """Supported execution modes for the Execution Engine adapter."""

    TUTORIAL = "TUTORIAL"
    LIVE = "LIVE"


class Settings(BaseSettings):
    """Application-wide validated configuration loaded from `.env`."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # 1. Trading Mode & Dual Safety Lock
    # ------------------------------------------------------------------
    trading_mode: TradingMode = Field(
        default=TradingMode.TUTORIAL,
        description="Active execution adapter mode (TUTORIAL or LIVE).",
    )
    strategy_profile: Literal["CONSERVADOR", "AGRESIVO"] = Field(
        default="CONSERVADOR",
        description="Risk & speed preset (CONSERVADOR vs AGRESIVO scalper).",
    )
    enable_live_execution: bool = Field(
        default=False,
        description="Second safety lock required alongside TRADING_MODE=LIVE.",
    )

    # ------------------------------------------------------------------
    # 2. Tutorial SOL Bankroll & Realistic Friction Simulation
    # ------------------------------------------------------------------
    tutorial_initial_bankroll_sol: float = Field(default=5.0, gt=0.0)
    trade_size_sol: float = Field(default=0.25, gt=0.0)
    sim_dex_fee_pct: float = Field(default=0.25, ge=0.0, le=10.0)
    sim_priority_fee_sol: float = Field(default=0.0005, ge=0.0)
    sim_jito_tip_sol: float = Field(default=0.002, ge=0.0)
    sim_base_network_fee_sol: float = Field(default=0.000005, ge=0.0)
    sim_buy_slippage_pct: float = Field(default=1.5, ge=0.0, le=50.0)
    sim_tp_slippage_pct: float = Field(default=2.0, ge=0.0, le=50.0)
    sim_sl_slippage_pct: float = Field(default=8.0, ge=0.0, le=90.0)
    fallback_sol_usd_price: float = Field(default=180.0, gt=0.0)

    # ------------------------------------------------------------------
    # 3. Phase 1 & 2 — Search (DexScreener) & Hard Risk (RugCheck)
    # ------------------------------------------------------------------
    search_interval_seconds: int = Field(default=15, ge=3, le=300)
    min_market_cap_usd: float = Field(default=50_000.0, gt=0.0)
    max_market_cap_usd: float = Field(default=1_500_000.0, gt=0.0)
    min_liquidity_usd: float = Field(default=12_000.0, gt=0.0)
    min_holders: int = Field(default=150, ge=1)

    require_mint_revoked: bool = Field(default=True)
    require_freeze_revoked: bool = Field(default=True)
    require_lp_locked_or_burned: bool = Field(default=True)
    max_top10_holders_pct: float = Field(default=25.0, gt=0.0, le=100.0)
    max_dev_wallet_pct: float = Field(default=4.0, ge=0.0, le=100.0)
    main_pool_liquidity_share_min: float = Field(default=0.80, gt=0.0, le=1.0)

    # ------------------------------------------------------------------
    # 4. Phase 3 — Whale Tracking & Helius RPC
    # ------------------------------------------------------------------
    helius_api_key: str = Field(default="", repr=False)
    solana_rpc_url: str = Field(default="https://api.mainnet-beta.solana.com")
    smart_wallets_path: str = Field(default="data/smart_wallets.json")
    organic_fallback_min_unique_makers_5m: int = Field(default=45, ge=1)

    # ------------------------------------------------------------------
    # 5. Phase 5 — Mute Judge (OpenRouter) OR Pure Algorithmic Sniper
    # ------------------------------------------------------------------
    use_llm_judge: bool = Field(
        default=True,
        description="When False, bypasses LLM completely and runs 0ms Algorithmic Sniper.",
    )
    sniper_max_entry_pc5m_pct: float = Field(default=20.0, gt=0.0, le=500.0)
    sniper_min_entry_pc5m_pct: float = Field(default=-8.0, ge=-90.0, le=50.0)
    sniper_max_entry_pc1h_pct: float = Field(default=140.0, gt=0.0, le=5000.0)
    sniper_max_age_minutes: float = Field(default=240.0, gt=5.0, le=100_000.0)
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")
    judge_model: str = Field(default="google/gemini-2.0-flash-001")
    judge_temperature: float = Field(default=0.0, ge=0.0, le=0.0)
    judge_timeout_seconds: float = Field(default=8.0, gt=0.5, le=30.0)

    # ------------------------------------------------------------------
    # 6. Phase 6 — Deterministic Gate & Circuit Breakers
    # ------------------------------------------------------------------
    low_mcap_threshold_usd: float = Field(default=150_000.0, gt=0.0)

    gate_min_action_prob: float = Field(default=0.70, ge=0.0, le=1.0)
    gate_min_organic_prob: float = Field(default=0.65, ge=0.0, le=1.0)
    gate_max_late_prob: float = Field(default=0.45, ge=0.0, le=1.0)
    gate_max_exit_risk: int = Field(default=3, ge=1, le=5)
    gate_max_exit_risk_prob: float = Field(default=0.35, ge=0.0, le=1.0)
    gate_max_manipulation_prob: float = Field(default=0.30, ge=0.0, le=1.0)

    gate_min_action_prob_low_mcap: float = Field(default=0.75, ge=0.0, le=1.0)
    gate_min_organic_prob_low_mcap: float = Field(default=0.75, ge=0.0, le=1.0)
    gate_max_late_prob_low_mcap: float = Field(default=0.30, ge=0.0, le=1.0)
    gate_max_exit_risk_prob_low_mcap: float = Field(default=0.22, ge=0.0, le=1.0)
    gate_max_manipulation_prob_low_mcap: float = Field(default=0.20, ge=0.0, le=1.0)

    watch_recheck_minutes: int = Field(default=4, ge=1, le=60)

    max_concurrent_trades: int = Field(default=3, ge=1, le=20)
    max_daily_loss_sol: float = Field(default=0.75, gt=0.0)
    max_daily_loss_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    max_consecutive_sl: int = Field(default=3, ge=1, le=20)
    max_entry_liquidity_pct: float = Field(default=1.0, gt=0.0, le=20.0)

    # ------------------------------------------------------------------
    # 7. Phase 8 — Volumetric Exit Engine & Semi-Auto
    # ------------------------------------------------------------------
    exit_poll_interval_seconds: float = Field(default=15.0, ge=0.25, le=60.0)
    disable_hard_stop_loss: bool = Field(default=False)
    only_close_positive: bool = Field(default=False)
    smart_be_arm_pct: float = Field(default=12.0, gt=0.0)
    smart_be_floor_pct: float = Field(default=3.0, ge=0.0)
    wick_harvest_arm_pct: float = Field(default=18.0, gt=0.0)
    wick_pullback_exit_pct: float = Field(default=6.0, gt=0.0, lt=100.0)
    initial_stop_loss_pct: float = Field(default=22.0, gt=0.0, lt=100.0)
    tp1_trigger_pct: float = Field(default=45.0, gt=0.0)
    tp1_sell_pct: float = Field(default=50.0, gt=0.0, le=100.0)
    tp1_breakeven_sl_pct: float = Field(default=5.0, ge=0.0)
    free_risk_trigger_pct: float = Field(default=100.0, gt=0.0)
    tp1_multiplier: float = Field(default=2.0, gt=1.0)
    trailing_stop_pct: float = Field(default=18.0, gt=0.0, lt=100.0)
    trailing_stop_x_boost_pct: float = Field(default=24.0, gt=0.0, lt=100.0)
    stagnation_window_minutes: int = Field(default=12, ge=1)
    stagnation_runner_window_minutes: int = Field(default=20, ge=1)
    stagnation_price_band_pct: float = Field(default=8.0, gt=0.0, lt=100.0)
    stagnation_volume_floor_pct: float = Field(default=35.0, gt=0.0, lt=100.0)
    stagnation_max_minutes: int = Field(default=20, ge=1)
    stagnation_volume_drop_pct: float = Field(default=60.0, gt=0.0, le=100.0)
    sell_ratio_kill_multiplier: float = Field(default=3.0, gt=1.0)
    sell_ratio_kill_consecutive_cycles: int = Field(default=2, ge=1)
    sell_ratio_kill_threshold: float = Field(default=0.72, gt=0.5, le=1.0)
    liquidity_panic_drop_pct: float = Field(default=25.0, gt=0.0, lt=100.0)
    liquidity_panic_window_seconds: int = Field(default=120, ge=10)
    liquidity_panic_blacklist_max_age_minutes: int = Field(default=15, ge=1)
    semi_auto_approval_timeout_seconds: int = Field(default=90, ge=10)

    # ------------------------------------------------------------------
    # 8. Optional X (Twitter) Catalyst Radar
    # ------------------------------------------------------------------
    enable_x_context_on_runner: bool = Field(default=False)
    x_api_url: str = Field(default="")
    x_api_key: str = Field(default="", repr=False)
    x_timeout_seconds: float = Field(default=2.5, gt=0.1, le=10.0)
    x_cache_ttl_seconds: int = Field(default=180, ge=10)
    x_notable_min_followers: int = Field(default=50_000, ge=1000)
    notable_accounts_path: str = Field(default="data/notable_accounts.json")
    x_radar_enabled: bool = Field(default=False)
    x_bearer_token: str = Field(default="", repr=False)
    x_min_volume_1h_usd: float = Field(default=100_000.0, ge=0.0)

    # ------------------------------------------------------------------
    # 9. Phase 9 — Telegram, Nightly Report & Calibration
    # ------------------------------------------------------------------
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_chat_id: str = Field(default="")
    nightly_report_hour: int = Field(default=21, ge=0, le=23)
    nightly_report_minute: int = Field(default=0, ge=0, le=59)
    system_timezone: str = Field(default="America/Caracas")

    # ------------------------------------------------------------------
    # 10. Storage, FastAPI & Command Center Auth
    # ------------------------------------------------------------------
    database_path: str = Field(default="data/desk.db")
    log_dir: str = Field(default="logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173"
    )
    dashboard_pin: str = Field(default="", repr=False)

    # ------------------------------------------------------------------
    # 11. Live Execution — Jupiter + Jito + Dedicated Wallet
    # ------------------------------------------------------------------
    solana_private_key: str = Field(default="", repr=False)
    live_wallet_private_key: str = Field(default="", repr=False)
    live_wallet_public_key: str = Field(default="")
    max_live_trade_sol: float = Field(default=0.50, gt=0.0, le=50.0)
    min_live_gas_reserve_sol: float = Field(default=0.15, ge=0.15, le=10.0)
    jupiter_api_base_url: str = Field(default="https://quote-api.jup.ag/v6")
    jito_block_engine_url: str = Field(
        default="https://mainnet.block-engine.jito.wtf"
    )
    live_priority_fee_lamports: int = Field(default=500_000, ge=0)
    live_jito_tip_lamports: int = Field(default=2_000_000, ge=0)
    live_buy_slippage_bps: int = Field(default=150, ge=1, le=5000)
    live_tp_slippage_bps: int = Field(default=200, ge=1, le=5000)
    live_sl_slippage_bps: int = Field(default=800, ge=1, le=9000)

    # ------------------------------------------------------------------
    # Cross-field Invariants & Safety Locks
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_invariants_and_safety_locks(self) -> "Settings":
        if self.min_market_cap_usd >= self.max_market_cap_usd:
            raise ValueError(
                "MIN_MARKET_CAP_USD must be strictly less than MAX_MARKET_CAP_USD."
            )

        if (
            self.trading_mode == TradingMode.TUTORIAL
            and self.trade_size_sol > self.tutorial_initial_bankroll_sol
        ):
            raise ValueError(
                "TRADE_SIZE_SOL cannot exceed TUTORIAL_INITIAL_BANKROLL_SOL in TUTORIAL mode."
            )

        if self.trading_mode == TradingMode.LIVE:
            if not self.enable_live_execution:
                raise ValueError(
                    "LIVE mode blocked by dual-lock: ENABLE_LIVE_EXECUTION must be true "
                    "when TRADING_MODE=LIVE."
                )
            if not self.effective_solana_private_key:
                raise ValueError(
                    "LIVE mode blocked: SOLANA_PRIVATE_KEY (or LIVE_WALLET_PRIVATE_KEY) "
                    "cannot be empty when TRADING_MODE=LIVE."
                )

        if self.gate_min_action_prob_low_mcap < self.gate_min_action_prob:
            raise ValueError(
                "Low-MCap action threshold (GATE_MIN_ACTION_PROB_LOW_MCAP) must be "
                ">= standard GATE_MIN_ACTION_PROB."
            )
        if self.gate_min_organic_prob_low_mcap < self.gate_min_organic_prob:
            raise ValueError(
                "Low-MCap organic threshold (GATE_MIN_ORGANIC_PROB_LOW_MCAP) must be "
                ">= standard GATE_MIN_ORGANIC_PROB."
            )
        if self.gate_max_late_prob_low_mcap > self.gate_max_late_prob:
            raise ValueError(
                "Low-MCap late threshold (GATE_MAX_LATE_PROB_LOW_MCAP) must be "
                "<= standard GATE_MAX_LATE_PROB."
            )
        if self.gate_max_exit_risk_prob_low_mcap > self.gate_max_exit_risk_prob:
            raise ValueError(
                "Low-MCap exit_risk threshold (GATE_MAX_EXIT_RISK_PROB_LOW_MCAP) must be "
                "<= standard GATE_MAX_EXIT_RISK_PROB."
            )
        if self.gate_max_manipulation_prob_low_mcap > self.gate_max_manipulation_prob:
            raise ValueError(
                "Low-MCap manipulation threshold (GATE_MAX_MANIPULATION_PROB_LOW_MCAP) must be "
                "<= standard GATE_MAX_MANIPULATION_PROB."
            )

        return self

    # ------------------------------------------------------------------
    # Convenience Properties
    # ------------------------------------------------------------------
    @property
    def effective_solana_private_key(self) -> str:
        """Primary SOLANA_PRIVATE_KEY with backward-compatible LIVE_WALLET_PRIVATE_KEY fallback."""
        return self.solana_private_key.strip() or self.live_wallet_private_key.strip()

    def secret_values(self) -> list[str]:
        """Return all non-empty secrets configured on this Settings instance."""
        candidates = (
            self.solana_private_key,
            self.live_wallet_private_key,
            self.openrouter_api_key,
            self.helius_api_key,
            self.telegram_bot_token,
            self.x_api_key,
            self.x_bearer_token,
            self.dashboard_pin,
        )
        return [val.strip() for val in candidates if val and val.strip()]

    @property
    def is_live_mode(self) -> bool:
        """True only when LIVE mode and dual-lock are both active."""
        return self.trading_mode == TradingMode.LIVE and self.enable_live_execution

    @property
    def cors_origins_list(self) -> list[str]:
        """Parsed list of allowed CORS origins for FastAPI."""
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    @property
    def resolved_database_path(self) -> Path:
        """Absolute path to the SQLite database inside `backend/`."""
        path = Path(self.database_path)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def resolved_smart_wallets_path(self) -> Path:
        """Absolute path to `smart_wallets.json` inside `backend/`."""
        path = Path(self.smart_wallets_path)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def resolved_notable_accounts_path(self) -> Path:
        """Absolute path to `notable_accounts.json` inside `backend/`."""
        path = Path(self.notable_accounts_path)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def resolved_log_dir(self) -> Path:
        """Absolute path to the logs directory inside `backend/`."""
        path = Path(self.log_dir)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def sim_fixed_tx_fee_sol(self) -> float:
        """Total fixed per-transaction Solana + Jito fee in TUTORIAL mode (in SOL, lamport precision)."""
        return round(
            self.sim_base_network_fee_sol
            + self.sim_priority_fee_sol
            + self.sim_jito_tip_sol,
            9,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()


STRATEGY_PROFILES: dict[str, dict[str, float | int | bool]] = {
    "CONSERVADOR": {
        "search_interval_seconds": 15,
        "exit_poll_interval_seconds": 15,
        "min_holders": 150,
        "gate_min_action_prob": 0.70,
        "gate_min_action_prob_low_mcap": 0.75,
        "gate_min_organic_prob": 0.65,
        "gate_min_organic_prob_low_mcap": 0.75,
        "gate_max_late_prob": 0.45,
        "gate_max_late_prob_low_mcap": 0.30,
        "gate_max_manipulation_prob": 0.30,
        "gate_max_manipulation_prob_low_mcap": 0.20,
        "tp1_trigger_pct": 45.0,
        "tp1_sell_pct": 50.0,
        "tp1_breakeven_sl_pct": 5.0,
        "free_risk_trigger_pct": 100.0,
        "initial_stop_loss_pct": 22.0,
        "disable_hard_stop_loss": False,
        "only_close_positive": False,
        "trailing_stop_pct": 18.0,
    },
    "AGRESIVO": {
        "search_interval_seconds": 6,
        "exit_poll_interval_seconds": 0.5,
        "min_holders": 60,
        "gate_min_action_prob": 0.55,
        "gate_min_action_prob_low_mcap": 0.58,
        "gate_min_organic_prob": 0.50,
        "gate_min_organic_prob_low_mcap": 0.52,
        "gate_max_late_prob": 0.72,
        "gate_max_late_prob_low_mcap": 0.68,
        "gate_max_manipulation_prob": 0.45,
        "gate_max_manipulation_prob_low_mcap": 0.40,
        "tp1_trigger_pct": 50.0,
        "tp1_sell_pct": 100.0,
        "tp1_breakeven_sl_pct": 6.0,
        "free_risk_trigger_pct": 100.0,
        "initial_stop_loss_pct": 22.0,
        "disable_hard_stop_loss": True,
        "only_close_positive": True,
        "smart_be_arm_pct": 10.0,
        "smart_be_floor_pct": 4.0,
        "wick_harvest_arm_pct": 25.0,
        "wick_pullback_exit_pct": 6.0,
        "trailing_stop_pct": 9.0,
    },
}


def apply_strategy_profile(settings: Settings, profile: str) -> str:
    """Apply CONSERVADOR or AGRESIVO scalper thresholds onto ``settings``."""
    normalized = "AGRESIVO" if str(profile).upper() == "AGRESIVO" else "CONSERVADOR"
    settings.strategy_profile = normalized  # type: ignore[assignment]
    for key, val in STRATEGY_PROFILES[normalized].items():
        setattr(settings, key, val)
    return normalized
