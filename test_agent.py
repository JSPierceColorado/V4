import threading

import autonomy
from alpaca_rest import AlpacaError
from autonomy import AutonomyEngine
from config import load_settings
from screener import screen_symbols


def test_autonomy_defaults_are_live_unbounded_all_symbols(monkeypatch) -> None:
    for name in (
        "AUTONOMY_ENABLED",
        "AUTONOMY_DRY_RUN",
        "AUTONOMY_INTERVAL_SECONDS",
        "AUTONOMY_SYMBOLS",
        "AUTONOMY_MIN_SCORE",
        "AUTONOMY_MAX_ORDERS_PER_CYCLE",
        "AUTONOMY_MAX_POSITIONS",
        "AUTONOMY_POSITION_BUYING_POWER_PCT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.autonomy_enabled is True
    assert settings.autonomy_dry_run is False
    assert settings.autonomy_interval_seconds == 600
    assert settings.autonomy_symbols == ()
    assert settings.autonomy_min_score == 0.0
    assert settings.autonomy_max_orders_per_cycle == 0
    assert settings.autonomy_max_positions == 0
    assert settings.autonomy_position_buying_power_pct == 0.02


def test_blank_symbol_universe_uses_active_tradable_assets() -> None:
    class FakeAlpaca:
        def __init__(self) -> None:
            self.used_assets = False

        def active_tradable_us_equity_symbols(self) -> list[str]:
            self.used_assets = True
            return ["AAA", "BBB"]

        def stock_bars(self, symbols, *, start):
            bars = [
                {"c": 10 + index * 0.1, "v": 250000}
                for index in range(30)
            ]
            return {"bars": {symbol: list(bars) for symbol in symbols}}

    alpaca = FakeAlpaca()
    result = screen_symbols(alpaca, None)

    assert alpaca.used_assets is True
    assert result["symbols_checked"] == 2
    assert len(result["candidates"]) == 2


def test_autonomy_start_returns_status_without_deadlock(monkeypatch) -> None:
    settings = load_settings()
    engine = AutonomyEngine(settings)
    monkeypatch.setattr(engine, "_loop", lambda alpaca_factory: None)

    result = {}

    def start_engine() -> None:
        result.update(engine.start(lambda: object()))

    thread = threading.Thread(target=start_engine, daemon=True)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert result["ok"] is True
    assert result["status"]["running"] is True


def test_autonomy_sizes_entry_at_two_percent_of_buying_power(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    engine = AutonomyEngine(settings)
    placed_orders = []

    class FakeAlpaca:
        def state(self):
            return {
                "account": {"buying_power": "123.94"},
                "positions": [],
                "open_orders": [],
            }

        def place_order(self, **kwargs):
            placed_orders.append(kwargs)
            return {"id": "paper-order-1", **kwargs}

    monkeypatch.setattr(
        autonomy,
        "screen_symbols",
        lambda alpaca, symbols: {
            "ok": True,
            "symbols_checked": 1,
            "rejected": 0,
            "candidates": [
                {
                    "symbol": "HIGH",
                    "close": 410.24,
                    "score": 99,
                }
            ],
        },
    )
    result = engine.run_cycle(FakeAlpaca())

    assert result["ok"] is True
    assert placed_orders == [
        {
            "symbol": "HIGH",
            "side": "buy",
            "notional": 2.48,
            "order_type": "market",
            "time_in_force": "day",
            "extended_hours": False,
        }
    ]


def test_autonomy_records_order_errors_without_crashing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    engine = AutonomyEngine(settings)

    class FakeAlpaca:
        def state(self):
            return {
                "account": {"buying_power": "123.94"},
                "positions": [],
                "open_orders": [],
            }

        def place_order(self, **kwargs):
            raise AlpacaError("insufficient buying power")

    monkeypatch.setattr(
        autonomy,
        "screen_symbols",
        lambda alpaca, symbols: {
            "ok": True,
            "symbols_checked": 1,
            "rejected": 0,
            "candidates": [
                {
                    "symbol": "HIGH",
                    "close": 410.24,
                    "score": 99,
                }
            ],
        },
    )
    result = engine.run_cycle(FakeAlpaca())

    assert result["ok"] is True
    assert result["actions"][0]["error"] == "insufficient buying power"


def test_autonomy_sells_position_at_take_profit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    engine = AutonomyEngine(settings)
    placed_orders = []

    class FakeAlpaca:
        def state(self):
            return {
                "account": {"buying_power": "1000"},
                "positions": [
                    {
                        "symbol": "WIN",
                        "qty": "0.5",
                        "unrealized_plpc": "0.05",
                    }
                ],
                "open_orders": [],
            }

        def place_order(self, **kwargs):
            placed_orders.append(kwargs)
            return {"id": "sell-order-1", **kwargs}

    monkeypatch.setattr(
        autonomy,
        "screen_symbols",
        lambda alpaca, symbols: {
            "ok": True,
            "symbols_checked": 0,
            "rejected": 0,
            "candidates": [],
        },
    )

    result = engine.run_cycle(FakeAlpaca())

    assert result["ok"] is True
    assert placed_orders == [
        {
            "symbol": "WIN",
            "side": "sell",
            "qty": 0.5,
            "order_type": "market",
            "time_in_force": "day",
            "extended_hours": False,
        }
    ]
    assert result["actions"][0]["type"] == "paper_sell_exit"
    assert result["actions"][0]["reason"] == "take_profit"
