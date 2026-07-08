from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_operator import (
    build_operator_context,
    journal_operator_cycle,
    model_plan,
)
from alpaca_rest import AlpacaError, AlpacaRest
from market_clock import get_market_clock, is_market_open
from config import Settings
from evolution import (
    active_strategy,
    adjusted_score,
    evolve,
    load_evolution_state,
    remove_missing_positions,
    save_evolution_state,
    strategy_snapshot,
    tag_entry,
    tag_exit,
)
from metrics import build_metrics
from market_intel import market_brief, position_review, strategy_ideas, symbol_research
from research import run_research
from screener import screen_symbols
from storage import append_event, utc_now
from trade_memory import (
    build_entry_thesis,
    build_exit_review,
    close_missing_position_theses,
    open_trade_theses,
    record_entry_thesis,
    record_exit_review,
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_days(opened_at: Any) -> float:
    if not opened_at:
        return 0.0
    try:
        raw = str(opened_at).replace("Z", "+00:00")
        opened = datetime.fromisoformat(raw)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 86400)
    except ValueError:
        return 0.0


def _seconds_since(timestamp: Any) -> float:
    if not timestamp:
        return 10**12
    try:
        raw = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
    except ValueError:
        return 10**12


def _market_clock(alpaca: AlpacaRest, state: Dict[str, Any]) -> Dict[str, Any]:
    return get_market_clock(alpaca, state)


def _is_market_open(clock: Dict[str, Any]) -> bool:
    return is_market_open(clock)


def _operator_autonomy_action(reason: str) -> Dict[str, Any]:
    return {"tool": "autonomy_cycle", "args": {}, "reason": reason}


