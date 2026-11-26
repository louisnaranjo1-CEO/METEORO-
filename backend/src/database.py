"""Async SQLite persistence layer (WAL mode) for METEORO TRADER SOLANA.

Uses ``aiosqlite`` over ``backend/data/desk.db`` (configurable via ``DATABASE_PATH``)
with Write-Ahead Logging (WAL) enabled so the FastAPI Command Center can perform
concurrent reads without blocking the autonomous trading loops.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from src.config import Settings, get_settings

VALID_LEAD_STAGES: frozenset[str] = frozenset(
    {
        "SEARCH",
        "RISK",
        "WHALE",
        "JUDGE",
        "GATE",
        "PENDING_APPROVAL",
        "OPEN",
        "RISK_FREE_RUNNER",
        "CLOSED",
        "REJECTED",
    }
)

VALID_TRADE_MODES: frozenset[str] = frozenset({"TUTORIAL", "LIVE"})
VALID_TRADE_STATUSES: frozenset[str] = frozenset(
    {"OPEN", "TP1_HIT", "RISK_FREE_RUNNER", "CLOSED"}
)

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS bot_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    trading_mode TEXT NOT NULL DEFAULT 'TUTORIAL' CHECK (trading_mode IN ('TUTORIAL', 'LIVE')),
    is_paused INTEGER NOT NULL DEFAULT 0,
    semi_auto_mode INTEGER NOT NULL DEFAULT 0,
    tutorial_bankroll_sol REAL NOT NULL DEFAULT 5.0,
    tutorial_available_sol REAL NOT NULL DEFAULT 5.0,
    trade_size_sol REAL NOT NULL DEFAULT 0.25,
    max_concurrent_trades INTEGER NOT NULL DEFAULT 3,
    daily_sl_count INTEGER NOT NULL DEFAULT 0,
    daily_pnl_sol REAL NOT NULL DEFAULT 0.0,
    last_reset_date TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    pair_address TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'solana',
    current_stage TEXT NOT NULL DEFAULT 'SEARCH' CHECK (
        current_stage IN (
            'SEARCH', 'RISK', 'WHALE', 'JUDGE', 'GATE',
            'PENDING_APPROVAL', 'OPEN', 'RISK_FREE_RUNNER', 'CLOSED', 'REJECTED'
        )
    ),
    market_cap REAL,
    liquidity_usd REAL,
    main_pool_share REAL,
    holders_count INTEGER,
    dev_wallet TEXT,
    price_usd REAL,
    kill_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_mint_created
    ON leads (mint_address, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_stage
    ON leads (current_stage, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_dev_wallet
    ON leads (dev_wallet);

CREATE TABLE IF NOT EXISTS judge_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    dossier_json TEXT NOT NULL,
    organic TEXT,
    organic_prob REAL,
    late TEXT,
    late_prob REAL,
    exit_risk INTEGER,
    manipulation_trap TEXT,
    trap_prob REAL,
    action TEXT NOT NULL,
    action_prob REAL,
    gate_passed INTEGER NOT NULL DEFAULT 0,
    kill_reason TEXT,
    price_at_verdict REAL,
    price_24h_later REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_judge_verdicts_lead
    ON judge_verdicts (lead_id, created_at DESC);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
    mint_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('TUTORIAL', 'LIVE')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (
        status IN ('OPEN', 'TP1_HIT', 'RISK_FREE_RUNNER', 'CLOSED')
    ),
    invested_sol REAL NOT NULL,
    invested_usd REAL NOT NULL,
    entry_price_usd REAL NOT NULL,
    effective_entry_price_usd REAL NOT NULL,
    tokens_initial REAL NOT NULL,
    tokens_remaining REAL NOT NULL,
    fees_paid_sol REAL NOT NULL DEFAULT 0.0,
    slippage_cost_sol REAL NOT NULL DEFAULT 0.0,
    realized_sol REAL NOT NULL DEFAULT 0.0,
    current_price_usd REAL NOT NULL,
    highest_price_seen REAL NOT NULL,
    peak_volume_5m REAL NOT NULL DEFAULT 0.0,
    pnl_sol REAL NOT NULL DEFAULT 0.0,
    pnl_usd REAL NOT NULL DEFAULT 0.0,
    pnl_pct REAL NOT NULL DEFAULT 0.0,
    capital_recovered INTEGER NOT NULL DEFAULT 0,
    exit_reason TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_mode_status
    ON trades (mode, status);
CREATE INDEX IF NOT EXISTS idx_trades_mint
    ON trades (mint_address);

CREATE TABLE IF NOT EXISTS trade_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    sol_amount REAL NOT NULL,
    tokens_amount REAL NOT NULL DEFAULT 0.0,
    price_usd REAL,
    effective_price_usd REAL,
    fee_sol REAL NOT NULL DEFAULT 0.0,
    slippage_sol REAL NOT NULL DEFAULT 0.0,
    tx_signature TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_events_trade
    ON trade_events (trade_id, created_at ASC);

CREATE TABLE IF NOT EXISTS blacklisted_devs (
    dev_wallet TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    offending_mint TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS x_mentions_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint_address TEXT NOT NULL,
    query TEXT NOT NULL,
    author_handle TEXT NOT NULL,
    author_followers INTEGER NOT NULL DEFAULT 0,
    is_verified INTEGER NOT NULL DEFAULT 0,
    is_notable_figure INTEGER NOT NULL DEFAULT 0,
    notable_tag TEXT,
    text TEXT NOT NULL,
    url TEXT,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_x_mentions_mint_fetched
    ON x_mentions_cache (mint_address, fetched_at DESC);

CREATE TABLE IF NOT EXISTS runtime_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def utc_today_iso() -> str:
    """Return current UTC calendar date (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).date().isoformat()


