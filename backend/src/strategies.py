"""Editable Strategy Preset Manager for METEORO TRADER SOLANA.

Persists built-in and custom user strategies in ``backend/data/strategies.json``
and applies all 6-phase parameters live onto ``Settings`` and ``bot_state``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import BACKEND_DIR, Settings

STRATEGIES_PATH: Path = BACKEND_DIR / "data" / "strategies.json"

# All editable parameters exposed across the 6 phases in StrategyTab
STRATEGY_PARAM_TYPES: dict[str, type] = {
    # Phase 1: SEARCH
    "search_interval_seconds": int,
    "min_market_cap_usd": float,
    "max_market_cap_usd": float,
    "min_liquidity_usd": float,
    # Phase 2: RISK & WHALE
    "min_holders": int,
    "max_top10_holders_pct": float,
    "max_dev_wallet_pct": float,
    "main_pool_liquidity_share_min": float,
    "require_mint_revoked": bool,
    "require_freeze_revoked": bool,
    "require_lp_locked_or_burned": bool,
    # Phase 3 & 4: JUDGE & GATE
    "use_llm_judge": bool,
    "sniper_max_entry_pc5m_pct": float,
    "sniper_min_entry_pc5m_pct": float,
    "sniper_max_entry_pc1h_pct": float,
    "sniper_max_age_minutes": float,
    "strategy_profile": str,
    "gate_min_action_prob": float,
    "gate_min_action_prob_low_mcap": float,
    "gate_min_organic_prob": float,
    "gate_min_organic_prob_low_mcap": float,
    "gate_max_late_prob": float,
    "gate_max_late_prob_low_mcap": float,
    "gate_max_exit_risk": int,
    "gate_max_manipulation_prob": float,
    "gate_max_manipulation_prob_low_mcap": float,
    "watch_recheck_minutes": int,
    "max_concurrent_trades": int,
    "max_daily_loss_sol": float,
    "max_daily_loss_pct": float,
    "max_consecutive_sl": int,
    "max_entry_liquidity_pct": float,
    # Phase 5: EXECUTION
    "trade_size_sol": float,
    "semi_auto_mode": bool,
    # Phase 6: EXIT MANAGER
    "exit_poll_interval_seconds": float,
    "disable_hard_stop_loss": bool,
    "only_close_positive": bool,
    "initial_stop_loss_pct": float,
    "smart_be_arm_pct": float,
    "smart_be_floor_pct": float,
    "wick_harvest_arm_pct": float,
    "wick_pullback_exit_pct": float,
    "tp1_trigger_pct": float,
    "tp1_sell_pct": float,
    "tp1_breakeven_sl_pct": float,
    "free_risk_trigger_pct": float,
    "trailing_stop_pct": float,
    "stagnation_window_minutes": int,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().upper()).strip("_")
    return slug[:40] or "CUSTOM"


def built_in_strategies() -> list[dict[str, Any]]:
    """Canonical default strategy presets (PREDETERMINADA, AGRESIVO_SCALPER, CONSERVADOR)."""
    base_default: dict[str, Any] = {
        "search_interval_seconds": 6,
        "min_market_cap_usd": 50_000.0,
        "max_market_cap_usd": 1_500_000.0,
        "min_liquidity_usd": 12_000.0,
        "min_holders": 60,
        "max_top10_holders_pct": 25.0,
        "max_dev_wallet_pct": 4.0,
        "main_pool_liquidity_share_min": 0.80,
        "require_mint_revoked": True,
        "require_freeze_revoked": True,
        "require_lp_locked_or_burned": True,
        "use_llm_judge": True,
        "sniper_max_entry_pc5m_pct": 20.0,
        "sniper_min_entry_pc5m_pct": -8.0,
        "sniper_max_entry_pc1h_pct": 140.0,
        "sniper_max_age_minutes": 240.0,
        "strategy_profile": "AGRESIVO",
        "gate_min_action_prob": 0.55,
        "gate_min_action_prob_low_mcap": 0.58,
        "gate_min_organic_prob": 0.50,
        "gate_min_organic_prob_low_mcap": 0.52,
        "gate_max_late_prob": 0.72,
        "gate_max_late_prob_low_mcap": 0.68,
        "gate_max_exit_risk": 3,
        "gate_max_manipulation_prob": 0.45,
        "gate_max_manipulation_prob_low_mcap": 0.40,
        "watch_recheck_minutes": 4,
        "max_concurrent_trades": 5,
        "max_daily_loss_sol": 2.0,
        "max_daily_loss_pct": 40.0,
        "max_consecutive_sl": 5,
        "max_entry_liquidity_pct": 1.0,
        "trade_size_sol": 0.25,
        "semi_auto_mode": False,
        "exit_poll_interval_seconds": 0.5,
        "disable_hard_stop_loss": True,
        "only_close_positive": True,
        "initial_stop_loss_pct": 22.0,
        "smart_be_arm_pct": 10.0,
        "smart_be_floor_pct": 4.0,
        "wick_harvest_arm_pct": 25.0,
        "wick_pullback_exit_pct": 6.0,
        "tp1_trigger_pct": 50.0,
        "tp1_sell_pct": 100.0,
        "tp1_breakeven_sl_pct": 6.0,
        "free_risk_trigger_pct": 100.0,
        "trailing_stop_pct": 9.0,
        "stagnation_window_minutes": 12,
    }

    conservative = {
        **base_default,
        "search_interval_seconds": 15,
        "min_holders": 150,
        "strategy_profile": "CONSERVADOR",
        "gate_min_action_prob": 0.70,
        "gate_min_action_prob_low_mcap": 0.75,
        "gate_min_organic_prob": 0.65,
        "gate_min_organic_prob_low_mcap": 0.75,
        "gate_max_late_prob": 0.45,
        "gate_max_late_prob_low_mcap": 0.30,
        "gate_max_manipulation_prob": 0.30,
        "gate_max_manipulation_prob_low_mcap": 0.20,
        "max_concurrent_trades": 3,
        "max_daily_loss_sol": 0.75,
        "max_daily_loss_pct": 10.0,
        "max_consecutive_sl": 3,
        "exit_poll_interval_seconds": 15,
        "disable_hard_stop_loss": False,
        "only_close_positive": False,
        "initial_stop_loss_pct": 22.0,
        "tp1_trigger_pct": 45.0,
        "tp1_sell_pct": 50.0,
        "tp1_breakeven_sl_pct": 5.0,
        "free_risk_trigger_pct": 100.0,
        "trailing_stop_pct": 18.0,
    }

    breakout_100k = {
        **base_default,
        "search_interval_seconds": 5,
        "min_market_cap_usd": 100_000.0,
        "max_market_cap_usd": 220_000.0,
        "min_liquidity_usd": 15_000.0,
        "min_holders": 120,
        "max_top10_holders_pct": 25.0,
        "max_dev_wallet_pct": 4.0,
        "main_pool_liquidity_share_min": 0.80,
        "require_mint_revoked": True,
        "require_freeze_revoked": True,
        "require_lp_locked_or_burned": True,
        "use_llm_judge": True,
        "sniper_max_entry_pc5m_pct": 20.0,
        "sniper_min_entry_pc5m_pct": -8.0,
        "sniper_max_entry_pc1h_pct": 140.0,
        "sniper_max_age_minutes": 240.0,
        "strategy_profile": "AGRESIVO",
        "gate_min_action_prob": 0.58,
        "gate_min_action_prob_low_mcap": 0.60,
        "gate_min_organic_prob": 0.60,
        "gate_min_organic_prob_low_mcap": 0.60,
        "gate_max_late_prob": 0.72,
        "gate_max_late_prob_low_mcap": 0.70,
        "gate_max_exit_risk": 3,
        "gate_max_manipulation_prob": 0.35,
        "gate_max_manipulation_prob_low_mcap": 0.35,
        "max_concurrent_trades": 5,
        "exit_poll_interval_seconds": 0.5,
        "disable_hard_stop_loss": True,
        "only_close_positive": True,
        "smart_be_arm_pct": 12.0,
        "smart_be_floor_pct": 4.0,
        "wick_harvest_arm_pct": 25.0,
        "wick_pullback_exit_pct": 6.0,
        "tp1_trigger_pct": 50.0,
        "tp1_sell_pct": 100.0,
        "free_risk_trigger_pct": 100.0,
    }

    sniper_algo = {
        **base_default,
        "use_llm_judge": False,
        "sniper_max_entry_pc5m_pct": 18.0,
        "sniper_min_entry_pc5m_pct": -7.0,
        "sniper_max_entry_pc1h_pct": 135.0,
        "sniper_max_age_minutes": 180.0,
        "search_interval_seconds": 3,
        "min_market_cap_usd": 45_000.0,
        "max_market_cap_usd": 260_000.0,
        "min_liquidity_usd": 12_000.0,
        "min_holders": 100,
        "max_top10_holders_pct": 25.0,
        "max_dev_wallet_pct": 4.0,
        "main_pool_liquidity_share_min": 0.80,
        "require_mint_revoked": True,
        "require_freeze_revoked": True,
        "require_lp_locked_or_burned": True,
        "strategy_profile": "AGRESIVO",
        "gate_min_action_prob": 0.56,
        "gate_min_action_prob_low_mcap": 0.58,
        "gate_min_organic_prob": 0.52,
        "gate_min_organic_prob_low_mcap": 0.54,
        "gate_max_late_prob": 0.55,
        "gate_max_late_prob_low_mcap": 0.55,
        "gate_max_exit_risk": 3,
        "gate_max_manipulation_prob": 0.35,
        "gate_max_manipulation_prob_low_mcap": 0.35,
        "max_concurrent_trades": 5,
        "max_daily_loss_sol": 1.5,
        "max_daily_loss_pct": 30.0,
        "max_consecutive_sl": 4,
        "trade_size_sol": 0.25,
        "exit_poll_interval_seconds": 0.5,
        "disable_hard_stop_loss": False,
        "only_close_positive": False,
        "initial_stop_loss_pct": 30.0,
        "smart_be_arm_pct": 12.0,
        "smart_be_floor_pct": 4.0,
        "wick_harvest_arm_pct": 22.0,
        "wick_pullback_exit_pct": 5.5,
        "tp1_trigger_pct": 40.0,
        "tp1_sell_pct": 100.0,
        "free_risk_trigger_pct": 100.0,
        "stagnation_window_minutes": 15,
    }

    return [
        {
            "id": "SNIPER_ALGO",
            "name": "Sniper Algorítmico 0ms (Sin LLM · Anti-Punta)",
            "description": "100% matemático (apaga el LLM → 0ms latencia, $0 API). Entra solo en pullbacks/olas frescas (-7% a +18% 5m, nunca en puntas), no repite moneda en 24h, TP 1.40x (100% venta), Cazador Mechas +22%, Smart BE +12% y SL de emergencia -30%.",
            "builtin": True,
            "updated_at": _now_iso(),
            "params": sniper_algo,
        },
        {
            "id": "PREDETERMINADA",
            "name": "Estrategia Predeterminada (Wave & Wick)",
            "description": "Perfil base de referencia: Wave Scalper 1.5x (50%) con venta 100%, sin SL negativo, Smart BE y vigilancia cada 500ms.",
            "builtin": True,
            "updated_at": _now_iso(),
            "params": base_default,
        },
        {
            "id": "BREAKOUT_100K",
            "name": "Breakout Orgánico $100K (Velas 1m/5m)",
            "description": "Entra en tokens limpios anti-rug que rompen $100K–$220K MCap con velas verdes 1m/5m, compras > ventas, Liq ≥ $15K, Holders ≥ 120 y cierra 100% al tocar TP 1.5x.",
            "builtin": True,
            "updated_at": _now_iso(),
            "params": breakout_100k,
        },
        {
            "id": "CONSERVADOR",
            "name": "Conservador Defensivo",
            "description": "Filtros estrictos (150 holders, Gate 75%/70%), Stop Loss -22%, TP1 +45% y Runner 2X.",
            "builtin": True,
            "updated_at": _now_iso(),
            "params": conservative,
        },
    ]


def coerce_strategy_params(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and type-cast editable strategy parameters."""
    cleaned: dict[str, Any] = {}
    for key, typ in STRATEGY_PARAM_TYPES.items():
        if key not in raw or raw[key] is None:
            continue
        val = raw[key]
        try:
            if typ is bool:
                if isinstance(val, bool):
                    cleaned[key] = val
                elif isinstance(val, str):
                    cleaned[key] = val.strip().lower() in {"1", "true", "yes", "on"}
                else:
                    cleaned[key] = bool(val)
            elif typ is int:
                cleaned[key] = int(round(float(val)))
            elif typ is float:
                cleaned[key] = float(val)
            else:
                text = str(val).strip().upper()
                if key == "strategy_profile":
                    cleaned[key] = "AGRESIVO" if text == "AGRESIVO" else "CONSERVADOR"
                else:
                    cleaned[key] = text
        except (TypeError, ValueError):
            continue
    return cleaned


