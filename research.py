from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Callable, Dict, Iterable, List, Tuple

from alpaca_rest import AlpacaError, AlpacaRest
from config import Settings
from evolution import (
    DEFAULT_STRATEGIES,
    load_evolution_state,
    save_evolution_state,
    strategy_snapshot,
)
from storage import utc_now
from v4_doctrine import STRATEGY_DSL_GUIDE, V4_DOCTRINE


DSL_METRICS = {
    "close",
    "sma20",
    "sma50",
    "sma200",
    "high_20",
    "low_20",
    "pos_52w",
    "ret_5d_pct",
    "ret_20d_pct",
    "volume_ratio",
    "volatility_20_pct",
    "score",
}
DSL_OPERATORS = {">", ">=", "<", "<="}


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


def _indicators(closes: List[float], volumes: List[float], index: int) -> Dict[str, float]:
    close = closes[index]
    history = closes[: index + 1]
    volume_history = volumes[: index + 1]
    low = min(history[-252:]) if history else close
    high = max(history[-252:]) if history else close
    pos_52w = (close - low) / (high - low) if high > low else 0.5
    high_20 = max(closes[max(0, index - 20) : index]) if index > 0 else close
    low_20 = min(closes[max(0, index - 20) : index]) if index > 0 else close
    sma20 = _sma(history, 20)
    sma50 = _sma(history, 50)
    sma200 = _sma(history, 200)
    recent_returns = [
        (history[i] / history[i - 1]) - 1
        for i in range(max(1, len(history) - 20), len(history))
        if history[i - 1] > 0
    ]
    ret_5d_pct = ((close / closes[index - 5]) - 1) * 100 if index >= 5 and closes[index - 5] else 0.0
    ret_20d_pct = ((close / closes[index - 20]) - 1) * 100 if index >= 20 and closes[index - 20] else 0.0
    avg_volume_20 = _sma(volume_history[:-1], 20)
    volume_ratio = volumes[index] / avg_volume_20 if avg_volume_20 else 0.0
    volatility_20_pct = pstdev(recent_returns) * 100 if len(recent_returns) >= 2 else 0.0
    return {
        "close": close,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "high_20": high_20,
        "low_20": low_20,
        "pos_52w": pos_52w,
        "ret_5d_pct": ret_5d_pct,
        "ret_20d_pct": ret_20d_pct,
        "volume_ratio": volume_ratio,
        "volatility_20_pct": volatility_20_pct,
    }


def _signal_score(closes: List[float], volumes: List[float], index: int, score_bias: float) -> float:
    indicators = _indicators(closes, volumes, index)
    close = indicators["close"]
    score = 0.0
    if close > indicators["sma50"]:
        score += 18
    if close > indicators["sma200"]:
        score += 18
    score += max(0.0, min(1.0, indicators["pos_52w"])) * 25
    score += max(-10.0, min(20.0, indicators["ret_20d_pct"]))
    score += max(0.0, min(12.0, indicators["volume_ratio"] * 4))
    return round(score + score_bias, 4)


def _strategy_id(params: Dict[str, Any]) -> str:
    family = str(params.get("family", "momentum")).replace("_", "")[:6]
    return (
        f"research_{family}_"
        f"tp{int(_float(params['take_profit_pct']) * 1000)}_"
        f"sl{int(abs(_float(params['stop_loss_pct'])) * 1000)}_"
        f"ms{int(_float(params['min_entry_score']))}_"
        f"mh{int(_float(params['max_hold_days']))}_"
        f"sb{int(_float(params['score_bias']))}"
    )


def _mutation_id(parent: Dict[str, Any], params: Dict[str, Any], generation: int) -> str:
    parent_slug = _slug(parent.get("id") or parent.get("label") or "parent")[:18]
    family = str(params.get("family", "mutation")).replace("_", "")[:6]
    return (
        f"mut{generation}_{parent_slug}_{family}_"
        f"tp{int(_float(params['take_profit_pct']) * 1000)}_"
        f"sl{int(abs(_float(params['stop_loss_pct'])) * 1000)}_"
        f"ms{int(_float(params['min_entry_score']))}_"
        f"mh{int(_float(params['max_hold_days']))}_"
        f"sb{int(_float(params['score_bias']))}"
    )[:120]


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return slug[:36] or "idea"


