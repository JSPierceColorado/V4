# Patch notes: circular import boot fix

This build fixes a Railway boot failure introduced by the market-clock fallback patch.

## Fixed
- Removed `market_clock.py -> alpaca_rest.py` import dependency.
- `market_clock.py` now catches generic exceptions from Alpaca clock/calendar calls instead of importing `AlpacaError`.
- This breaks the circular chain:
  - `main.py -> alpaca_rest.py -> market_clock.py -> alpaca_rest.py`

## Validation
- `python3 -m py_compile *.py` passed.
- `pytest -q test_market_clock.py test_autonomy.py test_agent_operator.py` passed: 29 passed.
- Full suite remains at 70 passed / 1 failed due to the same unrelated pre-existing research-promotion assertion.
