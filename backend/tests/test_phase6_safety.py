"""Phase 6 Safety & Deployment Verification Suite: LiveEngine, Secret Filter, Gas Reserve, & Compose."""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from solders.hash import Hash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from src.config import PROJECT_ROOT, Settings, TradingMode
from src.database import Database
from src.execution.live_engine import (
    InsufficientGasReserveError,
    LiveEngine,
    LiveHardCapError,
    LiveSafetyLockError,
)
from src.gate import GateDecision
from src.judge import JudgeVerdict
from src.orchestrator import TradingOrchestrator
from src.security import SecretRedactingFilter, sanitize_text
from src.telegram_bot import TelegramNotifier


def _build_dummy_unsigned_tx_b64(payer: Pubkey) -> str:
    msg = MessageV0.try_compile(
        payer,
        [Instruction(Pubkey.new_unique(), b"\x01\x02", [])],
        [],
        Hash.default(),
    )
    unsigned = VersionedTransaction.populate(msg, [Signature.default()])
    return base64.b64encode(bytes(unsigned)).decode("ascii")


async def _seed_live_lead(db: Database) -> dict[str, Any]:
    return await db.create_lead(
        mint_address="LiveTokenMint111111111111111111111111111111",
        symbol="LIVE1",
        name="Live Token 1",
        pair_address="LivePair1111111111111111111111111111111111",
        current_stage="GATE",
        market_cap=250_000.0,
        liquidity_usd=70_000.0,
        main_pool_share=1.0,
        holders_count=800,
        price_usd=0.001,
    )


async def test_1_live_engine_dual_lock_blocks_without_either_lock(tmp_path: Path) -> None:
    """1. LiveEngine MUST reject both buy and sell if TRADING_MODE != LIVE or ENABLE_LIVE_EXECUTION != True."""
    db = Database(db_path=tmp_path / "phase6_locks.db")
    await db.initialize()
    lead = await _seed_live_lead(db)

    kp = Keypair()
    http_calls = 0

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail_if_called)) as client:
        # Case A: TRADING_MODE=TUTORIAL even with ENABLE_LIVE_EXECUTION=True
        cfg_a = Settings.model_construct(
            trading_mode=TradingMode.TUTORIAL,
            enable_live_execution=True,
            solana_private_key=str(kp),
            live_wallet_private_key="",
            max_live_trade_sol=0.50,
            min_live_gas_reserve_sol=0.15,
            live_priority_fee_lamports=500_000,
            live_jito_tip_lamports=2_000_000,
            fallback_sol_usd_price=100.0,
        )
        eng_a = LiveEngine(db=db, settings=cfg_a, http_client=client)
        with pytest.raises(LiveSafetyLockError, match="TRADING_MODE"):
            await eng_a.execute_buy(lead, 0.25, sol_usd_price=100.0)

        # Case B: TRADING_MODE=LIVE with ENABLE_LIVE_EXECUTION=False (bypassing Settings validator)
        cfg_b = Settings.model_construct(
            trading_mode=TradingMode.LIVE,
            enable_live_execution=False,
            solana_private_key=str(kp),
            live_wallet_private_key="",
            max_live_trade_sol=0.50,
            min_live_gas_reserve_sol=0.15,
            live_priority_fee_lamports=500_000,
            live_jito_tip_lamports=2_000_000,
            fallback_sol_usd_price=100.0,
        )
        eng_b = LiveEngine(db=db, settings=cfg_b, http_client=client)
        with pytest.raises(LiveSafetyLockError, match="ENABLE_LIVE_EXECUTION"):
            await eng_b.execute_buy(lead, 0.25, sol_usd_price=100.0)

        dummy_trade = await db.create_trade(
            lead_id=lead["id"],
            mint_address=lead["mint_address"],
            symbol="LIVE1",
            mode="LIVE",
            invested_sol=0.25,
            invested_usd=25.0,
            entry_price_usd=0.001,
            effective_entry_price_usd=0.001,
            tokens_initial=100.0,
            tokens_remaining=100.0,
        )
        with pytest.raises(LiveSafetyLockError, match="ENABLE_LIVE_EXECUTION"):
            await eng_b.execute_sell(dummy_trade, 100.0, "MANUAL_EXIT", sol_usd_price=100.0)

    assert http_calls == 0


