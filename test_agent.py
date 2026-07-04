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
