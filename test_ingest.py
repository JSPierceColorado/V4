import threading

import autonomy
from alpaca_rest import AlpacaError
from autonomy import AutonomyEngine
from config import load_settings
from evolution import load_evolution_state
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
        "AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE",
        "AUTONOMY_RESEARCH_ENABLED",
        "AUTONOMY_RESEARCH_INTERVAL_SECONDS",
        "AUTONOMY_RESEARCH_SYMBOLS_PER_RUN",
        "AUTONOMY_RESEARCH_MAX_VARIANTS",
        "AUTONOMY_RESEARCH_LOOKBACK_DAYS",
        "AUTONOMY_RESEARCH_SCOUT_SYMBOLS",
        "AUTONOMY_RESEARCH_VALIDATE_TOP_VARIANTS",
        "AUTONOMY_RESEARCH_REQUIRE_PROFITABLE",
        "AUTONOMY_RESEARCH_CHALLENGER_MIN_FITNESS_EDGE",
        "AUTONOMY_RESEARCH_CHALLENGER_MIN_RETURN_EDGE",
        "AUTONOMY_MUTATION_ENABLED",
        "AUTONOMY_MUTATION_VARIANTS",
        "AUTONOMY_MUTATION_PARENT_COUNT",
        "AUTONOMY_AI_STRATEGY_LAB_ENABLED",
        "AUTONOMY_AI_STRATEGY_IDEAS",
        "AUTONOMY_AI_VARIANT_TRIAGE_ENABLED",
        "AUTONOMY_AI_VARIANT_TRIAGE_TARGET",
        "AGENT_OPERATOR_ENABLED",
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
    assert settings.autonomy_screen_symbols_per_cycle == 100
    assert settings.autonomy_research_enabled is True
    assert settings.autonomy_research_interval_seconds == 21600
    assert settings.autonomy_research_symbols_per_run == 0
    assert settings.autonomy_research_max_variants == 1000
    assert settings.autonomy_research_lookback_days == 1095
    assert settings.autonomy_research_scout_symbols == 60
    assert settings.autonomy_research_validate_top_variants == 50
    assert settings.autonomy_research_require_profitable is True
    assert settings.autonomy_research_challenger_min_fitness_edge == 0.01
    assert settings.autonomy_research_challenger_min_return_edge == 0.02
    assert settings.autonomy_mutation_enabled is True
    assert settings.autonomy_mutation_variants == 160
    assert settings.autonomy_mutation_parent_count == 8
    assert settings.autonomy_ai_strategy_lab_enabled is True
    assert settings.autonomy_ai_strategy_ideas == 48
    assert settings.autonomy_ai_variant_triage_enabled is True
    assert settings.autonomy_ai_variant_triage_target == 500
    assert settings.agent_operator_enabled is True


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


def test_screener_rotates_large_symbol_universe() -> None:
    class FakeAlpaca:
        def __init__(self) -> None:
            self.requested = []

        def stock_bars(self, symbols, *, start):
            self.requested.extend(symbols)
            bars = [
                {"c": 10 + index * 0.1, "v": 250000}
                for index in range(30)
            ]
            return {"bars": {symbol: list(bars) for symbol in symbols}}

    alpaca = FakeAlpaca()
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    result = screen_symbols(
        alpaca,
        symbols,
        max_symbols_per_cycle=2,
        offset=1,
    )

    assert alpaca.requested == ["BBB", "CCC"]
    assert result["universe_symbols"] == 4
    assert result["symbols_checked"] == 2
    assert result["screen_offset"] == 1
    assert result["next_screen_offset"] == 3


def test_screener_returns_partial_result_on_rate_limit() -> None:
    class FakeAlpaca:
        def stock_bars(self, symbols, *, start):
            raise AlpacaError('GET /v2/stocks/bars failed status=429 body={"message":"too many requests"}')

    result = screen_symbols(FakeAlpaca(), ["AAA", "BBB"])

    assert result["ok"] is True
    assert result["rate_limited"] is True
    assert result["warnings"]


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


def test_periodic_research_runs_when_due_then_skips(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    engine = AutonomyEngine(settings)
    calls = []

    def fake_research(settings_arg, alpaca_arg):
        calls.append((settings_arg, alpaca_arg))
        return {"ok": True, "reply": "researched"}

    monkeypatch.setattr(autonomy, "run_research", fake_research)

    first = engine.maybe_run_research(object())
    second = engine.maybe_run_research(object())
    state = load_evolution_state(settings.data_dir)

    assert first["ok"] is True
    assert second["skipped"] is True
    assert second["reason"] == "not_due"
    assert len(calls) == 1
    assert state["last_periodic_research_at"]


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
        lambda alpaca, symbols, **kwargs: {
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
        lambda alpaca, symbols, **kwargs: {
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
        lambda alpaca, symbols, **kwargs: {
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


def test_autonomy_skips_orders_when_market_is_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    engine = AutonomyEngine(settings)
    placed_orders = []

    class FakeAlpaca:
        def state(self):
            return {
                "account": {"buying_power": "1000"},
                "clock": {
                    "is_open": False,
                    "next_open": "2026-07-06T13:30:00Z",
                    "next_close": "2026-07-06T20:00:00Z",
                },
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
            return {"id": "should-not-happen", **kwargs}

    monkeypatch.setattr(
        autonomy,
        "screen_symbols",
        lambda alpaca, symbols, **kwargs: {
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
    assert result["market_clock"]["is_open"] is False
    assert placed_orders == []
    assert result["actions"][0]["type"] == "paper_sell_exit"
    assert result["actions"][0]["skipped"] == "market_closed"
    assert "Market open: False" in result["summary"]
