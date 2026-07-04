from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List, Tuple

from alpaca_rest import AlpacaError, AlpacaRest
from config import Settings
from evolution import (
    DEFAULT_STRATEGIES,
    load_evolution_state,
    save_evolution_state,
    strategy_snapshot,
)
from storage import utc_now


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _sma(values: List[float], window: int) -> float:
    clean = [value for value in values if value > 0]
    if not clean:
        return 0.0
    return mean(clean[-window:]) if len(clean) >= window else mean(clean)


def _signal_score(closes: List[float], volumes: List[float], index: int, score_bias: float) -> float:
    close = closes[index]
    history = closes[: index + 1]
    volume_history = volumes[: index + 1]
    low = min(history[-252:]) if history else close
    high = max(history[-252:]) if history else close
    pos_52w = (close - low) / (high - low) if high > low else 0.5
    ret_20d_pct = ((close / closes[index - 20]) - 1) * 100 if index >= 20 and closes[index - 20] else 0.0
    avg_volume_20 = _sma(volume_history[:-1], 20)
    volume_ratio = volumes[index] / avg_volume_20 if avg_volume_20 else 0.0
    score = 0.0
    if close > _sma(history, 50):
        score += 18
    if close > _sma(history, 200):
        score += 18
    score += max(0.0, min(1.0, pos_52w)) * 25
    score += max(-10.0, min(20.0, ret_20d_pct))
    score += max(0.0, min(12.0, volume_ratio * 4))
    return round(score + score_bias, 4)


def _strategy_id(params: Dict[str, Any]) -> str:
    return (
        "research_"
        f"tp{int(_float(params['take_profit_pct']) * 1000)}_"
        f"sl{int(abs(_float(params['stop_loss_pct'])) * 1000)}_"
        f"ms{int(_float(params['min_entry_score']))}_"
        f"mh{int(_float(params['max_hold_days']))}_"
        f"sb{int(_float(params['score_bias']))}"
    )


def generate_strategy_variants(seed_state: Dict[str, Any], max_variants: int = 48) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = []
    seen = set()
    bases = list((seed_state.get("strategies") or DEFAULT_STRATEGIES).values())
    take_profits = [0.02, 0.03, 0.04, 0.06, 0.08]
    stop_losses = [-0.0125, -0.02, -0.03, -0.045]
    min_scores = [45, 55, 65]
    max_holds = [5, 10, 20, 35]
    biases = [-5, 0, 5]
    for base in bases:
        for take_profit in take_profits:
            for stop_loss in stop_losses:
                for min_score in min_scores:
                    for max_hold in max_holds:
                        for bias in biases:
                            params = {
                                "score_bias": _float(base.get("score_bias")) + bias,
                                "min_entry_score": min_score,
                                "take_profit_pct": take_profit,
                                "stop_loss_pct": stop_loss,
                                "max_hold_days": max_hold,
                            }
                            strategy_id = _strategy_id(params)
                            if strategy_id in seen:
                                continue
                            seen.add(strategy_id)
                            variants.append(
                                {
                                    "id": strategy_id,
                                    "label": (
                                        f"Research TP {take_profit:.1%} / "
                                        f"SL {stop_loss:.1%} / min {min_score} / hold {max_hold}d"
                                    ),
                                    "source": "research",
                                    **params,
                                    "stats": deepcopy(base.get("stats") or {}),
                                }
                            )
                            if len(variants) >= max_variants:
                                return variants
    return variants


def _simulate_strategy_on_bars(strategy: Dict[str, Any], bars_by_symbol: Dict[str, Any]) -> Dict[str, Any]:
    trades: List[Dict[str, Any]] = []
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for symbol, bars in bars_by_symbol.items():
        closes = [_float(bar.get("c")) for bar in bars if _float(bar.get("c")) > 0]
        volumes = [_float(bar.get("v")) for bar in bars if _float(bar.get("c")) > 0]
        if len(closes) < 60 or len(closes) != len(volumes):
            continue
        position: Dict[str, Any] | None = None
        for index in range(55, len(closes)):
            close = closes[index]
            if position:
                ret = (close / position["entry_price"]) - 1
                hold_days = index - position["entry_index"]
                reason = ""
                if ret >= _float(strategy.get("take_profit_pct")):
                    reason = "take_profit"
                elif ret <= _float(strategy.get("stop_loss_pct")):
                    reason = "stop_loss"
                elif hold_days >= _float(strategy.get("max_hold_days")):
                    reason = "max_hold"
                if reason:
                    contribution = ret * 0.02
                    equity *= 1 + contribution
                    peak = max(peak, equity)
                    max_drawdown = min(max_drawdown, (equity / peak) - 1)
                    trades.append(
                        {
                            "symbol": symbol,
                            "entry_index": position["entry_index"],
                            "exit_index": index,
                            "return_pct": ret,
                            "reason": reason,
                        }
                    )
                    position = None
                continue

            score = _signal_score(closes, volumes, index, _float(strategy.get("score_bias")))
            if score >= _float(strategy.get("min_entry_score")):
                position = {"entry_index": index, "entry_price": close, "score": score}

    wins = sum(1 for trade in trades if trade["return_pct"] > 0)
    losses = len(trades) - wins
    avg_return = mean([trade["return_pct"] for trade in trades]) if trades else 0.0
    total_return = equity - 1
    win_rate = wins / len(trades) if trades else 0.0
    fitness = total_return + (win_rate * 0.02) + min(len(trades), 25) * 0.0005 + max_drawdown
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "avg_trade_return_pct": round(avg_return, 6),
        "total_return_pct": round(total_return, 6),
        "max_drawdown_pct": round(max_drawdown, 6),
        "fitness": round(fitness, 6),
    }


