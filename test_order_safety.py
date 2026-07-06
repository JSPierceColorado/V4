from order_safety import duplicate_order_reason
from storage import UploadRecord
from strategy import run_once


def test_duplicate_order_reason_blocks_existing_open_symbol_order() -> None:
    state = {
        "positions": [],
        "open_orders": [{"symbol": "AAPL", "side": "buy", "status": "accepted"}],
    }

    reason = duplicate_order_reason(state, symbol="AAPL", side="buy")

    assert reason == "AAPL already has an open order; skipping duplicate order entry."


def test_duplicate_order_reason_blocks_existing_long_buy() -> None:
    state = {
        "positions": [{"symbol": "MSFT", "qty": "2"}],
        "open_orders": [],
    }

    reason = duplicate_order_reason(state, symbol="MSFT", side="buy")

    assert reason == "MSFT is already held; skipping duplicate buy entry."


def test_run_once_skips_duplicate_latest_upload_buy() -> None:
    upload = UploadRecord(
        upload_id="up_test",
        filename="signals.csv",
        content_type="text/csv",
        path="/tmp/signals.csv",
        created_at="2026-07-05T00:00:00+00:00",
        kind="csv",
        summary={
            "top_candidates": [{"symbol": "NVDA", "score": 99, "row": {}}],
            "symbols": ["NVDA"],
        },
    )

    class FakeAlpaca:
        def state(self):
            return {
                "positions": [{"symbol": "NVDA", "qty": "1"}],
                "open_orders": [],
            }

        def latest_quote(self, symbol):
            raise AssertionError("quote should not be fetched for duplicates")

        def place_order(self, **kwargs):
            raise AssertionError("duplicate order should not be placed")

    result = run_once(
        upload=upload,
        alpaca=FakeAlpaca(),
        default_qty=1,
        time_in_force="day",
        extended_hours=False,
    )

    assert result["ok"] is False
    assert result["duplicate_order"] is True
