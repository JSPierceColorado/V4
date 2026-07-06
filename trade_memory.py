from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from storage import ensure_data_dirs, load_events, utc_now


ENTRY_FEATURES = (
    "close",
    "score",
    "adjusted_score",
    "sma_20",
    "sma_50",
    "sma_200",
    "pos_52w",
    "ret_1d_pct",
    "ret_5d_pct",
    "ret_10d_pct",
    "ret_20d_pct",
    "ret_60d_pct",
    "volume_ratio",
    "dollar_vol_m",
    "dollar_vol_20d_m",
    "atr_14_pct",
    "gap_pct",
    "range_20_pct",
    "bars",
)


def _ledger_path(data_dir: str) -> Path:
    return ensure_data_dirs(data_dir)["root"] / "trade_theses.jsonl"


def _write_record(data_dir: str, record: Dict[str, Any]) -> Dict[str, Any]:
    paths = ensure_data_dirs(data_dir)
    record = {"recorded_at": utc_now(), **record}
    with (paths["root"] / "trade_theses.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    return record


def load_trade_thesis_records(data_dir: str, limit: int = 200) -> List[Dict[str, Any]]:
    path = _ledger_path(data_dir)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def open_trade_theses(data_dir: str, limit: int = 50) -> List[Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    closed = set()
    for record in load_trade_thesis_records(data_dir, limit=1000):
        thesis_id = record.get("thesis_id")
        if not thesis_id:
            continue
        if record.get("type") == "entry_thesis":
            entries[thesis_id] = record
        elif record.get("type") == "exit_review":
            closed.add(thesis_id)
    open_rows = [
        row
        for thesis_id, row in entries.items()
        if thesis_id not in closed
    ]
    return open_rows[-limit:]


def find_open_thesis(data_dir: str, symbol: str) -> Optional[Dict[str, Any]]:
    target = str(symbol or "").upper()
    matches = [
        row
        for row in open_trade_theses(data_dir, limit=200)
        if str(row.get("symbol", "")).upper() == target
    ]
    return matches[-1] if matches else None


def _compact_strategy(strategy: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": strategy.get("id"),
        "label": strategy.get("label"),
        "source": strategy.get("source"),
        "family": strategy.get("family"),
        "thesis": strategy.get("thesis"),
        "entry_logic": strategy.get("entry_logic"),
        "entry_rules": strategy.get("entry_rules"),
        "take_profit_pct": strategy.get("take_profit_pct"),
        "stop_loss_pct": strategy.get("stop_loss_pct"),
        "max_hold_days": strategy.get("max_hold_days"),
        "min_entry_score": strategy.get("min_entry_score"),
        "score_bias": strategy.get("score_bias"),
    }


def _compact_candidate(candidate: Dict[str, Any], adjusted_score: float) -> Dict[str, Any]:
    compact = {
        key: candidate.get(key)
        for key in ENTRY_FEATURES
        if key in candidate
    }
    compact["adjusted_score"] = adjusted_score
    return compact


def _recent_market_context(data_dir: str) -> List[Dict[str, Any]]:
    rows = []
    for event in load_events(data_dir, limit=40):
        event_type = event.get("type")
        if event_type not in {"market_brief", "strategy_ideas", "symbol_research", "periodic_research", "research"}:
            continue
        payload = event.get("payload") or {}
        text = payload.get("reply") or payload.get("brief") or payload.get("ideas") or payload.get("research") or ""
        rows.append(
            {
                "type": event_type,
                "ts": event.get("ts"),
                "symbol": payload.get("symbol"),
                "summary": str(text)[:1200],
            }
        )
    return rows[-5:]


def build_entry_thesis(
    data_dir: str,
    *,
    symbol: str,
    strategy: Dict[str, Any],
    candidate: Dict[str, Any],
    adjusted_score: float,
    notional: float,
    order: Optional[Dict[str, Any]],
    market_clock: Dict[str, Any],
    account: Dict[str, Any],
    research_state: Dict[str, Any],
    source: str,
) -> Dict[str, Any]:
    strategy_id = strategy.get("id") or "unknown_strategy"
    thesis_id = f"th_{str(symbol).upper()}_{uuid.uuid4().hex[:10]}"
    last_research = research_state.get("last_research") or {}
    risk_plan = {
        "take_profit_pct": strategy.get("take_profit_pct"),
        "stop_loss_pct": strategy.get("stop_loss_pct"),
        "max_hold_days": strategy.get("max_hold_days"),
    }
    return {
        "type": "entry_thesis",
        "thesis_id": thesis_id,
        "symbol": str(symbol).upper(),
        "status": "open",
        "opened_at": utc_now(),
        "source": source,
        "strategy": _compact_strategy(strategy),
        "candidate_features": _compact_candidate(candidate, adjusted_score),
        "notional": notional,
        "order": order,
        "market_clock": market_clock,
        "account_snapshot": {
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
        },
        "research_context": {
            "active_strategy_id": research_state.get("active_strategy_id"),
            "last_research_at": last_research.get("researched_at"),
            "candidate_strategy_id": last_research.get("candidate_strategy_id"),
            "promoted_strategy_id": last_research.get("promoted_strategy_id"),
            "promotion_reason": last_research.get("promotion_reason"),
            "web_variants_tested": last_research.get("web_variants_tested"),
            "ai_variants_tested": last_research.get("ai_variants_tested"),
            "validation": ((last_research.get("best") or {}).get("validation") or {}),
        },
        "recent_market_context": _recent_market_context(data_dir),
        "entry_reason": (
            f"{symbol} matched active strategy {strategy_id} with adjusted score "
            f"{round(adjusted_score, 2)}. Risk plan: take profit "
            f"{risk_plan.get('take_profit_pct')}, stop {risk_plan.get('stop_loss_pct')}, "
            f"max hold {risk_plan.get('max_hold_days')} days."
        ),
        "invalidation_triggers": [
            "stop_loss",
            "max_hold",
            "open order conflict",
            "operator thesis review flags current context as broken",
        ],
    }


def record_entry_thesis(data_dir: str, thesis: Dict[str, Any]) -> Dict[str, Any]:
    return _write_record(data_dir, thesis)


def build_exit_review(
    data_dir: str,
    *,
    symbol: str,
    reason: str,
    plpc: float,
    position: Dict[str, Any],
    order: Optional[Dict[str, Any]],
    exit_record: Dict[str, Any],
) -> Dict[str, Any]:
    thesis = find_open_thesis(data_dir, symbol)
    thesis_id = (thesis or {}).get("thesis_id") or f"unknown_{str(symbol).upper()}"
    opened_at = (thesis or {}).get("opened_at")
    return {
        "type": "exit_review",
        "thesis_id": thesis_id,
        "symbol": str(symbol).upper(),
        "status": "closed",
        "opened_at": opened_at,
        "closed_at": utc_now(),
        "exit_reason": reason,
        "plpc": plpc,
        "position_snapshot": position,
        "order": order,
        "exit_record": exit_record,
        "entry_thesis_summary": {
            "entry_reason": (thesis or {}).get("entry_reason"),
            "strategy": (thesis or {}).get("strategy"),
            "candidate_features": (thesis or {}).get("candidate_features"),
            "research_context": (thesis or {}).get("research_context"),
        },
        "lesson": (
            "Thesis succeeded or protected capital."
            if plpc > 0
            else "Thesis failed, decayed, or was invalidated before profit."
        ),
    }


def record_exit_review(data_dir: str, review: Dict[str, Any]) -> Dict[str, Any]:
    return _write_record(data_dir, review)


def close_missing_position_theses(
    data_dir: str,
    live_symbols: Iterable[str],
) -> List[Dict[str, Any]]:
    live = {str(symbol or "").upper() for symbol in live_symbols if str(symbol or "").strip()}
    closed_reviews = []
    for thesis in open_trade_theses(data_dir, limit=500):
        symbol = str(thesis.get("symbol", "")).upper()
        if not symbol or symbol in live:
            continue
        review = {
            "type": "exit_review",
            "thesis_id": thesis.get("thesis_id"),
            "symbol": symbol,
            "status": "closed",
            "opened_at": thesis.get("opened_at"),
            "closed_at": utc_now(),
            "exit_reason": "position_missing",
            "plpc": None,
            "position_snapshot": {},
            "order": None,
            "exit_record": {
                "symbol": symbol,
                "reason": "position_missing",
                "note": "Alpaca no longer reports this position; thesis closed during reconciliation.",
            },
            "entry_thesis_summary": {
                "entry_reason": thesis.get("entry_reason"),
                "strategy": thesis.get("strategy"),
                "candidate_features": thesis.get("candidate_features"),
                "research_context": thesis.get("research_context"),
            },
            "lesson": "Position was no longer present during reconciliation.",
        }
        closed_reviews.append(record_exit_review(data_dir, review))
    return closed_reviews
