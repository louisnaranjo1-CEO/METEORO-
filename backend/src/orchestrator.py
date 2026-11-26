"""Autonomous Trading Orchestrator with Crash Recovery, Semi-Auto Mode, and Scheduled Audits."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.algo_sniper import evaluate_algorithmic_sniper
from src.config import Settings, TradingMode, get_settings
from src.database import Database, utc_today_iso
from src.execution.base import ExecutionEngine, ExecutionError, ExecutionReceipt
from src.execution.live_engine import LiveEngine
from src.execution.tutorial_engine import TutorialEngine
from src.exit_manager import ExitActionResult, VolumetricExitManager
from src.gate import DeterministicGate, GateDecision
from src.http_client import ResilientHttpClient
from src.judge import JudgeResult, JudgeVerdict, MuteJudge
from src.models import TokenDossier
from src.price_oracle import SolPriceOracle
from src.risk import HardRiskFilter, RiskEvaluationResult
from src.search import DEXSCREENER_SOLANA_TOKENS_URL, SearchScanner, extract_solana_pairs
from src.sunday_audit import SundayAuditor
from src.telegram_bot import TelegramNotifier
from src.x_radar import XRadar

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_timezone(tz_name: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Timezone %r unavailable (tzdata missing); falling back to UTC-04:00.",
            tz_name,
        )
        return timezone(timedelta(hours=-4))


@dataclass(slots=True)
class PendingApproval:
    """Trade awaiting manual confirmation in ``semi_auto_mode`` (90s window)."""

    lead_id: int
    lead: dict[str, Any]
    verdict: JudgeVerdict
    approved_size_sol: float
    created_at: datetime
    expires_at: datetime


class TradingOrchestrator:
    """Coordinates SEARCH -> RISK -> DOSSIER -> JUDGE -> GATE -> EXECUTION -> EXIT."""

    def __init__(
        self,
        *,
        db: Database | None = None,
        settings: Settings | None = None,
        http_client: ResilientHttpClient | None = None,
        engine: ExecutionEngine | None = None,
        notifier: TelegramNotifier | None = None,
        x_radar: XRadar | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db or Database(settings=self.settings)
        self._owns_http = http_client is None
        self.http = http_client or ResilientHttpClient(timeout=12.0, max_retries=3)

        self.price_oracle = SolPriceOracle(
            http_client=self.http, cache_ttl_seconds=3.0, settings=self.settings
        )
        self.scanner = SearchScanner(
            db=self.db, http_client=self.http, settings=self.settings
        )
        self.risk_filter = HardRiskFilter(
            db=self.db, http_client=self.http, settings=self.settings
        )
        self.judge = MuteJudge(settings=self.settings)
        self.gate = DeterministicGate(
            db=self.db, settings=self.settings, price_oracle=self.price_oracle
        )
        self.notifier = notifier or TelegramNotifier(settings=self.settings)
        self.x_radar = x_radar or XRadar(db=self.db, settings=self.settings)

        if engine is not None:
            self.engine: ExecutionEngine = engine
        elif self.settings.trading_mode == TradingMode.LIVE:
            self.engine = LiveEngine(
                db=self.db, price_oracle=self.price_oracle, settings=self.settings
            )
        else:
            self.engine = TutorialEngine(
                db=self.db, price_oracle=self.price_oracle, settings=self.settings
            )

        self.exit_manager = VolumetricExitManager(
            db=self.db,
            engine=self.engine,
            http_client=self.http,
            price_oracle=self.price_oracle,
            x_radar=self.x_radar,
            notifier=self.notifier,
            settings=self.settings,
        )
        self.sunday_auditor = SundayAuditor(
            db=self.db, http_client=self.http, settings=self.settings
        )

        self._stop_event = asyncio.Event()
        self.approval_lock = asyncio.Lock()
        self._pending_approvals: dict[int, PendingApproval] = {}
        self._pending_withdrawals: dict[str, dict[str, Any]] = {}
        self._watch_rechecks: dict[int, datetime] = {}
        self._last_nightly_report_date: date | None = None
        self._last_sunday_audit_date: date | None = None
        self._smart_wallets = self._load_smart_wallets()

    def _load_smart_wallets(self) -> set[str]:
        path = self.settings.resolved_smart_wallets_path
        if not path.exists():
            return set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        wallets = payload.get("wallets") if isinstance(payload, dict) else []
        out: set[str] = set()
        if isinstance(wallets, list):
            for w in wallets:
                if isinstance(w, str) and w.strip():
                    out.add(w.strip())
                elif isinstance(w, dict) and w.get("address"):
                    out.add(str(w["address"]).strip())
        return out

    async def aclose(self) -> None:
        """Close owned network clients cleanly."""
        await self.judge.aclose()
        await self.x_radar.aclose()
        await self.notifier.aclose()
        close_engine = getattr(self.engine, "aclose", None)
        if callable(close_engine):
            await close_engine()
        if self._owns_http:
            await self.http.aclose()

    def set_active_engine(self, engine: ExecutionEngine) -> None:
        """Swap active execution adapter between TutorialEngine and LiveEngine."""
        self.engine = engine
        self.exit_manager.engine = engine

    # ------------------------------------------------------------------
    # Crash Recovery
    # ------------------------------------------------------------------
    async def recover_open_positions(self) -> list[dict[str, Any]]:
        """Reload OPEN, TP1_HIT, and RISK_FREE_RUNNER trades from SQLite on boot."""
        await self.db.initialize()
        open_trades = await self.db.get_open_trades()
        registered = self.exit_manager.register_recovered_trades(open_trades)
        if open_trades:
            logger.info(
                "Crash Recovery: rehydrated %d open trade(s) (%d new runtime trackers).",
                len(open_trades),
                registered,
            )
        return open_trades

    # ------------------------------------------------------------------
    # Cold Dossier Assembly (Golden Rule: missing -> "missing")
    # ------------------------------------------------------------------
    async def build_cold_dossier(
        self,
        lead: dict[str, Any],
        risk_result: RiskEvaluationResult | None = None,
    ) -> TokenDossier:
        """Assemble a strictly numeric ``TokenDossier`` with zero guessed values."""
        mint = str(lead["mint_address"])
        pair_address = str(lead.get("pair_address") or "")
        bot_state = await self.db.get_bot_state()
        sol_usd = await self.price_oracle.get_sol_usd_price()
        planned_sol = float(bot_state["trade_size_sol"])
        planned_usd = round(planned_sol * sol_usd, 4)

        main_pair: dict[str, Any] | None = None
        try:
            url = DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint)
            payload = await self.http.get_json(url, timeout=6.0)
            pairs = extract_solana_pairs(payload)
            if pairs:
                for p in pairs:
                    if pair_address and p.get("pairAddress") == pair_address:
                        main_pair = p
                        break
                if main_pair is None:
                    pairs.sort(
                        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0),
                        reverse=True,
                    )
                    main_pair = pairs[0]
        except Exception as exc:
            logger.warning("Dossier pair fetch failed for %s: %s", mint[:8], exc)

        liq_usd = (
            _safe_float((main_pair.get("liquidity") or {}).get("usd"))
            if main_pair
            else _safe_float(lead.get("liquidity_usd"))
        )
        mcap_usd = (
            _safe_float(
                main_pair.get("marketCap")
                if main_pair.get("marketCap") is not None
                else main_pair.get("fdv")
            )
            if main_pair
            else _safe_float(lead.get("market_cap"))
        )
        price_usd = (
            _safe_float(main_pair.get("priceUsd"))
            if main_pair
            else _safe_float(lead.get("price_usd"))
        )

        vol_obj = (main_pair.get("volume") or {}) if main_pair else {}
        vol_5m = _safe_float(vol_obj.get("m5"))
        vol_1h = _safe_float(vol_obj.get("h1"))

        txns_m5 = ((main_pair.get("txns") or {}).get("m5") or {}) if main_pair else {}
        buys_5m = _safe_int(txns_m5.get("buys"))
        sells_5m = _safe_int(txns_m5.get("sells"))
        txns_h1 = ((main_pair.get("txns") or {}).get("h1") or {}) if main_pair else {}
        buys_1h = _safe_int(txns_h1.get("buys"))
        sells_1h = _safe_int(txns_h1.get("sells"))

        buy_sell_ratio_5m: float | None = None
        if buys_5m is not None and sells_5m is not None and sells_5m > 0:
            buy_sell_ratio_5m = round(buys_5m / sells_5m, 4)

        buy_sell_ratio_1h: float | None = None
        if buys_1h is not None and sells_1h is not None and sells_1h > 0:
            buy_sell_ratio_1h = round(buys_1h / sells_1h, 4)

        avg_tx_size_5m: float | None = None
        if (
            vol_5m is not None
            and buys_5m is not None
            and sells_5m is not None
            and (buys_5m + sells_5m) > 0
        ):
            avg_tx_size_5m = round(vol_5m / (buys_5m + sells_5m), 2)

        vol_to_liq_1h: float | None = None
        if vol_1h is not None and liq_usd is not None and liq_usd > 0:
            vol_to_liq_1h = round(vol_1h / liq_usd, 4)

        vol_accel_5m_vs_1h: float | None = None
        if vol_5m is not None and vol_1h is not None and vol_1h > 0:
            avg_5m = vol_1h / 12.0
            if avg_5m > 0:
                vol_accel_5m_vs_1h = round(vol_5m / avg_5m, 3)

        liq_to_mcap_pct: float | None = None
        if liq_usd is not None and mcap_usd is not None and mcap_usd > 0:
            liq_to_mcap_pct = round((liq_usd / mcap_usd) * 100.0, 2)

        pair_info = (main_pair.get("info") or {}) if main_pair else {}
        has_socials: bool | None = None
        if main_pair:
            has_socials = bool(pair_info.get("socials") or pair_info.get("websites"))

        price_change = (main_pair.get("priceChange") or {}) if main_pair else {}
        pc_5m = _safe_float(price_change.get("m5"))
        pc_1h = _safe_float(price_change.get("h1"))
        pc_6h = _safe_float(price_change.get("h6"))

        age_minutes: float | None = None
        if main_pair and main_pair.get("pairCreatedAt") is not None:
            created_ms = _safe_float(main_pair.get("pairCreatedAt"))
            if created_ms and created_ms > 0:
                age_minutes = round(
                    max(0.0, (datetime.now(timezone.utc).timestamp() * 1000.0 - created_ms) / 60_000.0),
                    2,
                )

        planned_liq_pct = (
            round((planned_usd / liq_usd) * 100.0, 4)
            if liq_usd is not None and liq_usd > 0
            else None
        )

        self._smart_wallets = self._load_smart_wallets()
        matched_set: set[str] = set()
        if self._smart_wallets and risk_result and risk_result.top_holder_owners:
            matched_set.update(self._smart_wallets.intersection(risk_result.top_holder_owners))

        # Helius Enhanced Transactions check: catch recent Smart Wallet buys even outside Top 20 holders
        helius_key = self.settings.helius_api_key.strip()
        if self._smart_wallets and helius_key:
            try:
                helius_url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions"
                txs = await self.http.get_json_safe(
                    helius_url,
                    params={"api-key": helius_key, "limit": 25},
                    default=[],
                )
                if isinstance(txs, list):
                    for tx in txs:
                        if not isinstance(tx, dict):
                            continue
                        fp = str(tx.get("feePayer") or "").strip()
                        if fp in self._smart_wallets:
                            matched_set.add(fp)
                        for tr in tx.get("tokenTransfers") or []:
                            if isinstance(tr, dict):
                                to_u = str(tr.get("toUserAccount") or "").strip()
                                if to_u in self._smart_wallets:
                                    matched_set.add(to_u)
            except Exception as exc:
                logger.debug("Helius recent smart-wallet check skipped for %s: %s", mint[:8], exc)

        matched_whales = len(matched_set)

        return TokenDossier(
            lead_id=int(lead["id"]),
            mint_address=mint,
            symbol=str(lead["symbol"]),
            market_cap_usd=mcap_usd,
            liquidity_usd=liq_usd,
            main_pool_share=(
                risk_result.main_pool_share
                if risk_result and risk_result.main_pool_share is not None
                else _safe_float(lead.get("main_pool_share"))
            ),
            price_usd=price_usd,
            age_minutes=age_minutes,
            holders_count=(
                risk_result.holders_count
                if risk_result and risk_result.holders_count is not None
                else _safe_int(lead.get("holders_count"))
            ),
            top10_holders_pct=risk_result.top10_pct if risk_result else None,
            dev_wallet_pct=risk_result.dev_pct if risk_result else None,
            lp_locked_pct=risk_result.lp_locked_pct if risk_result else None,
            liq_to_mcap_pct=liq_to_mcap_pct,
            mutable_metadata=risk_result.mutable_metadata if risk_result else None,
            transfer_fee_pct=risk_result.transfer_fee_pct if risk_result else None,
            rugcheck_score=risk_result.rugcheck_score if risk_result else None,
            rugcheck_insider_flag=risk_result.rugcheck_insider_flag if risk_result else None,
            creator_tokens_count=risk_result.creator_tokens_count if risk_result else None,
            has_socials=has_socials,
            volume_5m_usd=vol_5m,
            volume_1h_usd=vol_1h,
            volume_accel_5m_vs_1h=vol_accel_5m_vs_1h,
            unique_makers_5m="missing",  # DexScreener public API does not provide unique maker count
            vol_to_liquidity_ratio_1h=vol_to_liq_1h,
            avg_tx_size_5m_usd=avg_tx_size_5m,
            buys_5m=buys_5m,
            sells_5m=sells_5m,
            buy_sell_ratio_5m=buy_sell_ratio_5m,
            buys_1h=buys_1h,
            sells_1h=sells_1h,
            buy_sell_ratio_1h=buy_sell_ratio_1h,
            price_change_5m_pct=pc_5m,
            price_change_1h_pct=pc_1h,
            price_change_6h_pct=pc_6h,
            whale_confirm=(matched_whales > 0) if self._smart_wallets else "missing",
            whale_wallets_count=matched_whales if self._smart_wallets else "missing",
            planned_entry_sol=planned_sol,
            planned_entry_usd=planned_usd,
            planned_entry_pct_of_liquidity=planned_liq_pct,
        )

    # ------------------------------------------------------------------
    # Semi-Auto Approval & Trade Execution Dispatch
    # ------------------------------------------------------------------
    async def handle_approved_lead(
        self,
        lead: dict[str, Any],
        decision: GateDecision,
        verdict: JudgeVerdict,
        *,
        now: datetime | None = None,
    ) -> ExecutionReceipt | None:
        """Execute immediately when full-auto, or hold 90s in PENDING_APPROVAL if semi-auto."""
        if not decision.approved or decision.approved_size_sol is None:
            return None

        bot_state = await self.db.get_bot_state()
        now_utc = now or datetime.now(timezone.utc)
        lead_id = int(lead["id"])
        fresh_lead = await self.db.get_lead(lead_id) or lead

        if bool(bot_state["semi_auto_mode"]):
            timeout_s = int(self.settings.semi_auto_approval_timeout_seconds)
            if fresh_lead.get("current_stage") != "PENDING_APPROVAL":
                fresh_lead = await self.db.update_lead(
                    lead_id, current_stage="PENDING_APPROVAL"
                )
            self._pending_approvals[lead_id] = PendingApproval(
                lead_id=lead_id,
                lead=fresh_lead,
                verdict=verdict,
                approved_size_sol=decision.approved_size_sol,
                created_at=now_utc,
                expires_at=now_utc + timedelta(seconds=timeout_s),
            )
            await self.notifier.notify_pending_approval(
                fresh_lead, verdict.action_prob, decision.approved_size_sol, timeout_s
            )
            return None

        receipt = await self.engine.execute_buy(
            fresh_lead, decision.approved_size_sol
        )
        self.exit_manager.register_recovered_trades([receipt.trade])
        await self._send_rich_trade_open_alert(receipt.trade, fresh_lead)
        return receipt

    async def approve_pending_lead(self, lead_id: int) -> ExecutionReceipt:
        """Approve a lead waiting in PENDING_APPROVAL (synced across Dashboard & Telegram)."""
        async with self.approval_lock:
            pending = self._pending_approvals.pop(lead_id, None)
            if pending is None:
                raise LookupError(f"No pending approval found for lead_id={lead_id}.")
            fresh_lead = await self.db.get_lead(lead_id) or pending.lead
            if fresh_lead.get("current_stage") != "PENDING_APPROVAL":
                raise LookupError(
                    f"Lead {lead_id} already transitioned to {fresh_lead.get('current_stage')}."
                )
            receipt = await self.engine.execute_buy(fresh_lead, pending.approved_size_sol)
            self.exit_manager.register_recovered_trades([receipt.trade])
            await self._send_rich_trade_open_alert(receipt.trade, fresh_lead)
            return receipt

    async def _generate_ai_token_brief(
        self,
        trade: dict[str, Any],
        lead: dict[str, Any],
        pair: dict[str, Any] | None,
    ) -> str | None:
        """Ask DeepSeek 4.1 Flash for a 3-line Spanish analyst brief (narrative, hype/FOMO, risk)."""
        api_key = self.settings.openrouter_api_key.strip()
        if not api_key:
            return None
        info = (pair.get("info") or {}) if pair else {}
        vol = (pair.get("volume") or {}) if pair else {}
        tx5 = ((pair.get("txns") or {}).get("m5") or {}) if pair else {}
        tx1h = ((pair.get("txns") or {}).get("h1") or {}) if pair else {}
        socials = [s.get("type") for s in (info.get("socials") or []) if isinstance(s, dict) and s.get("type")]
        summary_data = {
            "symbol": trade.get("symbol"),
            "name": lead.get("name"),
            "mcap_usd": lead.get("market_cap"),
            "liq_usd": lead.get("liquidity_usd"),
            "holders": lead.get("holders_count"),
            "dev_wallet": lead.get("dev_wallet"),
            "vol_5m": vol.get("m5"),
            "vol_1h": vol.get("h1"),
            "buys_5m": tx5.get("buys"),
            "sells_5m": tx5.get("sells"),
            "buys_1h": tx1h.get("buys"),
            "sells_1h": tx1h.get("sells"),
            "socials": socials,
            "websites": len(info.get("websites") or []),
        }
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.settings.judge_model,
                        "temperature": 0.2,
                        "max_tokens": 220,
                        "reasoning": {"enabled": False},
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Eres un analista de memecoins en Solana. Responde en español en máximo 3 líneas cortas "
                                    "(sin markdown ni asteriscos): 1) Qué es el token/narrativa, 2) Nivel de Hype/FOMO "
                                    "(BAJO/MEDIO/ALTO/VIRAL) según volumen y compras vs ventas, 3) Riesgo clave o nota de redes/dev."
                                ),
                            },
                            {"role": "user", "content": json.dumps(summary_data, ensure_ascii=False)},
                        ],
                    },
                )
                if resp.status_code == 200:
                    txt = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                    if txt:
                        return txt[:420]
        except Exception as exc:
            logger.debug("AI brief skipped for %s: %s", trade.get("symbol"), exc)
        return None

    async def _send_rich_trade_open_alert(
        self, trade: dict[str, Any], lead: dict[str, Any]
    ) -> None:
        """Enrich trade-open Telegram card with DexScreener socials + DeepSeek AI Brief."""
        if not self.notifier.is_configured:
            await self.notifier.notify_trade_open(trade)
            return
        mint = str(trade["mint_address"])
        pair: dict[str, Any] | None = None
        socials: list[str] = []
        try:
            payload = await self.http.get_json_safe(
                DEXSCREENER_SOLANA_TOKENS_URL.format(mints=mint), default=[]
            )
            pairs = extract_solana_pairs(payload)
            if pairs:
                pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0), reverse=True)
                pair = pairs[0]
                info = pair.get("info") or {}
                for w in info.get("websites") or []:
                    if isinstance(w, dict) and w.get("url"):
                        socials.append(f"<a href=\"{w['url']}\">Web</a>")
                        break
                for s in info.get("socials") or []:
                    if isinstance(s, dict) and s.get("url"):
                        stype = str(s.get("type") or "Social").capitalize()
                        if stype.lower() == "twitter":
                            stype = "X/Twitter"
                        socials.append(f"<a href=\"{s['url']}\">{stype}</a>")
        except Exception:
            pass
        ai_brief = (
            await self._generate_ai_token_brief(trade, lead, pair)
            if self.settings.use_llm_judge
            else "⚡ Entrada por Motor Sniper Algorítmico (0ms · LLM apagado · Anti-Punta)."
        )
        await self.notifier.notify_trade_open(
            trade,
            ai_brief=ai_brief,
            socials=socials or None,
            mcap_usd=_safe_float(lead.get("market_cap")),
            liq_usd=_safe_float(lead.get("liquidity_usd")),
        )

    async def handle_telegram_refresh_trade(
        self, trade_id: int
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        """Inline button `🔄 Actualizar en Vivo`: fetch live spot & update Telegram card."""
        trade = await self.db.get_trade(trade_id)
        if trade is None:
            return ("Operación no encontrada.", None, None)
        if trade["status"] == "CLOSED":
            return (
                "Operación ya cerrada.",
                self.notifier.format_trade_closed(trade),
                self.notifier.trade_inline_keyboard(trade, include_controls=False),
            )
        sol_usd = await self.price_oracle.get_sol_usd_price()
        live_price = float(trade.get("current_price_usd") or trade.get("entry_price_usd") or 0.0)
        try:
            snap = await self.exit_manager.fetch_market_snapshot(
                str(trade["mint_address"]), pair_address=trade.get("lead_pair_address")
            )
            if snap and snap.price_usd > 0:
                live_price = snap.price_usd
        except Exception:
            pass
        inv_sol = float(trade["invested_sol"])
        rem_sol = ((float(trade["tokens_remaining"]) * live_price) / sol_usd) if (live_price > 0 and sol_usd > 0) else 0.0
        pnl_sol = round((float(trade["realized_sol"]) + rem_sol) - inv_sol, 6)
        pnl_usd = round(pnl_sol * sol_usd, 2)
        pnl_pct = round((pnl_sol / inv_sol) * 100.0, 2) if inv_sol > 0 else 0.0
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        text = self.notifier.format_trade_live_status(
            trade,
            live_price=live_price,
            live_pnl_sol=pnl_sol,
            live_pnl_usd=pnl_usd,
            live_pnl_pct=pnl_pct,
            tp_target_pct=float(self.settings.tp1_trigger_pct),
            updated_hhmmss=now_str,
        )
        return (
            f"Actualizado: {pnl_sol:+.4f} SOL ({pnl_pct:+.1f}%)",
            text,
            self.notifier.trade_inline_keyboard(trade, include_controls=True),
        )

    async def handle_telegram_close_trade(self, trade_id: int) -> tuple[str, str | None]:
        """Inline button `🚪 Cerrar 100%`: execute immediate full sell from Telegram."""
        trade = await self.db.get_trade(trade_id)
        if trade is None:
            return ("Operación no encontrada.", None)
        if trade["status"] == "CLOSED":
            return ("Ya estaba cerrada.", self.notifier.format_trade_closed(trade))
        spot = None
        try:
            snap = await self.exit_manager.fetch_market_snapshot(
                str(trade["mint_address"]), pair_address=trade.get("lead_pair_address")
            )
            if snap and snap.price_usd > 0:
                spot = snap.price_usd
        except Exception:
            pass
        try:
            sol_usd = await self.price_oracle.get_sol_usd_price()
            receipt = await self.engine.execute_sell(
                trade,
                100.0,
                "TELEGRAM_MANUAL_EXIT",
                is_emergency=True,
                spot_price_usd=spot,
                sol_usd_price=sol_usd,
            )
            self.exit_manager.forget_trade(trade_id)
            closed = receipt.trade
            return (
                f"Cerrada: {float(closed['pnl_sol']):+.4f} SOL (${float(closed['pnl_usd']):+.2f})",
                self.notifier.format_trade_closed(closed),
            )
        except ExecutionError as exc:
            return (f"Error al cerrar: {exc}", None)

    async def handle_telegram_withdraw_decision(
        self, req_id: str, approved: bool
    ) -> tuple[str, str | None]:
        """Execute or cancel a 2-step confirmed SOL withdrawal from Telegram."""
        from src.wallet_manager import withdraw_sol_to_whitelisted

        req = self._pending_withdrawals.pop(req_id, None)
        if req is None:
            return ("Solicitud expirada o ya procesada.", "⚠️ Solicitud de retiro expirada.")
        if not approved:
            return ("Retiro cancelado.", "❌ <b>Retiro cancelado por el usuario.</b>")
        try:
            res = await withdraw_sol_to_whitelisted(
                self.settings,
                req["destination"],
                float(req["amount_sol"]),
            )
            return (
                f"Enviado {res['amount_sol']:.4f} SOL!",
                f"💸 <b>RETIRO COMPLETADO</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 Monto: <b>{res['amount_sol']:.4f} SOL</b>\n"
                f"🎯 Destino: <b>{res['destination_label']}</b>\n"
                f"🔗 <a href=\"{res['solscan_url']}\">Ver TX en Solscan</a>",
            )
        except Exception as exc:
            return (f"Error: {exc}", f"🔴 <b>Error en retiro:</b> <code>{exc}</code>")

    async def reject_pending_lead(
        self, lead_id: int, reason: str = "MANUAL_REJECT"
    ) -> dict[str, Any]:
        """Reject a lead waiting in PENDING_APPROVAL (synced across Dashboard & Telegram)."""
        async with self.approval_lock:
            self._pending_approvals.pop(lead_id, None)
            return await self.db.update_lead(
                lead_id, current_stage="REJECTED", kill_reason=reason
            )

    async def handle_telegram_approve(self, lead_id: int) -> str:
        """Callback invoked when the user taps ✅ APROBAR inside Telegram."""
        try:
            receipt = await self.approve_pending_lead(lead_id)
            return f"✅ Compra ejecutada: {receipt.trade['symbol']}"
        except LookupError:
            return "ℹ️ Lead ya procesado o expirado (sincronizado con Dashboard)."
        except ExecutionError as exc:
            return f"⚠️ No se pudo ejecutar: {exc}"

    async def handle_telegram_reject(self, lead_id: int) -> str:
        """Callback invoked when the user taps ❌ DESCARTAR inside Telegram."""
        lead = await self.db.get_lead(lead_id)
        if lead is None or lead.get("current_stage") != "PENDING_APPROVAL":
            return "ℹ️ Lead ya procesado o expirado."
        await self.reject_pending_lead(lead_id, "MANUAL_REJECT")
        return f"❌ Descartado: {lead['symbol']}"

    async def expire_pending_approvals(
        self, *, now: datetime | None = None
    ) -> list[int]:
        """Expire semi-auto pending approvals older than 90s."""
        now_utc = now or datetime.now(timezone.utc)
        expired_ids = [
            lid
            for lid, item in self._pending_approvals.items()
            if now_utc >= item.expires_at
        ]
        for lid in expired_ids:
            self._pending_approvals.pop(lid, None)
            await self.db.update_lead(
                lid, current_stage="REJECTED", kill_reason="APPROVAL_TIMEOUT"
            )
        return expired_ids

    @property
    def pending_approvals(self) -> dict[int, PendingApproval]:
        return dict(self._pending_approvals)

    # ------------------------------------------------------------------
    # Pipeline & Exit Cycles
    # ------------------------------------------------------------------
    async def _evaluate_lead_through_gate(
        self,
        lead: dict[str, Any],
        risk_result: RiskEvaluationResult | None = None,
    ) -> None:
        # Anti-repeat guard: never re-buy a mint we already traded in the last 24h
        if await self.db.has_recent_trade_for_mint(str(lead["mint_address"]), hours=24.0):
            await self.db.update_lead(
                int(lead["id"]),
                current_stage="REJECTED",
                kill_reason="ALREADY_TRADED_24H",
            )
            logger.info(
                "ANTI-REPEAT KILL %s (%s): already traded within 24h",
                lead.get("symbol"),
                str(lead.get("mint_address"))[:8],
            )
            return

        dossier = await self.build_cold_dossier(lead, risk_result=risk_result)
        if self.settings.use_llm_judge:
            judge_res: JudgeResult = await self.judge.evaluate(dossier)
        else:
            judge_res = evaluate_algorithmic_sniper(dossier, self.settings)
        decision = await self.gate.evaluate(
            lead=lead, dossier=dossier, judge_result=judge_res
        )
        if decision.decision == "WATCH" and decision.recheck_at:
            self._watch_rechecks[int(lead["id"])] = datetime.fromisoformat(
                decision.recheck_at
            )
        elif decision.approved:
            await self.handle_approved_lead(lead, decision, judge_res.verdict)

    async def run_discovery_cycle(self) -> None:
        """Run one SEARCH -> RISK -> JUDGE -> GATE -> EXECUTION pass."""
        state = await self.db.get_bot_state()
        if bool(state["is_paused"]):
            return

        # Re-evaluate due WATCH leads once (both in-memory and recovered from SQLite after restart)
        now_utc = datetime.now(timezone.utc)
        watch_delay = timedelta(minutes=int(self.settings.watch_recheck_minutes))
        judge_leads = await self.db.list_leads(stage="JUDGE", limit=20)
        for jl in judge_leads:
            lid = int(jl["id"])
            if lid not in self._watch_rechecks:
                verdicts = await self.db.get_judge_verdicts_for_lead(lid)
                if verdicts:
                    last_created = datetime.fromisoformat(str(verdicts[-1]["created_at"]))
                    if last_created.tzinfo is None:
                        last_created = last_created.replace(tzinfo=timezone.utc)
                    self._watch_rechecks[lid] = last_created + watch_delay
                else:
                    self._watch_rechecks[lid] = now_utc

        due_watches = [
            lid for lid, due_at in self._watch_rechecks.items() if now_utc >= due_at
        ]
        for lid in due_watches:
            self._watch_rechecks.pop(lid, None)
            lead = await self.db.get_lead(lid)
            if lead is not None and lead["current_stage"] == "JUDGE":
                await self._evaluate_lead_through_gate(lead)

        leads = await self.scanner.scan(max_candidates=10)

        # Also pick up any leads already sitting in SEARCH or RISK (e.g. after restart)
        pending_search = await self.db.list_leads(stage="SEARCH", limit=10)
        seen_ids = {int(l["id"]) for l in leads}
        for ps in pending_search:
            if int(ps["id"]) not in seen_ids:
                leads.append(ps)
                seen_ids.add(int(ps["id"]))

        for lead in leads:
            risk_res = await self.risk_filter.evaluate_lead(lead)
            if not risk_res.passed:
                continue
            refreshed = await self.db.get_lead(int(lead["id"])) or lead
            await self._evaluate_lead_through_gate(refreshed, risk_result=risk_res)

        # Advance any pre-existing RISK/WHALE leads into JUDGE -> GATE
        for stage in ("RISK", "WHALE"):
            stuck_leads = await self.db.list_leads(stage=stage, limit=8)
            for stuck in stuck_leads:
                await self._evaluate_lead_through_gate(stuck)

    async def refresh_active_radar_prices(self) -> int:
        """Batch-refresh live MCap, liquidity, and price for tokens currently visible on the Radar."""
        active_stages = ("SEARCH", "RISK", "WHALE", "JUDGE", "GATE", "PENDING_APPROVAL", "OPEN", "RISK_FREE_RUNNER")
        by_mint: dict[str, list[dict[str, Any]]] = {}
        for stage in active_stages:
            for lead in await self.db.list_leads(stage=stage, limit=25):
                by_mint.setdefault(str(lead["mint_address"]), []).append(lead)

        mints = list(by_mint.keys())[:30]
        if not mints:
            return 0

        pairs = await self.scanner.fetch_pairs_for_mints(mints)
        best_by_mint: dict[str, dict[str, Any]] = {}
        for pair in pairs:
            mint = (pair.get("baseToken") or {}).get("address")
            if not mint or mint not in by_mint:
                continue
            liq = _safe_float((pair.get("liquidity") or {}).get("usd")) or 0.0
            prev = best_by_mint.get(mint)
            prev_liq = (_safe_float((prev.get("liquidity") or {}).get("usd")) or 0.0) if prev else -1.0
            if liq > prev_liq:
                best_by_mint[mint] = pair

        updated = 0
        for mint, pair in best_by_mint.items():
            mcap = _safe_float(
                pair.get("marketCap") if pair.get("marketCap") is not None else pair.get("fdv")
            )
            liq = _safe_float((pair.get("liquidity") or {}).get("usd"))
            px = _safe_float(pair.get("priceUsd"))
            for lead in by_mint[mint]:
                await self.db.update_lead(
                    int(lead["id"]),
                    market_cap=round(mcap, 2) if mcap is not None else None,
                    liquidity_usd=round(liq, 2) if liq is not None else None,
                    price_usd=px if px and px > 0 else None,
                )
                updated += 1
        return updated

    async def run_exit_cycle(self) -> list[ExitActionResult]:
        """Run one VolumetricExitManager monitoring cycle across active trades."""
        state = await self.db.get_bot_state()
        return await self.exit_manager.poll_all_open_trades(
            mode=str(state["trading_mode"])
        )

    # ------------------------------------------------------------------
    # Daily Reset & Scheduled Audits (21:00 Nightly + Sunday Calibration)
    # ------------------------------------------------------------------
    async def run_scheduled_audits(self, *, now: datetime | None = None) -> None:
        """Reset daily counters on UTC date rollover and emit 21:00 / Sunday Telegram reports."""
        now_utc = now or datetime.now(timezone.utc)
        today_utc = now_utc.date().isoformat()

        state = await self.db.get_bot_state()
        if state.get("last_reset_date") != today_utc:
            await self.db.update_bot_state(
                daily_sl_count=0,
                daily_pnl_sol=0.0,
                last_reset_date=today_utc,
            )

        tz = _resolve_timezone(self.settings.system_timezone)
        local_now = now_utc.astimezone(tz)
        local_today = local_now.date()

        due_time = (
            local_now.hour > int(self.settings.nightly_report_hour)
            or (
                local_now.hour == int(self.settings.nightly_report_hour)
                and local_now.minute >= int(self.settings.nightly_report_minute)
            )
        )
        if due_time and self._last_nightly_report_date != local_today:
            summary = await self.db.get_daily_summary(
                utc_today_iso(), mode=str(state["trading_mode"])
            )
            await self.notifier.notify_nightly_report(summary)
            self._last_nightly_report_date = local_today

        # Sunday (weekday == 6) calibration table
        if (
            local_now.weekday() == 6
            and due_time
            and self._last_sunday_audit_date != local_today
        ):
            report = await self.sunday_auditor.generate_weekly_report(
                days=7,
                mode=str(state["trading_mode"]),
                refresh_prices=True,
                now=now_utc,
            )
            await self.notifier.notify_sunday_calibration(
                report.formatted_text, str(state["trading_mode"])
            )
            self._last_sunday_audit_date = local_today

    # ------------------------------------------------------------------
    # Async Main Loops
    # ------------------------------------------------------------------
    async def _exit_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_exit_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Error in exit loop: %s", exc)
            interval = max(0.25, float(self.settings.exit_poll_interval_seconds))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _discovery_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_discovery_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Error in discovery loop: %s", exc)
            interval = int(self.settings.search_interval_seconds)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _maintenance_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.expire_pending_approvals()
                await self.run_scheduled_audits()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Error in maintenance loop: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                pass

    async def handle_telegram_command(self, cmd: str, args: str) -> str | None:
        """Handle interactive Telegram commands for status & Smart Wallets management."""
        path = self.settings.resolved_smart_wallets_path

        def _load_entries() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            payload: dict[str, Any] = {
                "version": 2,
                "description": "Curated Solana smart-money wallets.",
                "wallets": [],
            }
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        payload = raw
                except (OSError, ValueError):
                    pass
            entries: list[dict[str, Any]] = []
            for item in payload.get("wallets") or []:
                if isinstance(item, str) and item.strip():
                    entries.append({"address": item.strip(), "label": "", "added_at": None})
                elif isinstance(item, dict) and item.get("address"):
                    entries.append(dict(item))
            return payload, entries

        def _save_entries(payload: dict[str, Any], entries: list[dict[str, Any]]) -> None:
            payload["wallets"] = entries
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            self._smart_wallets = self._load_smart_wallets()

        if cmd == "/status":
            state = await self.db.get_bot_state()
            sol_usd = await self.price_oracle.get_sol_usd_price()
            open_trades = await self.db.get_open_trades(mode=str(state["trading_mode"]))
            avail = float(state["tutorial_available_sol"])
            pnl_sol = float(state["daily_pnl_sol"])
            _, entries = _load_entries()
            return (
                f"METEORO TRADER — ESTADO EN VIVO\n"
                f"Modo: {state['trading_mode']} | Perfil: {self.settings.strategy_profile}\n"
                f"Estado: {'PAUSADO' if state['is_paused'] else 'ACTIVO'}\n"
                f"Banca Disponible: {avail:.4f} SOL (${avail * sol_usd:.2f})\n"
                f"PnL Día: {pnl_sol:+.4f} SOL (${pnl_sol * sol_usd:+.2f})\n"
                f"Trades activos: {len(open_trades)}/{state['max_concurrent_trades']} | "
                f"SL hoy: {state['daily_sl_count']}/{self.settings.max_consecutive_sl}\n"
                f"Smart Wallets activas: {len(entries)}"
            )

        if cmd == "/wallets":
            _, entries = _load_entries()
            q = args.lower().strip()
            matched = [
                w
                for w in entries
                if not q
                or q in str(w.get("address", "")).lower()
                or q in str(w.get("label", "")).lower()
                or q in str(w.get("twitter_username") or "").lower()
            ]
            if not matched:
                return f"No encontré Smart Wallets que coincidan con '{args}' (Total: {len(entries)})."
            lines = [f"Smart Wallets ({len(matched)} de {len(entries)}):"]
            for idx, w in enumerate(matched[:12], 1):
                addr = str(w["address"])
                short = f"{addr[:6]}...{addr[-4:]}"
                lbl = str(w.get("label") or short)
                tw = f" (@{w['twitter_username']})" if w.get("twitter_username") else ""
                wr = w.get("winrate_30d") or w.get("winrate_7d")
                wr_txt = f" · WR {wr}%" if wr is not None else ""
                lines.append(f"{idx}. {lbl}{tw}{wr_txt}\n   {addr}")
            if len(matched) > 12:
                lines.append(f"... y {len(matched) - 12} más. Usa /wallets <texto> para filtrar.")
            return "\n".join(lines)

        if cmd == "/addwallet":
            parts = args.split(None, 1)
            if not parts:
                return "Uso: /addwallet <DIRECCION_SOLANA> [Alias]\nEjemplo:\n/addwallet CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o Ballena Pro"
            addr = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else ""
            try:
                from solders.pubkey import Pubkey
                Pubkey.from_string(addr)
            except Exception:
                return f"Dirección Solana inválida: {addr}\nVerifica que sea una dirección Base58 completa de Solana."
            payload, entries = _load_entries()
            if any(str(w.get("address")) == addr for w in entries):
                return f"Esa wallet ya está en tu lista. Usa /editwallet {addr} <Nuevo Alias> para modificarla."
            entries.insert(
                0,
                {
                    "address": addr,
                    "label": label or f"Wallet {addr[:6]}...{addr[-4:]}",
                    "category": "MANUAL_TG",
                    "source": "Agregada desde Telegram",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _save_entries(payload, entries)
            return (
                f"Smart Wallet agregada (#{len(entries)} en lista):\n"
                f"Alias: {label or addr[:6] + '...' + addr[-4:]}\n"
                f"Dirección: {addr}\n"
                f"Solscan: https://solscan.io/account/{addr}"
            )

        if cmd == "/editwallet":
            parts = args.split(None, 1)
            if len(parts) < 2:
                return "Uso: /editwallet <DIRECCION_O_PREFIJO> <Nuevo Alias>\nEjemplo:\n/editwallet CyaE1V Mi Trader Top"
            target, new_label = parts[0].strip(), parts[1].strip()
            payload, entries = _load_entries()
            hits = [w for w in entries if str(w.get("address", "")).startswith(target)]
            if not hits:
                return f"No encontré ninguna wallet que empiece por '{target}'."
            if len(hits) > 1:
                return f"Hay {len(hits)} wallets que empiezan por '{target}'. Escribe más caracteres de la dirección."
            old_label = hits[0].get("label") or ""
            hits[0]["label"] = new_label
            _save_entries(payload, entries)
            return f"Wallet actualizada:\n{hits[0]['address']}\nAntes: {old_label}\nAhora: {new_label}"

        if cmd == "/delwallet":
            target = args.strip()
            if not target:
                return "Uso: /delwallet <DIRECCION_O_PREFIJO>"
            payload, entries = _load_entries()
            hits = [w for w in entries if str(w.get("address", "")).startswith(target)]
            if not hits:
                return f"No encontré ninguna wallet que empiece por '{target}'."
            if len(hits) > 1:
                return f"Hay {len(hits)} wallets que empiezan por '{target}'. Escribe más caracteres de la dirección."
            removed = hits[0]
            remaining = [w for w in entries if w is not removed]
            _save_entries(payload, remaining)
            return f"Smart Wallet eliminada:\n{removed.get('label') or removed['address']}\nQuedan {len(remaining)} wallets en seguimiento."

        if cmd == "/wallet":
            from src.wallet_manager import (
                fetch_live_wallet_balance_sol,
                get_public_address_from_settings,
            )

            pub = get_public_address_from_settings(self.settings)
            if not pub:
                return (
                    "💼 <b>Billetera Real (LIVE) no configurada aún.</b>\n"
                    "Ábrela desde el Dashboard en <b>💼 Billetera Real</b> para importar tu clave privada Base58 o generar una wallet dedicada."
                )
            sol_usd = await self.price_oracle.get_sol_usd_price()
            bal = await fetch_live_wallet_balance_sol(self.settings, pub)
            bal_txt = f"{bal:.4f} SOL (${bal * sol_usd:.2f})" if bal is not None else "Consultando RPC..."
            return (
                f"💼 <b>BILLETERA REAL SOLANA</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Saldo:</b> <b>{bal_txt}</b>\n"
                f"🔐 <b>Doble Candado LIVE:</b> {'ACTIVO ✅' if self.settings.enable_live_execution else 'BLOQUEADO 🔒'}\n"
                f"🧾 <code>{pub}</code>\n"
                f"🔗 <a href=\"https://solscan.io/account/{pub}\">Ver en Solscan</a>"
            )

        if cmd == "/retiros":
            from src.wallet_manager import list_withdrawal_wallets

            dests = list_withdrawal_wallets()
            if not dests:
                return (
                    "🏦 <b>Sin wallets de retiro registradas.</b>\n"
                    "Registra una con:\n<code>/addretiro &lt;DIRECCION_SOLANA&gt; Mi Phantom</code>"
                )
            lines = [f"🏦 <b>Wallets de Retiro Autorizadas ({len(dests)}):</b>"]
            for i, d in enumerate(dests, 1):
                lines.append(f"{i}. <b>{d['label']}</b>\n   <code>{d['address']}</code>")
            lines.append("\nPara retirar usa:\n<code>/retirar 0.25 Mi Phantom</code>")
            return "\n".join(lines)

        if cmd == "/addretiro":
            from src.wallet_manager import add_withdrawal_wallet

            parts = args.split(None, 1)
            if not parts:
                return "Uso: <code>/addretiro &lt;DIRECCION_SOLANA&gt; [Alias]</code>"
            try:
                items = add_withdrawal_wallet(parts[0], parts[1] if len(parts) > 1 else "")
                return f"✅ Wallet de retiro registrada (#{len(items)}):\n<code>{parts[0]}</code>"
            except Exception as exc:
                return f"🔴 Error: {exc}"

        if cmd == "/retirar":
            from src.wallet_manager import list_withdrawal_wallets
            import secrets

            parts = args.split(None, 1)
            if len(parts) < 2:
                return "Uso: <code>/retirar &lt;CANTIDAD_SOL&gt; &lt;Alias o Prefijo&gt;</code>\nEj: <code>/retirar 0.25 Phantom</code>"
            try:
                amt = float(parts[0].replace(",", "."))
            except ValueError:
                return "Cantidad de SOL inválida."
            q = parts[1].strip().lower()
            dests = list_withdrawal_wallets()
            hits = [
                d
                for d in dests
                if q in str(d.get("label", "")).lower() or str(d.get("address", "")).lower().startswith(q)
            ]
            if not hits:
                return f"No encontré ninguna wallet de retiro autorizada que coincida con '{parts[1]}'. Revisa /retiros."
            dest = hits[0]
            req_id = secrets.token_hex(4)
            self._pending_withdrawals[req_id] = {
                "destination": dest["address"],
                "label": dest["label"],
                "amount_sol": amt,
            }
            kb = {
                "inline_keyboard": [
                    [
                        {"text": "✅ CONFIRMAR RETIRO", "callback_data": f"w_ok:{req_id}"},
                        {"text": "❌ CANCELAR", "callback_data": f"w_no:{req_id}"},
                    ]
                ]
            }
            return (
                f"💸 <b>CONFIRMAR RETIRO DE SOL</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Monto:</b> <b>{amt:.4f} SOL</b>\n"
                f"🎯 <b>Destino autorizado:</b> <b>{dest['label']}</b>\n"
                f"🧾 <code>{dest['address']}</code>\n\n"
                f"<i>Presiona confirmar para firmar y enviar vía Helius RPC.</i>",
                kb,
            )

        return None

    async def _telegram_callback_loop(self) -> None:
        """Listen for inline Telegram buttons and chat commands."""
        await self.notifier.delete_webhook()
        offset: int | None = None
        while not self._stop_event.is_set():
            try:
                updates = await self.notifier.get_updates(offset=offset, timeout=15)
                for upd in updates:
                    upd_id = int(upd.get("update_id") or 0)
                    offset = upd_id + 1
                    cb = upd.get("callback_query")
                    if isinstance(cb, dict):
                        await self.notifier.handle_callback_query(
                            cb,
                            on_approve=self.handle_telegram_approve,
                            on_reject=self.handle_telegram_reject,
                            on_refresh_trade=self.handle_telegram_refresh_trade,
                            on_close_trade=self.handle_telegram_close_trade,
                            on_withdraw_decision=self.handle_telegram_withdraw_decision,
                        )
                    msg = upd.get("message")
                    if isinstance(msg, dict):
                        await self.notifier.handle_incoming_message(
                            msg,
                            on_command=self.handle_telegram_command,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram callback polling error: %s", exc)
                await asyncio.sleep(3.0)

    async def _radar_refresh_loop(self) -> None:
        """Keep MCap, liquidity, and price ticking live every 5 seconds for Radar cards."""
        while not self._stop_event.is_set():
            try:
                await self.refresh_active_radar_prices()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Radar live refresh error: %s", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        """Signal all orchestrator background loops to stop gracefully."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Initialize DB, run Crash Recovery, and execute autonomous loops until stopped."""
        self._stop_event.clear()
        await self.recover_open_positions()
        tasks = [
            asyncio.create_task(self._exit_loop(), name="meteoro_exit_loop"),
            asyncio.create_task(self._discovery_loop(), name="meteoro_discovery_loop"),
            asyncio.create_task(self._radar_refresh_loop(), name="meteoro_radar_refresh_loop"),
            asyncio.create_task(self._maintenance_loop(), name="meteoro_maintenance_loop"),
        ]
        if self.notifier.has_token:
            tasks.append(
                asyncio.create_task(
                    self._telegram_callback_loop(), name="meteoro_telegram_callbacks"
                )
            )
        try:
            await self._stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.aclose()
