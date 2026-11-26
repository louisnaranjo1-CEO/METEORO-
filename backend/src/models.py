"""Shared Pydantic data models for METEORO TRADER SOLANA.

Defines ``TokenDossier`` (the Cold Dossier produced by HEAD and consumed by JUDGE/GATE).
GOLDEN RULE: Any unavailable metric is serialized literally as ``"missing"`` — never guessed.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

MissingStr = Literal["missing"]
NumericOrMissing = float | int | MissingStr
IntOrMissing = int | MissingStr
BoolOrMissing = bool | MissingStr


class TokenDossier(BaseModel):
    """Strictly numerical cold dossier passed to the Mute Judge.

    Every optional metric defaults to the literal string ``"missing"``, and any
    ``None`` or blank string supplied at construction time is automatically coerced to
    ``"missing"`` so the LLM never sees null ambiguity or guessed placeholders.
    """

    model_config = ConfigDict(extra="allow")

    # Token identity (metadata for audit/linkage; judge focuses on numbers)
    mint_address: str
    symbol: str
    lead_id: int | None = None

    # Core market & pool metrics
    market_cap_usd: NumericOrMissing = "missing"
    liquidity_usd: NumericOrMissing = "missing"
    main_pool_share: NumericOrMissing = "missing"
    price_usd: NumericOrMissing = "missing"
    age_minutes: NumericOrMissing = "missing"

    # Holder & contract risk metrics
    holders_count: IntOrMissing = "missing"
    top10_holders_pct: NumericOrMissing = "missing"
    dev_wallet_pct: NumericOrMissing = "missing"
    lp_locked_pct: NumericOrMissing = "missing"
    liq_to_mcap_pct: NumericOrMissing = "missing"
    mutable_metadata: BoolOrMissing = "missing"
    transfer_fee_pct: NumericOrMissing = "missing"
    rugcheck_score: NumericOrMissing = "missing"
    rugcheck_insider_flag: BoolOrMissing = "missing"
    creator_tokens_count: IntOrMissing = "missing"
    has_socials: BoolOrMissing = "missing"

    # Anti-wash trading & orderflow metrics
    volume_5m_usd: NumericOrMissing = "missing"
    volume_1h_usd: NumericOrMissing = "missing"
    volume_accel_5m_vs_1h: NumericOrMissing = "missing"
    unique_makers_5m: IntOrMissing = "missing"
    vol_to_liquidity_ratio_1h: NumericOrMissing = "missing"
    avg_tx_size_5m_usd: NumericOrMissing = "missing"
    buys_5m: IntOrMissing = "missing"
    sells_5m: IntOrMissing = "missing"
    buy_sell_ratio_5m: NumericOrMissing = "missing"
    buys_1h: IntOrMissing = "missing"
    sells_1h: IntOrMissing = "missing"
    buy_sell_ratio_1h: NumericOrMissing = "missing"
    price_change_5m_pct: NumericOrMissing = "missing"
    price_change_1h_pct: NumericOrMissing = "missing"
    price_change_6h_pct: NumericOrMissing = "missing"

    # Whale fast-track signal (Phase 3 WHALE)
    whale_confirm: BoolOrMissing = "missing"
    whale_wallets_count: IntOrMissing = "missing"

    # Planned fixed entry footprint (for exit_risk evaluation)
    planned_entry_sol: NumericOrMissing = "missing"
    planned_entry_usd: NumericOrMissing = "missing"
    planned_entry_pct_of_liquidity: NumericOrMissing = "missing"

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_missing(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if key in {"mint_address", "symbol", "lead_id"}:
                coerced[key] = value
            elif value is None or (isinstance(value, str) and value.strip() == ""):
                coerced[key] = "missing"
            else:
                coerced[key] = value
        return coerced

    def to_judge_payload(self) -> dict[str, Any]:
        """Return compact dictionary stripped of internal DB IDs for the Mute Judge."""
        data = self.model_dump()
        data.pop("lead_id", None)
        return data

    def to_prompt_json(self) -> str:
        """Serialize the cold dossier as compact JSON for OpenRouter."""
        return json.dumps(self.to_judge_payload(), separators=(",", ":"), sort_keys=True)

    def numeric_or_none(self, field_name: str) -> float | None:
        """Return a numeric field as float, or ``None`` if it is ``'missing'``."""
        val = getattr(self, field_name, "missing")
        if val == "missing" or val is None or isinstance(val, bool):
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