def generate_strategy_variants(seed_state: Dict[str, Any], max_variants: int = 48) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = []
    seen = set()
    bases = list((seed_state.get("strategies") or DEFAULT_STRATEGIES).values())
    families = [
        "momentum",
        "breakout",
        "volume_momentum",
        "trend_pullback",
        "dip_buy",
        "mean_reversion",
        "oversold_reclaim",
        "volatility_runner",
    ]
    take_profits = [0.0125, 0.02, 0.03, 0.04, 0.06, 0.08]
    stop_losses = [-0.01, -0.0125, -0.02, -0.03, -0.045]
    min_scores = [20, 30, 40, 50, 60, 70]
    max_holds = [2, 3, 5, 10, 20, 35]
    biases = [-10, -5, 0, 5, 10]
    for base in bases:
        for take_profit in take_profits:
            for stop_loss in stop_losses:
                for min_score in min_scores:
                    for max_hold in max_holds:
                        for bias in biases:
                            for family in families:
                                params = {
                                    "family": family,
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
                                            f"Research {family.replace('_', ' ')} / "
                                            f"TP {take_profit:.1%} / SL {stop_loss:.1%} / "
                                            f"min {min_score} / hold {max_hold}d"
                                        ),
                                        "source": "research",
                                        **params,
                                        "stats": deepcopy(base.get("stats") or {}),
                                    }
                                )
                                if len(variants) >= max_variants:
                                    return variants
    return variants


