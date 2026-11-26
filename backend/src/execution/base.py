"""Abstract ExecutionEngine interface shared by TutorialEngine and LiveEngine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal


class ExecutionError(RuntimeError):
    """Base error raised by an execution adapter."""


class InsufficientBalanceError(ExecutionError):
    """Raised when available SOL cannot cover trade size plus transaction fees."""


@dataclass(slots=True)
class ExecutionReceipt:
    """Normalized receipt returned after every simulated or on-chain fill."""

    trade: dict[str, Any]
    event: dict[str, Any]
    mode: str
    side: Literal["BUY", "SELL"]
    sol_amount: float
    tokens_amount: float
    spot_price_usd: float
    effective_price_usd: float
    fee_sol: float
    slippage_sol: float
    tx_signature: str


class ExecutionEngine(ABC):
    """Adapter contract guaranteeing identical pipeline behavior in TUTORIAL and LIVE."""

    @abstractmethod
    async def execute_buy(
        self,
        lead: dict[str, Any],
        amount_sol: float,
        *,
        spot_price_usd: float | None = None,
        sol_usd_price: float | None = None,
    ) -> ExecutionReceipt:
        """Open a position for ``lead`` spending ``amount_sol`` plus network fees."""

    @abstractmethod
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
        """Sell ``share_pct`` (0..100] of ``trade['tokens_remaining']``."""