async def test_2_secret_redaction_filter_censors_keys_in_logs_exceptions_and_telegram(
    tmp_path: Path,
) -> None:
    """2. Logging filter & sanitizer must censor SOLANA_PRIVATE_KEY, OpenRouter, Helius, Telegram, & PIN."""
    kp = Keypair()
    sol_priv = str(kp)
    openrouter_secret = "sk-or-v1-testsecret9876543210abcdef"
    helius_secret = "helius-super-secret-key-xyz987"
    tg_token = "987654321:AAFakeTelegramBotTokenSecret123456"
    pin_secret = "99887766"
    json_keypair = json.dumps(list(bytes(kp)))

    settings = Settings(
        _env_file=None,
        solana_private_key=sol_priv,
        openrouter_api_key=openrouter_secret,
        helius_api_key=helius_secret,
        telegram_bot_token=tg_token,
        telegram_chat_id="555111",
        dashboard_pin=pin_secret,
    )

    # Confirm Settings repr & LiveEngine repr never contain private key
    assert sol_priv not in repr(settings)
    db = Database(db_path=tmp_path / "phase6_logs.db")
    eng = LiveEngine(db=db, settings=settings)
    assert sol_priv not in repr(eng)

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    redactor = SecretRedactingFilter(settings.secret_values())
    handler.addFilter(redactor)

    test_logger = logging.getLogger("meteoro.test.secrets")
    test_logger.handlers = [handler]
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False

    test_logger.info("Direct leak %s and %s", sol_priv, openrouter_secret)
    test_logger.warning(
        f"URL https://api.telegram.org/bot{tg_token}/sendMessage?api-key={helius_secret} pin={pin_secret}"
    )
    test_logger.error("Raw array: %s", json_keypair)
    try:
        raise RuntimeError(f"Crash carrying {sol_priv} and Bearer {openrouter_secret}")
    except RuntimeError:
        test_logger.exception("Unhandled failure")

    output = buf.getvalue()
    for secret in (sol_priv, openrouter_secret, helius_secret, tg_token, pin_secret, json_keypair):
        assert secret not in output, f"Secret leaked in logs: {secret}"
    assert "[REDACTED]" in output
    assert "[REDACTED_KEYPAIR]" in output

    # Telegram outbound messages are also sanitized
    sent_payloads: list[str] = []

    def tg_handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)) as tg_client:
        notifier = TelegramNotifier(http_client=tg_client, settings=settings)
        await notifier.send_message(f"Debug {sol_priv} {openrouter_secret}")

    assert len(sent_payloads) == 1
    assert sol_priv not in sent_payloads[0]
    assert openrouter_secret not in sent_payloads[0]
    assert "[REDACTED]" in sent_payloads[0]
    assert sanitize_text(f"https://mainnet.helius-rpc.com/?api-key={helius_secret}") == (
        "https://mainnet.helius-rpc.com/?api-key=[REDACTED]"
    )


async def test_3_gas_reserve_015_sol_and_max_live_trade_cap(tmp_path: Path) -> None:
    """3. Enforce 0.15 SOL minimum gas reserve and MAX_LIVE_TRADE_SOL before Jupiter/Jito."""
    db = Database(db_path=tmp_path / "phase6_caps.db")
    await db.initialize()
    lead = await _seed_live_lead(db)
    kp = Keypair()

    settings = Settings(
        _env_file=None,
        trading_mode=TradingMode.LIVE,
        enable_live_execution=True,
        solana_private_key=str(kp),
        max_live_trade_sol=0.50,
        min_live_gas_reserve_sol=0.15,
        live_priority_fee_lamports=500_000,
        live_jito_tip_lamports=2_000_000,
    )

    jupiter_or_jito_called = False

    def rpc_only_handler(request: httpx.Request) -> httpx.Response:
        nonlocal jupiter_or_jito_called
        url_str = str(request.url)
        if "jup.ag" in url_str or "jito.wtf" in url_str:
            jupiter_or_jito_called = True
            return httpx.Response(500)
        body = json.loads(request.content.decode("utf-8"))
        if body["method"] == "getBalance":
            # Wallet holds 0.30 SOL -> buying 0.20 SOL leaves ~0.0975 SOL (< 0.15 SOL!)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 300_000_000}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(rpc_only_handler)) as client:
        engine = LiveEngine(db=db, settings=settings, http_client=client)

        # Exceeding MAX_LIVE_TRADE_SOL is blocked instantly
        with pytest.raises(LiveHardCapError, match="MAX_LIVE_TRADE_SOL"):
            await engine.execute_buy(lead, 0.75, sol_usd_price=100.0)

        # Violating 0.15 SOL reserve is blocked BEFORE Jupiter or Jito
        with pytest.raises(InsufficientGasReserveError, match="0.15"):
            await engine.execute_buy(lead, 0.20, sol_usd_price=100.0)

    assert jupiter_or_jito_called is False


