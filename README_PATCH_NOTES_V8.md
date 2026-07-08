# v8 boot compatibility patch

This patch keeps the v7 flat-root packaging, but removes fragile direct imports of
`AlpacaError` from `alpaca_rest` in production modules. Railway logs showed:

    ImportError: cannot import name 'AlpacaError' from 'alpaca_rest' (/app/alpaca_rest.py)

Even though the bundled `alpaca_rest.py` defines `AlpacaError`, this patch makes the
app tolerant if a deployed/old `alpaca_rest.py` exposes `AlpacaRest` but not
`AlpacaError`. Production modules now import `alpaca_rest` as a module and use:

    AlpacaError = getattr(_alpaca_rest, "AlpacaError", RuntimeError)

so startup will not fail only because the custom error class is unavailable.
