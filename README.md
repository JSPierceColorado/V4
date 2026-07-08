# v4 Agentic Trader

One FastAPI service for Railway. It accepts CSV/PDF/text inputs, understands natural-language commands, and has direct control over an Alpaca paper account.

## Endpoints

- `GET /health`
- `GET /state`
- `GET /uploads`
- `GET /metrics`
- `GET /screen`
- `POST /market/brief`
- `POST /market/symbol/{symbol}`
- `POST /market/strategy-ideas`
- `POST /market/review-positions`
- `GET /theses`
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
OPENAI_MODEL=gpt-5.4
```

The app also accepts short aliases like `5.5`, `5.4`, `5.2`, `5.1`, `mini`, and `nano`.

`gpt-5.4` is the default because v4 uses the model mostly for strategy generation and triage, while the backtester remains the final judge. Use `gpt-5.5` when you want to spend more on frontier reasoning experiments and compare research outcomes.

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

Research defaults to the full universe that v4 is allowed to trade. Set `AUTONOMY_RESEARCH_SYMBOLS_PER_RUN=0` to keep that behavior. If you set `AUTONOMY_SYMBOLS`, research uses that exact list; otherwise it uses all active tradable US equities. Positive `AUTONOMY_RESEARCH_SYMBOLS_PER_RUN` values restore rotating research batches.

Historical bar fetching is resumable. v4 caches completed research chunks in `DATA_DIR`, waits and retries when Alpaca returns a 429 rate limit, and resumes from cached chunks on the next research run if the limit persists or the app restarts.

Promotion uses a champion/challenger gate. The active strategy is re-backtested on the same train/test data as the best new candidate. v4 only promotes the challenger when it is profitable and beats the champion by both the configured fitness edge and validation-return edge.

```env
AUTONOMY_ENABLED=true
AUTONOMY_DRY_RUN=false
AUTONOMY_INTERVAL_SECONDS=600
AUTONOMY_SYMBOLS=
AUTONOMY_MIN_SCORE=0
AUTONOMY_MAX_ORDERS_PER_CYCLE=0
AUTONOMY_MAX_POSITIONS=0
AUTONOMY_POSITION_BUYING_POWER_PCT=0.02
AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE=0
AUTONOMY_RESEARCH_ENABLED=true
AUTONOMY_RESEARCH_INTERVAL_SECONDS=21600
AUTONOMY_RESEARCH_SYMBOLS_PER_RUN=0
AUTONOMY_RESEARCH_MAX_VARIANTS=1000
AUTONOMY_RESEARCH_LOOKBACK_DAYS=1095
AUTONOMY_RESEARCH_SCOUT_SYMBOLS=60
AUTONOMY_RESEARCH_VALIDATE_TOP_VARIANTS=50
AUTONOMY_RESEARCH_REQUIRE_PROFITABLE=true
AUTONOMY_RESEARCH_CHALLENGER_MIN_FITNESS_EDGE=0.01
AUTONOMY_RESEARCH_CHALLENGER_MIN_RETURN_EDGE=0.02
AUTONOMY_MUTATION_ENABLED=true
AUTONOMY_MUTATION_VARIANTS=160
AUTONOMY_MUTATION_PARENT_COUNT=8
AUTONOMY_AI_STRATEGY_LAB_ENABLED=true
AUTONOMY_AI_STRATEGY_IDEAS=48
AUTONOMY_WEB_RESEARCH_ENABLED=true
AUTONOMY_WEB_STRATEGY_IDEAS=24
AUTONOMY_AI_VARIANT_TRIAGE_ENABLED=true
AUTONOMY_AI_VARIANT_TRIAGE_TARGET=500
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
market brief
research NVDA
strategy ideas
review positions
show trade theses
```

## Research and Backtesting

The research layer generates strategy variants, backtests them on historical daily bars, and deploys the highest-fitness variant into the live paper strategy state. It uses a train/test split, then reports validation return, win rate, trade count, and the active strategy.

By default, each research job selects 250 rotating symbols from the tradable universe, looks back 1095 calendar days when Alpaca has that history, and generates 1000 variants. To keep the lab practical on Railway, v4 can use AI triage to choose a smaller scout set, scouts that set on 60 symbols, then validates the top 50 finalists across the full train/test split. The current strategy grammar includes momentum, breakout, volume momentum, trend pullback, dip buy, mean reversion, oversold reclaim, and volatility runner families. This gives the operator a richer search space while staying inside one Railway program and Alpaca's data limits.

When `AUTONOMY_AI_VARIANT_TRIAGE_ENABLED=true`, v4 uses OpenAI to preselect the most promising and diverse strategy IDs before CPU scouting. The default target is 500 scouted variants from the 1000-variant generated pool, which makes the research split roughly half AI judgment and half Railway backtesting while keeping validation as the final judge.

If AI triage returns too few usable IDs or the model call fails, v4 falls back to a deterministic diverse downselect to the same target instead of scouting the full generated pool.

Research records the best candidate every run, but with `AUTONOMY_RESEARCH_REQUIRE_PROFITABLE=true` it only promotes a strategy into the active paper-trading slot if validation return is positive and it produced enough trades. Losing candidates stay as research evidence instead of becoming the active strategy.

The agent keeps a rolling research history and the autonomous operator checks the research schedule every cycle, even when `AGENT_OPERATOR_ENABLED=true`. With the defaults, it can keep trading every 10 minutes while running a new research cycle when the 6-hour research interval is due.

When `AUTONOMY_MUTATION_ENABLED=true`, each new research cycle also mutates the strongest recent candidates. Mutation changes take-profit, stop-loss, hold duration, score thresholds, score bias, and AI-DSL rule thresholds, then tests those descendants beside fresh AI ideas and coded variants. This lets v4 push on a dead end instead of restarting from zero.

When `AUTONOMY_AI_STRATEGY_LAB_ENABLED=true`, research also asks the model to create thesis-driven strategy candidates as JSON rule data. Those AI-authored candidates are not executable code; they are sanitized into a small DSL, backtested beside the coded variants, and promoted only if they win validation.

When `AUTONOMY_WEB_RESEARCH_ENABLED=true`, research can also use the model's web search tool to look for current market regimes, catalysts, sector themes, and risks, then convert that context into sanitized DSL candidates. These web-informed ideas are backtested beside AI lab, mutation, and coded variants before promotion.

Every autonomous entry writes an append-only trade thesis record to `DATA_DIR/trade_theses.jsonl`. The thesis includes the active strategy, candidate feature snapshot, account/clock snapshot, recent market context, research context, risk plan, and invalidation triggers. Exits append an exit review that links back to the original thesis. Ask `show trade theses` to inspect open theses, or `review positions` to have the model compare open theses against current context.

The guiding v4 doctrine is built into the operator: observe the regime, form a thesis, generate candidates, backtest, deploy small, review live outcomes, evolve winners, and retire decaying ideas. Signals are evidence, not commandments.

Research also runs automatically inside the background autonomy engine. By default, v4 checks before each 10-minute trading cycle and runs research when at least 6 hours have passed since the last periodic research job.

Manual research requests start a background job and return immediately, which prevents Railway or the browser from timing out during a large backtest. Ask `research status` to see whether the job is fetching bars, scouting variants, validating finalists, failed, or completed with a promoted strategy.

Ask in chat:

```text
research strategies
backtest strategies
deploy best strategy
research status
market brief
research NVDA
strategy ideas
review positions
show trade theses
```