async def test_4_live_engine_jupiter_jito_local_signing_and_telegram_button_sync(
    tmp_path: Path,
) -> None:
    """4. End-to-end mocked LiveEngine buy/sell (on-chain SPL verify) + synced Telegram inline buttons."""
    db = Database(db_path=tmp_path / "phase6_live_e2e.db")
    await db.initialize()
    lead = await _seed_live_lead(db)

    kp = Keypair()
    unsigned_b64 = _build_dummy_unsigned_tx_b64(kp.pubkey())
    seen_jito_bundles: list[str] = []
    spl_queries = 0

    def live_network_handler(request: httpx.Request) -> httpx.Response:
        nonlocal spl_queries
        path = request.url.path
        if path.endswith("/quote"):
            input_mint = request.url.params.get("inputMint")
            if input_mint == "So11111111111111111111111111111111111111112":
                return httpx.Response(200, json={"outAmount": "250000000"})  # 250 tokens (6 dec)
            return httpx.Response(200, json={"outAmount": "300000000"})  # 0.30 SOL back
        if path.endswith("/swap"):
            return httpx.Response(200, json={"swapTransaction": unsigned_b64})
        if path.endswith("/api/v1/bundles"):
            body = json.loads(request.content.decode("utf-8"))
            signed_tx_b64 = body["params"][0][0]
            # Verify signed transaction decodes cleanly via solders and signature is non-default
            decoded = VersionedTransaction.from_bytes(base64.b64decode(signed_tx_b64))
            assert str(decoded.signatures[0]) != str(Signature.default())
            seen_jito_bundles.append(signed_tx_b64)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "bundle_ok_123"})
        # Solana RPC
        body = json.loads(request.content.decode("utf-8"))
        method = body["method"]
        if method == "getBalance":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 2_000_000_000}})
        if method == "getTokenAccountsByOwner":
            spl_queries += 1
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "value": [
                            {
                                "account": {
                                    "data": {
                                        "parsed": {
                                            "info": {
                                                "tokenAmount": {
                                                    "amount": "250000000",
                                                    "decimals": 6,
                                                    "uiAmount": 250.0,
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        ]
                    },
                },
            )
        if method == "getSignatureStatuses":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"value": [{"err": None, "confirmationStatus": "confirmed"}]}},
            )
        return httpx.Response(404)

    settings = Settings(
        _env_file=None,
        trading_mode=TradingMode.LIVE,
        enable_live_execution=True,
        solana_private_key=str(kp),
        max_live_trade_sol=0.50,
        min_live_gas_reserve_sol=0.15,
        telegram_bot_token="123456:AAFakeTelegramTokenForTest12345",
        telegram_chat_id="777888",
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(live_network_handler)) as client:
        engine = LiveEngine(db=db, settings=settings, http_client=client)
        buy_receipt = await engine.execute_buy(lead, 0.25, sol_usd_price=100.0)
        assert buy_receipt.mode == "LIVE"
        assert buy_receipt.trade["mode"] == "LIVE"
        assert buy_receipt.trade["tokens_initial"] == pytest.approx(250.0)
        assert len(seen_jito_bundles) == 1

        # Sell 50% -> queries on-chain SPL token balance BEFORE selling
        sell_receipt = await engine.execute_sell(
            buy_receipt.trade, 50.0, "TP1", is_emergency=False, spot_price_usd=0.0024, sol_usd_price=100.0
        )
        assert spl_queries >= 2  # checked post-buy & pre-sell on-chain
        assert sell_receipt.tokens_amount == pytest.approx(125.0)
        assert sell_receipt.trade["tokens_remaining"] == pytest.approx(125.0)
        assert sell_receipt.trade["realized_sol"] > 0.29

    # Verify Telegram inline keyboard buttons & synchronized approval with Orchestrator
    tg_requests: list[tuple[str, dict[str, Any]]] = []

    def tg_handler(request: httpx.Request) -> httpx.Response:
        tg_requests.append((request.url.path, json.loads(request.content.decode("utf-8"))))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    tut_settings = Settings(
        _env_file=None,
        trading_mode=TradingMode.TUTORIAL,
        telegram_bot_token="123456:AAFakeTelegramTokenForTest12345",
        telegram_chat_id="777888",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)) as tg_client:
        notifier = TelegramNotifier(http_client=tg_client, settings=tut_settings)
        orch = TradingOrchestrator(db=db, settings=tut_settings, notifier=notifier)
        try:
            await db.update_bot_state(semi_auto_mode=1)
            semi_lead = await db.create_lead(
                mint_address="SyncMint1111111111111111111111111111111111",
                symbol="SYNC",
                name="Sync Coin",
                pair_address="PairSync1111111111111111111111111111111111",
                current_stage="GATE",
                market_cap=100_000.0,
                liquidity_usd=45_000.0,
                price_usd=0.001,
            )
            await orch.handle_approved_lead(
                semi_lead,
                GateDecision(
                    approved=True,
                    decision="APPROVE",
                    kill_reason=None,
                    approved_size_sol=0.25,
                    recheck_at=None,
                    verdict_id=1,
                    lead_stage="PENDING_APPROVAL",
                    zone="EARLY",
                    entry_liquidity_pct=0.05,
                ),
                JudgeVerdict(
                    organic="yes",
                    organic_prob=0.9,
                    late="no",
                    late_prob=0.1,
                    exit_risk=1,
                    manipulation_trap="no",
                    trap_prob=0.05,
                    action="buy",
                    action_prob=0.9,
                ),
            )
            # Inline buttons sent to Telegram
            send_call = next(body for path, body in tg_requests if path.endswith("/sendMessage"))
            buttons = send_call["reply_markup"]["inline_keyboard"][0]
            assert buttons[0]["callback_data"] == f"approve:{semi_lead['id']}"
            assert buttons[1]["callback_data"] == f"reject:{semi_lead['id']}"

            # Tap ✅ APROBAR via Telegram callback -> executes & edits message
            cb_payload = {
                "id": "cb_1",
                "data": f"approve:{semi_lead['id']}",
                "message": {
                    "message_id": 42,
                    "chat": {"id": 777888},
                    "text": send_call["text"],
                },
            }
            res1 = await notifier.handle_callback_query(
                cb_payload,
                on_approve=orch.handle_telegram_approve,
                on_reject=orch.handle_telegram_reject,
            )
            assert "Compra ejecutada" in res1
            assert (await db.get_lead(semi_lead["id"]))["current_stage"] == "OPEN"

            # Second tap (as if already approved from Dashboard) cleanly reports synced state
            res2 = await notifier.handle_callback_query(
                cb_payload,
                on_approve=orch.handle_telegram_approve,
                on_reject=orch.handle_telegram_reject,
            )
            assert "ya procesado" in res2
        finally:
            await orch.aclose()


