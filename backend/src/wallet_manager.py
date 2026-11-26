"""Live Solana Wallet Vault & Whitelisted SOL Withdrawal Manager.

Security invariants:
- Private keys live ONLY in local `.env` (never stored in SQLite, never returned by GET endpoints).
- SOL withdrawals are STRICTLY restricted to addresses registered in `withdrawal_wallets.json`.
- Transactions are signed locally with `solders` and broadcast directly through Helius / Solana RPC.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.config import BACKEND_DIR, PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

WITHDRAWAL_WALLETS_PATH: Path = BACKEND_DIR / "data" / "withdrawal_wallets.json"
LAMPORTS_PER_SOL: int = 1_000_000_000
MIN_TRANSFER_GAS_RESERVE_SOL: float = 0.005


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_keypair(raw_secret: str) -> Any:
    """Parse a Solana Keypair from Base58 string or JSON byte array [12, 34, ...]."""
    from solders.keypair import Keypair

    cleaned = raw_secret.strip()
    if not cleaned:
        raise ValueError("Clave privada vacía.")
    if cleaned.startswith("["):
        arr = json.loads(cleaned)
        if not isinstance(arr, list) or len(arr) != 64:
            raise ValueError("El array JSON de Solana debe tener exactamente 64 bytes.")
        return Keypair.from_bytes(bytes(int(b) for b in arr))
    return Keypair.from_base58_string(cleaned)


def get_public_address_from_settings(settings: Settings) -> str | None:
    """Derive the public Solana address from the configured private key (never leaks secret)."""
    secret = settings.effective_solana_private_key
    if not secret:
        return None
    try:
        kp = _parse_keypair(secret)
        return str(kp.pubkey())
    except Exception:
        return None


def _update_env_keys(updates: dict[str, str]) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def configure_live_wallet_in_env(
    settings: Settings,
    *,
    private_key_raw: str | None = None,
    enable_live_execution: bool | None = None,
) -> dict[str, Any]:
    """Validate and save a Solana Base58 private key & live-lock flag strictly to local `.env`."""
    updates: dict[str, str] = {}
    public_addr = get_public_address_from_settings(settings)

    if private_key_raw is not None and private_key_raw.strip():
        kp = _parse_keypair(private_key_raw)
        b58 = str(kp)
        public_addr = str(kp.pubkey())
        updates["SOLANA_PRIVATE_KEY"] = b58
        updates["LIVE_WALLET_PRIVATE_KEY"] = b58
        settings.solana_private_key = b58
        settings.live_wallet_private_key = b58

    if enable_live_execution is not None:
        updates["ENABLE_LIVE_EXECUTION"] = "true" if enable_live_execution else "false"
        settings.enable_live_execution = bool(enable_live_execution)

    if updates:
        _update_env_keys(updates)

    return {
        "configured": bool(public_addr),
        "public_address": public_addr,
        "enable_live_execution": bool(settings.enable_live_execution),
    }


def generate_dedicated_trading_wallet(settings: Settings) -> dict[str, Any]:
    """Generate a brand-new dedicated Solana burner trading wallet locally and save to `.env`."""
    from solders.keypair import Keypair

    kp = Keypair()
    b58 = str(kp)
    pub = str(kp.pubkey())
    _update_env_keys({"SOLANA_PRIVATE_KEY": b58, "LIVE_WALLET_PRIVATE_KEY": b58})
    settings.solana_private_key = b58
    settings.live_wallet_private_key = b58
    return {
        "configured": True,
        "public_address": pub,
        "enable_live_execution": bool(settings.enable_live_execution),
        "solscan_url": f"https://solscan.io/account/{pub}",
    }


# ----------------------------------------------------------------------
# Whitelisted Withdrawal Destinations
# ----------------------------------------------------------------------
def list_withdrawal_wallets(path: Path = WITHDRAWAL_WALLETS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload.get("wallets") if isinstance(payload, dict) else []
        return [dict(w) for w in raw if isinstance(w, dict) and w.get("address")]
    except (OSError, ValueError):
        return []


def _save_withdrawal_wallets(wallets: list[dict[str, Any]], path: Path = WITHDRAWAL_WALLETS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "description": "Whitelisted Solana destination addresses authorized for SOL withdrawals.",
        "wallets": wallets,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def add_withdrawal_wallet(
    address: str,
    label: str,
    path: Path = WITHDRAWAL_WALLETS_PATH,
) -> list[dict[str, Any]]:
    from solders.pubkey import Pubkey

    clean = address.strip()
    Pubkey.from_string(clean)  # raises ValueError if invalid Solana pubkey
    wallets = list_withdrawal_wallets(path)
    if any(w["address"] == clean for w in wallets):
        raise ValueError("WALLET_ALREADY_REGISTERED")
    wallets.append(
        {
            "address": clean,
            "label": label.strip() or f"Destino {clean[:6]}...{clean[-4:]}",
            "added_at": _now_iso(),
        }
    )
    _save_withdrawal_wallets(wallets, path)
    return wallets


def remove_withdrawal_wallet(address: str, path: Path = WITHDRAWAL_WALLETS_PATH) -> list[dict[str, Any]]:
    clean = address.strip()
    wallets = list_withdrawal_wallets(path)
    remaining = [w for w in wallets if w["address"] != clean]
    if len(remaining) == len(wallets):
        raise KeyError("WALLET_NOT_FOUND")
    _save_withdrawal_wallets(remaining, path)
    return remaining


# ----------------------------------------------------------------------
# Live RPC Balance & Whitelisted SOL Transfer
# ----------------------------------------------------------------------
async def fetch_live_wallet_balance_sol(settings: Settings, pubkey: str) -> float | None:
    rpc_url = settings.solana_rpc_url or "https://api.mainnet-beta.solana.com"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [pubkey]},
            )
            if resp.status_code == 200:
                val = (resp.json().get("result") or {}).get("value")
                if isinstance(val, (int, float)):
                    return round(float(val) / LAMPORTS_PER_SOL, 6)
    except Exception as exc:
        logger.warning("RPC getBalance failed: %s", exc)
    return None


async def withdraw_sol_to_whitelisted(
    settings: Settings,
    destination: str,
    amount_sol: float,
    *,
    whitelist_path: Path = WITHDRAWAL_WALLETS_PATH,
) -> dict[str, Any]:
    """Send native SOL from the configured bot wallet to a STRICTLY whitelisted address."""
    from solders.hash import Hash
    from solders.message import MessageV0
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer
    from solders.transaction import VersionedTransaction

    dest_clean = destination.strip()
    whitelist = list_withdrawal_wallets(whitelist_path)
    dest_entry = next((w for w in whitelist if w["address"] == dest_clean), None)
    if dest_entry is None:
        raise PermissionError("DESTINATION_NOT_WHITELISTED")

    secret = settings.effective_solana_private_key
    if not secret:
        raise ValueError("LIVE_WALLET_NOT_CONFIGURED")
    if amount_sol <= 0:
        raise ValueError("AMOUNT_MUST_BE_POSITIVE")

    kp = _parse_keypair(secret)
    from_pub = kp.pubkey()
    to_pub = Pubkey.from_string(dest_clean)

    balance = await fetch_live_wallet_balance_sol(settings, str(from_pub))
    if balance is None:
        raise RuntimeError("RPC_BALANCE_UNAVAILABLE")
    max_sendable = max(0.0, balance - MIN_TRANSFER_GAS_RESERVE_SOL)
    if amount_sol > max_sendable:
        raise ValueError(
            f"INSUFFICIENT_BALANCE (disponible para retirar: {max_sendable:.4f} SOL, reserva gas {MIN_TRANSFER_GAS_RESERVE_SOL} SOL)"
        )

    rpc_url = settings.solana_rpc_url or "https://api.mainnet-beta.solana.com"
    lamports = int(round(amount_sol * LAMPORTS_PER_SOL))
    async with httpx.AsyncClient(timeout=12.0) as client:
        bh_resp = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getLatestBlockhash",
                "params": [{"commitment": "confirmed"}],
            },
        )
        bh_data = bh_resp.json()
        bh_str = ((bh_data.get("result") or {}).get("value") or {}).get("blockhash")
        if not bh_str:
            raise RuntimeError("FAILED_TO_FETCH_BLOCKHASH")

        ix = transfer(TransferParams(from_pubkey=from_pub, to_pubkey=to_pub, lamports=lamports))
        msg = MessageV0.try_compile(
            payer=from_pub,
            instructions=[ix],
            address_lookup_table_accounts=[],
            recent_blockhash=Hash.from_string(bh_str),
        )
        tx = VersionedTransaction(msg, [kp])
        tx_b64 = base64.b64encode(bytes(tx)).decode("ascii")

        send_resp = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "sendTransaction",
                "params": [tx_b64, {"encoding": "base64", "skipPreflight": False}],
            },
        )
        send_body = send_resp.json()
        if "error" in send_body:
            raise RuntimeError(f"RPC_SEND_ERROR: {send_body['error']}")
        sig = str(send_body.get("result") or "")
        if not sig:
            raise RuntimeError("EMPTY_TX_SIGNATURE")

    return {
        "ok": True,
        "signature": sig,
        "amount_sol": round(amount_sol, 6),
        "destination": dest_clean,
        "destination_label": dest_entry.get("label") or dest_clean,
        "solscan_url": f"https://solscan.io/tx/{sig}",
    }
