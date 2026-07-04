from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from alpaca_rest import AlpacaRest
from config import Settings
from screener import screen_symbols
from storage import append_event, utc_now
from strategy import latest_midpoint


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
            "last_started_at": self.snapshot.last_started_at,
            "last_finished_at": self.snapshot.last_finished_at,
            "last_error": self.snapshot.last_error,
            "last_result": self.snapshot.last_result,
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
        positions = state.get("positions") or []
        open_orders = state.get("open_orders") or []
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
        for candidate in screen.get("candidates", []):
            if len(actions) >= order_slots:
                break
            symbol = candidate["symbol"]
            if self.settings.autonomy_min_score > 0 and candidate["score"] < self.settings.autonomy_min_score:
                continue
            if symbol in held or symbol in ordered:
                continue
            midpoint = latest_midpoint(alpaca, symbol)
            order_args = {
                "symbol": symbol,
                "side": "buy",
                "qty": self.settings.default_order_qty,
                "order_type": "limit" if midpoint else "market",
                "time_in_force": self.settings.default_time_in_force,
                "limit_price": midpoint,
                "extended_hours": self.settings.extended_hours,
            }
            action: Dict[str, Any] = {
                "type": "paper_buy_candidate",
                "candidate": candidate,
                "order_args": order_args,
                "dry_run": self.settings.autonomy_dry_run,
            }
            if not self.settings.autonomy_dry_run:
                action["order"] = alpaca.place_order(**order_args)
            actions.append(action)

        result = {
            "ok": True,
            "started_at": started,
            "finished_at": utc_now(),
            "summary": (
                f"Autonomy screened {screen.get('symbols_checked', 0)} symbols, "
                f"found {len(screen.get('candidates', []))} candidates, "
                f"prepared {len(actions)} action(s). "
                f"Dry run: {self.settings.autonomy_dry_run}."
            ),
            "screen": screen,
            "actions": actions,
        }
        append_event(self.settings.data_dir, "autonomy_cycle", result)
        with self._lock:
            self.snapshot.last_finished_at = result["finished_at"]
            self.snapshot.last_result = result
            self.snapshot.cycles += 1
        return result