def test_5_docker_compose_and_vps_artifacts_valid() -> None:
    """5. Validate docker-compose.yml structure, Dockerfiles, Nginx proxy, and LF backup script."""
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    assert compose_path.exists()
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services.keys()) == {"bot-api", "dashboard"}

    bot_api = services["bot-api"]
    assert bot_api["restart"] == "unless-stopped"
    assert "./backend/data:/app/data" in bot_api["volumes"]
    assert "./backend/logs:/app/logs" in bot_api["volumes"]
    assert "ports" not in bot_api
    assert "8000" in bot_api["expose"]

    dash = services["dashboard"]
    assert dash["restart"] == "unless-stopped"
    assert "bot-api" in dash["depends_on"]

    assert (PROJECT_ROOT / "backend" / "Dockerfile").exists()
    assert (PROJECT_ROOT / "dashboard" / "Dockerfile").exists()
    nginx_conf = (PROJECT_ROOT / "dashboard" / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://bot-api:8000" in nginx_conf

    backup_sh = PROJECT_ROOT / "scripts" / "backup_db.sh"
    assert backup_sh.exists()
    raw_sh = backup_sh.read_bytes()
    assert b"\r" not in raw_sh, "backup_db.sh must use LF line endings for Ubuntu"
    assert b".backup" in raw_sh and b"-mtime" in raw_sh
