# V9 Patch Notes — Full Alpaca Universe + Fractionable Order Handling

Changes:
- AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE now defaults to 0, which means each autonomous cycle evaluates every active tradable Alpaca US equity returned by `/v2/assets` unless `AUTONOMY_SYMBOLS` narrows the universe.
- Existing Railway env vars still win. If `AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=500` is set in Railway, change it to `0` or remove it to use the new all-symbol default.
- Buy order sizing now checks Alpaca asset metadata before submitting a notional order.
- Fractionable assets still use notional dollar orders.
- Non-fractionable assets use whole-share quantity orders based on latest trade price when available.
- If a notional order is rejected because an asset is not fractionable, the cycle retries once as a whole-share order if the notional can buy at least one share.
- The autonomy summary now reports submitted/error/skipped counts for entry actions.

Validation:
- `python3 -m py_compile *.py` passed.
- Targeted tests passed: `pytest -q test_market_clock.py test_autonomy.py test_agent_operator.py test_ingest.py` -> 42 passed.
- Full test suite remains 72 passed / 1 failed due to the pre-existing unrelated research-promotion assertion.