def _split_bars(bars_by_symbol: Dict[str, Any], split: float = 0.65) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    train: Dict[str, Any] = {}
    test: Dict[str, Any] = {}
    for symbol, bars in bars_by_symbol.items():
        if len(bars) < 90:
            continue
        split_index = max(60, int(len(bars) * split))
        train[symbol] = bars[:split_index]
        test[symbol] = bars[max(0, split_index - 60) :]
    return train, test


def _select_symbols(settings: Settings, alpaca: AlpacaRest, state: Dict[str, Any], limit: int) -> List[str]:
    if settings.autonomy_symbols:
        universe = list(settings.autonomy_symbols)
    else:
        universe = alpaca.active_tradable_us_equity_symbols()
    if not universe:
        return []
    offset = int(state.get("research_offset") or 0) % len(universe)
    rotated = universe[offset:] + universe[:offset]
    selected = rotated[:limit]
    state["research_offset"] = (offset + len(selected)) % len(universe)
    return selected


def _fetch_bars(alpaca: AlpacaRest, symbols: List[str]) -> Dict[str, Any]:
    start = (datetime.now(timezone.utc) - timedelta(days=390)).date().isoformat()
    merged: Dict[str, Any] = {}
    for chunk in _chunks(symbols, 50):
        response = alpaca.stock_bars(chunk, start=start)
        merged.update(response.get("bars") or {})
    return merged


def run_research(settings: Settings, alpaca: AlpacaRest) -> Dict[str, Any]:
    state = load_evolution_state(settings.data_dir)
    symbols = _select_symbols(settings, alpaca, state, limit=25)
    if not symbols:
        return {"ok": False, "reply": "No symbols were available for research."}
    try:
        bars = _fetch_bars(alpaca, symbols)
    except AlpacaError as exc:
        if "429" not in str(exc) and "too many requests" not in str(exc).lower():
            raise
        save_evolution_state(settings.data_dir, state)
        return {
            "ok": True,
            "reply": "Research paused because Alpaca rate-limited historical bars.",
            "rate_limited": True,
            "warning": str(exc),
            "symbols": symbols,
        }

    train_bars, test_bars = _split_bars(bars)
    variants = generate_strategy_variants(state, max_variants=48)
    leaderboard = []
    for strategy in variants:
        train = _simulate_strategy_on_bars(strategy, train_bars)
        validation = _simulate_strategy_on_bars(strategy, test_bars)
        combined_fitness = round(train["fitness"] * 0.35 + validation["fitness"] * 0.65, 6)
        leaderboard.append(
            {
                "strategy": strategy,
                "train": train,
                "validation": validation,
                "combined_fitness": combined_fitness,
                "profitable": validation["total_return_pct"] > 0 and validation["trades"] >= 3,
            }
        )
    leaderboard.sort(key=lambda row: row["combined_fitness"], reverse=True)
    best = leaderboard[0] if leaderboard else None
    if not best:
        return {"ok": False, "reply": "Research did not produce any strategy variants."}

    best_strategy = deepcopy(best["strategy"])
    best_strategy["backtest"] = {
        "researched_at": utc_now(),
        "symbols": symbols,
        "train": best["train"],
        "validation": best["validation"],
        "combined_fitness": best["combined_fitness"],
        "profitable": best["profitable"],
    }
    best_strategy.setdefault("stats", {})
    state.setdefault("strategies", {})[best_strategy["id"]] = best_strategy
    state["active_strategy_id"] = best_strategy["id"]
    state["last_research"] = {
        "researched_at": utc_now(),
        "symbols": symbols,
        "bars_symbols": len(bars),
        "variants_tested": len(leaderboard),
        "best_strategy_id": best_strategy["id"],
        "best": best,
        "top": leaderboard[:10],
    }
    save_evolution_state(settings.data_dir, state)
    return {
        "ok": True,
        "reply": (
            f"Research tested {len(leaderboard)} variants on {len(bars)} symbols. "
            f"Deployed {best_strategy['id']} with validation return "
            f"{best['validation']['total_return_pct']:.2%}, "
            f"win rate {best['validation']['win_rate']:.1%}, "
            f"{best['validation']['trades']} trades."
        ),
        "research": state["last_research"],
        "strategy": strategy_snapshot(state),
    }