def _enforce_operator_autonomy_guardrails(
    context: Dict[str, Any],
    plan: Dict[str, Any],
) -> Dict[str, Any]:
    """Prevent market-open operator plans from waiting before live evaluation.

    This lives in autonomy.py to avoid a fragile cross-module import during deploy.
    During market hours the deterministic autonomy cycle must run before wait-only
    or review-only plans are allowed, because that cycle screens live symbols,
    manages exits, enforces sizing, and returns a concrete no-candidate reason.
    """
    clock = (context.get("state") or {}).get("clock") or {}
    if not is_market_open(clock):
        return plan

    raw_actions = plan.get("actions") or []
    actions = [dict(action) for action in raw_actions if isinstance(action, dict)]
    if any(action.get("tool") == "autonomy_cycle" for action in actions):
        patched = dict(plan)
        patched["actions"] = actions[:3]
        return patched

    forced = _operator_autonomy_action(
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

    patched = dict(plan)
    patched["actions"] = actions[:3]
    guardrails = list(patched.get("guardrails") or [])
    guardrails.append("market_open_requires_autonomy_cycle_before_wait")
    patched["guardrails"] = guardrails
    rationale = patched.get("rationale") or ""
    if "Autonomy guardrail" not in rationale:
        patched["rationale"] = (
            f"{rationale} Autonomy guardrail: market is open, so a wait-only or "
            "review-only plan must run autonomy_cycle first to screen live candidates "
            "and manage exits."
        ).strip()
    return patched


@dataclass
class AutonomySnapshot:
    running: bool
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_error: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    last_research_at: Optional[str] = None
    last_research_result: Optional[Dict[str, Any]] = None
    last_research_error: Optional[str] = None
    cycles: int = 0


class AutonomyEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.snapshot = AutonomySnapshot(running=False)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status_unlocked()

    def start(self, alpaca_factory) -> Dict[str, Any]:
        thread_to_start: Optional[threading.Thread] = None
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"ok": True, "status": self._status_unlocked()}
            self._stop.clear()
            self.snapshot.running = True
            thread_to_start = threading.Thread(
                target=self._loop,
                args=(alpaca_factory,),
                name="v4-autonomy",
                daemon=True,
            )
            self._thread = thread_to_start
        thread_to_start.start()
        return {"ok": True, "status": self.status()}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        with self._lock:
            self.snapshot.running = False
        return {"ok": True, "status": self.status()}

    def _status_unlocked(self) -> Dict[str, Any]:
        return {
            "running": self.snapshot.running,
            "dry_run": self.settings.autonomy_dry_run,
            "interval_seconds": self.settings.autonomy_interval_seconds,
            "symbols": (
                list(self.settings.autonomy_symbols)
                if self.settings.autonomy_symbols
                else "ALL_ACTIVE_TRADABLE_US_EQUITIES"
            ),
            "min_score": self.settings.autonomy_min_score,
            "max_orders_per_cycle": (
                self.settings.autonomy_max_orders_per_cycle
                if self.settings.autonomy_max_orders_per_cycle > 0
                else "unlimited"
            ),
            "max_positions": (
                self.settings.autonomy_max_positions
                if self.settings.autonomy_max_positions > 0
                else "unlimited"
            ),
            "position_buying_power_pct": self.settings.autonomy_position_buying_power_pct,
            "screen_symbols_per_cycle": self.settings.autonomy_screen_symbols_per_cycle,
            "research_enabled": self.settings.autonomy_research_enabled,
            "research_interval_seconds": self.settings.autonomy_research_interval_seconds,
            "web_research_enabled": self.settings.autonomy_web_research_enabled,
            "web_strategy_ideas": self.settings.autonomy_web_strategy_ideas,
            "agent_operator_enabled": self.settings.agent_operator_enabled,
            "last_started_at": self.snapshot.last_started_at,
            "last_finished_at": self.snapshot.last_finished_at,
            "last_error": self.snapshot.last_error,
            "last_result": self.snapshot.last_result,
            "last_research_at": self.snapshot.last_research_at,
            "last_research_result": self.snapshot.last_research_result,
            "last_research_error": self.snapshot.last_research_error,
            "strategy": strategy_snapshot(load_evolution_state(self.settings.data_dir)),
            "cycles": self.snapshot.cycles,
        }

    def _loop(self, alpaca_factory) -> None:
        while not self._stop.is_set():
            try:
                alpaca = alpaca_factory()
                if self.settings.agent_operator_enabled:
                    self.run_operator_cycle(alpaca)
                else:
                    self.maybe_run_research(alpaca)
                    self.run_cycle(alpaca)
            except Exception as exc:
                with self._lock:
                    self.snapshot.last_error = str(exc)
                    self.snapshot.last_finished_at = utc_now()
                append_event(self.settings.data_dir, "autonomy_error", {"error": str(exc)})
            self._stop.wait(max(10, self.settings.autonomy_interval_seconds))
        with self._lock:
            self.snapshot.running = False

    def run_operator_cycle(self, alpaca: AlpacaRest) -> Dict[str, Any]:
        started = utc_now()
        with self._lock:
            self.snapshot.last_started_at = started
            self.snapshot.last_error = None

        results = []
        if self.settings.autonomy_research_enabled:
            scheduled_research = self.maybe_run_research(alpaca)
            if not scheduled_research.get("skipped"):
                results.append(
                    {
                        "tool": "research",
                        "reason": "scheduled periodic research",
                        **scheduled_research,
                    }
                )

        state = alpaca.state()
        state["clock"] = _market_clock(alpaca, state)
        context = build_operator_context(
            self.settings,
            state=state,
            autonomy_status=self.status(),
        )
        plan = _enforce_operator_autonomy_guardrails(context, model_plan(self.settings, context))
        for action in plan.get("actions") or []:
            tool = action.get("tool")
            args = action.get("args") or {}
            try:
                if tool == "research":
                    result = self.maybe_run_research(alpaca)
                elif tool == "market_brief":
                    screen = screen_symbols(
                        alpaca,
                        self.settings.autonomy_symbols or None,
                        max_symbols_per_cycle=self.settings.autonomy_screen_symbols_per_cycle,
                    )
                    result = market_brief(self.settings, state=state, screen=screen)
                elif tool == "symbol_research":
                    symbol = args.get("symbol") or self._default_research_symbol(state)
                    result = symbol_research(self.settings, symbol=symbol, state=state)
                elif tool == "strategy_ideas":
                    screen = screen_symbols(
                        alpaca,
                        self.settings.autonomy_symbols or None,
                        max_symbols_per_cycle=self.settings.autonomy_screen_symbols_per_cycle,
                    )
                    result = strategy_ideas(self.settings, state=state, screen=screen)
                elif tool == "review_positions":
                    result = position_review(
                        self.settings,
                        state=state,
                        open_theses=open_trade_theses(self.settings.data_dir),
                    )
                elif tool == "autonomy_cycle":
                    result = self.run_cycle(alpaca)
                elif tool == "screen":
                    result = screen_symbols(
                        alpaca,
                        self.settings.autonomy_symbols or None,
                        max_symbols_per_cycle=self.settings.autonomy_screen_symbols_per_cycle,
                    )
                elif tool == "metrics":
                    result = {
                        "ok": True,
                        "reply": "Metrics loaded.",
                        "metrics": build_metrics(alpaca),
                    }
                elif tool == "clock":
                    result = {"ok": True, "reply": "Clock loaded.", "clock": state.get("clock") or alpaca.clock()}
                elif tool == "state":
                    result = {"ok": True, "reply": "State loaded.", "state": state}
                elif tool == "wait":
                    result = {"ok": True, "reply": action.get("reason") or "Waiting."}
                else:
                    result = {"ok": False, "error": f"Unknown operator tool: {tool}"}
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            results.append(
                {
                    "tool": tool,
                    "reason": action.get("reason"),
                    **result,
                }
            )

        journal = journal_operator_cycle(
            self.settings.data_dir,
            plan=plan,
            results=results,
        )
        result = {
            "ok": True,
            "started_at": started,
            "finished_at": utc_now(),
            "summary": journal["summary"],
            "operator": journal,
        }
        append_event(self.settings.data_dir, "agent_operator_cycle", result)
        with self._lock:
            self.snapshot.last_finished_at = result["finished_at"]
            self.snapshot.last_result = result
            self.snapshot.cycles += 1
        return result

    def _default_research_symbol(self, state: Dict[str, Any]) -> str:
        positions = state.get("positions") or []
        if positions:
            return str(positions[0].get("symbol") or "").upper()
        open_orders = state.get("open_orders") or []
        if open_orders:
            return str(open_orders[0].get("symbol") or "").upper()
        return "SPY"

    def maybe_run_research(self, alpaca: AlpacaRest) -> Dict[str, Any]:
        if not self.settings.autonomy_research_enabled:
            return {"ok": True, "skipped": True, "reason": "research_disabled"}
        state = load_evolution_state(self.settings.data_dir)
        last_at = state.get("last_periodic_research_at") or (
            state.get("last_research") or {}
        ).get("researched_at")
        if _seconds_since(last_at) < self.settings.autonomy_research_interval_seconds:
            return {
                "ok": True,
                "skipped": True,
                "reason": "not_due",
                "last_periodic_research_at": last_at,
            }

        try:
            result = run_research(self.settings, alpaca)
            state = load_evolution_state(self.settings.data_dir)
            state["last_periodic_research_at"] = utc_now()
            save_evolution_state(self.settings.data_dir, state)
            with self._lock:
                self.snapshot.last_research_at = state["last_periodic_research_at"]
                self.snapshot.last_research_result = result
                self.snapshot.last_research_error = None
            append_event(self.settings.data_dir, "periodic_research", result)
            return result
        except Exception as exc:
            state = load_evolution_state(self.settings.data_dir)
            state["last_periodic_research_at"] = utc_now()
            save_evolution_state(self.settings.data_dir, state)
            with self._lock:
                self.snapshot.last_research_at = state["last_periodic_research_at"]
                self.snapshot.last_research_error = str(exc)
            append_event(
                self.settings.data_dir,
                "periodic_research_error",
                {"error": str(exc)},
            )
            return {"ok": False, "error": str(exc)}

    def run_cycle(self, alpaca: AlpacaRest) -> Dict[str, Any]:
        started = utc_now()
        with self._lock:
            self.snapshot.last_started_at = started
            self.snapshot.last_error = None

        state = alpaca.state()
        account = state.get("account") or {}
        clock = _market_clock(alpaca, state)
        market_open = _is_market_open(clock)
        positions = state.get("positions") or []
        open_orders = state.get("open_orders") or []
        evolution_state = load_evolution_state(self.settings.data_dir)
        live_symbols = [str(pos.get("symbol", "")).upper() for pos in positions]
        for review in close_missing_position_theses(self.settings.data_dir, live_symbols):
            append_event(self.settings.data_dir, "exit_review", review)
        remove_missing_positions(
            evolution_state,
            live_symbols,
        )
        local_buying_power = _float(account.get("buying_power"))
        held = {str(pos.get("symbol", "")).upper() for pos in positions}
        ordered = {str(order.get("symbol", "")).upper() for order in open_orders}
        if self.settings.autonomy_max_positions > 0:
            available_slots = max(0, self.settings.autonomy_max_positions - len(positions))
        else:
            available_slots = 10**9
        if self.settings.autonomy_max_orders_per_cycle > 0:
            order_slots = min(self.settings.autonomy_max_orders_per_cycle, available_slots)
        else:
            order_slots = available_slots

        actions = []
        exit_actions = []
        for position in positions:
            symbol = str(position.get("symbol", "")).upper()
            if not symbol or symbol in ordered:
                continue
            tracked = evolution_state.get("positions", {}).get(symbol) or {}
            strategy_id = tracked.get("strategy_id") or evolution_state.get("active_strategy_id")
            strategy = (
                evolution_state.get("strategies", {}).get(strategy_id)
                or active_strategy(evolution_state)
            )
            plpc = _float(position.get("unrealized_plpc"))
            age_days = _age_days(tracked.get("opened_at"))
            reason = ""
            if plpc >= _float(strategy.get("take_profit_pct")):
                reason = "take_profit"
            elif plpc <= _float(strategy.get("stop_loss_pct")):
                reason = "stop_loss"
            elif age_days >= _float(strategy.get("max_hold_days"), 10**9):
                reason = "max_hold"
            if not reason:
                continue
            qty = abs(_float(position.get("qty")))
            if qty <= 0:
                continue
            order_args = {
                "symbol": symbol,
                "side": "sell",
                "qty": qty,
                "order_type": "market",
                "time_in_force": self.settings.default_time_in_force,
                "extended_hours": False,
            }
            action: Dict[str, Any] = {
                "type": "paper_sell_exit",
                "strategy": strategy,
                "reason": reason,
                "plpc": plpc,
                "age_days": round(age_days, 2),
                "market_open": market_open,
                "position": position,
                "order_args": order_args,
                "dry_run": self.settings.autonomy_dry_run,
            }
            if not market_open:
                action["skipped"] = "market_closed"
            elif not self.settings.autonomy_dry_run:
                try:
                    action["order"] = alpaca.place_order(**order_args)
                    action["exit_record"] = tag_exit(
                        evolution_state,
                        symbol=symbol,
                        plpc=plpc,
                        reason=reason,
                    )
                    action["exit_review"] = record_exit_review(
                        self.settings.data_dir,
                        build_exit_review(
                            self.settings.data_dir,
                            symbol=symbol,
                            reason=reason,
                            plpc=plpc,
                            position=position,
                            order=action.get("order"),
                            exit_record=action["exit_record"],
                        ),
                    )
                    append_event(self.settings.data_dir, "exit_review", action["exit_review"])
                    held.discard(symbol)
                    ordered.add(symbol)
                except AlpacaError as exc:
                    action["error"] = str(exc)
            exit_actions.append(action)
            actions.append(action)

        strategy = evolve(evolution_state)
        symbols = self.settings.autonomy_symbols or None
        screen = screen_symbols(
            alpaca,
            symbols,
            max_symbols_per_cycle=self.settings.autonomy_screen_symbols_per_cycle,
            offset=int(evolution_state.get("screen_offset") or 0),
        )
        evolution_state["screen_offset"] = screen.get(
            "next_screen_offset",
            evolution_state.get("screen_offset", 0),
        )
        evolution_state["screen_universe_symbols"] = screen.get("universe_symbols", 0)
        candidates = sorted(
            screen.get("candidates", []),
            key=lambda candidate: adjusted_score(candidate, strategy),
            reverse=True,
        )
        entry_actions = []
        for candidate in ([] if not market_open else candidates):
            if len(entry_actions) >= order_slots:
                break
            symbol = candidate["symbol"]
            if self.settings.autonomy_min_score > 0 and candidate["score"] < self.settings.autonomy_min_score:
                continue
            if adjusted_score(candidate, strategy) < _float(strategy.get("min_entry_score")):
                continue
            if symbol in held or symbol in ordered:
                continue
            if local_buying_power <= 1:
                break
            notional = round(
                max(1.0, local_buying_power * self.settings.autonomy_position_buying_power_pct),
                2,
            )
            order_args = {
                "symbol": symbol,
                "side": "buy",
                "notional": notional,
                "order_type": "market",
                "time_in_force": self.settings.default_time_in_force,
                "extended_hours": False,
            }
            action: Dict[str, Any] = {
                "type": "paper_buy_candidate",
                "strategy": strategy,
                "candidate": candidate,
                "adjusted_score": adjusted_score(candidate, strategy),
                "market_open": market_open,
                "order_args": order_args,
                "dry_run": self.settings.autonomy_dry_run,
            }
            if not self.settings.autonomy_dry_run:
                try:
                    action["order"] = alpaca.place_order(**order_args)
                    entry_thesis = build_entry_thesis(
                        self.settings.data_dir,
                        symbol=symbol,
                        strategy=strategy,
                        candidate=candidate,
                        adjusted_score=adjusted_score(candidate, strategy),
                        notional=notional,
                        order=action.get("order"),
                        market_clock=clock,
                        account=account,
                        research_state=evolution_state,
                        source="autonomy_cycle",
                    )
                    action["entry_thesis"] = record_entry_thesis(
                        self.settings.data_dir,
                        entry_thesis,
                    )
                    append_event(self.settings.data_dir, "entry_thesis", action["entry_thesis"])
                    tag_entry(
                        evolution_state,
                        symbol=symbol,
                        strategy=strategy,
                        candidate=candidate,
                        notional=notional,
                    )
                    evolution_state["positions"][symbol]["thesis_id"] = entry_thesis["thesis_id"]
                    evolution_state["positions"][symbol]["entry_thesis_summary"] = {
                        "entry_reason": entry_thesis["entry_reason"],
                        "risk_plan": {
                            "take_profit_pct": strategy.get("take_profit_pct"),
                            "stop_loss_pct": strategy.get("stop_loss_pct"),
                            "max_hold_days": strategy.get("max_hold_days"),
                        },
                    }
                    local_buying_power = max(0.0, local_buying_power - notional)
                except AlpacaError as exc:
                    action["error"] = str(exc)
            entry_actions.append(action)
            actions.append(action)

        save_evolution_state(self.settings.data_dir, evolution_state)
        strategy_report = strategy_snapshot(evolution_state)
        result = {
            "ok": True,
            "started_at": started,
            "finished_at": utc_now(),
            "summary": (
                f"Autonomy screened {screen.get('symbols_checked', 0)} symbols, "
                f"found {len(screen.get('candidates', []))} candidates, "
                f"prepared {len(exit_actions)} exit(s) and "
                f"{len(entry_actions)} entry action(s). "
                f"Market open: {market_open}. "
                f"Dry run: {self.settings.autonomy_dry_run}."
            ),
            "market_clock": clock,
            "screen": screen,
            "strategy": strategy_report,
            "open_trade_theses": open_trade_theses(self.settings.data_dir),
            "actions": actions,
        }
        append_event(self.settings.data_dir, "autonomy_cycle", result)
        with self._lock:
            self.snapshot.last_finished_at = result["finished_at"]
            self.snapshot.last_result = result
            self.snapshot.cycles += 1
        return result