def snapshot_current_params(settings: Settings, bot_state: dict[str, Any]) -> dict[str, Any]:
    """Read current live values of all 6-phase parameters from Settings + bot_state."""
    out: dict[str, Any] = {}
    for key in STRATEGY_PARAM_TYPES:
        if key == "trade_size_sol":
            out[key] = float(bot_state.get("trade_size_sol", settings.trade_size_sol))
        elif key == "max_concurrent_trades":
            out[key] = int(bot_state.get("max_concurrent_trades", settings.max_concurrent_trades))
        elif key == "semi_auto_mode":
            out[key] = bool(bot_state.get("semi_auto_mode", False))
        else:
            out[key] = getattr(settings, key)
    return out


def apply_params_to_settings(settings: Settings, params: dict[str, Any]) -> dict[str, Any]:
    """Apply validated strategy parameters onto ``settings`` and return bot_state updates."""
    clean = coerce_strategy_params(params)
    bot_updates: dict[str, Any] = {}
    for key, val in clean.items():
        if key == "trade_size_sol":
            bot_updates["trade_size_sol"] = float(val)
            settings.trade_size_sol = float(val)
        elif key == "max_concurrent_trades":
            mct = max(1, min(20, int(val)))
            bot_updates["max_concurrent_trades"] = mct
            settings.max_concurrent_trades = mct
        elif key == "semi_auto_mode":
            bot_updates["semi_auto_mode"] = 1 if bool(val) else 0
        elif hasattr(settings, key):
            setattr(settings, key, val)
    return bot_updates


