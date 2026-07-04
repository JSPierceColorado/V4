# v4 Agentic Trader

One FastAPI service for Railway. It accepts CSV/PDF/text inputs, understands natural-language commands, and has direct control over an Alpaca paper account.

## Endpoints

- `GET /health`
- `GET /state`
- `GET /uploads`
- `GET /metrics`
- `GET /screen`
- `GET /research/status`
- `GET /autonomy/status`
- `POST /autonomy/start`
- `POST /autonomy/stop`
- `POST /autonomy/cycle`
- `POST /research`
- `POST /upload`
- `POST /query`
- `POST /run`
- `POST /order`
- `POST /cancel-all`
- `POST /close-all`

Open the browser UI at:

```text
https://your-railway-url/chat
```

All endpoints except `/health` require:

```text
X-Admin-Token: your ADMIN_TOKEN
```

## Railway

Deploy the `v4_agent` folder as the service root, then set env vars from `env.example`.

## Try It

```bash
curl https://your-railway-url/health
```

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://your-railway-url/state
```

Upload a CSV:

```bash
curl -X POST \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "file=@signals.csv" \
  https://your-railway-url/upload
```

Ask it to act:

```bash
curl -X POST \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"buy 1 share of AAPL with a limit order at 190","execute":true}' \
  https://your-railway-url/query
```

Run the latest upload as a simple one-candidate paper strategy:

```bash
curl -X POST \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  "https://your-railway-url/run?dry_run=true"
```

Remove `dry_run=true` to place the paper order.

## OpenAI Model

Use a full model id:

```env
OPENAI_MODEL=gpt-5.2
```

The app also accepts short aliases like `5.2`, `5.1`, `mini`, and `nano`.

## Metrics

Ask in chat:

```text
show metrics
```

or call:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" https://your-railway-url/metrics
```

The metrics view includes compact KPI tiles plus charts for equity trend, drawdown trend, daily P/L, and a simple projected equity path.

## Autonomy

The autonomous loop screens symbols, manages exits, evolves the active strategy, and places paper orders. Blank `AUTONOMY_SYMBOLS` means all active tradable US equities. To avoid Alpaca rate limits, v4 rotates through the full universe in batches each cycle. Zero order/position caps mean unlimited. New entries default to 2% of Alpaca `buying_power`, which includes margin buying power.

```env
AUTONOMY_ENABLED=true
AUTONOMY_DRY_RUN=false
AUTONOMY_INTERVAL_SECONDS=600
AUTONOMY_SYMBOLS=
AUTONOMY_MIN_SCORE=0
AUTONOMY_MAX_ORDERS_PER_CYCLE=0
AUTONOMY_MAX_POSITIONS=0
AUTONOMY_POSITION_BUYING_POWER_PCT=0.02
AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=100
AUTONOMY_RESEARCH_ENABLED=true
AUTONOMY_RESEARCH_INTERVAL_SECONDS=21600
AUTONOMY_RESEARCH_SYMBOLS_PER_RUN=250
AUTONOMY_RESEARCH_MAX_VARIANTS=1000
AUTONOMY_AI_STRATEGY_LAB_ENABLED=true
AUTONOMY_AI_STRATEGY_IDEAS=48
AGENT_OPERATOR_ENABLED=true
```

The strategy layer starts with balanced, fast, and runner variants. Positions are tagged to the strategy that opened them. Exits update each strategy's win rate, average P/L, and fitness, then the loop promotes the best performer.

When `AGENT_OPERATOR_ENABLED=true`, the background loop asks the model to choose which tools to run. The existing screener, research engine, metrics, clock, and order-management cycle become tools for the operator instead of acting as the whole brain.

Ask in chat:

```text
screen the market
is the market open?
run one autonomous cycle
start autonomy
stop autonomy
autonomy status
research strategies
research status
run operator cycle
show recent actions
```

## Research and Backtesting

The research layer generates strategy variants, backtests them on historical daily bars, and deploys the highest-fitness variant into the live paper strategy state. It uses a train/test split, then reports validation return, win rate, trade count, and the active strategy.

By default, each research job selects 250 rotating symbols from the tradable universe and tests 1000 variants. The current strategy grammar includes momentum, breakout, volume momentum, trend pullback, dip buy, mean reversion, oversold reclaim, and volatility runner families. This gives the operator a richer search space while staying inside one Railway program and Alpaca's data limits.

When `AUTONOMY_AI_STRATEGY_LAB_ENABLED=true`, research also asks the model to create thesis-driven strategy candidates as JSON rule data. Those AI-authored candidates are not executable code; they are sanitized into a small DSL, backtested beside the coded variants, and promoted only if they win validation.

The guiding v4 doctrine is built into the operator: observe the regime, form a thesis, generate candidates, backtest, deploy small, review live outcomes, evolve winners, and retire decaying ideas. Signals are evidence, not commandments.

Research also runs automatically inside the background autonomy engine. By default, v4 checks before each 10-minute trading cycle and runs research when at least 6 hours have passed since the last periodic research job.

Manual research requests start a background job and return immediately, which prevents Railway or the browser from timing out during a large backtest. Ask `research status` to see whether the job is still running, failed, or completed with a promoted strategy.

Ask in chat:

```text
research strategies
backtest strategies
deploy best strategy
research status
```
