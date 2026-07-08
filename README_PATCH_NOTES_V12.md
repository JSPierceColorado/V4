# V12 patch notes

- Prevents `agent_operator.py` corruption from blocking app boot by lazy-loading the optional operator planner.
- If `agent_operator.py` cannot be imported, the operator falls back to a deterministic plan that runs `autonomy_cycle` whenever the market is open.
- Adds `start.py`, which removes stray NUL bytes and `__pycache__` at runtime before launching uvicorn.
- Dockerfile now uses JSON-form CMD and repeats source cleanup/compile checks at build time.
- Keeps v9/v10/v11 trading changes: full Alpaca-universe screening with `AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=0`, fractionable asset checks, whole-share fallback, and clearer order status counts.