def _candidate_parents(seed_state: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    parents: List[Dict[str, Any]] = []
    seen = set()
    for row in (seed_state.get("last_research") or {}).get("top") or []:
        strategy = row.get("strategy") if isinstance(row, dict) else None
        if not isinstance(strategy, dict):
            continue
        strategy_id = strategy.get("id")
        if strategy_id and strategy_id not in seen:
            seen.add(strategy_id)
            parents.append(strategy)
        if len(parents) >= limit:
            return parents
    for strategy in (seed_state.get("strategies") or {}).values():
        if not isinstance(strategy, dict):
            continue
        strategy_id = strategy.get("id")
        if strategy_id and strategy_id not in seen:
            seen.add(strategy_id)
            parents.append(strategy)
        if len(parents) >= limit:
            return parents
    return parents


def _mutate_rules(parent: Dict[str, Any], factor: float) -> List[Dict[str, Any]]:
    rules = []
    for rule in parent.get("entry_rules") or []:
        if not isinstance(rule, dict):
            continue
        metric = str(rule.get("metric") or "").strip()
        op = str(rule.get("op") or "").strip()
        if metric not in DSL_METRICS or op not in DSL_OPERATORS:
            continue
        value = _float(rule.get("value"))
        if metric in {"pos_52w", "volume_ratio", "volatility_20_pct", "score"}:
            mutated_value = value * factor
        else:
            mutated_value = value + ((factor - 1) * max(1.0, abs(value)))
        rules.append({"metric": metric, "op": op, "value": round(mutated_value, 4)})
    return rules[:8]


def generate_mutation_variants(
    seed_state: Dict[str, Any],
    *,
    max_variants: int,
    parent_count: int,
) -> List[Dict[str, Any]]:
    if max_variants <= 0 or parent_count <= 0:
        return []
    variants: List[Dict[str, Any]] = []
    seen = set()
    generation = len(seed_state.get("research_history") or []) + 1
    parents = _candidate_parents(seed_state, parent_count)
    take_profit_factors = [0.75, 1.0, 1.25, 1.5]
    stop_loss_factors = [0.75, 1.0, 1.25]
    score_shifts = [-10, -5, 0, 5, 10]
    hold_factors = [0.6, 0.85, 1.0, 1.25, 1.6]
    rule_factors = [0.85, 1.0, 1.15]
    for parent in parents:
        for take_factor in take_profit_factors:
            for stop_factor in stop_loss_factors:
                for score_shift in score_shifts:
                    for hold_factor in hold_factors:
                        for rule_factor in rule_factors:
                            params = {
                                "family": parent.get("family", "mutation"),
                                "score_bias": _clamp(
                                    _float(parent.get("score_bias")) + score_shift,
                                    -25,
                                    25,
                                    0,
                                ),
                                "min_entry_score": _clamp(
                                    _float(parent.get("min_entry_score")) + score_shift,
                                    0,
                                    95,
                                    20,
                                ),
                                "take_profit_pct": _clamp(
                                    _float(parent.get("take_profit_pct"), 0.04) * take_factor,
                                    0.005,
                                    0.18,
                                    0.04,
                                ),
                                "stop_loss_pct": -_clamp(
                                    abs(_float(parent.get("stop_loss_pct"), -0.025)) * stop_factor,
                                    0.005,
                                    0.14,
                                    0.025,
                                ),
                                "max_hold_days": int(
                                    _clamp(
                                        _float(parent.get("max_hold_days"), 20) * hold_factor,
                                        1,
                                        80,
                                        20,
                                    )
                                ),
                            }
                            mutated = {
                                "label": f"Mutation of {parent.get('label') or parent.get('id')}",
                                "source": "mutation",
                                "parent_strategy_id": parent.get("id"),
                                "mutation_generation": generation,
                                "thesis": parent.get("thesis"),
                                "entry_logic": parent.get("entry_logic", "all"),
                                **params,
                                "stats": deepcopy(parent.get("stats") or {}),
                            }
                            if parent.get("entry_rules"):
                                mutated["entry_rules"] = _mutate_rules(parent, rule_factor)
                                if not mutated["entry_rules"]:
                                    continue
                            mutated["id"] = _mutation_id(parent, mutated, generation)
                            if mutated["id"] in seen:
                                continue
                            seen.add(mutated["id"])
                            variants.append(mutated)
                            if len(variants) >= max_variants:
                                return variants
    return variants


def _compare(left: float, op: str, right: float) -> bool:
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def _dsl_entry_signal(strategy: Dict[str, Any], indicators: Dict[str, float], score: float) -> bool:
    rules = strategy.get("entry_rules") or []
    if not isinstance(rules, list) or not rules:
        return False
    values = {**indicators, "score": score}
    passed = []
    for rule in rules[:8]:
        if not isinstance(rule, dict):
            continue
        metric = str(rule.get("metric") or "").strip()
        op = str(rule.get("op") or "").strip()
        if metric not in DSL_METRICS or op not in DSL_OPERATORS:
            continue
        passed.append(_compare(_float(values.get(metric)), op, _float(rule.get("value"))))
    if not passed:
        return False
    logic = str(strategy.get("entry_logic") or "all").lower()
    if logic == "any":
        return any(passed)
    return all(passed)


def _entry_signal(strategy: Dict[str, Any], closes: List[float], volumes: List[float], index: int) -> tuple[bool, float]:
    score = _signal_score(closes, volumes, index, _float(strategy.get("score_bias")))
    if score < _float(strategy.get("min_entry_score")):
        return False, score
    indicators = _indicators(closes, volumes, index)
    close = indicators["close"]
    if strategy.get("entry_rules"):
        return _dsl_entry_signal(strategy, indicators, score), score
    family = strategy.get("family", "momentum")
    if family == "momentum":
        return close > indicators["sma50"] and indicators["ret_20d_pct"] > 0, score
    if family == "breakout":
        return close >= indicators["high_20"] * 0.995 and indicators["volume_ratio"] >= 1.05, score
    if family == "volume_momentum":
        return (
            close > indicators["sma20"]
            and indicators["ret_5d_pct"] > 1.0
            and indicators["volume_ratio"] >= 1.2
        ), score
    if family == "trend_pullback":
        return (
            close > indicators["sma50"]
            and close < indicators["sma20"] * 1.01
            and indicators["ret_20d_pct"] > -4
        ), score
    if family == "dip_buy":
        return (
            close > indicators["sma200"]
            and indicators["ret_5d_pct"] < -1.5
            and indicators["pos_52w"] > 0.35
        ), score
    if family == "mean_reversion":
        return close <= indicators["low_20"] * 1.02 and indicators["ret_20d_pct"] < -3, score
    if family == "oversold_reclaim":
        return (
            indicators["ret_5d_pct"] < -2.0
            and close > indicators["low_20"] * 1.025
            and indicators["volume_ratio"] >= 0.8
        ), score
    if family == "volatility_runner":
        return (
            close > indicators["sma50"]
            and indicators["ret_20d_pct"] > 4.0
            and indicators["volatility_20_pct"] >= 1.2
            and indicators["volume_ratio"] >= 0.9
        ), score
    return score >= _float(strategy.get("min_entry_score")), score


def _json_from_text(text: str) -> Dict[str, Any] | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    number = _float(value, default)
    return max(low, min(high, number))


def _normalize_ai_strategy(raw: Dict[str, Any], index: int) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    rules = []
    for rule in raw.get("entry_rules") or []:
        if not isinstance(rule, dict):
            continue
        metric = str(rule.get("metric") or "").strip()
        op = str(rule.get("op") or "").strip()
        if metric not in DSL_METRICS or op not in DSL_OPERATORS:
            continue
        rules.append({"metric": metric, "op": op, "value": _float(rule.get("value"))})
    if not rules:
        return None
    name = _slug(raw.get("name") or f"idea_{index}")
    take_profit = _clamp(raw.get("take_profit_pct"), 0.005, 0.15, 0.04)
    stop_loss = -_clamp(abs(_float(raw.get("stop_loss_pct"), 0.025)), 0.005, 0.12, 0.025)
    max_hold = int(_clamp(raw.get("max_hold_days"), 1, 60, 20))
    min_score = _clamp(raw.get("min_entry_score"), 0, 90, 20)
    strategy_id = f"ai_{index}_{name}"
    return {
        "id": strategy_id,
        "label": str(raw.get("label") or raw.get("name") or f"AI idea {index}")[:80],
        "source": "ai_strategy_lab",
        "family": "ai_dsl",
        "thesis": str(raw.get("thesis") or "AI-authored strategy proposal")[:700],
        "entry_logic": "any" if str(raw.get("entry_logic") or "").lower() == "any" else "all",
        "entry_rules": rules[:8],
        "score_bias": _clamp(raw.get("score_bias"), -20, 20, 0),
        "min_entry_score": min_score,
        "take_profit_pct": take_profit,
        "stop_loss_pct": stop_loss,
        "max_hold_days": max_hold,
        "stats": {
            "entries": 0,
            "exits": 0,
            "wins": 0,
            "losses": 0,
            "total_plpc": 0.0,
            "fitness": 0.0,
        },
    }


def _symbol_snapshot(symbol: str, bars: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    closes = [_float(bar.get("c")) for bar in bars if _float(bar.get("c")) > 0]
    volumes = [_float(bar.get("v")) for bar in bars if _float(bar.get("c")) > 0]
    if len(closes) < 60 or len(closes) != len(volumes):
        return None
    indicators = _indicators(closes, volumes, len(closes) - 1)
    return {
        "symbol": symbol,
        "bars": len(closes),
        "price": round(indicators["close"], 4),
        "ret_5d_pct": round(indicators["ret_5d_pct"], 3),
        "ret_20d_pct": round(indicators["ret_20d_pct"], 3),
        "pos_52w": round(indicators["pos_52w"], 3),
        "volume_ratio": round(indicators["volume_ratio"], 3),
        "volatility_20_pct": round(indicators["volatility_20_pct"], 3),
        "above_sma50": indicators["close"] > indicators["sma50"],
        "above_sma200": indicators["close"] > indicators["sma200"],
    }


def generate_ai_strategy_variants(
    settings: Settings,
    seed_state: Dict[str, Any],
    bars_by_symbol: Dict[str, Any],
    *,
    max_variants: int,
) -> List[Dict[str, Any]]:
    if (
        not settings.autonomy_ai_strategy_lab_enabled
        or not settings.openai_ready
        or max_variants <= 0
    ):
        return []
    try:
        from openai import OpenAI
    except ImportError:
        return []

    snapshots = [
        snapshot
        for symbol, bars in list(bars_by_symbol.items())[:120]
        if (snapshot := _symbol_snapshot(symbol, bars))
    ]
    if not snapshots:
        return []
    context = {
        "doctrine": V4_DOCTRINE,
        "strategy_dsl": STRATEGY_DSL_GUIDE,
        "max_strategies": max_variants,
        "recent_research": seed_state.get("last_research"),
        "symbol_snapshots": snapshots,
    }
    prompt = (
        "You are v4's autonomous trading research strategist. "
        "Create diverse, thesis-driven paper-trading strategy candidates as JSON data. "
        "Do not return prose. Return only JSON shaped like "
        '{"strategies":[{"name":"...","thesis":"...","entry_logic":"all",'
        '"entry_rules":[{"metric":"ret_20d_pct","op":">","value":3}],'
        '"take_profit_pct":0.04,"stop_loss_pct":-0.025,"max_hold_days":20,'
        '"min_entry_score":20,"score_bias":0}]}. '
        "Prefer varied ideas over small parameter tweaks."
    )
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=f"{prompt}\n\nContext:\n{json.dumps(context, default=str)[:50000]}",
        )
        parsed = _json_from_text(getattr(response, "output_text", "") or "")
    except Exception:
        return []
    strategies = []
    seen = set()
    for index, raw in enumerate((parsed or {}).get("strategies") or [], start=1):
        strategy = _normalize_ai_strategy(raw, index)
        if not strategy or strategy["id"] in seen:
            continue
        seen.add(strategy["id"])
        strategies.append(strategy)
        if len(strategies) >= max_variants:
            break
    return strategies


def _compact_strategy(strategy: Dict[str, Any]) -> Dict[str, Any]:
    compact = {
        "id": strategy.get("id"),
        "source": strategy.get("source"),
        "family": strategy.get("family"),
        "take_profit_pct": strategy.get("take_profit_pct"),
        "stop_loss_pct": strategy.get("stop_loss_pct"),
        "max_hold_days": strategy.get("max_hold_days"),
        "min_entry_score": strategy.get("min_entry_score"),
        "score_bias": strategy.get("score_bias"),
    }
    if strategy.get("parent_strategy_id"):
        compact["parent_strategy_id"] = strategy.get("parent_strategy_id")
    if strategy.get("thesis"):
        compact["thesis"] = str(strategy.get("thesis"))[:240]
    if strategy.get("entry_rules"):
        compact["entry_logic"] = strategy.get("entry_logic", "all")
        compact["entry_rules"] = strategy.get("entry_rules")[:5]
    return compact


def ai_triage_variants(
    settings: Settings,
    seed_state: Dict[str, Any],
    bars_by_symbol: Dict[str, Any],
    variants: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    target = max(1, settings.autonomy_ai_variant_triage_target)
    if (
        not settings.autonomy_ai_variant_triage_enabled
        or not settings.openai_ready
        or len(variants) <= target
    ):
        return variants, {
            "enabled": settings.autonomy_ai_variant_triage_enabled,
            "used": False,
            "reason": "not_needed",
            "input_variants": len(variants),
            "selected_variants": len(variants),
            "target": target,
        }
    try:
        from openai import OpenAI
    except ImportError:
        return variants, {
            "enabled": True,
            "used": False,
            "reason": "openai_package_missing",
            "input_variants": len(variants),
            "selected_variants": len(variants),
            "target": target,
        }

    snapshots = [
        snapshot
        for symbol, bars in list(bars_by_symbol.items())[:80]
        if (snapshot := _symbol_snapshot(symbol, bars))
    ]
    payload = {
        "doctrine": V4_DOCTRINE,
        "task": (
            "Select the most promising, diverse strategy variant IDs for CPU backtesting. "
            "Favor thesis diversity, mutation descendants of near-winners, AI-authored rules, "
            "reasonable risk/reward, enough trade potential, and avoid tiny parameter duplicates."
        ),
        "target_count": target,
        "recent_research": seed_state.get("last_research"),
        "research_history": (seed_state.get("research_history") or [])[-10:],
        "symbol_snapshots": snapshots,
        "variants": [_compact_strategy(strategy) for strategy in variants],
    }
    prompt = (
        "You are v4's research director. Use judgment to reduce CPU work before "
        "backtesting. Return only JSON: "
        '{"selected_ids":["id1","id2"],"rationale":"short reason"}. '
        f"Select up to {target} IDs from the provided variants. Do not invent IDs."
    )
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=f"{prompt}\n\nContext:\n{json.dumps(payload, default=str)[:180000]}",
        )
        parsed = _json_from_text(getattr(response, "output_text", "") or "")
    except Exception as exc:
        return variants, {
            "enabled": True,
            "used": False,
            "reason": "openai_error",
            "error": str(exc),
            "input_variants": len(variants),
            "selected_variants": len(variants),
            "target": target,
        }

    selected_ids = []
    seen_ids = set()
    for raw_id in (parsed or {}).get("selected_ids") or []:
        variant_id = str(raw_id)
        if variant_id and variant_id not in seen_ids:
            seen_ids.add(variant_id)
            selected_ids.append(variant_id)
    by_id = {str(strategy.get("id")): strategy for strategy in variants}
    selected = [by_id[variant_id] for variant_id in selected_ids if variant_id in by_id]
    minimum_valid = min(len(variants), max(3, min(target, len(variants)) // 3))
    if len(selected) < minimum_valid:
        return variants, {
            "enabled": True,
            "used": False,
            "reason": "too_few_valid_ids",
            "input_variants": len(variants),
            "selected_variants": len(variants),
            "target": target,
            "valid_ids_returned": len(selected),
        }
    for strategy in variants:
        if len(selected) >= target:
            break
        if strategy not in selected:
            selected.append(strategy)
    return selected[:target], {
        "enabled": True,
        "used": True,
        "reason": "openai_selected",
        "input_variants": len(variants),
        "selected_variants": len(selected[:target]),
        "target": target,
        "rationale": (parsed or {}).get("rationale"),
    }


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

            should_enter, score = _entry_signal(strategy, closes, volumes, index)
            if should_enter:
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


def _fetch_bars(alpaca: AlpacaRest, symbols: List[str], lookback_days: int) -> Dict[str, Any]:
    start = (
        datetime.now(timezone.utc)
        - timedelta(days=max(90, lookback_days))
    ).date().isoformat()
    merged: Dict[str, Any] = {}
    for chunk in _chunks(symbols, 50):
        response = alpaca.stock_bars(chunk, start=start)
        merged.update(response.get("bars") or {})
    return merged


def _subset_bars(bars_by_symbol: Dict[str, Any], limit: int) -> Dict[str, Any]:
    if limit <= 0:
        return dict(bars_by_symbol)
    return {
        symbol: bars
        for symbol, bars in list(bars_by_symbol.items())[:limit]
    }


def run_research(
    settings: Settings,
    alpaca: AlpacaRest,
    progress_callback: Callable[[Dict[str, Any]], None] | None = None,
) -> Dict[str, Any]:
    def progress(update: Dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(update)

    state = load_evolution_state(settings.data_dir)
    progress(
        {
            "stage": "selecting_symbols",
            "message": "Selecting rotating research universe.",
        }
    )
    symbols = _select_symbols(
        settings,
        alpaca,
        state,
        limit=settings.autonomy_research_symbols_per_run,
    )
    if not symbols:
        return {"ok": False, "reply": "No symbols were available for research."}
    try:
        progress(
            {
                "stage": "fetching_bars",
                "message": f"Fetching historical bars for {len(symbols)} selected symbols.",
                "selected_symbols": len(symbols),
            }
        )
        bars = _fetch_bars(alpaca, symbols, settings.autonomy_research_lookback_days)
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
    scout_train_bars = _subset_bars(train_bars, settings.autonomy_research_scout_symbols)
    progress(
        {
            "stage": "generating_variants",
            "message": "Generating AI lab and coded strategy variants.",
            "usable_symbols": len(bars),
            "train_symbols": len(train_bars),
            "validation_symbols": len(test_bars),
            "scout_symbols": len(scout_train_bars),
        }
    )
    ai_variants = generate_ai_strategy_variants(
        settings,
        state,
        bars,
        max_variants=settings.autonomy_ai_strategy_ideas,
    )
    mutation_variants = (
        generate_mutation_variants(
            state,
            max_variants=settings.autonomy_mutation_variants,
            parent_count=settings.autonomy_mutation_parent_count,
        )
        if settings.autonomy_mutation_enabled
        else []
    )
    deterministic_variants = generate_strategy_variants(
        state,
        max_variants=max(
            0,
            settings.autonomy_research_max_variants - len(ai_variants) - len(mutation_variants),
        ),
    )
    generated_variants = ai_variants + mutation_variants + deterministic_variants
    progress(
        {
            "stage": "ai_triage",
            "message": (
                f"AI triaging {len(generated_variants)} generated variants "
                f"toward {settings.autonomy_ai_variant_triage_target} scout candidates."
            ),
            "variants_total": len(generated_variants),
        }
    )
    variants, triage = ai_triage_variants(settings, state, bars, generated_variants)
    scout_rows = []
    total_variants = len(variants)
    progress(
        {
            "stage": "scouting",
            "message": f"Scouting {total_variants} variants on {len(scout_train_bars)} symbols.",
            "variants_total": total_variants,
            "variants_completed": 0,
        }
    )
    for index, strategy in enumerate(variants, start=1):
        scout = _simulate_strategy_on_bars(strategy, scout_train_bars)
        scout_rows.append(
            {
                "strategy": strategy,
                "scout": scout,
                "scout_fitness": scout["fitness"],
            }
        )
        if index == total_variants or index % 25 == 0:
            progress(
                {
                    "stage": "scouting",
                    "message": f"Scouted {index}/{total_variants} variants.",
                    "variants_total": total_variants,
                    "variants_completed": index,
                }
            )
    scout_rows.sort(key=lambda row: row["scout_fitness"], reverse=True)
    validation_limit = max(1, settings.autonomy_research_validate_top_variants)
    finalists = scout_rows[:validation_limit]
    leaderboard = []
    progress(
        {
            "stage": "validating",
            "message": f"Validating top {len(finalists)} variants on full train/test split.",
            "variants_total": len(finalists),
            "variants_completed": 0,
        }
    )
    for index, row in enumerate(finalists, start=1):
        strategy = row["strategy"]
        train = _simulate_strategy_on_bars(strategy, train_bars)
        validation = _simulate_strategy_on_bars(strategy, test_bars)
        combined_fitness = round(train["fitness"] * 0.35 + validation["fitness"] * 0.65, 6)
        leaderboard.append(
            {
                "strategy": strategy,
                "scout": row["scout"],
                "train": train,
                "validation": validation,
                "combined_fitness": combined_fitness,
                "profitable": validation["total_return_pct"] > 0 and validation["trades"] >= 3,
            }
        )
        if index == len(finalists) or index % 5 == 0:
            progress(
                {
                    "stage": "validating",
                    "message": f"Validated {index}/{len(finalists)} finalist variants.",
                    "variants_total": len(finalists),
                    "variants_completed": index,
                }
            )
    leaderboard.sort(key=lambda row: row["combined_fitness"], reverse=True)
    best = leaderboard[0] if leaderboard else None
    if not best:
        return {"ok": False, "reply": "Research did not produce any strategy variants."}

    should_promote = bool(best["profitable"] or not settings.autonomy_research_require_profitable)
    progress(
        {
            "stage": "promoting" if should_promote else "not_promoting",
            "message": (
                f"Promoting best strategy {best['strategy']['id']}."
                if should_promote
                else (
                    f"Best candidate {best['strategy']['id']} was not promoted "
                    "because validation was not profitable."
                )
            ),
        }
    )
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
    previous_active_strategy_id = state.get("active_strategy_id")
    if should_promote:
        state.setdefault("strategies", {})[best_strategy["id"]] = best_strategy
        state["active_strategy_id"] = best_strategy["id"]
    state["last_research"] = {
        "researched_at": utc_now(),
        "symbols": symbols,
        "selected_symbols_count": len(symbols),
        "bars_symbols": len(bars),
        "lookback_days": settings.autonomy_research_lookback_days,
        "variants_generated": len(generated_variants),
        "variants_tested": len(variants),
        "variants_validated": len(leaderboard),
        "ai_triage": triage,
        "scout_symbols": len(scout_train_bars),
        "validation_top_variants": len(finalists),
        "ai_variants_tested": len(ai_variants),
        "mutation_variants_tested": len(mutation_variants),
        "coded_variants_tested": len(deterministic_variants),
        "best_strategy_id": best_strategy["id"],
        "candidate_strategy_id": best_strategy["id"],
        "promoted_strategy_id": best_strategy["id"] if should_promote else None,
        "previous_active_strategy_id": previous_active_strategy_id,
        "active_strategy_id": state.get("active_strategy_id"),
        "promoted": should_promote,
        "promotion_reason": (
            "validation_profitable"
            if should_promote and best["profitable"]
            else "profitability_required_disabled"
            if should_promote
            else "validation_not_profitable"
        ),
        "best": best,
        "top": leaderboard[:10],
        "family_counts": {
            family: sum(
                1
                for row in leaderboard
                if (row.get("strategy") or {}).get("family") == family
            )
            for family in sorted(
                {
                    (row.get("strategy") or {}).get("family")
                    for row in leaderboard
                    if (row.get("strategy") or {}).get("family")
                }
            )
        },
    }
    state.setdefault("research_history", []).append(
        {
            "researched_at": state["last_research"]["researched_at"],
            "candidate_strategy_id": best_strategy["id"],
            "promoted": should_promote,
            "promoted_strategy_id": best_strategy["id"] if should_promote else None,
            "validation": best["validation"],
            "combined_fitness": best["combined_fitness"],
            "symbols_selected": len(symbols),
            "symbols_usable": len(bars),
            "lookback_days": settings.autonomy_research_lookback_days,
            "variants_generated": len(generated_variants),
            "variants_tested": len(variants),
            "variants_validated": len(leaderboard),
            "ai_triage_used": bool(triage.get("used")),
            "ai_variants_tested": len(ai_variants),
            "mutation_variants_tested": len(mutation_variants),
        }
    )
    state["research_history"] = state["research_history"][-50:]
    save_evolution_state(settings.data_dir, state)
    return {
        "ok": True,
        "reply": (
            f"Research generated {len(generated_variants)} variants, "
            f"{'AI-triaged to' if triage.get('used') else 'scouted'} {len(variants)} variants "
            f"on {len(scout_train_bars)} scout symbols "
            f"and validated {len(leaderboard)} finalists on {len(bars)} usable symbols "
            f"({len(symbols)} selected). "
            f"AI lab contributed {len(ai_variants)} thesis-driven variants. "
            f"Mutation contributed {len(mutation_variants)} descendants. "
            f"{'Deployed' if should_promote else 'Did not deploy'} {best_strategy['id']} "
            f"with validation return {best['validation']['total_return_pct']:.2%}, "
            f"win rate {best['validation']['win_rate']:.1%}, "
            f"{best['validation']['trades']} trades."
            + ("" if should_promote else " Keeping previous active strategy.")
        ),
        "research": state["last_research"],
        "strategy": strategy_snapshot(state),
    }
