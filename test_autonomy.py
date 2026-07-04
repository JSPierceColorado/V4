from config import load_settings
from evolution import load_evolution_state
from research import run_research


def _bars(count: int = 180):
    bars = []
    price = 10.0
    for index in range(count):
        price *= 1.004
        bars.append({"c": round(price, 4), "v": 500000 + index * 1000})
    return bars


def test_research_backtests_and_deploys_best_strategy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()

    class FakeAlpaca:
        def active_tradable_us_equity_symbols(self):
            return ["AAA", "BBB", "CCC"]

        def stock_bars(self, symbols, *, start):
            return {"bars": {symbol: _bars() for symbol in symbols}}

    result = run_research(settings, FakeAlpaca())
    state = load_evolution_state(settings.data_dir)

    assert result["ok"] is True
    assert result["research"]["variants_tested"] > 0
    assert result["research"]["best_strategy_id"] == state["active_strategy_id"]
    assert state["active_strategy_id"].startswith("research_")
    assert state["strategies"][state["active_strategy_id"]]["backtest"]["validation"]["trades"] > 0
