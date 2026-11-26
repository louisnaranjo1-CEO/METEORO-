"""Hardened Real-Money Execution Engine (Jupiter v6 + Jito Block Engine + solders).

Enforces strict safety invariants:
1. Dual-lock: refuses execution unless TRADING_MODE == "LIVE" AND ENABLE_LIVE_EXECUTION is True.
2. Hard caps: blocks orders exceeding MAX_LIVE_TRADE_SOL and enforces a hard minimum
   0.15 SOL gas/Jito reserve in the wallet.
3. On-chain truth: verifies real SPL token balances via Solana RPC before every sell.
4. Zero secret leaks: private keys live only inside an in-memory ``Keypair`` and are never
   logged, stored in SQLite, or exposed in exceptions.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from src.config import Settings, TradingMode, get_settings
from src.database import Database, utc_now_iso
from src.execution.base import (
    ExecutionEngine,
    ExecutionError,
    ExecutionReceipt,
    InsufficientBalanceError,
)
from src.price_oracle import SolPriceOracle
from src.security import sanitize_text

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL: int = 1_000_000_000
WSOL_MINT: str = "So11111111111111111111111111111111111111112"


class LiveSafetyLockError(ExecutionError):
    """Raised when LIVE execution is attempted without both dual-lock conditions."""


class LiveHardCapError(ExecutionError):
    """Raised when an order violates MAX_LIVE_TRADE_SOL or other hard safety caps."""


class InsufficientGasReserveError(InsufficientBalanceError):
    """Raised when executing a trade would breach the 0.15 SOL minimum gas reserve."""


def _parse_keypair(raw_secret: str) -> Keypair:
    """Parse a Solana private key from Base58 string or 64-byte JSON array."""
    secret = raw_secret.strip()
    if not secret:
        raise LiveSafetyLockError("SOLANA_PRIVATE_KEY is empty.")
    try:
        if secret.startswith("["):
            byte_list = json.loads(secret)
            if not isinstance(byte_list, list) or len(byte_list) != 64:
                raise ValueError("JSON keypair must contain 64 byte integers.")
            return Keypair.from_bytes(bytes(int(b) for b in byte_list))
        return Keypair.from_base58_string(secret)
    except Exception as exc:
        raise LiveSafetyLockError("Invalid SOLANA_PRIVATE_KEY format.") from exc


class LiveEngine(ExecutionEngine):
    """Production Solana execution adapter using Jupiter Swap v6 + Jito bundles."""

    HARD_MIN_GAS_RESERVE_SOL: float = 0.15

    def __init__(
        self,
        db: Database,
        *,
        settings: Settings | None = None,
        price_oracle: SolPriceOracle | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.price_oracle = price_oracle
        self._owns_client = http_client is None
        self._client = http_client
        self._keypair: Keypair | None = None

    def __repr__(self) -> str:
        pub = self.wallet_public_key or "UNLOADED"
        return f"<LiveEngine mode={self.settings.trading_mode.value} pubkey={pub}>"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=4.0),
                headers={"Accept": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _load_keypair(self) -> Keypair:
        if self._keypair is None:
            self._keypair = _parse_keypair(self.settings.effective_solana_private_key)
        return self._keypair

    @property
    def wallet_public_key(self) -> str | None:
        if self._keypair is not None:
            return str(self._keypair.pubkey())
        raw = self.settings.effective_solana_private_key
        if not raw:
            return self.settings.live_wallet_public_key.strip() or None
        try:
            return str(self._load_keypair().pubkey())
        except LiveSafetyLockError:
            return None

    def _rpc_url(self) -> str:
        rpc = self.settings.solana_rpc_url.strip()
        helius = self.settings.helius_api_key.strip()
        if (
            helius
            and "mainnet-beta.solana.com" in rpc
        ):
            return f"https://mainnet.helius-rpc.com/?api-key={helius}"
        return rpc

    def _assert_dual_lock(self) -> Keypair:
        """Enforce TRADING_MODE == LIVE and ENABLE_LIVE_EXECUTION == True before any call."""
        if self.settings.trading_mode != TradingMode.LIVE:
            raise LiveSafetyLockError(
                f"LiveEngine blocked: TRADING_MODE is {self.settings.trading_mode.value}, expected LIVE."
            )
        if not self.settings.enable_live_execution:
            raise LiveSafetyLockError(
                "LiveEngine blocked by dual-lock: ENABLE_LIVE_EXECUTION must be true."
            )
        return self._load_keypair()

    async def _resolve_sol_usd(self, sol_usd_price: float | None) -> float:
        if sol_usd_price is not None and sol_usd_price > 0:
            return float(sol_usd_price)
        if self.price_oracle is not None:
            return await self.price_oracle.get_sol_usd_price()
        return float(self.settings.fallback_sol_usd_price)

    def _safe_error(self, text: str) -> str:
        return sanitize_text(text, self.settings.secret_values())

    # ------------------------------------------------------------------
    # Solana RPC Queries
    # ------------------------------------------------------------------
    async def _rpc_call(self, method: str, params: list[Any]) -> Any:
        client = await self._get_client()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            resp = await client.post(self._rpc_url(), json=payload, timeout=10.0)
            if resp.status_code >= 400:
                raise ExecutionError(
                    self._safe_error(f"Solana RPC HTTP {resp.status_code}: {resp.text[:200]}")
                )
            body = resp.json()
        except httpx.HTTPError as exc:
            raise ExecutionError(self._safe_error(f"Solana RPC error ({method}): {exc}")) from exc

        if isinstance(body, dict) and body.get("error"):
            raise ExecutionError(self._safe_error(f"Solana RPC {method} failed: {body['error']}"))
        return body.get("result") if isinstance(body, dict) else None

    async def get_sol_balance_sol(self, pubkey: str) -> float:
        """Query real on-chain SOL balance for ``pubkey``."""
        result = await self._rpc_call("getBalance", [pubkey, {"commitment": "confirmed"}])
        lamports = int((result or {}).get("value") or 0)
        return lamports / LAMPORTS_PER_SOL

    async def get_token_balance(
        self, pubkey: str, mint_address: str
    ) -> tuple[int, int, float]:
        """Query real on-chain SPL token balance returning ``(raw_amount, decimals, ui_amount)``."""
        result = await self._rpc_call(
            "getTokenAccountsByOwner",
            [
                pubkey,
                {"mint": mint_address},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )
        accounts = (result or {}).get("value") or []
        total_raw = 0
        decimals = 0
        total_ui = 0.0
        for acc in accounts:
            parsed = (
                ((acc.get("account") or {}).get("data") or {}).get("parsed") or {}
            ).get("info") or {}
            token_amount = parsed.get("tokenAmount") or {}
            try:
                raw_str = token_amount.get("amount") or "0"
                total_raw += int(raw_str)
                decimals = int(token_amount.get("decimals") or decimals)
                total_ui += float(token_amount.get("uiAmount") or 0.0)
            except (TypeError, ValueError):
                continue
        if total_ui == 0.0 and total_raw > 0 and decimals >= 0:
            total_ui = total_raw / (10**decimals)
        return total_raw, decimals, total_ui

    async def confirm_signature(
        self, signature: str, *, timeout_seconds: float = 25.0, poll_interval: float = 1.0
    ) -> bool:
        """Poll Solana RPC until transaction signature reaches confirmed/finalized."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            result = await self._rpc_call(
                "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]
            )
            statuses = (result or {}).get("value") or []
            status = statuses[0] if statuses else None
            if isinstance(status, dict):
                if status.get("err") is not None:
                    raise ExecutionError(
                        self._safe_error(
                            f"On-chain transaction failed ({signature}): {status['err']}"
                        )
                    )
                conf = str(status.get("confirmationStatus") or "")
                if conf in {"confirmed", "finalized"}:
                    return True
            await asyncio.sleep(poll_interval)
        raise ExecutionError(f"Transaction confirmation timeout for {signature}")

    # ------------------------------------------------------------------
    # Jupiter Swap API v6 + Local Signing + Jito Block Engine
    # ------------------------------------------------------------------
    async def _jupiter_quote(
        self,
        *,
        input_mint: str,
        output_mint: str,
        amount_raw: int,
        slippage_bps: int,
    ) -> dict[str, Any]:
        base = self.settings.jupiter_api_base_url.rstrip("/")
        client = await self._get_client()
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_raw),
            "slippageBps": str(slippage_bps),
        }
        try:
            resp = await client.get(f"{base}/quote", params=params, timeout=10.0)
            if resp.status_code >= 400:
                raise ExecutionError(
                    self._safe_error(f"Jupiter quote HTTP {resp.status_code}: {resp.text[:200]}")
                )
            quote = resp.json()
        except httpx.HTTPError as exc:
            raise ExecutionError(self._safe_error(f"Jupiter quote failed: {exc}")) from exc
        if not isinstance(quote, dict) or not quote.get("outAmount"):
            raise ExecutionError("Jupiter quote returned invalid payload (missing outAmount).")
        return quote

    async def _jupiter_swap_tx(
        self, quote_response: dict[str, Any], user_pubkey: str
    ) -> str:
        base = self.settings.jupiter_api_base_url.rstrip("/")
        client = await self._get_client()
        body = {
            "quoteResponse": quote_response,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": int(self.settings.live_priority_fee_lamports),
        }
        try:
            resp = await client.post(f"{base}/swap", json=body, timeout=10.0)
            if resp.status_code >= 400:
                raise ExecutionError(
                    self._safe_error(f"Jupiter swap HTTP {resp.status_code}: {resp.text[:200]}")
                )
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise ExecutionError(self._safe_error(f"Jupiter swap failed: {exc}")) from exc
        swap_tx = payload.get("swapTransaction") if isinstance(payload, dict) else None
        if not isinstance(swap_tx, str) or not swap_tx:
            raise ExecutionError("Jupiter swap missing base64 swapTransaction.")
        return swap_tx

    @staticmethod
    def _sign_versioned_tx(swap_tx_b64: str, keypair: Keypair) -> tuple[str, str]:
        """Deserialize Jupiter's base64 VersionedTransaction and sign locally with ``solders``."""
        try:
            raw_bytes = base64.b64decode(swap_tx_b64)
            unsigned_tx = VersionedTransaction.from_bytes(raw_bytes)
            signed_tx = VersionedTransaction(unsigned_tx.message, [keypair])
            signed_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")
            signature = str(signed_tx.signatures[0])
            return signed_b64, signature
        except Exception as exc:
            raise ExecutionError(f"Failed local transaction signing: {exc}") from exc

    async def _send_jito_bundle(self, signed_tx_b64: str) -> str:
        base = self.settings.jito_block_engine_url.rstrip("/")
        url = f"{base}/api/v1/bundles"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[signed_tx_b64], {"encoding": "base64"}],
        }
        client = await self._get_client()
        try:
            resp = await client.post(url, json=payload, timeout=10.0)
            if resp.status_code >= 400:
                raise ExecutionError(
                    self._safe_error(f"Jito bundle HTTP {resp.status_code}: {resp.text[:200]}")
                )
            body = resp.json()
        except httpx.HTTPError as exc:
            raise ExecutionError(self._safe_error(f"Jito bundle submission failed: {exc}")) from exc

        if isinstance(body, dict) and body.get("error"):
            raise ExecutionError(self._safe_error(f"Jito bundle rejected: {body['error']}"))
        bundle_id = body.get("result") if isinstance(body, dict) else None
        if not bundle_id:
            raise ExecutionError("Jito sendBundle returned empty bundle id.")
        return str(bundle_id)

    # ------------------------------------------------------------------
    # ExecutionEngine Interface (Buy & Sell)
    # ------------------------------------------------------------------
    async def execute_buy(
        self,
        lead: dict[str, Any],
        amount_sol: float,
        *,
        spot_price_usd: float | None = None,
        sol_usd_price: float | None = None,
    ) -> ExecutionReceipt:
        """Execute a real SOL -> Token swap via Jupiter v6 + Jito with hard caps & reserve."""
        keypair = self._assert_dual_lock()
        pubkey = str(keypair.pubkey())

        if amount_sol <= 0:
            raise ExecutionError("amount_sol must be positive.")
        if amount_sol > float(self.settings.max_live_trade_sol):
            raise LiveHardCapError(
                f"Order {amount_sol:.4f} SOL exceeds MAX_LIVE_TRADE_SOL "
                f"({self.settings.max_live_trade_sol:.4f} SOL)."
            )

        entry_price_usd = (
            float(spot_price_usd)
            if spot_price_usd is not None
            else float(lead.get("price_usd") or 0.0)
        )
        if entry_price_usd <= 0:
            raise ExecutionError(
                f"Cannot buy {lead.get('symbol', '?')}: missing positive spot price_usd."
            )

        tx_fee_sol = round(
            (
                int(self.settings.live_priority_fee_lamports)
                + int(self.settings.live_jito_tip_lamports)
            )
            / LAMPORTS_PER_SOL,
            9,
        )
        min_reserve = max(
            self.HARD_MIN_GAS_RESERVE_SOL,
            float(self.settings.min_live_gas_reserve_sol),
        )

        # Enforce on-chain gas reserve BEFORE touching Jupiter or Jito
        wallet_sol = await self.get_sol_balance_sol(pubkey)
        remaining_after = wallet_sol - (amount_sol + tx_fee_sol)
        if remaining_after < min_reserve - 1e-9:
            raise InsufficientGasReserveError(
                f"LIVE buy blocked by minimum gas reserve ({min_reserve:.2f} SOL): "
                f"wallet={wallet_sol:.4f} SOL, order+fees={amount_sol + tx_fee_sol:.4f} SOL, "
                f"remaining={remaining_after:.4f} SOL."
            )

        sol_usd = await self._resolve_sol_usd(sol_usd_price)
        mint = str(lead["mint_address"])
        amount_lamports = int(round(amount_sol * LAMPORTS_PER_SOL))
        slippage_bps = int(self.settings.live_buy_slippage_bps)

        quote = await self._jupiter_quote(
            input_mint=WSOL_MINT,
            output_mint=mint,
            amount_raw=amount_lamports,
            slippage_bps=slippage_bps,
        )
        swap_b64 = await self._jupiter_swap_tx(quote, pubkey)
        signed_b64, tx_signature = self._sign_versioned_tx(swap_b64, keypair)
        bundle_id = await self._send_jito_bundle(signed_b64)
        await self.confirm_signature(tx_signature)

        _, decimals, onchain_ui = await self.get_token_balance(pubkey, mint)
        if onchain_ui > 0:
            tokens_received = onchain_ui
        else:
            out_raw = int(quote["outAmount"])
            tokens_received = out_raw / (10**decimals) if decimals > 0 else float(out_raw)

        total_spent_sol = round(amount_sol + tx_fee_sol, 9)
        invested_usd = round(total_spent_sol * sol_usd, 4)
        effective_entry_price_usd = (
            (amount_sol * sol_usd) / tokens_received
            if tokens_received > 0
            else entry_price_usd
        )
        slippage_cost_sol = max(
            0.0,
            round(
                amount_sol - ((tokens_received * entry_price_usd) / sol_usd),
                9,
            ),
        )
        spot_val_sol = (tokens_received * entry_price_usd) / sol_usd
        initial_pnl_sol = round(spot_val_sol - total_spent_sol, 9)
        initial_pnl_usd = round(initial_pnl_sol * sol_usd, 4)
        initial_pnl_pct = (
            round((initial_pnl_sol / total_spent_sol) * 100.0, 2)
            if total_spent_sol > 0
            else 0.0
        )

        trade = await self.db.create_trade(
            lead_id=lead.get("id"),
            mint_address=mint,
            symbol=str(lead["symbol"]),
            mode="LIVE",
            invested_sol=total_spent_sol,
            invested_usd=invested_usd,
            entry_price_usd=entry_price_usd,
            effective_entry_price_usd=effective_entry_price_usd,
            tokens_initial=tokens_received,
            tokens_remaining=tokens_received,
            fees_paid_sol=tx_fee_sol,
            slippage_cost_sol=slippage_cost_sol,
            realized_sol=0.0,
            current_price_usd=entry_price_usd,
            highest_price_seen=entry_price_usd,
            peak_volume_5m=0.0,
            pnl_sol=initial_pnl_sol,
            pnl_usd=initial_pnl_usd,
            pnl_pct=initial_pnl_pct,
            capital_recovered=False,
        )

        event = await self.db.record_trade_event(
            trade_id=trade["id"],
            event_type="BUY",
            sol_amount=total_spent_sol,
            tokens_amount=tokens_received,
            price_usd=entry_price_usd,
            effective_price_usd=effective_entry_price_usd,
            fee_sol=tx_fee_sol,
            slippage_sol=slippage_cost_sol,
            tx_signature=tx_signature,
            notes=f"LIVE jito_bundle={bundle_id} | slippage_bps={slippage_bps}",
        )

        if lead.get("id") is not None:
            await self.db.update_lead(int(lead["id"]), current_stage="OPEN")

        logger.info(
            "[REAL BUY] %s | spent=%.4f SOL | tokens=%.4f | sig=%s | bundle=%s",
            trade["symbol"],
            total_spent_sol,
            tokens_received,
            tx_signature,
            bundle_id,
        )

        return ExecutionReceipt(
            trade=trade,
            event=event,
            mode="LIVE",
            side="BUY",
            sol_amount=total_spent_sol,
            tokens_amount=tokens_received,
            spot_price_usd=entry_price_usd,
            effective_price_usd=effective_entry_price_usd,
            fee_sol=tx_fee_sol,
            slippage_sol=slippage_cost_sol,
            tx_signature=tx_signature,
        )

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
        """Execute a real Token -> SOL swap after verifying on-chain SPL token balance."""
        keypair = self._assert_dual_lock()
        pubkey = str(keypair.pubkey())

        if not (0.0 < share_pct <= 100.0):
            raise ExecutionError(f"share_pct must be in (0, 100], got {share_pct}.")

        fresh_trade = await self.db.get_trade(int(trade["id"]))
        if fresh_trade is None:
            raise ExecutionError(f"Trade id={trade['id']} not found.")
        if fresh_trade["status"] == "CLOSED":
            raise ExecutionError(f"Trade id={fresh_trade['id']} is already CLOSED.")

        mint = str(fresh_trade["mint_address"])
        tx_fee_sol = round(
            (
                int(self.settings.live_priority_fee_lamports)
                + int(self.settings.live_jito_tip_lamports)
            )
            / LAMPORTS_PER_SOL,
            9,
        )

        wallet_sol = await self.get_sol_balance_sol(pubkey)
        if wallet_sol < tx_fee_sol + 0.002:
            raise InsufficientGasReserveError(
                f"Insufficient SOL on-chain ({wallet_sol:.4f}) to cover sell fees ({tx_fee_sol:.4f})."
            )

        # Always check REAL on-chain SPL token balance before quoting/selling
        raw_balance, decimals, ui_balance = await self.get_token_balance(pubkey, mint)
        if raw_balance <= 0 or ui_balance <= 0:
            raise ExecutionError(
                f"ONCHAIN_TOKEN_BALANCE_ZERO for {fresh_trade['symbol']} ({mint[:8]})."
            )

        is_full_close = share_pct >= 99.9999
        raw_to_sell = raw_balance if is_full_close else int(raw_balance * (share_pct / 100.0))
        if raw_to_sell <= 0:
            raw_to_sell = raw_balance
            is_full_close = True

        tokens_sold = raw_to_sell / (10**decimals) if decimals > 0 else float(raw_to_sell)
        tokens_after = max(0.0, ui_balance - tokens_sold)

        spot_px = (
            float(spot_price_usd)
            if spot_price_usd is not None
            else float(fresh_trade["current_price_usd"])
        )
        sol_usd = await self._resolve_sol_usd(sol_usd_price)
        slippage_bps = int(
            self.settings.live_sl_slippage_bps
            if is_emergency
            else self.settings.live_tp_slippage_bps
        )

        quote = await self._jupiter_quote(
            input_mint=mint,
            output_mint=WSOL_MINT,
            amount_raw=raw_to_sell,
            slippage_bps=slippage_bps,
        )
        swap_b64 = await self._jupiter_swap_tx(quote, pubkey)
        signed_b64, tx_signature = self._sign_versioned_tx(swap_b64, keypair)
        bundle_id = await self._send_jito_bundle(signed_b64)
        await self.confirm_signature(tx_signature)

        gross_sol_out = int(quote["outAmount"]) / LAMPORTS_PER_SOL
        net_sol_received = round(gross_sol_out - tx_fee_sol, 9)
        effective_sell_price_usd = (
            (gross_sol_out * sol_usd) / tokens_sold if tokens_sold > 0 else spot_px
        )
        ideal_sol = (tokens_sold * spot_px) / sol_usd if spot_px > 0 else gross_sol_out
        sell_slippage_sol = max(0.0, round(ideal_sol - gross_sol_out, 9))

        invested_sol = float(fresh_trade["invested_sol"])
        new_realized_sol = round(float(fresh_trade["realized_sol"]) + net_sol_received, 9)
        new_fees_sol = round(float(fresh_trade["fees_paid_sol"]) + tx_fee_sol, 9)
        new_slippage_sol = round(
            float(fresh_trade["slippage_cost_sol"]) + sell_slippage_sol, 9
        )
        remaining_val_sol = (
            0.0 if is_full_close else (tokens_after * spot_px) / sol_usd
        )
        new_pnl_sol = round(new_realized_sol + remaining_val_sol - invested_sol, 9)
        new_pnl_usd = round(new_pnl_sol * sol_usd, 4)
        new_pnl_pct = (
            round((new_pnl_sol / invested_sol) * 100.0, 2) if invested_sol > 0 else 0.0
        )
        new_highest = max(float(fresh_trade["highest_price_seen"]), spot_px)
        capital_recovered = bool(fresh_trade["capital_recovered"]) or (
            new_realized_sol >= invested_sol - 1e-9
        )

        updates: dict[str, Any] = {
            "tokens_remaining": 0.0 if is_full_close else tokens_after,
            "fees_paid_sol": new_fees_sol,
            "slippage_cost_sol": new_slippage_sol,
            "realized_sol": new_realized_sol,
            "current_price_usd": spot_px,
            "highest_price_seen": new_highest,
            "pnl_sol": new_pnl_sol,
            "pnl_usd": new_pnl_usd,
            "pnl_pct": new_pnl_pct,
            "capital_recovered": capital_recovered,
        }
        if is_full_close:
            updates["status"] = "CLOSED"
            updates["exit_reason"] = reason
            updates["closed_at"] = utc_now_iso()

        updated_trade = await self.db.update_trade(fresh_trade["id"], **updates)

        event = await self.db.record_trade_event(
            trade_id=fresh_trade["id"],
            event_type=reason,
            sol_amount=net_sol_received,
            tokens_amount=tokens_sold,
            price_usd=spot_px,
            effective_price_usd=effective_sell_price_usd,
            fee_sol=tx_fee_sol,
            slippage_sol=sell_slippage_sol,
            tx_signature=tx_signature,
            notes=f"LIVE jito_bundle={bundle_id} | share={share_pct:.1f}% | slippage_bps={slippage_bps}",
        )

        if is_full_close:
            state = await self.db.get_bot_state()
            await self.db.update_bot_state(
                daily_pnl_sol=round(float(state["daily_pnl_sol"]) + new_pnl_sol, 9)
            )
            if fresh_trade.get("lead_id") is not None:
                await self.db.update_lead(
                    int(fresh_trade["lead_id"]), current_stage="CLOSED"
                )

        logger.info(
            "[REAL SELL %s] %s | received=%.4f SOL | pnl=%.4f SOL | sig=%s",
            reason,
            updated_trade["symbol"],
            net_sol_received,
            new_pnl_sol,
            tx_signature,
        )

        return ExecutionReceipt(
            trade=updated_trade,
            event=event,
            mode="LIVE",
            side="SELL",
            sol_amount=net_sol_received,
            tokens_amount=tokens_sold,
            spot_price_usd=spot_px,
            effective_price_usd=effective_sell_price_usd,
            fee_sol=tx_fee_sol,
            slippage_sol=sell_slippage_sol,
            tx_signature=tx_signature,
        )
