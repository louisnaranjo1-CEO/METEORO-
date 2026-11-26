"""Phase 5 Verification: Command Center API endpoints, PIN auth, and LIVE dual-lock."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.dashboard_api import create_app
from src.database import Database
from src.execution.tutorial_engine import TutorialEngine

PIN = "4321"


class FakeOracle:
    async def get_sol_usd_price(self, *, force_refresh: bool = False) -> float:
        return 100.0


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "dashboard_pin": PIN,
        "smart_wallets_path": str(tmp_path / "smart_wallets.json"),
    }
    base.update(overrides)
    return Settings(**base)


def _client(
    tmp_path: Path, *, live_engine_available: bool = True, **overrides: Any
) -> tuple[TestClient, Database, Settings]:
    settings = _settings(tmp_path, **overrides)
    db = Database(db_path=tmp_path / "desk_test.db", settings=settings)
    app = create_app(
        settings=settings,
        db=db,
        price_oracle=FakeOracle(),
        serve_static=False,
        live_engine_available=live_engine_available,
    )
    return TestClient(app), db, settings


H = {"X-Dashboard-Pin": PIN}


def test_pin_auth_blocks_without_pin_and_allows_login(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    with client:
        assert client.get("/api/hud").status_code == 401
        assert client.get("/api/auth/status").json() == {"pin_required": True, "authenticated": False}
        assert client.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
        assert client.post("/api/auth/login", json={"pin": PIN}).status_code == 200
        # Cookie set by login now authenticates subsequent calls
        hud = client.get("/api/hud")
        assert hud.status_code == 200
        body = hud.json()
        assert body["mode"] == "TUTORIAL"
        assert body["available_sol"] == pytest.approx(5.0)
        assert body["available_usd"] == pytest.approx(500.0)
        assert body["max_concurrent_trades"] == 3


def test_mode_switch_blocks_live_when_dual_lock_disabled(tmp_path: Path) -> None:
    client, db, _ = _client(tmp_path, enable_live_execution=False)
    with client:
        res = client.post("/api/mode/switch", json={"mode": "LIVE"}, headers=H)
        assert res.status_code == 403
        assert "ENABLE_LIVE_EXECUTION=true" in res.json()["detail"]
        assert "LIVE_WALLET_PRIVATE_KEY" in res.json()["detail"]
        assert client.get("/api/hud", headers=H).json()["mode"] == "TUTORIAL"


def test_mode_switch_live_with_locks_succeeds_and_501_if_engine_disabled(tmp_path: Path) -> None:
    client_no_eng, _, _ = _client(
        tmp_path / "no_eng",
        trading_mode="TUTORIAL",
        enable_live_execution=True,
        live_wallet_private_key="5TestDedicatedHotWalletKeyBase58String",
        live_engine_available=False,
    )
    with client_no_eng:
        res = client_no_eng.post("/api/mode/switch", json={"mode": "LIVE"}, headers=H)
        assert res.status_code == 501
        assert client_no_eng.get("/api/hud", headers=H).json()["mode"] == "TUTORIAL"

    client_ok, _, _ = _client(
        tmp_path / "ok_eng",
        trading_mode="TUTORIAL",
        enable_live_execution=True,
        solana_private_key="5TestDedicatedHotWalletKeyBase58String",
    )
    with client_ok:
        res_ok = client_ok.post("/api/mode/switch", json={"mode": "LIVE"}, headers=H)
        assert res_ok.status_code == 200
        assert res_ok.json()["mode"] == "LIVE"
        assert client_ok.get("/api/hud", headers=H).json()["mode"] == "LIVE"


def test_pause_tutorial_config_and_reset(tmp_path: Path) -> None:
    client, db, settings = _client(tmp_path)
    with client:
        assert client.post("/api/control/pause", headers=H).json()["is_paused"] is True
        assert client.post("/api/control/pause", json={"paused": False}, headers=H).json()["is_paused"] is False

        res = client.post(
            "/api/tutorial/config",
            json={"trade_size_sol": 0.5, "sim_sl_slippage_pct": 10.0, "semi_auto_mode": True},
            headers=H,
        )
        assert res.status_code == 200
        hud = client.get("/api/hud", headers=H).json()
        assert hud["trade_size_sol"] == pytest.approx(0.5)
        assert hud["semi_auto_mode"] is True
        assert hud["tutorial_config"]["sim_sl_slippage_pct"] == pytest.approx(10.0)
        assert settings.sim_sl_slippage_pct == pytest.approx(10.0)

        bad = client.post("/api/tutorial/config", json={"trade_size_sol": 99.0}, headers=H)
        assert bad.status_code == 422

        reset = client.post("/api/tutorial/reset", json={"initial_sol": 8.0}, headers=H)
        assert reset.status_code == 200
        assert client.get("/api/hud", headers=H).json()["available_sol"] == pytest.approx(8.0)


async def _seed_pending_and_open(db: Database, settings: Settings) -> tuple[dict[str, Any], dict[str, Any]]:
    await db.initialize()
    pending = await db.create_lead(
        mint_address="PendMint1111111111111111111111111111111111",
        symbol="PEND",
        name="Pending",
        pair_address="PairPend111111111111111111111111111111111",
        current_stage="PENDING_APPROVAL",
        market_cap=90_000.0,
        liquidity_usd=40_000.0,
        price_usd=0.001,
    )
    open_lead = await db.create_lead(
        mint_address="OpenMint1111111111111111111111111111111111",
        symbol="OPN",
        name="Open",
        pair_address="PairOpen111111111111111111111111111111111",
        current_stage="GATE",
        market_cap=200_000.0,
        liquidity_usd=60_000.0,
        price_usd=0.002,
        dev_wallet="DevOpen11111111111111111111111111111111111",
    )
    engine = TutorialEngine(db=db, settings=settings)
    receipt = await engine.execute_buy(open_lead, 0.25, sol_usd_price=100.0)
    await db.record_judge_verdict(
        lead_id=pending["id"], dossier_json="{}", organic="yes", organic_prob=0.85,
        late="no", late_prob=0.1, exit_risk=2, manipulation_trap="no", trap_prob=0.05,
        action="buy", action_prob=0.82, gate_passed=True, kill_reason=None, price_at_verdict=0.001,
    )
    return pending, receipt.trade


def test_radar_positions_approve_exit_and_panic(tmp_path: Path) -> None:
    client, db, settings = _client(tmp_path)
    with client:
        pending, open_trade = client.portal.call(_seed_pending_and_open, db, settings)

        radar = client.get("/api/radar", headers=H).json()
        judge_lane = radar["lanes"]["JUDGE"]
        assert any(c["id"] == pending["id"] and c["verdict"]["action"] == "buy" for c in judge_lane)
        assert judge_lane[0]["approval_expires_at"] is not None
        assert any(c["symbol"] == "OPN" for c in radar["lanes"]["POSITION"])

        pos = client.get("/api/positions?mode=TUTORIAL", headers=H).json()
        assert len(pos["open"]) == 1
        assert pos["open"][0]["events"][0]["event_type"] == "BUY"
        assert [b["label"] for b in pos["calibration"]["buckets"]] == ["0.70-0.79", "0.80-0.89", "0.90+"]

        approve = client.post(f"/api/control/approve/{pending['id']}", headers=H)
        assert approve.status_code == 200
        assert client.get("/api/hud", headers=H).json()["open_trades"] == 2

        # Approving again is rejected (no longer pending)
        assert client.post(f"/api/control/approve/{pending['id']}", headers=H).status_code == 409

        exit_res = client.post(f"/api/control/exit-trade/{open_trade['id']}", headers=H)
        assert exit_res.status_code == 200
        assert client.post(f"/api/control/exit-trade/{open_trade['id']}", headers=H).status_code == 409

        panic = client.post("/api/control/panic-close-all", headers=H).json()
        assert panic["is_paused"] is True
        assert len(panic["closed"]) == 1
        hud = client.get("/api/hud", headers=H).json()
        assert hud["open_trades"] == 0
        assert hud["is_paused"] is True


def test_blacklist_smart_wallets_rejected_and_xfeed(tmp_path: Path) -> None:
    client, db, _ = _client(tmp_path)
    wallet = "BadDev99999999999999999999999999999999999"
    with client:
        assert client.post("/api/blacklist", json={"dev_wallet": wallet, "reason": "RUG"}, headers=H).status_code == 200
        items = client.get("/api/blacklist", headers=H).json()["items"]
        assert items[0]["dev_wallet"] == wallet
        assert client.delete(f"/api/blacklist/{wallet}", headers=H).status_code == 200
        assert client.delete(f"/api/blacklist/{wallet}", headers=H).status_code == 404

        sw = "Whale1111111111111111111111111111111111111"
        assert client.post("/api/smart-wallets", json={"address": sw, "label": "Ballena 1"}, headers=H).status_code == 200
        assert client.post("/api/smart-wallets", json={"address": sw}, headers=H).status_code == 409
        assert client.get("/api/smart-wallets", headers=H).json()["items"][0]["label"] == "Ballena 1"

        async def seed() -> None:
            lead = await db.create_lead(
                mint_address="RejMint11111111111111111111111111111111111", symbol="REJ", name="Rej",
                pair_address="PairRej", current_stage="REJECTED", kill_reason="FRAGMENTED_LIQUIDITY",
            )
            await db.save_x_mentions(
                "RejMint11111111111111111111111111111111111", "$REJ OR RejMint",
                [{"author_handle": "bigacct", "author_followers": 900_000, "is_verified": True,
                  "is_notable_figure": True, "notable_tag": "Alto Alcance", "text": "$REJ"}],
            )
            assert lead["id"] > 0

        client.portal.call(seed)
        rejected = client.get("/api/rejected", headers=H).json()["items"]
        assert rejected[0]["phase"] == "RISK"
        assert rejected[0]["kill_reason"] == "FRAGMENTED_LIQUIDITY"

        feed = client.get("/api/x-feed", headers=H).json()
        assert feed["groups"][0]["notable_count"] == 1
        assert feed["groups"][0]["mentions"][0]["notable_tag"] == "Alto Alcance"
