from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from config import Settings
from market_clock import is_market_open
from evolution import load_evolution_state, strategy_snapshot
from market_intel import market_brief, position_review, strategy_ideas, symbol_research
from storage import append_event, load_events, utc_now
from trade_memory import open_trade_theses
from v4_doctrine import STRATEGY_DSL_GUIDE, V4_DOCTRINE


ALLOWED_TOOLS = {
    "research",
    "market_brief",
    "symbol_research",
    "strategy_ideas",
    "review_positions",
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
            "research_depth": {
                "symbols_per_run": settings.autonomy_research_symbols_per_run,
                "max_variants": settings.autonomy_research_max_variants,
                "ai_strategy_lab_enabled": settings.autonomy_ai_strategy_lab_enabled,
                "ai_strategy_ideas": settings.autonomy_ai_strategy_ideas,
                "web_research_enabled": settings.autonomy_web_research_enabled,
                "web_strategy_ideas": settings.autonomy_web_strategy_ideas,
                "ai_variant_triage_enabled": settings.autonomy_ai_variant_triage_enabled,
                "ai_variant_triage_target": settings.autonomy_ai_variant_triage_target,
            },
            "doctrine": V4_DOCTRINE,
            "strategy_dsl": STRATEGY_DSL_GUIDE,
            "notes": [
                "Research/backtesting may run while market is closed.",
                "Use market_brief, symbol_research, strategy_ideas, and review_positions to bring current web context into the research desk.",
                "Autonomous order placement should use autonomy_cycle, which enforces market clock and sizing.",
                "Prefer wait when market is closed and research is not due.",
            ],
        },
        "state": _compact_state(state),
        "autonomy_status": _compact_autonomy_status(autonomy_status),
        "strategy": strategy_snapshot(evolution_state),
        "open_trade_theses": open_trade_theses(settings.data_dir),
        "last_research": evolution_state.get("last_research"),
        "research_history": (evolution_state.get("research_history") or [])[-10:],
        "recent_events": load_events(settings.data_dir, limit=12),
    }


def fallback_plan(context: Dict[str, Any]) -> Dict[str, Any]:
    clock = (context.get("state") or {}).get("clock") or {}
    status = context.get("autonomy_status") or {}
    market_open = is_market_open(clock)
    if status.get("research_enabled") and not status.get("last_research_at"):
        plan = {
            "rationale": "No research result is recorded yet, so research should run before more trading.",
            "actions": [{"tool": "research", "args": {}, "reason": "seed active strategy"}],
        }
        return enforce_autonomy_guardrails(context, plan)
    if market_open:
        return {
            "rationale": "Market is open, so run the clock-aware trading cycle.",
            "actions": [{"tool": "autonomy_cycle", "args": {}, "reason": "manage exits and entries"}],
        }
    return {
        "rationale": "Market is closed and no research is immediately required.",
        "actions": [{"tool": "wait", "args": {}, "reason": "market closed"}],
    }


def _autonomy_action(reason: str) -> Dict[str, Any]:
    return {"tool": "autonomy_cycle", "args": {}, "reason": reason}


