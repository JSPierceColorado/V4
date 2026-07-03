# v4 Agentic Trader

One FastAPI service for Railway. It accepts CSV/PDF/text inputs, understands natural-language commands, and has direct control over an Alpaca paper account.

## Endpoints

- `GET /health`
- `GET /state`
- `GET /uploads`
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
