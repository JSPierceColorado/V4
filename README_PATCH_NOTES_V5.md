# V5 patch: deterministic autonomy cycle + upstream diagnostics

This patch addresses the case where `/clock` and `autonomy status` work, but `run autonomous cycle` returns `upstream error`.

Changes:
- `/autonomy/cycle` now always runs the deterministic `AutonomyEngine.run_cycle()` instead of routing through the OpenAI operator when `AGENT_OPERATOR_ENABLED=true`.
- `/agent/cycle` remains the OpenAI/operator-planned cycle.
- `AutonomyEngine.run_cycle()` now reads Alpaca account, clock, positions, and open orders one endpoint at a time. If account/positions/orders cannot be read safely, it returns and logs a no-trade diagnostic instead of raising a UI `upstream error`.
- `screen_symbols()` now handles transient failures while fetching the full Alpaca asset universe and returns a warning with zero candidates rather than crashing the cycle.

Safety behavior:
- If account, positions, or open orders cannot be read, the bot does not submit paper orders.
- If the asset universe or bars are temporarily unavailable, the bot reports zero candidates and includes warnings.

Validation:
- `python3 -m py_compile *.py` passed.
- Targeted tests passed: `pytest -q test_market_clock.py test_autonomy.py test_agent_operator.py test_ui.py` => 34 passed.
- Full suite has the same unrelated pre-existing research-promotion failure: 70 passed / 1 failed.