def enforce_autonomy_guardrails(
    context: Dict[str, Any],
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """Make market-open operator plans prove no live setup exists before waiting.

    The LLM operator can sound prudent while doing only review_positions -> wait on a
    flat book. That never asks the deterministic trading engine to screen symbols.
    During regular market hours the clock-aware autonomy cycle is the thing that
    manages exits, screens for entries, applies thresholds, enforces sizing, and then
    either prepares/sends orders or returns a concrete no-candidate reason.
    """
    clock = (context.get("state") or {}).get("clock") or {}
    if not is_market_open(clock):
        return plan

    raw_actions = plan.get("actions") or []
    actions = [dict(action) for action in raw_actions if isinstance(action, dict)]
    if any(action.get("tool") == "autonomy_cycle" for action in actions):
        plan["actions"] = actions[:3]
        return plan

    forced = _autonomy_action(
        "market is open; run deterministic entry/exit evaluation before any wait"
    )
    replaced_wait = False
    for index, action in enumerate(actions):
        if action.get("tool") == "wait":
            actions[index] = forced
            replaced_wait = True
            break

    if not actions:
        actions = [forced]
    elif not replaced_wait:
        if len(actions) < 3:
            actions.append(forced)
        else:
            actions[-1] = forced

    plan = dict(plan)
    plan["actions"] = actions[:3]
    guardrails = list(plan.get("guardrails") or [])
    guardrails.append(
        "market_open_requires_autonomy_cycle_before_wait"
    )
    plan["guardrails"] = guardrails
    rationale = plan.get("rationale") or ""
    if "Autonomy guardrail" not in rationale:
        plan["rationale"] = (
            f"{rationale} Autonomy guardrail: market is open, so a wait-only or "
            "review-only plan must run autonomy_cycle first to screen live candidates "
            "and manage exits."
        ).strip()
    return plan


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
        f"{V4_DOCTRINE} "
        "Return only JSON with keys rationale and actions. actions is an array of "
        "objects with keys tool, args, reason. Allowed tools: research, autonomy_cycle, "
        "market_brief, symbol_research, strategy_ideas, review_positions, screen, metrics, clock, state, wait. Keep to at most 3 actions. "
        "Use autonomy_cycle for paper orders; it enforces market clock, exits, screening, "
        "2% buying-power sizing, strategy rules, max positions, and no-candidate decisions. "
        "If the market is open, do not choose wait until autonomy_cycle has run in the current plan; "
        "a flat book with no open orders is a reason to screen for entries, not a reason to wait. "
        "Use market_brief or symbol_research when current market context could change the thesis. "
        "Use review_positions when open trade theses need to be checked against current conditions. Use strategy_ideas and research "
        "for web-informed candidate generation, backtesting, and strategy promotion. If market "
        "is closed, do not request order placement; research, search, or wait instead. Prefer "
        "simple, useful actions over busywork."
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
        return enforce_autonomy_guardrails(
            context,
            {
                "source": "openai",
                "rationale": parsed.get("rationale") or "",
                "actions": actions[:3],
            },
        )
    except Exception as exc:
        plan = fallback_plan(context)
        plan["source"] = "fallback_error"
        plan["error"] = str(exc)
        return plan


def _format_pct(value: Any) -> str:
    try:
        return f"{float(value):+.2%}"
    except (TypeError, ValueError):
        return "n/a"


def summarize_tool_result(result: Dict[str, Any]) -> str:
    tool = result.get("tool") or "tool"
    if result.get("ok") is False:
        return f"{tool}: failed - {result.get('error') or result.get('reply') or 'unknown error'}"

    if result.get("skipped"):
        reason = result.get("reason") or result.get("skipped")
        if reason == "not_due":
            last_at = result.get("last_periodic_research_at") or "unknown"
            return f"{tool}: skipped - not due yet. Last periodic research: {last_at}."
        return f"{tool}: skipped - {reason}."

    if result.get("rate_limited"):
        return f"{tool}: paused - Alpaca rate limit. {result.get('warning') or ''}".strip()

    if tool == "clock":
        clock = result.get("clock") or {}
        if clock.get("is_open") is True:
            return f"clock: market open. Next close: {clock.get('next_close', 'unknown')}."
        return f"clock: market closed. Next open: {clock.get('next_open', 'unknown')}."

    if tool == "research":
        research = result.get("research") or {}
        best = research.get("best") or {}
        validation = best.get("validation") or {}
        strategy_id = research.get("best_strategy_id")
        if strategy_id:
            selected = research.get("selected_symbols_count")
            bars_symbols = research.get("bars_symbols", 0)
            symbol_text = (
                f"on {bars_symbols} usable symbols ({selected} selected)"
                if selected
                else f"on {bars_symbols} symbols"
            )
            promoted = research.get("promoted")
            action_text = (
                f"Deployed {research.get('promoted_strategy_id') or strategy_id}."
                if promoted is not False
                else f"Did not deploy {strategy_id}; kept {research.get('active_strategy_id') or 'previous strategy'}."
            )
            generated = research.get("variants_generated")
            tested = research.get("variants_tested", 0)
            triage = research.get("ai_triage") or {}
            triage_text = (
                f"generated {generated}, AI-triaged to {tested}"
                if triage.get("used") and generated
                else f"scouted {tested}"
            )
            return (
                f"research: {triage_text} variants "
                f"and validated {research.get('variants_validated', research.get('variants_tested', 0))} finalists "
                f"{symbol_text}. "
                f"AI lab variants {research.get('ai_variants_tested', 0)}. "
                f"Web variants {research.get('web_variants_tested', 0)}. "
                f"Mutation variants {research.get('mutation_variants_tested', 0)}. "
                f"{action_text} "
                f"Validation return {_format_pct(validation.get('total_return_pct'))}, "
                f"win rate {_format_pct(validation.get('win_rate'))}, "
                f"trades {validation.get('trades', 0)}."
            )
        return f"research: {result.get('reply') or 'completed'}"

    if tool == "market_brief":
        return f"market_brief: {result.get('reply') or result.get('brief') or 'completed'}"

    if tool == "symbol_research":
        symbol = result.get("symbol") or "symbol"
        return f"symbol_research {symbol}: {result.get('reply') or result.get('research') or 'completed'}"

    if tool == "strategy_ideas":
        return f"strategy_ideas: {result.get('reply') or result.get('ideas') or 'completed'}"

    if tool == "review_positions":
        return f"review_positions: {result.get('reply') or result.get('review') or 'completed'}"

    if tool == "autonomy_cycle":
        autonomy = result.get("autonomy") or result
        return f"autonomy_cycle: {autonomy.get('summary') or result.get('reply') or 'completed'}"

    if tool == "screen":
        candidates = result.get("candidates") or (result.get("screen") or {}).get("candidates") or []
        top = candidates[0].get("symbol") if candidates else "none"
        return (
            f"screen: checked {result.get('symbols_checked', 0)} symbols, "
            f"found {len(candidates)} candidates. Top: {top}."
        )

    if tool == "metrics":
        metrics = result.get("metrics") or {}
        return f"metrics: {metrics.get('summary') or result.get('reply') or 'loaded'}"

    if tool == "state":
        state = result.get("state") or {}
        account = state.get("account") or {}
        positions = state.get("positions") or []
        orders = state.get("open_orders") or []
        return (
            f"state: equity ${account.get('equity', 'unknown')}, "
            f"buying power ${account.get('buying_power', 'unknown')}, "
            f"positions {len(positions)}, open orders {len(orders)}."
        )

    return f"{tool}: {result.get('reply') or result.get('summary') or result.get('status') or 'completed'}"


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
        lines.append(f"{index}. {summarize_tool_result(result)}")
    journal = {
        "ok": True,
        "created_at": utc_now(),
        "summary": "\n".join(lines),
        "plan": plan,
        "results": results,
    }
    append_event(data_dir, "agent_operator_journal", journal)
    return journal
