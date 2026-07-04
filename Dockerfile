from metrics import build_metrics


class FakeAlpaca:
    def state(self):
        return {
            "account": {
                "status": "ACTIVE",
                "equity": "1000",
                "last_equity": "990",
                "cash": "250",
                "buying_power": "500",
                "initial_margin": "100",
            },
            "positions": [
                {
                    "symbol": "AAPL",
                    "market_value": "300",
                    "unrealized_pl": "10",
                    "unrealized_plpc": "0.05",
                },
                {
                    "symbol": "MSFT",
                    "market_value": "200",
                    "unrealized_pl": "-5",
                    "unrealized_plpc": "-0.025",
                },
            ],
            "open_orders": [{"id": "1"}],
        }

    def portfolio_history(self):
        return {
            "timestamp": [1000, 2000, 3000],
            "equity": [900, 1100, 1000],
            "profit_loss": [0, 200, -100],
        }


def test_build_metrics_shape() -> None:
    metrics = build_metrics(FakeAlpaca())
    assert metrics["tiles"]
    assert metrics["charts"]["equity"]
    assert metrics["charts"]["drawdown"][-1]["drawdown"] < 0
    assert "Equity" == metrics["tiles"][0]["label"]
