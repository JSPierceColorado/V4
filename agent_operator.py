from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from config import Settings
from evolution import load_evolution_state, strategy_snapshot
from storage import append_event, load_events, utc_now


ALLOWED_TOOLS = {
    "research",
    "autonomy_cycle",
    "screen",
    "metrics",
    "clock",
    "state",
    "wait",
}


def _json_from_text(text: str) -> Optional[Dict[str, Any]]:
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


def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    account = state.get("account") or {}
    positions = state.get("positions") or []
    open_orders = state.get("open_orders") or []
    clock = state.get("clock") or {}
    return {
        "clock": clock,
        "account": {
            "status": account.get("status"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "equity": account.get("equity"),
            "portfolio_value": account.get("portfolio_value"),
            "daytrading_buying_power": account.get("daytrading_buying_power"),
        },
        "positions": [
            {
                "symbol": pos.get("symbol"),
                "qty": pos.get("qty"),
                "market_value": pos.get("market_value"),
                "unrealized_pl": pos.get("unrealized_pl"),
                "unrealized_plpc": pos.get("unrealized_plpc"),
            }
            for pos in positions[:30]
        ],
        "open_orders": [
            {
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "qty": order.get("qty"),
                "notional": order.get("notional"),
                "status": order.get("status"),
                "type": order.get("type"),
            }
            for order in open_orders[:30]
        ],
    }


def _compact_autonomy_status(status: Dict[str, Any]) -> Dict[str, Any]:
    last_result = status.get("last_result") or {}
    last_operator = last_result.get("operator") or {}
    return {
        "running": status.get("running"),
        "dry_run": status.get("dry_run"),
        "interval_seconds": status.get("interval_seconds"),
        "agent_operator_enabled": status.get("agent_operator_enabled"),
        "research_enabled": status.get("research_enabled"),
        "research_interval_seconds": status.get("research_interval_seconds"),
        "last_started_at": status.get("last_started_at"),
        "last_finished_at": status.get("last_finished_at"),
        "last_error": status.get("last_error"),
        "last_research_at": status.get("last_research_at"),
        "last_research_error": status.get("last_research_error"),
        "cycles": status.get("cycles"),
        "last_summary": last_result.get("summary"),
        "last_operator_plan": last_operator.get("plan"),
    }


def build_operator_context(
    settings: Settings,
    *,
    state: Dict[str, Any],
    autonomy_status: Dict[str, Any],
) -> Dict[str, Any]:
    evolution_state = load_evolution_state(settings.data_dir)
    return {
        "timestamp": utc_now(),
        "paper": settings.alpaca_paper,
        "operator_policy": {
            "paper_account_only": True,
            "position_size_rule": (
                f"{settings.autonomy_position_buying_power_pct:.2%} of Alpaca buying_power "
                "for each new autonomous entry"
            ),
            "market_clock_required_for_orders": True,
            "allowed_tools": sorted(ALLOWED_TOOLS),
            "notes": [
                "Research/backtesting may run while market is closed.",
                "Autonomous order placement should use autonomy_cycle, which enforces market clock and sizing.",
                "Prefer wait when market is closed and research is not due.",
            ],
        },
        "state": _compact_state(state),
        "autonomy_status": _compact_autonomy_status(autonomy_status),
        "strategy": strategy_snapshot(evolution_state),
        "last_research": evolution_state.get("last_research"),
        "recent_events": load_events(settings.data_dir, limit=12),
    }


def fallback_plan(context: Dict[str, Any]) -> Dict[str, Any]:
    clock = (context.get("state") or {}).get("clock") or {}
    status = context.get("autonomy_status") or {}
    market_open = bool(clock.get("is_open") is True)
    if status.get("research_enabled") and not status.get("last_research_at"):
        return {
            "rationale": "No research result is recorded yet, so research should run before more trading.",
            "actions": [{"tool": "research", "args": {}, "reason": "seed active strategy"}],
        }
    if market_open:
        return {
            "rationale": "Market is open, so run the clock-aware trading cycle.",
            "actions": [{"tool": "autonomy_cycle", "args": {}, "reason": "manage exits and entries"}],
        }
    return {
        "rationale": "Market is closed and no research is immediately required.",
        "actions": [{"tool": "wait", "args": {}, "reason": "market closed"}],
    }


def model_plan(settings: Settings, context: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.openai_ready:
        plan = fallback_plan(context)
        plan["source"] = "fallback_no_openai"
        return plan

    try:
        from openai import OpenAI
    except ImportError:
        plan = fallback_plan(context)
        plan["source"] = "fallback_no_openai_package"
        return plan

    system = (
        "You are the v4 Agent Operator for an Alpaca paper-trading account. "
        "You are not chatting; you are deciding which internal tools to run now. "
        "Return only JSON with keys rationale and actions. actions is an array of "
        "objects with keys tool, args, reason. Allowed tools: research, autonomy_cycle, "
        "screen, metrics, clock, state, wait. Keep to at most 3 actions. "
        "Use autonomy_cycle for paper orders; it enforces market clock, exits, screening, "
        "2% buying-power sizing, and strategy rules. Use research for backtesting and "
        "strategy promotion. If market is closed, do not request order placement; research "
        "or wait instead. Prefer simple, useful actions over busywork."
    )
    payload = json.dumps(context, default=str)[:50000]
    try:
        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=f"{system}\n\nContext:\n{payload}",
        )
        parsed = _json_from_text(getattr(response, "output_text", "") or "")
        if not parsed:
            raise ValueError("operator returned no JSON")
        actions = []
        for action in parsed.get("actions") or []:
            tool = str(action.get("tool") or "").strip()
            if tool in ALLOWED_TOOLS:
                actions.append(
                    {
                        "tool": tool,
                        "args": action.get("args") or {},
                        "reason": action.get("reason") or "",
                    }
                )
        if not actions:
            raise ValueError("operator returned no allowed actions")
        return {
            "source": "openai",
            "rationale": parsed.get("rationale") or "",
            "actions": actions[:3],
        }
    except Exception as exc:
        plan = fallback_plan(context)
        plan["source"] = "fallback_error"
        plan["error"] = str(exc)
        return plan


def journal_operator_cycle(
    data_dir: str,
    *,
    plan: Dict[str, Any],
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    lines = [f"Operator plan source: {plan.get('source', 'unknown')}."]
    rationale = plan.get("rationale")
    if rationale:
        lines.append(f"Rationale: {rationale}")
    for index, result in enumerate(results, start=1):
        tool = result.get("tool")
        if result.get("ok") is False:
            lines.append(f"{index}. {tool}: failed - {result.get('error') or result.get('reply')}")
        else:
            reply = result.get("reply") or result.get("summary") or result.get("status") or "completed"
            lines.append(f"{index}. {tool}: {reply}")
    journal = {
        "ok": True,
        "created_at": utc_now(),
        "summary": "\n".join(lines),
        "plan": plan,
        "results": results,
    }
    append_event(data_dir, "agent_operator_journal", journal)
    return journal