def load_strategies(path: Path = STRATEGIES_PATH) -> dict[str, Any]:
    """Load saved strategies ensuring built-in defaults always exist."""
    defaults = built_in_strategies()
    if not path.exists():
        data = {"active_id": "PREDETERMINADA", "strategies": defaults}
        save_strategies_store(data, path)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("strategies"), list):
            raise ValueError("invalid store")
    except (OSError, ValueError):
        data = {"active_id": "PREDETERMINADA", "strategies": defaults}
        save_strategies_store(data, path)
        return data

    existing_ids = {str(s.get("id")) for s in data["strategies"] if isinstance(s, dict)}
    changed = False
    for d in defaults:
        if d["id"] not in existing_ids:
            data["strategies"].insert(0, d)
            changed = True
    if not data.get("active_id"):
        data["active_id"] = "PREDETERMINADA"
        changed = True
    if changed:
        save_strategies_store(data, path)
    return data


def save_strategies_store(data: dict[str, Any], path: Path = STRATEGIES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def upsert_strategy(
    *,
    name: str,
    params: dict[str, Any],
    description: str = "",
    strategy_id: str | None = None,
    path: Path = STRATEGIES_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create or update a strategy preset and mark it active."""
    store = load_strategies(path)
    clean_params = coerce_strategy_params(params)
    target_id = (strategy_id or _slugify(name)).strip()
    now = _now_iso()

    found = next((s for s in store["strategies"] if s.get("id") == target_id), None)
    if found is not None:
        found["name"] = name.strip() or found["name"]
        if description.strip():
            found["description"] = description.strip()
        found["params"] = {**(found.get("params") or {}), **clean_params}
        found["updated_at"] = now
        entry = found
    else:
        # Ensure unique ID if custom slug already exists
        base_id = target_id
        n = 2
        existing = {s.get("id") for s in store["strategies"]}
        while target_id in existing:
            target_id = f"{base_id}_{n}"
            n += 1
        entry = {
            "id": target_id,
            "name": name.strip() or target_id,
            "description": description.strip() or "Estrategia personalizada del usuario.",
            "builtin": False,
            "updated_at": now,
            "params": clean_params,
        }
        store["strategies"].append(entry)

    store["active_id"] = entry["id"]
    save_strategies_store(store, path)
    return entry, store


def delete_strategy(strategy_id: str, path: Path = STRATEGIES_PATH) -> dict[str, Any]:
    store = load_strategies(path)
    target = next((s for s in store["strategies"] if s.get("id") == strategy_id), None)
    if target is None:
        raise KeyError("STRATEGY_NOT_FOUND")
    if target.get("builtin"):
        raise PermissionError("BUILTIN_STRATEGY_PROTECTED")
    store["strategies"] = [s for s in store["strategies"] if s.get("id") != strategy_id]
    if store.get("active_id") == strategy_id:
        store["active_id"] = "PREDETERMINADA"
    save_strategies_store(store, path)
    return store
