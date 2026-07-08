# V10 patch notes — clean source ZIP / NUL-byte deploy guard

This build is based on v9 but removes generated caches and binary artifacts from the deployment ZIP.

Changes:
- Removes `__pycache__`, `.pytest_cache`, and generated runtime data from the ZIP.
- Normalizes all Python source files as UTF-8 text with Unix newlines.
- Adds a Docker build-time sanity check that fails the image build if any `.py` file contains NUL bytes.

This targets Railway boot failures like:

`SyntaxError: source code string cannot contain null bytes`

The v9 trading changes are preserved:
- full Alpaca universe screening when `AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=0`
- fractionable asset detection
- whole-share fallback for non-fractionable assets
- clearer order submitted/error/skipped counts
