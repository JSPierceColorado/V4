# v13 operator shim boot fix

Railway repeatedly received/cached a corrupted `agent_operator.py`. This build hard-overwrites that optional module during Docker build with a small deterministic shim before compiling Python files.

Preserves v9+ trading behavior:
- `AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=0` scans the full Alpaca tradable US equity universe.
- Non-fractionable assets are converted to whole-share `qty` orders or retried after notional rejection.
- Summaries report submitted/error/skipped counts.

The OpenAI operator planner is temporarily replaced by deterministic guardrail behavior. The deterministic autonomy cycle, research engine, strategy evolution, Alpaca clock, and paper trading path remain available.
