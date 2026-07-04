from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from alpaca_rest import AlpacaError, AlpacaRest
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
from screener import screen_symbols
from storage import append_event, utc_now


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class AutonomySnapshot:
    running: bool
    last_started_at: Optional[str] = None
    last_finished_at: Optional[str] = None
    last_error: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
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
            "last_started_at": self.snapshot.last_started_at,
            "last_finished_at": self.snapshot.last_finished_at,
            "last_error": self.snapshot.last_error,
            "last_result": self.snapshot.last_result,
            "strategy": strategy_snapshot(load_evolution_state(self.settings.data_dir)),
            "cycles": self.snapshot.cycles,
        }

    def _loop(self, alpaca_factory) -> None:
        while not self._stop.is_set():
            try:
                self.run_cycle(alpaca_factory())
            except Exception as exc:
                with self._lock:
                    self.snapshot.last_error = str(exc)
                    self.snapshot.last_finished_at = utc_now()
                append_event(self.settings.data_dir, "autonomy_error", {"error": str(exc)})
            self._stop.wait(max(10, self.settings.autonomy_interval_seconds))
        with self._lock:
            self.snapshot.running = False

    def run_cycle(self, alpaca: AlpacaRest) -> Dict[str, Any]:
        started = utc_now()
        with self._lock:
            self.snapshot.last_started_at = started
            self.snapshot.last_error = None

        symbols = self.settings.autonomy_symbols or None
        screen = screen_symbols(alpaca, symbols)
        state = alpaca.state()
        account = state.get("account") or {}
        positions = state.get("positions") or []
        open_orders = state.get("open_orders") or []
        evolution_state = load_evolution_state(self.settings.data_dir)
        remove_missing_positions(
            evolution_state,
            [str(pos.get("symbol", "")).upper() for pos in positions],
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
            reason = ""
            if plpc >= _float(strategy.get("take_profit_pct")):
                reason = "take_profit"
            elif plpc <= _float(strategy.get("stop_loss_pct")):
                reason = "stop_loss"
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
                "position": position,
                "order_args": order_args,
                "dry_run": self.settings.autonomy_dry_run,
            }
            if not self.settings.autonomy_dry_run:
                try:
                    action["order"] = alpaca.place_order(**order_args)
                    action["exit_record"] = tag_exit(
                        evolution_state,
                        symbol=symbol,
                        plpc=plpc,
                        reason=reason,
                    )
                    held.discard(symbol)
                    ordered.add(symbol)
                except AlpacaError as exc:
                    action["error"] = str(exc)
            exit_actions.append(action)
            actions.append(action)

        strategy = evolve(evolution_state)
        candidates = sorted(
            screen.get("candidates", []),
            key=lambda candidate: adjusted_score(candidate, strategy),
            reverse=True,
        )
        entry_actions = []
        for candidate in candidates:
            if len(entry_actions) >= order_slots:
                break
            symbol = candidate["symbol"]
            if self.settings.autonomy_min_score > 0 and candidate["score"] < self.settings.autonomy_min_score:
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
                "order_args": order_args,
                "dry_run": self.settings.autonomy_dry_run,
            }
            if not self.settings.autonomy_dry_run:
                try:
                    action["order"] = alpaca.place_order(**order_args)
                    tag_entry(
                        evolution_state,
                        symbol=symbol,
                        strategy=strategy,
                        candidate=candidate,
                        notional=notional,
                    )
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
                f"Dry run: {self.settings.autonomy_dry_run}."
            ),
            "screen": screen,
            "strategy": strategy_report,
            "actions": actions,
        }
        append_event(self.settings.data_dir, "autonomy_cycle", result)
        with self._lock:
            self.snapshot.last_finished_at = result["finished_at"]
            self.snapshot.last_result = result
            self.snapshot.cycles += 1
        return result
