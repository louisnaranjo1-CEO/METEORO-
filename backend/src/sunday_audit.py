"""Phase 9 (AUDIT): Weekly Sunday Calibration Audit comparing Judge probabilities vs outcomes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config import Settings, get_settings
from src.database import Database
from src.http_client import HttpRequestError, ResilientHttpClient
from src.search import DEXSCREENER_SOLANA_TOKENS_URL, extract_solana_pairs

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BuyBucketStats:
    """Calibration metrics for one Mute Judge BUY confidence bucket."""

    label: str
    verdicts_count: int
    closed_trades_count: int
    wins: int
    win_rate_pct: float
    avg_pnl_pct: float
    net_pnl_sol: float


@dataclass(slots=True)
class PassAuditStats:
    """Counterfactual audit of tokens rejected via PASS after passing RISK."""

    total_pass_verdicts: int
    evaluated_24h_count: int
    died_or_dropped_50pct_count: int
    saved_rate_pct: float


@dataclass(slots=True)
class SundayCalibrationReport:
    """Complete Sunday calibration table for Telegram & archival logs."""

    generated_at: str
    period_days: int
    buy_buckets: list[BuyBucketStats]
    pass_audit: PassAuditStats
    formatted_text: str


class SundayAuditor:
    """Computes weekly Mute Judge probability calibration and PASS accuracy."""

    def __init__(
        self,
        db: Database,
        *,
        http_client: ResilientHttpClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or ResilientHttpClient(timeout=8.0, max_retries=2)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _fetch_current_price_usd(self, mint_address: str) -> float | None:
        url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint_address)
        try:
            payload = await self._http.get_json(url, timeout=6.0)
        except HttpRequestError:
            return None
        pairs = extract_solana_pairs(payload)
        if not pairs:
            return 0.0
        pairs.sort(
            key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0),
            reverse=True,
        )
        try:
            px = float(pairs[0].get("priceUsd") or 0.0)
            return px if px >= 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    async def refresh_missing_24h_prices(self, *, days: int = 7) -> int:
        """Populate ``price_24h_later`` on verdicts older than 24h via DexScreener."""
        now = datetime.now(timezone.utc)
        since_iso = (now - timedelta(days=days)).isoformat()
        cutoff_24h = now - timedelta(hours=24)

        verdicts = await self.db.get_verdicts_since(since_iso)
        updated = 0
        for row in verdicts:
            if row.get("price_24h_later") is not None:
                continue
            created = datetime.fromisoformat(row["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created > cutoff_24h:
                continue
            px = await self._fetch_current_price_usd(str(row["mint_address"]))
            if px is not None:
                await self.db.update_verdict_price_24h(int(row["id"]), px)
                updated += 1
        return updated

    @staticmethod
    def _bucket_label(action_prob: float | None) -> str | None:
        if action_prob is None:
            return None
        if 0.70 <= action_prob < 0.80:
            return "0.70-0.79"
        if 0.80 <= action_prob < 0.90:
            return "0.80-0.89"
        if action_prob >= 0.90:
            return "0.90+"
        return None

    async def generate_weekly_report(
        self,
        *,
        days: int = 7,
        mode: str | None = None,
        refresh_prices: bool = False,
        now: datetime | None = None,
    ) -> SundayCalibrationReport:
        """Generate weekly calibration report grouping BUY buckets and PASS 24h accuracy."""
        if refresh_prices:
            await self.refresh_missing_24h_prices(days=days)

        now_utc = now or datetime.now(timezone.utc)
        since_iso = (now_utc - timedelta(days=days)).isoformat()
        verdicts = await self.db.get_verdicts_since(since_iso)

        buy_verdicts = [v for v in verdicts if v.get("action") == "buy"]
        pass_verdicts = [v for v in verdicts if v.get("action") == "pass"]

        lead_ids = sorted({int(v["lead_id"]) for v in buy_verdicts})
        trades_by_lead = await self.db.get_trades_by_lead_ids(lead_ids, mode=mode)

        bucket_order = ("0.70-0.79", "0.80-0.89", "0.90+")
        bucket_acc: dict[str, dict[str, Any]] = {
            label: {
                "verdicts_count": 0,
                "closed_trades": [],
            }
            for label in bucket_order
        }

        for v in buy_verdicts:
            label = self._bucket_label(
                float(v["action_prob"]) if v.get("action_prob") is not None else None
            )
            if label is None:
                continue
            bucket_acc[label]["verdicts_count"] += 1
            trade = trades_by_lead.get(int(v["lead_id"]))
            if trade is not None and trade.get("status") == "CLOSED":
                bucket_acc[label]["closed_trades"].append(trade)

        buy_buckets: list[BuyBucketStats] = []
        for label in bucket_order:
            info = bucket_acc[label]
            closed_list: list[dict[str, Any]] = info["closed_trades"]
            closed_count = len(closed_list)
            wins = sum(1 for t in closed_list if float(t["pnl_sol"]) > 0.0)
            win_rate = round((wins / closed_count) * 100.0, 2) if closed_count > 0 else 0.0
            avg_pnl_pct = (
                round(
                    sum(float(t["pnl_pct"]) for t in closed_list) / closed_count, 2
                )
                if closed_count > 0
                else 0.0
            )
            net_pnl_sol = round(sum(float(t["pnl_sol"]) for t in closed_list), 6)
            buy_buckets.append(
                BuyBucketStats(
                    label=label,
                    verdicts_count=info["verdicts_count"],
                    closed_trades_count=closed_count,
                    wins=wins,
                    win_rate_pct=win_rate,
                    avg_pnl_pct=avg_pnl_pct,
                    net_pnl_sol=net_pnl_sol,
                )
            )

        # Audit PASS verdicts (tokens that survived RISK and reached Judge)
        evaluated_24h = 0
        died_or_dropped_50pct = 0
        for v in pass_verdicts:
            p0 = v.get("price_at_verdict")
            p24 = v.get("price_24h_later")
            if p0 is None or p24 is None or float(p0) <= 0:
                continue
            evaluated_24h += 1
            if float(p24) <= float(p0) * 0.50:
                died_or_dropped_50pct += 1

        saved_rate = (
            round((died_or_dropped_50pct / evaluated_24h) * 100.0, 2)
            if evaluated_24h > 0
            else 0.0
        )
        pass_audit = PassAuditStats(
            total_pass_verdicts=len(pass_verdicts),
            evaluated_24h_count=evaluated_24h,
            died_or_dropped_50pct_count=died_or_dropped_50pct,
            saved_rate_pct=saved_rate,
        )

        lines = [
            f"CALIBRACIÓN SEMANAL JUEZ MUDO (últimos {days} días)",
            "Bucket    | Veredictos | Cerrados | Wins | WinRate | AvgPnL% | NetSOL",
            "-" * 70,
        ]
        for b in buy_buckets:
            lines.append(
                f"{b.label:<9} | {b.verdicts_count:>10} | {b.closed_trades_count:>8} | "
                f"{b.wins:>4} | {b.win_rate_pct:>6.1f}% | {b.avg_pnl_pct:>+6.1f}% | {b.net_pnl_sol:>+7.4f}"
            )
        lines.extend(
            [
                "-" * 70,
                f"PASS post-RISK: {pass_audit.total_pass_verdicts} totales | "
                f"{pass_audit.evaluated_24h_count} evaluados 24h | "
                f"{pass_audit.died_or_dropped_50pct_count} cayeron >=50% / murieron "
                f"({pass_audit.saved_rate_pct:.1f}% acierto defensivo)",
            ]
        )

        return SundayCalibrationReport(
            generated_at=now_utc.isoformat(),
            period_days=days,
            buy_buckets=buy_buckets,
            pass_audit=pass_audit,
            formatted_text="\n".join(lines),
        )