class Database:
    """Async SQLite manager configured in WAL mode for METEORO TRADER."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        resolved = Path(db_path) if db_path is not None else self.settings.resolved_database_path
        self.db_path = resolved

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a WAL-configured aiosqlite connection with dict-like Row factory."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = aiosqlite.Row
        try:
            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA foreign_keys=ON;")
            await conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            await conn.close()

    async def initialize(self) -> None:
        """Create schema tables/indexes and ensure singleton ``bot_state`` row exists."""
        now = utc_now_iso()
        today = utc_today_iso()
        async with self.connect() as conn:
            await conn.executescript(SCHEMA_SQL)
            await conn.execute(
                """
                INSERT OR IGNORE INTO bot_state (
                    id, trading_mode, is_paused, semi_auto_mode,
                    tutorial_bankroll_sol, tutorial_available_sol,
                    trade_size_sol, max_concurrent_trades,
                    daily_sl_count, daily_pnl_sol, last_reset_date, updated_at
                ) VALUES (1, ?, 0, 0, ?, ?, ?, ?, 0, 0.0, ?, ?)
                """,
                (
                    self.settings.trading_mode.value,
                    self.settings.tutorial_initial_bankroll_sol,
                    self.settings.tutorial_initial_bankroll_sol,
                    self.settings.trade_size_sol,
                    self.settings.max_concurrent_trades,
                    today,
                    now,
                ),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Bot State
    # ------------------------------------------------------------------
    async def get_bot_state(self) -> dict[str, Any]:
        """Fetch the singleton ``bot_state`` row as a plain dictionary."""
        async with self.connect() as conn:
            cursor = await conn.execute("SELECT * FROM bot_state WHERE id = 1")
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("Database not initialized: bot_state row missing.")
            return dict(row)

    async def update_bot_state(self, **fields: Any) -> dict[str, Any]:
        """Update allowed columns in ``bot_state`` and return the refreshed state."""
        allowed = {
            "trading_mode",
            "is_paused",
            "semi_auto_mode",
            "tutorial_bankroll_sol",
            "tutorial_available_sol",
            "trade_size_sol",
            "max_concurrent_trades",
            "daily_sl_count",
            "daily_pnl_sol",
            "last_reset_date",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Unsupported bot_state column(s): {sorted(invalid)}")
        if not fields:
            return await self.get_bot_state()

        fields["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values())

        async with self.connect() as conn:
            await conn.execute(
                f"UPDATE bot_state SET {assignments} WHERE id = 1",
                values,
            )
            await conn.commit()
        return await self.get_bot_state()

    # ------------------------------------------------------------------
    # Leads (Pipeline Candidates)
    # ------------------------------------------------------------------
    async def get_recent_lead_mints(self, minutes: int = 60) -> set[str]:
        """Return mint addresses already evaluated in the last ``minutes`` minutes."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT DISTINCT mint_address FROM leads WHERE created_at >= ?",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return {str(row["mint_address"]) for row in rows}

    async def create_lead(
        self,
        *,
        mint_address: str,
        symbol: str,
        name: str,
        pair_address: str,
        chain: str = "solana",
        current_stage: str = "SEARCH",
        market_cap: float | None = None,
        liquidity_usd: float | None = None,
        main_pool_share: float | None = None,
        holders_count: int | None = None,
        dev_wallet: str | None = None,
        price_usd: float | None = None,
        kill_reason: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new token candidate into ``leads`` and return the created row."""
        if current_stage not in VALID_LEAD_STAGES:
            raise ValueError(f"Invalid lead stage: {current_stage}")
        now = utc_now_iso()
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO leads (
                    mint_address, symbol, name, pair_address, chain,
                    current_stage, market_cap, liquidity_usd, main_pool_share,
                    holders_count, dev_wallet, price_usd, kill_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mint_address,
                    symbol,
                    name,
                    pair_address,
                    chain,
                    current_stage,
                    market_cap,
                    liquidity_usd,
                    main_pool_share,
                    holders_count,
                    dev_wallet,
                    price_usd,
                    kill_reason,
                    now,
                    now,
                ),
            )
            lead_id = cursor.lastrowid
            await conn.commit()
            row_cursor = await conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
            row = await row_cursor.fetchone()
            return dict(row)

    async def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        """Fetch a single lead by primary key."""
        async with self.connect() as conn:
            cursor = await conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def update_lead(
        self,
        lead_id: int,
        *,
        current_stage: str | None = None,
        market_cap: float | None = None,
        liquidity_usd: float | None = None,
        main_pool_share: float | None = None,
        holders_count: int | None = None,
        dev_wallet: str | None = None,
        price_usd: float | None = None,
        kill_reason: str | None = None,
    ) -> dict[str, Any]:
        """Update selected fields on a lead and return the updated row."""
        updates: dict[str, Any] = {}
        if current_stage is not None:
            if current_stage not in VALID_LEAD_STAGES:
                raise ValueError(f"Invalid lead stage: {current_stage}")
            updates["current_stage"] = current_stage
        if market_cap is not None:
            updates["market_cap"] = market_cap
        if liquidity_usd is not None:
            updates["liquidity_usd"] = liquidity_usd
        if main_pool_share is not None:
            updates["main_pool_share"] = main_pool_share
        if holders_count is not None:
            updates["holders_count"] = holders_count
        if dev_wallet is not None:
            updates["dev_wallet"] = dev_wallet
        if price_usd is not None:
            updates["price_usd"] = price_usd
        if kill_reason is not None:
            updates["kill_reason"] = kill_reason

        updates["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{col} = ?" for col in updates)
        values = list(updates.values()) + [lead_id]

        async with self.connect() as conn:
            await conn.execute(
                f"UPDATE leads SET {assignments} WHERE id = ?",
                values,
            )
            await conn.commit()
            cursor = await conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
            row = await cursor.fetchone()
            if row is None:
                raise LookupError(f"Lead id={lead_id} not found.")
            return dict(row)

    async def list_leads(
        self,
        *,
        stage: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List leads ordered by most recently updated."""
        async with self.connect() as conn:
            if stage is not None:
                cursor = await conn.execute(
                    "SELECT * FROM leads WHERE current_stage = ? ORDER BY updated_at DESC LIMIT ?",
                    (stage, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT * FROM leads ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Judge Verdicts & Open Trade Counts (Phase 3)
    # ------------------------------------------------------------------
    async def record_judge_verdict(
        self,
        *,
        lead_id: int,
        dossier_json: str,
        organic: str,
        organic_prob: float,
        late: str,
        late_prob: float,
        exit_risk: int,
        manipulation_trap: str,
        trap_prob: float,
        action: str,
        action_prob: float,
        gate_passed: bool,
        kill_reason: str | None,
        price_at_verdict: float | None,
        price_24h_later: float | None = None,
    ) -> dict[str, Any]:
        """Persist a Mute Judge verdict (buy, watch, or pass) for calibration."""
        now = utc_now_iso()
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO judge_verdicts (
                    lead_id, dossier_json,
                    organic, organic_prob,
                    late, late_prob,
                    exit_risk,
                    manipulation_trap, trap_prob,
                    action, action_prob,
                    gate_passed, kill_reason,
                    price_at_verdict, price_24h_later, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    dossier_json,
                    organic,
                    organic_prob,
                    late,
                    late_prob,
                    exit_risk,
                    manipulation_trap,
                    trap_prob,
                    action,
                    action_prob,
                    1 if gate_passed else 0,
                    kill_reason,
                    price_at_verdict,
                    price_24h_later,
                    now,
                ),
            )
            verdict_id = cursor.lastrowid
            await conn.commit()
            row_cursor = await conn.execute(
                "SELECT * FROM judge_verdicts WHERE id = ?",
                (verdict_id,),
            )
            row = await row_cursor.fetchone()
            return dict(row)

    async def get_judge_verdicts_for_lead(self, lead_id: int) -> list[dict[str, Any]]:
        """Fetch all recorded Judge verdicts for a lead ordered oldest first."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM judge_verdicts WHERE lead_id = ? ORDER BY id ASC",
                (lead_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def has_recent_trade_for_mint(
        self, mint_address: str, *, hours: float = 24.0
    ) -> bool:
        """Return True if this mint already has a trade opened within the last `hours`."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT id FROM trades WHERE mint_address = ? AND opened_at >= ? LIMIT 1",
                (mint_address, cutoff),
            )
            return (await cursor.fetchone()) is not None

    async def count_watch_verdicts_for_lead(self, lead_id: int) -> int:
        """Count prior 'watch' verdicts recorded for a specific lead."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM judge_verdicts WHERE lead_id = ? AND action = 'watch'",
                (lead_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row is not None else 0

    async def count_open_trades(self, mode: str | None = None) -> int:
        """Count active trades (OPEN, TP1_HIT, RISK_FREE_RUNNER), optionally filtered by mode."""
        async with self.connect() as conn:
            if mode is not None:
                if mode not in VALID_TRADE_MODES:
                    raise ValueError(f"Invalid trade mode: {mode}")
                cursor = await conn.execute(
                    """
                    SELECT COUNT(*) FROM trades
                    WHERE mode = ? AND status IN ('OPEN', 'TP1_HIT', 'RISK_FREE_RUNNER')
                    """,
                    (mode,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT COUNT(*) FROM trades
                    WHERE status IN ('OPEN', 'TP1_HIT', 'RISK_FREE_RUNNER')
                    """
                )
            row = await cursor.fetchone()
            return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Trades & Trade Events (Phase 4)
    # ------------------------------------------------------------------
    async def create_trade(
        self,
        *,
        lead_id: int | None,
        mint_address: str,
        symbol: str,
        mode: str,
        invested_sol: float,
        invested_usd: float,
        entry_price_usd: float,
        effective_entry_price_usd: float,
        tokens_initial: float,
        tokens_remaining: float,
        fees_paid_sol: float = 0.0,
        slippage_cost_sol: float = 0.0,
        realized_sol: float = 0.0,
        current_price_usd: float | None = None,
        highest_price_seen: float | None = None,
        peak_volume_5m: float = 0.0,
        pnl_sol: float = 0.0,
        pnl_usd: float = 0.0,
        pnl_pct: float = 0.0,
        capital_recovered: bool = False,
        opened_at: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new open trade into ``trades`` and return it enriched with lead context."""
        if mode not in VALID_TRADE_MODES:
            raise ValueError(f"Invalid trade mode: {mode}")
        opened = opened_at or utc_now_iso()
        curr_price = current_price_usd if current_price_usd is not None else entry_price_usd
        high_price = highest_price_seen if highest_price_seen is not None else entry_price_usd

        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO trades (
                    lead_id, mint_address, symbol, mode, status,
                    invested_sol, invested_usd,
                    entry_price_usd, effective_entry_price_usd,
                    tokens_initial, tokens_remaining,
                    fees_paid_sol, slippage_cost_sol, realized_sol,
                    current_price_usd, highest_price_seen, peak_volume_5m,
                    pnl_sol, pnl_usd, pnl_pct,
                    capital_recovered, opened_at
                ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    mint_address,
                    symbol,
                    mode,
                    invested_sol,
                    invested_usd,
                    entry_price_usd,
                    effective_entry_price_usd,
                    tokens_initial,
                    tokens_remaining,
                    fees_paid_sol,
                    slippage_cost_sol,
                    realized_sol,
                    curr_price,
                    high_price,
                    peak_volume_5m,
                    pnl_sol,
                    pnl_usd,
                    pnl_pct,
                    1 if capital_recovered else 0,
                    opened,
                ),
            )
            trade_id = int(cursor.lastrowid)
            await conn.commit()
        created = await self.get_trade(trade_id)
        if created is None:  # pragma: no cover
            raise RuntimeError(f"Created trade id={trade_id} disappeared.")
        return created

    async def get_trade(self, trade_id: int) -> dict[str, Any] | None:
        """Fetch a single trade enriched with lead dev_wallet, pair_address, and entry liquidity."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT t.*,
                       l.dev_wallet AS lead_dev_wallet,
                       l.pair_address AS lead_pair_address,
                       l.liquidity_usd AS lead_liquidity_usd
                FROM trades t
                LEFT JOIN leads l ON l.id = t.lead_id
                WHERE t.id = ?
                """,
                (trade_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def get_open_trades(self, mode: str | None = None) -> list[dict[str, Any]]:
        """Return all non-closed trades (OPEN, TP1_HIT, RISK_FREE_RUNNER) for execution/recovery."""
        async with self.connect() as conn:
            if mode is not None:
                if mode not in VALID_TRADE_MODES:
                    raise ValueError(f"Invalid trade mode: {mode}")
                cursor = await conn.execute(
                    """
                    SELECT t.*,
                           l.dev_wallet AS lead_dev_wallet,
                           l.pair_address AS lead_pair_address,
                           l.liquidity_usd AS lead_liquidity_usd
                    FROM trades t
                    LEFT JOIN leads l ON l.id = t.lead_id
                    WHERE t.mode = ? AND t.status IN ('OPEN', 'TP1_HIT', 'RISK_FREE_RUNNER')
                    ORDER BY t.id ASC
                    """,
                    (mode,),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT t.*,
                           l.dev_wallet AS lead_dev_wallet,
                           l.pair_address AS lead_pair_address,
                           l.liquidity_usd AS lead_liquidity_usd
                    FROM trades t
                    LEFT JOIN leads l ON l.id = t.lead_id
                    WHERE t.status IN ('OPEN', 'TP1_HIT', 'RISK_FREE_RUNNER')
                    ORDER BY t.id ASC
                    """
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_trade(self, trade_id: int, **fields: Any) -> dict[str, Any]:
        """Update mutable columns on ``trades`` and return the refreshed enriched row."""
        allowed = {
            "status",
            "tokens_remaining",
            "fees_paid_sol",
            "slippage_cost_sol",
            "realized_sol",
            "current_price_usd",
            "highest_price_seen",
            "peak_volume_5m",
            "pnl_sol",
            "pnl_usd",
            "pnl_pct",
            "capital_recovered",
            "exit_reason",
            "closed_at",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Unsupported trades column(s): {sorted(invalid)}")
        if "status" in fields and fields["status"] not in VALID_TRADE_STATUSES:
            raise ValueError(f"Invalid trade status: {fields['status']}")
        if "capital_recovered" in fields:
            fields["capital_recovered"] = 1 if fields["capital_recovered"] else 0
        if not fields:
            existing = await self.get_trade(trade_id)
            if existing is None:
                raise LookupError(f"Trade id={trade_id} not found.")
            return existing

        assignments = ", ".join(f"{col} = ?" for col in fields)
        values = list(fields.values()) + [trade_id]

        async with self.connect() as conn:
            await conn.execute(f"UPDATE trades SET {assignments} WHERE id = ?", values)
            await conn.commit()

        refreshed = await self.get_trade(trade_id)
        if refreshed is None:
            raise LookupError(f"Trade id={trade_id} not found.")
        return refreshed

    async def list_trades(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List trades filtered optionally by ``mode`` and ``status`` ordered newest first."""
        conditions: list[str] = []
        params: list[Any] = []
        if mode is not None:
            if mode not in VALID_TRADE_MODES:
                raise ValueError(f"Invalid trade mode: {mode}")
            conditions.append("t.mode = ?")
            params.append(mode)
        if status is not None:
            if status not in VALID_TRADE_STATUSES:
                raise ValueError(f"Invalid trade status: {status}")
            conditions.append("t.status = ?")
            params.append(status)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        async with self.connect() as conn:
            cursor = await conn.execute(
                f"""
                SELECT t.*,
                       l.dev_wallet AS lead_dev_wallet,
                       l.pair_address AS lead_pair_address,
                       l.liquidity_usd AS lead_liquidity_usd
                FROM trades t
                LEFT JOIN leads l ON l.id = t.lead_id
                {where_clause}
                ORDER BY t.id DESC
                LIMIT ?
                """,
                params,
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def record_trade_event(
        self,
        *,
        trade_id: int,
        event_type: str,
        sol_amount: float,
        tokens_amount: float,
        price_usd: float | None,
        effective_price_usd: float | None,
        fee_sol: float,
        slippage_sol: float,
        tx_signature: str | None = None,
        notes: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Append an immutable execution event row to ``trade_events``."""
        now = created_at or utc_now_iso()
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO trade_events (
                    trade_id, event_type, sol_amount, tokens_amount,
                    price_usd, effective_price_usd, fee_sol, slippage_sol,
                    tx_signature, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    event_type,
                    sol_amount,
                    tokens_amount,
                    price_usd,
                    effective_price_usd,
                    fee_sol,
                    slippage_sol,
                    tx_signature,
                    notes,
                    now,
                ),
            )
            event_id = int(cursor.lastrowid)
            await conn.commit()
            row_cur = await conn.execute(
                "SELECT * FROM trade_events WHERE id = ?",
                (event_id,),
            )
            row = await row_cur.fetchone()
            return dict(row)

    async def get_trade_events(self, trade_id: int) -> list[dict[str, Any]]:
        """Fetch all execution events for a trade ordered chronologically."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY id ASC",
                (trade_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # X Mentions Cache & Audit Queries (Phase 4)
    # ------------------------------------------------------------------
    async def save_x_mentions(
        self,
        mint_address: str,
        query: str,
        mentions: list[dict[str, Any]],
        *,
        fetched_at: str | None = None,
    ) -> None:
        """Persist X mentions for ``mint_address`` in ``x_mentions_cache``."""
        ts = fetched_at or utc_now_iso()
        async with self.connect() as conn:
            for m in mentions:
                await conn.execute(
                    """
                    INSERT INTO x_mentions_cache (
                        mint_address, query, author_handle, author_followers,
                        is_verified, is_notable_figure, notable_tag,
                        text, url, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mint_address,
                        query,
                        str(m.get("author_handle") or ""),
                        int(m.get("author_followers") or 0),
                        1 if m.get("is_verified") else 0,
                        1 if m.get("is_notable_figure") else 0,
                        m.get("notable_tag"),
                        str(m.get("text") or ""),
                        m.get("url"),
                        ts,
                    ),
                )
            await conn.commit()

    async def get_cached_x_mentions(
        self,
        mint_address: str,
        max_age_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        """Fetch cached X mentions for ``mint_address`` within ``max_age_seconds``."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM x_mentions_cache
                WHERE mint_address = ? AND fetched_at >= ?
                ORDER BY is_notable_figure DESC, author_followers DESC, id DESC
                """,
                (mint_address, cutoff),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_verdicts_since(self, since_iso: str) -> list[dict[str, Any]]:
        """Fetch Judge verdicts joined with lead metadata since ``since_iso``."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT jv.*,
                       l.mint_address,
                       l.symbol,
                       l.current_stage
                FROM judge_verdicts jv
                JOIN leads l ON l.id = jv.lead_id
                WHERE jv.created_at >= ?
                ORDER BY jv.id ASC
                """,
                (since_iso,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_verdict_price_24h(
        self,
        verdict_id: int,
        price_24h_later: float,
    ) -> None:
        """Store 24h follow-up spot price on a ``judge_verdicts`` record."""
        async with self.connect() as conn:
            await conn.execute(
                "UPDATE judge_verdicts SET price_24h_later = ? WHERE id = ?",
                (price_24h_later, verdict_id),
            )
            await conn.commit()

    async def get_trades_by_lead_ids(
        self,
        lead_ids: list[int],
        *,
        mode: str | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Return latest trade mapped by ``lead_id`` for weekly calibration audits."""
        if not lead_ids:
            return {}
        placeholders = ",".join("?" for _ in lead_ids)
        params: list[Any] = list(lead_ids)
        mode_filter = ""
        if mode is not None:
            if mode not in VALID_TRADE_MODES:
                raise ValueError(f"Invalid trade mode: {mode}")
            mode_filter = " AND mode = ?"
            params.append(mode)

        async with self.connect() as conn:
            cursor = await conn.execute(
                f"""
                SELECT * FROM trades
                WHERE lead_id IN ({placeholders}){mode_filter}
                ORDER BY id ASC
                """,
                params,
            )
            rows = await cursor.fetchall()
        mapping: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            if item.get("lead_id") is not None:
                mapping[int(item["lead_id"])] = item
        return mapping

    async def get_daily_summary(
        self,
        date_iso: str,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Compute daily trade & PnL summary for nightly Telegram reports."""
        bot_state = await self.get_bot_state()
        active_mode = mode or str(bot_state["trading_mode"])
        if active_mode not in VALID_TRADE_MODES:
            raise ValueError(f"Invalid trade mode: {active_mode}")

        async with self.connect() as conn:
            opened_cur = await conn.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE mode = ? AND substr(opened_at, 1, 10) = ?
                """,
                (active_mode, date_iso),
            )
            opened_count = int((await opened_cur.fetchone())[0])

            closed_cur = await conn.execute(
                """
                SELECT pnl_sol, pnl_usd, fees_paid_sol, slippage_cost_sol
                FROM trades
                WHERE mode = ? AND status = 'CLOSED' AND substr(closed_at, 1, 10) = ?
                """,
                (active_mode, date_iso),
            )
            closed_rows = [dict(r) for r in await closed_cur.fetchall()]

        open_count = await self.count_open_trades(mode=active_mode)
        wins = sum(1 for r in closed_rows if float(r["pnl_sol"]) > 0)
        losses = sum(1 for r in closed_rows if float(r["pnl_sol"]) <= 0)
        net_pnl_sol = round(sum(float(r["pnl_sol"]) for r in closed_rows), 9)
        net_pnl_usd = round(sum(float(r["pnl_usd"]) for r in closed_rows), 4)
        total_fees_sol = round(sum(float(r["fees_paid_sol"]) for r in closed_rows), 9)
        total_slip_sol = round(sum(float(r["slippage_cost_sol"]) for r in closed_rows), 9)

        return {
            "date": date_iso,
            "mode": active_mode,
            "opened_count": opened_count,
            "closed_count": len(closed_rows),
            "open_count": open_count,
            "wins": wins,
            "losses": losses,
            "net_pnl_sol": net_pnl_sol,
            "net_pnl_usd": net_pnl_usd,
            "total_fees_sol": total_fees_sol,
            "total_slippage_sol": total_slip_sol,
            "bot_state": bot_state,
        }

    # ------------------------------------------------------------------
    # Runtime Config Overrides & Dashboard Queries (Phase 5)
    # ------------------------------------------------------------------
    async def get_runtime_config(self) -> dict[str, str]:
        """Return persisted runtime overrides (e.g. simulated fees edited from dashboard)."""
        async with self.connect() as conn:
            cursor = await conn.execute("SELECT key, value FROM runtime_config")
            rows = await cursor.fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}

    async def set_runtime_config(self, values: dict[str, Any]) -> None:
        """Upsert runtime override key/value pairs."""
        now = utc_now_iso()
        async with self.connect() as conn:
            for key, value in values.items():
                await conn.execute(
                    """
                    INSERT INTO runtime_config (key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, str(value), now),
                )
            await conn.commit()

    async def unblacklist_dev(self, dev_wallet: str) -> bool:
        """Remove a wallet from ``blacklisted_devs``; returns True if a row was deleted."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM blacklisted_devs WHERE dev_wallet = ?",
                (dev_wallet.strip(),),
            )
            await conn.commit()
            return (cursor.rowcount or 0) > 0

    async def get_latest_verdicts(self, lead_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Return the most recent Judge verdict per lead id."""
        if not lead_ids:
            return {}
        placeholders = ",".join("?" for _ in lead_ids)
        async with self.connect() as conn:
            cursor = await conn.execute(
                f"""
                SELECT jv.* FROM judge_verdicts jv
                JOIN (
                    SELECT lead_id, MAX(id) AS max_id FROM judge_verdicts
                    WHERE lead_id IN ({placeholders}) GROUP BY lead_id
                ) latest ON latest.max_id = jv.id
                """,
                list(lead_ids),
            )
            rows = await cursor.fetchall()
            return {int(r["lead_id"]): dict(r) for r in rows}

    async def list_recent_leads(self, *, minutes: int = 180, limit: int = 200) -> list[dict[str, Any]]:
        """List leads updated within the last ``minutes``."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM leads WHERE updated_at >= ? ORDER BY updated_at DESC LIMIT ?",
                (cutoff, limit),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_x_mentions_for_mints(
        self,
        mints: list[str],
        *,
        max_age_hours: int = 24,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Return cached X mentions for given mints (or all recent if ``mints`` empty)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        async with self.connect() as conn:
            if mints:
                placeholders = ",".join("?" for _ in mints)
                cursor = await conn.execute(
                    f"""
                    SELECT * FROM x_mentions_cache
                    WHERE fetched_at >= ? AND mint_address IN ({placeholders})
                    ORDER BY is_notable_figure DESC, author_followers DESC, id DESC
                    LIMIT ?
                    """,
                    [cutoff, *mints, limit],
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM x_mentions_cache WHERE fetched_at >= ?
                    ORDER BY is_notable_figure DESC, author_followers DESC, id DESC
                    LIMIT ?
                    """,
                    (cutoff, limit),
                )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Blacklisted Devs
    # ------------------------------------------------------------------
    async def blacklist_dev(
        self,
        dev_wallet: str,
        reason: str,
        mint_address: str | None = None,
    ) -> dict[str, Any]:
        """Add or update a malicious deployer wallet in ``blacklisted_devs``."""
        wallet = dev_wallet.strip()
        if not wallet:
            raise ValueError("dev_wallet cannot be empty.")
        now = utc_now_iso()
        async with self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO blacklisted_devs (dev_wallet, reason, offending_mint, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(dev_wallet) DO UPDATE SET
                    reason = excluded.reason,
                    offending_mint = COALESCE(excluded.offending_mint, blacklisted_devs.offending_mint)
                """,
                (wallet, reason, mint_address, now),
            )
            await conn.commit()
            cursor = await conn.execute(
                "SELECT * FROM blacklisted_devs WHERE dev_wallet = ?",
                (wallet,),
            )
            row = await cursor.fetchone()
            return dict(row)

    async def is_dev_blacklisted(self, dev_wallet: str | None) -> dict[str, Any] | None:
        """Return the blacklist entry if ``dev_wallet`` is banned, else ``None``."""
        if not dev_wallet or not dev_wallet.strip():
            return None
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM blacklisted_devs WHERE dev_wallet = ?",
                (dev_wallet.strip(),),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def list_blacklisted_devs(self) -> list[dict[str, Any]]:
        """Return all blacklisted deployer wallets ordered newest first."""
        async with self.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM blacklisted_devs ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Tutorial Account Reset (STRICTLY isolated from LIVE & blacklist)
    # ------------------------------------------------------------------
    async def reset_tutorial_account(self, initial_sol: float) -> dict[str, Any]:
        """Reset ONLY TUTORIAL trades, events, and bankroll without touching LIVE or blacklist.

        - Deletes ``trade_events`` linked to ``mode='TUTORIAL'`` trades.
        - Deletes ``trades`` with ``mode='TUTORIAL'``.
        - Resets ``bot_state.tutorial_bankroll_sol`` and ``tutorial_available_sol`` to ``initial_sol``,
          and resets daily counters if currently in ``TUTORIAL`` mode.
        - Preserves all ``LIVE`` trades, ``LIVE`` trade_events, and ``blacklisted_devs``.
        """
        if initial_sol <= 0:
            raise ValueError("initial_sol must be greater than 0.")
        now = utc_now_iso()
        today = utc_today_iso()
        async with self.connect() as conn:
            await conn.execute(
                """
                DELETE FROM trade_events
                WHERE trade_id IN (SELECT id FROM trades WHERE mode = 'TUTORIAL')
                """
            )
            await conn.execute("DELETE FROM trades WHERE mode = 'TUTORIAL'")
            await conn.execute(
                """
                UPDATE bot_state
                SET tutorial_bankroll_sol = ?,
                    tutorial_available_sol = ?,
                    daily_sl_count = CASE WHEN trading_mode = 'TUTORIAL' THEN 0 ELSE daily_sl_count END,
                    daily_pnl_sol = CASE WHEN trading_mode = 'TUTORIAL' THEN 0.0 ELSE daily_pnl_sol END,
                    last_reset_date = CASE WHEN trading_mode = 'TUTORIAL' THEN ? ELSE last_reset_date END,
                    updated_at = ?
                WHERE id = 1
                """,
                (initial_sol, initial_sol, today, now),
            )
            await conn.commit()
        return await self.get_bot_state()


# ----------------------------------------------------------------------
# Module-level Convenience Helpers
# ----------------------------------------------------------------------
async def blacklist_dev(
    dev_wallet: str,
    reason: str,
    mint_address: str | None = None,
    *,
    db: Database | None = None,
) -> dict[str, Any]:
    """Module-level helper to blacklist a deployer wallet."""
    database = db or Database()
    return await database.blacklist_dev(dev_wallet, reason, mint_address)


async def reset_tutorial_account(
    initial_sol: float,
    *,
    db: Database | None = None,
) -> dict[str, Any]:
    """Module-level helper to cleanly reset the TUTORIAL account."""
    database = db or Database()
    return await database.reset_tutorial_account(initial_sol)
