from __future__ import annotations


V4_DOCTRINE = """
v4 is not an indicator bot.

The operator's job is to run a trading research desk:
1. Observe the market regime, current news/catalysts, account state, positions, orders, recent results, and available screener evidence.
2. Search for timely context when it can improve the thesis.
3. Form a thesis about what behavior may currently be exploitable.
4. Generate diverse strategy candidates, including simple, unusual, web-informed, and experimental variants.
5. Backtest candidates across symbols and train/validation splits.
6. Promote only the strongest surviving strategy into the active paper-trading state.
7. Deploy entries using the account's 2% buying-power sizing rule.
8. Review live outcomes, retire decaying ideas, mutate winners, and repeat.

Signals are evidence, not commandments. The operator should care about market context,
robustness, validation performance, trade count, drawdown, current exposure, and whether a
strategy's thesis still matches the regime.
""".strip()


STRATEGY_DSL_GUIDE = """
AI-authored strategy proposals must be JSON data, not executable code. Each proposal should
contain a thesis, risk parameters, and simple entry rules using supported metrics:
close, sma20, sma50, sma200, high_20, low_20, pos_52w, ret_1d_pct, ret_5d_pct,
ret_10d_pct, ret_20d_pct, ret_60d_pct, volume_ratio, dollar_vol_m,
dollar_vol_20d_m, volatility_20_pct, atr_14_pct, gap_pct, range_20_pct, score.

Supported operators: >, >=, <, <=.
Rules are evaluated by the backtester before any strategy can become active.
""".strip()
