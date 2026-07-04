from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable

from storage import ensure_data_dirs, utc_now


DEFAULT_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "momentum_balanced": {
        "id": "momentum_balanced",
        "label": "Momentum balanced",
        "score_bias": 0.0,
        "take_profit_pct": 0.04,
        "stop_loss_pct": -0.025,
        "stats": {
            "entries": 0,
            "exits": 0,
            "wins": 0,
            "losses": 0,
            "total_plpc": 0.0,
            "fitness": 0.0,
        },
    },
    "momentum_fast": {
        "id": "momentum_fast",
        "label": "Momentum fast",
        "score_bias": 4.0,
        "take_profit_pct": 0.025,
        "stop_loss_pct": -0.015,
        "stats": {
            "entries": 0,
            "exits": 0,
            "wins": 0,
            "losses": 0,
            "total_plpc": 0.0,
            "fitness": 0.0,
        },
    },
    "momentum_runner": {
        "id": "momentum_runner",
        "label": "Momentum runner",
        "score_bias": -3.0,
        "take_profit_pct": 0.075,
        "stop_loss_pct": -0.04,
        "stats": {
            "entries": 0,
            "exits": 0,
            "wins": 0,
            "losses": 0,
            "total_plpc": 0.0,
            "fitness": 0.0,
        },
    },
}


def _state_path(data_dir: str) -> Path:
    return ensure_data_dirs(data_dir)["root"] / "evolution_state.json"


def default_state() -> Dict[str, Any]:
    return {
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "active_strategy_id": "momentum_balanced",
        "positions": {},
        "strategies": deepcopy(DEFAULT_STRATEGIES),
    }


def load_evolution_state(data_dir: str) -> Dict[str, Any]:
    path = _state_path(data_dir)
    if not path.exists():
        return default_state()
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    strategies = state.setdefault("strategies", {})
    for strategy_id, strategy in DEFAULT_STRATEGIES.items():
        if strategy_id not in strategies:
            strategies[strategy_id] = deepcopy(strategy)
    state.setdefault("positions", {})
    state.setdefault("active_strategy_id", "momentum_balanced")
    return state


def save_evolution_state(data_dir: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    path = _state_path(data_dir)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def active_strategy(state: Dict[str, Any]) -> Dict[str, Any]:
    strategies = state.get("strategies") or {}
    strategy_id = state.get("active_strategy_id") or "momentum_balanced"
    return strategies.get(strategy_id) or strategies["momentum_balanced"]


def adjusted_score(candidate: Dict[str, Any], strategy: Dict[str, Any]) -> float:
    return float(candidate.get("score") or 0.0) + float(strategy.get("score_bias") or 0.0)


def tag_entry(
    state: Dict[str, Any],
    *,
    symbol: str,
    strategy: Dict[str, Any],
    candidate: Dict[str, Any],
    notional: float,
) -> None:
    symbol = symbol.upper()
    strategy_id = strategy["id"]
    stats = state["strategies"][strategy_id]["stats"]
    stats["entries"] = int(stats.get("entries") or 0) + 1
    state["positions"][symbol] = {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "entry_score": candidate.get("score"),
        "adjusted_score": adjusted_score(candidate, strategy),
        "entry_notional": notional,
        "opened_at": utc_now(),
    }


def tag_exit(
    state: Dict[str, Any],
    *,
    symbol: str,
    plpc: float,
    reason: str,
) -> Dict[str, Any]:
    symbol = symbol.upper()
    tracked = state.get("positions", {}).pop(symbol, None) or {}
    strategy_id = tracked.get("strategy_id") or state.get("active_strategy_id") or "momentum_balanced"
    strategy = state["strategies"].get(strategy_id) or state["strategies"]["momentum_balanced"]
    stats = strategy["stats"]
    stats["exits"] = int(stats.get("exits") or 0) + 1
    stats["wins"] = int(stats.get("wins") or 0) + (1 if plpc > 0 else 0)
    stats["losses"] = int(stats.get("losses") or 0) + (1 if plpc <= 0 else 0)
    stats["total_plpc"] = round(float(stats.get("total_plpc") or 0.0) + plpc, 6)
    exits = max(1, int(stats.get("exits") or 0))
    win_rate = int(stats.get("wins") or 0) / exits
    avg_plpc = float(stats.get("total_plpc") or 0.0) / exits
    stats["fitness"] = round(avg_plpc + (win_rate * 0.01), 6)
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "plpc": plpc,
        "reason": reason,
        "closed_at": utc_now(),
    }


def evolve(state: Dict[str, Any]) -> Dict[str, Any]:
    strategies = state.get("strategies") or {}
    eligible = [
        strategy
        for strategy in strategies.values()
        if int((strategy.get("stats") or {}).get("exits") or 0) > 0
    ]
    if eligible:
        winner = max(
            eligible,
            key=lambda strategy: float((strategy.get("stats") or {}).get("fitness") or 0.0),
        )
        state["active_strategy_id"] = winner["id"]
    return active_strategy(state)


def strategy_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    strategies = state.get("strategies") or {}
    rows = sorted(
        strategies.values(),
        key=lambda strategy: float((strategy.get("stats") or {}).get("fitness") or 0.0),
        reverse=True,
    )
    return {
        "active_strategy_id": state.get("active_strategy_id"),
        "open_tracked_positions": len(state.get("positions") or {}),
        "strategies": rows,
    }


def remove_missing_positions(state: Dict[str, Any], symbols: Iterable[str]) -> None:
    live = {symbol.upper() for symbol in symbols}
    tracked = state.get("positions") or {}
    for symbol in list(tracked):
        if symbol.upper() not in live:
            tracked.pop(symbol, None)
