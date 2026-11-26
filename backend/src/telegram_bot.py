"""Telegram alerts and scheduled reporting for METEORO TRADER SOLANA."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from src.config import PROJECT_ROOT, Settings, get_settings
from src.security import sanitize_text

logger = logging.getLogger(__name__)


def mode_prefix(mode: str) -> str:
    """Always prefix every Telegram alert with [TUTORIAL] or [REAL]."""
    return "[REAL]" if str(mode).upper() == "LIVE" else "[TUTORIAL]"


class TelegramNotifier:
    """Async Telegram Bot API client with clean no-op behavior when unconfigured."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._owns_client = http_client is None
        self._client = http_client

    @property
    def has_token(self) -> bool:
        return bool(self.settings.telegram_bot_token.strip())

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.telegram_bot_token.strip()
            and self.settings.telegram_chat_id.strip()
        )

    def _persist_chat_id(self, chat_id: str) -> None:
        """Persist auto-discovered TELEGRAM_CHAT_ID to runtime settings and .env."""
        clean = str(chat_id).strip()
        if not clean:
            return
        self.settings.telegram_chat_id = clean
        env_path = PROJECT_ROOT / ".env"
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
            updated = False
            out: list[str] = []
            for line in lines:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == "TELEGRAM_CHAT_ID":
                    out.append(f"TELEGRAM_CHAT_ID={clean}")
                    updated = True
                else:
                    out.append(line)
            if not updated:
                out.append(f"TELEGRAM_CHAT_ID={clean}")
            env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist TELEGRAM_CHAT_ID to .env: %s", exc)

    async def delete_webhook(self) -> bool:
        """Clear any stale webhook so Telegram getUpdates long-polling never hits 409."""
        if not self.has_token:
            return False
        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token.strip()}"
            "/deleteWebhook"
        )
        try:
            client = await self._get_client()
            resp = await client.post(url, json={"drop_pending_updates": False}, timeout=8.0)
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    async def handle_incoming_message(
        self,
        message: dict[str, Any],
        *,
        on_command: Callable[[str, str], Awaitable[str | None]] | None = None,
    ) -> bool:
        """Auto-bind chat_id and dispatch Telegram commands (/status, /wallets, /addwallet, ...)."""
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            return False
        text = str(message.get("text") or "").strip()

        if not self.settings.telegram_chat_id.strip():
            self._persist_chat_id(chat_id)
            logger.info("Telegram chat_id auto-connected via incoming message.")
        elif chat_id != self.settings.telegram_chat_id.strip():
            return False

        if not text:
            return False

        parts = text.split(None, 1)
        cmd = parts[0].lower().split("@", 1)[0]
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"/start", "/ayuda", "/help"}:
            await self.send_message(
                "⚡ <b>METEORO TRADER — COMANDO CENTRAL</b>\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "📊 /status — Estado, banca en vivo y PnL\n"
                "💼 /wallet — Ver billetera real (LIVE) y saldo SOL\n"
                "🐋 /wallets [filtro] — Ver o buscar Smart Wallets\n"
                "➕ /addwallet &lt;DIR&gt; [Alias] — Agregar Smart Wallet\n"
                "✏️ /editwallet &lt;DIR&gt; &lt;Alias&gt; — Modificar alias\n"
                "🗑️ /delwallet &lt;DIR&gt; — Quitar Smart Wallet\n"
                "🏦 /retiros — Ver wallets destino autorizadas\n"
                "➕ /addretiro &lt;DIR&gt; &lt;Alias&gt; — Registrar destino retiro\n"
                "💸 /retirar &lt;SOL&gt; &lt;Alias/Prefijo&gt; — Retirar SOL a destino autorizado\n"
                "ℹ️ /ayuda — Mostrar este menú",
                parse_mode="HTML",
            )
            return True

        if on_command is not None and cmd.startswith("/"):
            reply = await on_command(cmd, args)
            if isinstance(reply, tuple):
                msg_text, markup = reply
                if msg_text:
                    await self.send_message(msg_text, reply_markup=markup, parse_mode="HTML")
                    return True
            elif reply:
                await self.send_message(reply, parse_mode="HTML")
                return True
        return False

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0))
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def send_message(
        self,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> bool:
        """Send a secret-sanitized message (optionally with inline keyboard & HTML) to Telegram."""
        safe_text = sanitize_text(text, self.settings.secret_values())
        if not self.is_configured:
            logger.debug("Telegram not configured; skipping alert: %s", safe_text.splitlines()[0])
            return False

        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token.strip()}"
            "/sendMessage"
        )
        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id.strip(),
            "text": safe_text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            client = await self._get_client()
            resp = await client.post(url, json=payload, timeout=8.0)
            if resp.status_code >= 400 and parse_mode:
                # Fallback to plain text if HTML entity rejected
                payload.pop("parse_mode", None)
                resp = await client.post(url, json=payload, timeout=8.0)
            if resp.status_code >= 400:
                logger.warning(
                    "Telegram API HTTP %s: %s",
                    resp.status_code,
                    sanitize_text(resp.text[:200], self.settings.secret_values()),
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning(
                "Telegram send failed: %s",
                sanitize_text(str(exc), self.settings.secret_values()),
            )
            return False

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
        *,
        show_alert: bool = False,
    ) -> bool:
        """Acknowledge an inline button tap in Telegram."""
        if not self.is_configured:
            return False
        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token.strip()}"
            "/answerCallbackQuery"
        )
        payload = {
            "callback_query_id": callback_query_id,
            "text": sanitize_text(text, self.settings.secret_values())[:200],
            "show_alert": show_alert,
        }
        try:
            client = await self._get_client()
            resp = await client.post(url, json=payload, timeout=8.0)
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    async def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
    ) -> bool:
        """Update an existing Telegram message in place."""
        if not self.is_configured:
            return False
        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token.strip()}"
            "/editMessageText"
        )
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": sanitize_text(text, self.settings.secret_values()),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            client = await self._get_client()
            resp = await client.post(url, json=payload, timeout=8.0)
            if resp.status_code >= 400 and parse_mode:
                payload.pop("parse_mode", None)
                resp = await client.post(url, json=payload, timeout=8.0)
            return resp.status_code < 400
        except httpx.HTTPError:
            return False

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 20,
    ) -> list[dict[str, Any]]:
        """Long-poll Telegram updates for inline callback queries and /start auto-binding."""
        if not self.has_token:
            return []
        url = (
            f"https://api.telegram.org/bot{self.settings.telegram_bot_token.strip()}"
            "/getUpdates"
        )
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query", "message"],
        }
        if offset is not None:
            params["offset"] = offset
        try:
            client = await self._get_client()
            resp = await client.post(url, json=params, timeout=float(timeout) + 5.0)
            if resp.status_code >= 400:
                return []
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get("result"), list):
                return body["result"]
            return []
        except (httpx.HTTPError, ValueError):
            return []

    async def handle_callback_query(
        self,
        callback_query: dict[str, Any],
        *,
        on_approve: Callable[[int], Awaitable[str]],
        on_reject: Callable[[int], Awaitable[str]],
        on_refresh_trade: Callable[[int], Awaitable[tuple[str, str | None, dict[str, Any] | None]]] | None = None,
        on_close_trade: Callable[[int], Awaitable[tuple[str, str | None]]] | None = None,
        on_withdraw_decision: Callable[[str, bool], Awaitable[tuple[str, str | None]]] | None = None,
    ) -> str:
        """Handle inline Telegram buttons: approve/reject, refresh/close trade, & confirm withdrawal."""
        cb_id = str(callback_query.get("id") or "")
        data = str(callback_query.get("data") or "")
        msg = callback_query.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id") or "")
        message_id = msg.get("message_id")
        original_text = str(msg.get("text") or "")

        authorized_chat = self.settings.telegram_chat_id.strip()
        if authorized_chat and chat_id != authorized_chat:
            if cb_id:
                await self.answer_callback_query(cb_id, "Chat no autorizado.", show_alert=True)
            return "UNAUTHORIZED"

        if ":" not in data:
            if cb_id:
                await self.answer_callback_query(cb_id, "Acción inválida.")
            return "INVALID"

        action, raw_id = data.split(":", 1)

        if action in {"w_ok", "w_no"} and on_withdraw_decision is not None:
            toast, new_text = await on_withdraw_decision(raw_id, action == "w_ok")
            if cb_id:
                await self.answer_callback_query(cb_id, toast)
            if message_id is not None and chat_id and new_text:
                await self.edit_message_text(chat_id, int(message_id), new_text)
            return toast

        try:
            item_id = int(raw_id)
        except ValueError:
            if cb_id:
                await self.answer_callback_query(cb_id, "ID inválido.")
            return "INVALID"

        if action == "refresh" and on_refresh_trade is not None:
            toast, new_text, new_markup = await on_refresh_trade(item_id)
            if cb_id:
                await self.answer_callback_query(cb_id, toast)
            if message_id is not None and chat_id and new_text:
                await self.edit_message_text(
                    chat_id, int(message_id), new_text, reply_markup=new_markup
                )
            return toast

        if action == "close" and on_close_trade is not None:
            toast, new_text = await on_close_trade(item_id)
            if cb_id:
                await self.answer_callback_query(cb_id, toast)
            if message_id is not None and chat_id and new_text:
                await self.edit_message_text(chat_id, int(message_id), new_text)
            return toast

        if action == "approve":
            status_msg = await on_approve(item_id)
        elif action == "reject":
            status_msg = await on_reject(item_id)
        else:
            if cb_id:
                await self.answer_callback_query(cb_id, "Acción desconocida.")
            return "UNKNOWN"

        if cb_id:
            await self.answer_callback_query(cb_id, status_msg)
        if message_id is not None and chat_id:
            updated_text = f"{html.escape(original_text)}\n\n<b>Resultado:</b> {html.escape(status_msg)}".strip()
            await self.edit_message_text(chat_id, int(message_id), updated_text)
        return status_msg

    @staticmethod
    def trade_inline_keyboard(trade: dict[str, Any], *, include_controls: bool = True) -> dict[str, Any]:
        mint = str(trade["mint_address"])
        trade_id = trade.get("id")
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "📈 DexScreener", "url": f"https://dexscreener.com/solana/{mint}"},
                {"text": "🧠 GMGN", "url": f"https://gmgn.ai/sol/token/{mint}"},
            ],
            [
                {"text": "🔥 FOMO", "url": f"https://fomo.family/token/{mint}"},
                {"text": "⚡ Pump.fun", "url": f"https://pump.fun/coin/{mint}"},
                {"text": "🔎 Solscan", "url": f"https://solscan.io/token/{mint}"},
            ],
        ]
        if include_controls and trade_id is not None and str(trade.get("status")) != "CLOSED":
            rows.append(
                [
                    {"text": "🔄 Actualizar en Vivo", "callback_data": f"refresh:{int(trade_id)}"},
                    {"text": "🚪 Cerrar 100%", "callback_data": f"close:{int(trade_id)}"},
                ]
            )
        return {"inline_keyboard": rows}

    @staticmethod
    def format_trade_open(
        trade: dict[str, Any],
        *,
        ai_brief: str | None = None,
        socials: list[str] | None = None,
        mcap_usd: float | None = None,
        liq_usd: float | None = None,
        whales_matched: int = 0,
    ) -> str:
        prefix = mode_prefix(trade.get("mode", "TUTORIAL"))
        sym = html.escape(str(trade["symbol"]))
        mint = html.escape(str(trade["mint_address"]))
        inv_sol = float(trade["invested_sol"])
        inv_usd = float(trade["invested_usd"])
        lines = [
            f"🚀 <b>{prefix} COMPRA ABIERTA · ${sym}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"💰 <b>Invertido:</b> <code>{inv_sol:.4f} SOL</code> (<b>${inv_usd:.2f}</b>)",
            f"🎯 <b>Spot:</b> <code>${float(trade['entry_price_usd']):.8f}</code>",
            f"⚡ <b>Efectivo:</b> <code>${float(trade['effective_entry_price_usd']):.8f}</code>",
            f"⛽ <b>Fees entrada:</b> <code>{float(trade['fees_paid_sol']):.5f} SOL</code>",
        ]
        if mcap_usd or liq_usd:
            mc_txt = f"${mcap_usd:,.0f}" if mcap_usd else "—"
            lq_txt = f"${liq_usd:,.0f}" if liq_usd else "—"
            lines.append(f"📊 <b>MCap:</b> {mc_txt} | <b>Liquidez:</b> {lq_txt}")
        if whales_matched > 0:
            lines.append(f"🐋 <b>Smart Wallets dentro:</b> {whales_matched} detectada(s)")
        if socials:
            lines.append(f"🌐 <b>Redes:</b> {' · '.join(socials[:4])}")
        if ai_brief:
            lines.append(f"\n🤖 <b>Análisis IA:</b>\n<i>{html.escape(ai_brief)}</i>")
        lines.append(f"\n🧾 <code>{mint}</code>")
        return "\n".join(lines)

    @staticmethod
    def format_trade_live_status(
        trade: dict[str, Any],
        *,
        live_price: float,
        live_pnl_sol: float,
        live_pnl_usd: float,
        live_pnl_pct: float,
        tp_target_pct: float,
        updated_hhmmss: str,
    ) -> str:
        prefix = mode_prefix(trade.get("mode", "TUTORIAL"))
        sym = html.escape(str(trade["symbol"]))
        mint = html.escape(str(trade["mint_address"]))
        tp_prog = min(1000.0, max(0.0, (live_pnl_pct / tp_target_pct) * 100.0)) if tp_target_pct > 0 and live_pnl_pct > 0 else 0.0
        icon = "🔵⚡" if tp_prog >= 100 else ("🟢" if live_pnl_sol >= 0 else "🔴")
        return (
            f"{icon} <b>{prefix} EN VIVO · ${sym}</b> ({html.escape(str(trade['status']))})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>PnL Actual:</b> <b>{live_pnl_sol:+.4f} SOL</b> (<b>${live_pnl_usd:+.2f}</b> | <b>{live_pnl_pct:+.1f}%</b>)\n"
            f"🎯 <b>Meta TP (+{tp_target_pct:.0f}%):</b> <b>{tp_prog:.0f}%</b>\n"
            f"📈 <b>Precio actual:</b> <code>${live_price:.8f}</code>\n"
            f"💰 <b>Invertido:</b> <code>{float(trade['invested_sol']):.4f} SOL</code> | <b>Cobrado:</b> <code>{float(trade['realized_sol']):.4f} SOL</code>\n"
            f"🕒 <i>Actualizado: {updated_hhmmss} UTC</i>\n"
            f"🧾 <code>{mint}</code>"
        )

    @staticmethod
    def format_tp1_hit(trade: dict[str, Any]) -> str:
        prefix = mode_prefix(trade.get("mode", "TUTORIAL"))
        sym = html.escape(str(trade["symbol"]))
        pnl_sol = float(trade.get("pnl_sol") or 0.0)
        pnl_usd = float(trade.get("pnl_usd") or 0.0)
        pnl_pct = float(trade.get("pnl_pct") or 0.0)
        return (
            f"💎 <b>{prefix} TP1 PARCIAL COBRADO · ${sym}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Invertido:</b> <code>{float(trade['invested_sol']):.4f} SOL</code> (${float(trade['invested_usd']):.2f})\n"
            f"🏦 <b>Cobrado:</b> <code>{float(trade['realized_sol']):.4f} SOL</code>\n"
            f"📈 <b>Ganancia:</b> <b>{pnl_sol:+.4f} SOL</b> (<b>${pnl_usd:+.2f}</b> | <b>{pnl_pct:+.2f}%</b>)\n"
            f"🛡️ <b>Piso protegido:</b> Breakeven activo"
        )

    @staticmethod
    def format_capital_recovered(trade: dict[str, Any]) -> str:
        prefix = mode_prefix(trade.get("mode", "TUTORIAL"))
        sym = html.escape(str(trade["symbol"]))
        return (
            f"🔥 <b>{prefix} CAPITAL RECUPERADO · ${sym} → RUNNER LIBRE</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Invertido:</b> <code>{float(trade['invested_sol']):.4f} SOL</code> | "
            f"<b>Recuperado:</b> <code>{float(trade['realized_sol']):.4f} SOL</code>\n"
            f"🚀 <b>Tokens libres corriendo:</b> <code>{float(trade['tokens_remaining']):.2f}</code>\n"
            f"📈 <b>PnL Total:</b> <b>{float(trade['pnl_sol']):+.4f} SOL</b> (<b>${float(trade['pnl_usd']):+.2f}</b>)"
        )

    @staticmethod
    def format_trade_closed(trade: dict[str, Any]) -> str:
        prefix = mode_prefix(trade.get("mode", "TUTORIAL"))
        sym = html.escape(str(trade["symbol"]))
        mint = html.escape(str(trade["mint_address"]))
        inv_sol = float(trade["invested_sol"])
        inv_usd = float(trade.get("invested_usd") or 0.0)
        real_sol = float(trade["realized_sol"])
        pnl_sol = float(trade.get("pnl_sol") or 0.0)
        pnl_usd = float(trade.get("pnl_usd") or 0.0)
        pnl_pct = float(trade.get("pnl_pct") or 0.0)
        real_usd = max(0.0, inv_usd + pnl_usd) if inv_usd > 0 else 0.0
        badge = "✅ GANANCIA" if pnl_sol >= 0 else "🔴 PÉRDIDA"
        reason = html.escape(str(trade.get("exit_reason") or "CLOSED"))
        return (
            f"{badge} · <b>{prefix} TRADE CERRADO · ${sym}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ <b>Motivo:</b> <code>{reason}</code>\n"
            f"💰 <b>Invertido:</b> <code>{inv_sol:.4f} SOL</code> (${inv_usd:.2f})\n"
            f"🏦 <b>Recibido:</b> <code>{real_sol:.4f} SOL</code> (${real_usd:.2f})\n"
            f"📊 <b>PnL Neto:</b> <b>{pnl_sol:+.4f} SOL</b> (<b>${pnl_usd:+.2f}</b> | <b>{pnl_pct:+.2f}%</b>)\n"
            f"⛽ <b>Fees:</b> <code>{float(trade['fees_paid_sol']):.5f} SOL</code> | "
            f"<b>Slippage:</b> <code>{float(trade['slippage_cost_sol']):.5f} SOL</code>\n"
            f"🧾 <code>{mint}</code>"
        )

    @staticmethod
    def format_pending_approval(
        lead: dict[str, Any],
        action_prob: float,
        size_sol: float,
        timeout_seconds: int,
    ) -> str:
        sym = html.escape(str(lead["symbol"]))
        mint = html.escape(str(lead["mint_address"]))
        return (
            f"⏳ <b>[SEMI-AUTO] APROBACIÓN REQUERIDA ({timeout_seconds}s) · ${sym}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>MCap:</b> ${(lead.get('market_cap') or 0):,.0f} | "
            f"<b>Liq:</b> ${(lead.get('liquidity_usd') or 0):,.0f}\n"
            f"🧠 <b>Confianza Juez:</b> {action_prob * 100:.0f}%\n"
            f"💰 <b>Entrada fija:</b> <code>{size_sol:.4f} SOL</code>\n"
            f"🧾 <code>{mint}</code>\n"
            f"<i>Confirma abajo o desde el Dashboard.</i>"
        )

    @staticmethod
    def format_nightly_report(summary: dict[str, Any]) -> str:
        prefix = mode_prefix(summary.get("mode", "TUTORIAL"))
        state = summary.get("bot_state") or {}
        pnl_sol = float(summary["net_pnl_sol"])
        pnl_usd = float(summary["net_pnl_usd"])
        icon = "🟢" if pnl_sol >= 0 else "🔴"
        return (
            f"📊 <b>{prefix} REPORTE NOCTURNO ({html.escape(str(summary['date']))})</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Abiertos hoy:</b> {summary['opened_count']} | <b>Cerrados:</b> {summary['closed_count']} | <b>Activos:</b> {summary['open_count']}\n"
            f"🏆 <b>Wins / Losses:</b> {summary['wins']}W / {summary['losses']}L\n"
            f"{icon} <b>PnL Neto Día:</b> <b>{pnl_sol:+.4f} SOL</b> (<b>${pnl_usd:+.2f}</b>)\n"
            f"⛽ <b>Fees:</b> <code>{float(summary['total_fees_sol']):.5f} SOL</code> | <b>Slippage:</b> <code>{float(summary['total_slippage_sol']):.5f} SOL</code>\n"
            f"💼 <b>Disponible:</b> <code>{float(state.get('tutorial_available_sol') or 0.0):.4f} SOL</code> | <b>SL hoy:</b> {int(state.get('daily_sl_count') or 0)}"
        )

    async def notify_trade_open(
        self,
        trade: dict[str, Any],
        *,
        ai_brief: str | None = None,
        socials: list[str] | None = None,
        mcap_usd: float | None = None,
        liq_usd: float | None = None,
        whales_matched: int = 0,
    ) -> bool:
        return await self.send_message(
            self.format_trade_open(
                trade,
                ai_brief=ai_brief,
                socials=socials,
                mcap_usd=mcap_usd,
                liq_usd=liq_usd,
                whales_matched=whales_matched,
            ),
            reply_markup=self.trade_inline_keyboard(trade, include_controls=True),
        )

    async def notify_tp1_hit(self, trade: dict[str, Any]) -> bool:
        return await self.send_message(
            self.format_tp1_hit(trade),
            reply_markup=self.trade_inline_keyboard(trade, include_controls=True),
        )

    async def notify_capital_recovered(self, trade: dict[str, Any]) -> bool:
        return await self.send_message(
            self.format_capital_recovered(trade),
            reply_markup=self.trade_inline_keyboard(trade, include_controls=True),
        )

    async def notify_trade_closed(self, trade: dict[str, Any]) -> bool:
        return await self.send_message(
            self.format_trade_closed(trade),
            reply_markup=self.trade_inline_keyboard(trade, include_controls=False),
        )

    async def notify_pending_approval(
        self,
        lead: dict[str, Any],
        action_prob: float,
        size_sol: float,
        timeout_seconds: int = 90,
    ) -> bool:
        lead_id = int(lead["id"])
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ APROBAR", "callback_data": f"approve:{lead_id}"},
                    {"text": "❌ DESCARTAR", "callback_data": f"reject:{lead_id}"},
                ]
            ]
        }
        return await self.send_message(
            self.format_pending_approval(lead, action_prob, size_sol, timeout_seconds),
            reply_markup=keyboard,
        )

    async def notify_nightly_report(self, summary: dict[str, Any]) -> bool:
        return await self.send_message(self.format_nightly_report(summary))

    async def notify_sunday_calibration(self, report_text: str, mode: str) -> bool:
        prefix = mode_prefix(mode)
        return await self.send_message(f"{prefix} AUDITORÍA DOMINICAL\n{report_text}")
