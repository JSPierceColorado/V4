# v11 boot-hardening patch

This build preserves the v9/v10 trading changes and adds a Docker build-time cleanup pass for stray NUL bytes in Python source files.

Why: Railway reported `SyntaxError: source code string cannot contain null bytes` while importing `agent_operator.py`. The local source package was clean, so this build defensively strips NUL bytes from `.py` files after `COPY . .` and then compiles every Python file during the image build.

Preserved behavior:
- `AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=0` scans the full active/tradable Alpaca US equity universe.
- Fractionable assets use notional orders.
- Non-fractionable assets use whole-share quantity orders.
- Notional rejection for non-fractionable assets is retried once as a whole-share order.
- Autonomy summaries include submitted/error/skipped counts.
