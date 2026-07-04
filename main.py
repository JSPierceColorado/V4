import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from alpaca_rest import AlpacaError, AlpacaRest
from autonomy import AutonomyEngine
from config import Settings, load_settings
from ingest import parse_upload
from metrics import build_metrics
from research import run_research
from screener import screen_symbols
from storage import (
    UploadRecord,
    append_event,
    latest_upload,
    load_events,
    load_uploads,
    new_upload_path,
    save_upload_record,
    utc_now,
)
from strategy import run_once
from v4_brain import llm_parse, llm_reply, rule_parse


settings = load_settings()
app = FastAPI(title="v4 Agentic Trader", version="0.1.0")
autonomy_engine = AutonomyEngine(settings)


class ResearchJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._status: Dict[str, Any] = {
            "running": False,
            "job_id": None,
            "started_at": None,
            "finished_at": None,
            "progress": None,
            "result": None,
            "error": None,
        }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"started": False, "status": dict(self._status)}
            job_id = utc_now()
            self._status = {
                "running": True,
                "job_id": job_id,
                "started_at": job_id,
                "finished_at": None,
                "progress": {
                    "stage": "queued",
                    "message": "Research job queued.",
                    "updated_at": job_id,
                },
                "result": None,
                "error": None,
            }
            thread = threading.Thread(
                target=self._run,
                name="v4-research",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        append_event(
            settings.data_dir,
            "research_started",
            {
                "job_id": job_id,
                "reply": "Research started in the background.",
                "symbols_per_run": settings.autonomy_research_symbols_per_run,
                "max_variants": settings.autonomy_research_max_variants,
                "ai_strategy_ideas": settings.autonomy_ai_strategy_ideas,
            },
        )
        return {"started": True, "status": self.status()}

    def _run(self) -> None:
        try:
            client = alpaca()

            def progress(update: Dict[str, Any]) -> None:
                with self._lock:
                    self._status["progress"] = {
                        "updated_at": utc_now(),
                        **update,
                    }

            result = run_research(settings, client, progress_callback=progress)
            append_event(settings.data_dir, "research", result)
            with self._lock:
                self._status["running"] = False
                self._status["finished_at"] = utc_now()
                self._status["progress"] = {
                    "stage": "completed",
                    "message": result.get("reply") or "Research completed.",
                    "updated_at": self._status["finished_at"],
                }
                self._status["result"] = result
                self._status["error"] = None
        except Exception as exc:
            error = str(getattr(exc, "detail", exc))
            append_event(settings.data_dir, "research_error", {"error": error})
            with self._lock:
                self._status["running"] = False
                self._status["finished_at"] = utc_now()
                self._status["progress"] = {
                    "stage": "failed",
                    "message": error,
                    "updated_at": self._status["finished_at"],
                }
                self._status["result"] = None
                self._status["error"] = error


research_jobs = ResearchJobManager()


CHAT_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>v4 Agentic Trader</title>
  <style>
    :root {
      --bg: #f6f7f8;
      --panel: #ffffff;
      --ink: #17191c;
      --muted: #68707a;
      --line: #d9dee5;
      --accent: #0f766e;
      --accent-dark: #115e59;
      --danger: #b42318;
      --code: #eef2f5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 { font-size: 18px; margin: 0; letter-spacing: 0; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      flex-wrap: wrap;
    }
    .topbar input {
      width: min(280px, 36vw);
      padding: 8px 10px;
    }
    .topbar button { padding: 8px 10px; }
    .status { color: var(--muted); font-size: 13px; white-space: nowrap; }
    main {
      min-height: calc(100vh - 58px);
    }
    section.chat {
      display: grid;
      grid-template-rows: 1fr auto;
      min-height: calc(100vh - 58px);
    }
    label { display: block; font-weight: 650; margin-bottom: 6px; }
    input[type="password"], input[type="text"], textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      font: inherit;
      background: white;
      color: var(--ink);
    }
    textarea {
      min-height: 88px;
      max-height: 220px;
      resize: vertical;
    }
    input[type="file"] { width: 100%; }
    button {
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      border-radius: 7px;
      padding: 9px 11px;
      font: inherit;
      cursor: pointer;
    }
    button.primary {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
      font-weight: 700;
    }
    button.primary:hover { background: var(--accent-dark); }
    button.danger { color: var(--danger); border-color: #f1b8b3; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .row > button { flex: 1 1 auto; }
    .checkline { display: flex; align-items: center; gap: 8px; color: var(--muted); }
    .small { font-size: 12px; color: var(--muted); }
    .messages {
      padding: 18px;
      overflow: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .msg {
      max-width: min(880px, 92%);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .msg.user {
      align-self: flex-end;
      background: #e8f4f2;
      border-color: #b8ded8;
    }
    .msg.system { border-left: 4px solid var(--accent); }
    .msg.error { border-left: 4px solid var(--danger); }
    .msg.metrics {
      max-width: min(1120px, 96%);
      width: min(1120px, 96%);
      border-left: 4px solid var(--accent);
    }
    .metrics-title {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      margin-bottom: 12px;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric-tile {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: white;
      min-height: 106px;
    }
    .metric-label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .metric-value {
      margin-top: 12px;
      font-size: 27px;
      font-weight: 800;
      color: #8b9096;
    }
    .metric-note { margin-top: 6px; color: var(--muted); }
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .chart-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: white;
      min-height: 220px;
    }
    .chart-title {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }
    svg.chart { width: 100%; height: 160px; display: block; }
    details {
      margin-top: 10px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
      color: var(--muted);
    }
    summary { cursor: pointer; font-size: 12px; }
    .composer {
      border-top: 1px solid var(--line);
      background: var(--panel);
      padding: 12px;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 8px;
      align-items: end;
    }
    .composer textarea { min-height: 52px; }
    .attach { width: 44px; height: 44px; font-size: 18px; }
    .send { min-width: 84px; height: 44px; }
    .compose-middle { display: grid; gap: 8px; }
    .file-chip {
      display: none;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      width: fit-content;
      max-width: 100%;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 8px;
      color: var(--muted);
      background: #f9fbfc;
      font-size: 12px;
    }
    .file-chip button {
      border: 0;
      padding: 0 4px;
      background: transparent;
      color: var(--muted);
    }
    .composer-options {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    pre {
      margin: 8px 0 0;
      padding: 10px;
      border-radius: 7px;
      background: var(--code);
      overflow: auto;
      white-space: pre-wrap;
    }
    @media (max-width: 820px) {
      header { align-items: flex-start; flex-direction: column; }
      .topbar { justify-content: flex-start; }
      .topbar input { width: 100%; }
      .composer { grid-template-columns: auto minmax(0, 1fr); }
      .send { grid-column: 1 / -1; width: 100%; }
      .metric-grid, .chart-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>v4 Agentic Trader</h1>
    <div class="topbar">
      <div id="status" class="status">Checking service...</div>
      <input id="token" type="password" placeholder="Admin token" />
      <button id="saveToken">Save</button>
      <button id="clearToken">Clear</button>
    </div>
  </header>
  <main>
    <section class="chat">
      <div id="messages" class="messages"></div>
      <div class="composer">
        <input id="file" type="file" accept=".csv,.pdf,text/csv,application/pdf,text/plain" hidden />
        <button id="attachBtn" class="attach" title="Attach CSV or PDF">+</button>
        <div class="compose-middle">
          <div id="fileChip" class="file-chip"><span id="fileName"></span><button id="clearFile">x</button></div>
          <textarea id="message" placeholder="Message v4, or attach a CSV/PDF and ask what to do..."></textarea>
          <div class="composer-options">
            <label class="checkline">
              <input id="execute" type="checkbox" checked />
              Execute paper-account actions
            </label>
            <span class="small">Enter sends. Shift+Enter adds a line.</span>
          </div>
        </div>
        <button id="sendBtn" class="primary send">Send</button>
      </div>
    </section>
  </main>

  <script>
    const $ = (id) => document.getElementById(id);
    const messages = $("messages");
    const tokenInput = $("token");
    tokenInput.value = localStorage.getItem("v4_admin_token") || "";

    function token() { return tokenInput.value.trim(); }
    function headers(json=false) {
      const h = {"X-Admin-Token": token()};
      if (json) h["Content-Type"] = "application/json";
      return h;
    }
    function addMessage(kind, text, detail) {
      const div = document.createElement("div");
      div.className = "msg " + kind;
      div.textContent = text;
      if (detail !== undefined) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Details";
        const pre = document.createElement("pre");
        pre.textContent = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
        details.appendChild(summary);
        details.appendChild(pre);
        div.appendChild(details);
      }
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }
    function addNodeMessage(node) {
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
    }
    function stateSummary(state) {
      const account = state?.account || state?.state?.account || {};
      const positions = state?.positions || state?.state?.positions || [];
      const orders = state?.open_orders || state?.state?.open_orders || [];
      const bp = account.buying_power ?? "unknown";
      const cash = account.cash ?? "unknown";
      const equity = account.equity ?? "unknown";
      const status = account.status ?? "unknown";
      return `Connected to Alpaca paper. Status: ${status}. Buying power: $${bp}. Cash: $${cash}. Equity: $${equity}. Positions: ${positions.length}. Open orders: ${orders.length}.`;
    }
    function summarizeResponse(data) {
      if (data?.result?.metrics) return data.result.metrics.summary;
      if (data?.metrics) return data.metrics.summary;
      if (data?.result?.reply) return data.result.reply;
      if (data?.result?.state || data?.result?.account) return stateSummary(data.result);
      if (data?.account) return stateSummary(data);
      if (data?.result?.order) {
        const order = data.result.order;
        return `Paper order submitted: ${order.side || ""} ${order.qty || order.notional || ""} ${order.symbol || ""}. Status: ${order.status || "submitted"}.`;
      }
      if (data?.upload) {
        const symbols = data.upload.summary?.symbols || [];
        return `Uploaded ${data.upload.filename}. Found ${symbols.length} possible symbols${symbols.length ? `: ${symbols.slice(0, 8).join(", ")}` : ""}.`;
      }
      return data?.result?.reason || data?.parsed?.args?.text || "Done.";
    }
    function numericValues(points, key) {
      return points.map((point) => Number(point[key])).filter((value) => Number.isFinite(value));
    }
    function pathFor(points, key, width, height, pad) {
      const values = numericValues(points, key);
      if (!values.length) return "";
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = max - min || 1;
      return points.map((point, index) => {
        const value = Number(point[key]);
        const x = pad + (index / Math.max(points.length - 1, 1)) * (width - pad * 2);
        const y = height - pad - ((value - min) / span) * (height - pad * 2);
        return `${index ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`;
      }).join(" ");
    }
    function lineChart(title, points, key) {
      const width = 420, height = 160, pad = 22;
      const values = numericValues(points, key);
      if (!values.length) {
        return `<div class="chart-card"><div class="chart-title">${title}</div>
          <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}">
            <text x="${width/2}" y="${height/2}" text-anchor="middle" font-size="14" fill="#8b9096">Not enough history yet</text>
          </svg></div>`;
      }
      const min = values.length ? Math.min(...values) : 0;
      const max = values.length ? Math.max(...values) : 0;
      return `<div class="chart-card"><div class="chart-title">${title}</div>
        <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}">
          <line x1="${pad}" y1="${pad}" x2="${width-pad}" y2="${pad}" stroke="#20252b" stroke-width="1" />
          <line x1="${pad}" y1="${height-pad}" x2="${width-pad}" y2="${height-pad}" stroke="#20252b" stroke-width="1" />
          <text x="2" y="${pad+4}" font-size="10" fill="#8b9096">${max.toFixed(2)}</text>
          <text x="2" y="${height-pad+4}" font-size="10" fill="#8b9096">${min.toFixed(2)}</text>
          <path d="${pathFor(points, key, width, height, pad)}" fill="none" stroke="#86b6ff" stroke-width="3" />
        </svg></div>`;
    }
    function barChart(title, points, key) {
      const width = 420, height = 160, pad = 22;
      const values = numericValues(points, key);
      if (!values.length) {
        return `<div class="chart-card"><div class="chart-title">${title}</div>
          <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}">
            <text x="${width/2}" y="${height/2}" text-anchor="middle" font-size="14" fill="#8b9096">No data yet</text>
          </svg></div>`;
      }
      const maxAbs = Math.max(...values.map((v) => Math.abs(v)), 1);
      const zeroY = height / 2;
      const barW = Math.max(4, (width - pad * 2) / Math.max(points.length, 1) - 3);
      const bars = points.map((point, index) => {
        const value = Number(point[key]) || 0;
        const x = pad + index * ((width - pad * 2) / Math.max(points.length, 1));
        const h = Math.abs(value) / maxAbs * (height / 2 - pad);
        const y = value >= 0 ? zeroY - h : zeroY;
        const color = value >= 0 ? "#85d99a" : "#ef6b6b";
        return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${color}" />`;
      }).join("");
      return `<div class="chart-card"><div class="chart-title">${title}</div>
        <svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}">
          <line x1="${pad}" y1="${zeroY}" x2="${width-pad}" y2="${zeroY}" stroke="#20252b" stroke-width="1" />
          ${bars}
        </svg></div>`;
    }
    function renderMetrics(metrics, detail) {
      const div = document.createElement("div");
      div.className = "msg metrics";
      const tiles = (metrics.tiles || []).map((tile) => `
        <div class="metric-tile">
          <div class="metric-label">${tile.label}</div>
          <div class="metric-value">${tile.value}</div>
          <div class="metric-note">${tile.note || ""}</div>
        </div>`).join("");
      const charts = metrics.charts || {};
      div.innerHTML = `
        <div class="metrics-title">
          <strong>Paper Account Metrics</strong>
          <span>${metrics.generated_at || ""}</span>
        </div>
        <div>${metrics.summary || ""}</div>
        <div class="small" style="margin:8px 0 14px">${metrics.notes?.projection || ""}</div>
        <div class="metric-grid">${tiles}</div>
        <div class="chart-grid">
          ${lineChart("Equity Trend", charts.equity || [], "equity")}
          ${lineChart("Drawdown Trend", charts.drawdown || [], "drawdown")}
          ${barChart("Daily P/L", charts.daily_pl || [], "profit_loss")}
          ${lineChart("Projected Equity Path", charts.projected_equity || [], "equity")}
        </div>`;
      if (detail !== undefined) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Details";
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(detail, null, 2);
        details.appendChild(summary);
        details.appendChild(pre);
        div.appendChild(details);
      }
      addNodeMessage(div);
    }
    async function api(path, options={}) {
      const res = await fetch(path, options);
      const text = await res.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { data = {raw: text}; }
      if (!res.ok) throw new Error(data.detail || text || res.statusText);
      return data;
    }
    async function refreshHealth() {
      try {
        const data = await api("/health");
        $("status").textContent = `Paper: ${data.paper} | Alpaca: ${data.alpaca_configured ? "ready" : "missing"} | OpenAI: ${data.openai_configured ? "ready" : "off"}`;
      } catch (err) {
        $("status").textContent = "Service unavailable";
      }
    }
    $("saveToken").onclick = () => {
      localStorage.setItem("v4_admin_token", token());
      addMessage("system", "Admin token saved in this browser.");
    };
    $("clearToken").onclick = () => {
      localStorage.removeItem("v4_admin_token");
      tokenInput.value = "";
      addMessage("system", "Admin token cleared.");
    };
    $("attachBtn").onclick = () => $("file").click();
    $("file").onchange = () => {
      const file = $("file").files[0];
      $("fileChip").style.display = file ? "flex" : "none";
      $("fileName").textContent = file ? file.name : "";
    };
    $("clearFile").onclick = (event) => {
      event.preventDefault();
      $("file").value = "";
      $("fileChip").style.display = "none";
      $("fileName").textContent = "";
    };
    $("sendBtn").onclick = async () => {
      const message = $("message").value.trim();
      const file = $("file").files[0];
      if (!message && !file) return;
      const execute = $("execute").checked;
      addMessage("user", `${file ? `[attached ${file.name}]\\n` : ""}${message || "Upload this file."}${execute ? "" : "\\n\\n[parse only]"}`);
      $("message").value = "";
      try {
        if (file) {
          const form = new FormData();
          form.append("file", file);
          const uploaded = await api("/upload", {method: "POST", headers: headers(false), body: form});
          addMessage("system", summarizeResponse(uploaded), uploaded);
          $("file").value = "";
          $("fileChip").style.display = "none";
          $("fileName").textContent = "";
        }
        if (!message) return;
        const data = await api("/query", {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify({message, execute})
        });
        const warning = data.parsed?.warning;
        const metrics = data.result?.metrics || data.metrics;
        if (!warning && metrics) {
          renderMetrics(metrics, data);
        } else {
          addMessage(warning ? "error" : "system", warning || summarizeResponse(data), data);
        }
      } catch (err) {
        addMessage("error", err.message);
      }
    };
    $("message").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        $("sendBtn").click();
      }
    });
    addMessage("system", "v4 is ready. Save your admin token, then chat naturally. Attach a CSV/PDF with + when you want to feed it data.");
    refreshHealth();
  </script>
</body>
</html>
"""


class QueryRequest(BaseModel):
    message: str = Field(..., min_length=1)
    execute: bool = True


class OrderRequest(BaseModel):
    symbol: str
    side: str
    qty: Optional[float] = None
    notional: Optional[float] = None
    order_type: str = "market"
    time_in_force: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    extended_hours: Optional[bool] = None


class MetricsResponse(BaseModel):
    ok: bool
    metrics: Dict[str, Any]


@app.on_event("startup")
def maybe_start_autonomy() -> None:
    if settings.autonomy_enabled and settings.alpaca_ready:
        autonomy_engine.start(alpaca)


def require_auth(x_admin_token: str = Header(default="")) -> None:
    if not settings.admin_token:
        raise HTTPException(
            status_code=500,
            detail="ADMIN_TOKEN is not configured. Set it in Railway before using v4.",
        )
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


def alpaca() -> AlpacaRest:
    if not settings.alpaca_ready:
        raise HTTPException(
            status_code=500,
            detail="Alpaca paper credentials are not configured.",
        )
    return AlpacaRest(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        settings.alpaca_trading_base_url,
        settings.alpaca_data_base_url,
        settings.alpaca_data_feed,
    )


def api_result(fn):
    try:
        return fn()
    except AlpacaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def summarize_state(raw: Dict[str, Any]) -> str:
    account = raw.get("account") or {}
    positions = raw.get("positions") or []
    orders = raw.get("open_orders") or []
    return (
        "Connected to Alpaca paper. "
        f"Status: {account.get('status', 'unknown')}. "
        f"Buying power: ${account.get('buying_power', 'unknown')}. "
        f"Cash: ${account.get('cash', 'unknown')}. "
        f"Equity: ${account.get('equity', 'unknown')}. "
        f"Positions: {len(positions)}. "
        f"Open orders: {len(orders)}."
    )


def summarize_order(raw: Dict[str, Any]) -> str:
    return (
        "Paper order submitted. "
        f"{str(raw.get('side', '')).upper()} {raw.get('qty') or raw.get('notional') or ''} "
        f"{raw.get('symbol', '')}. Status: {raw.get('status', 'submitted')}."
    ).strip()


def summarize_clock(raw: Dict[str, Any]) -> str:
    is_open = raw.get("is_open")
    next_open = raw.get("next_open", "unknown")
    next_close = raw.get("next_close", "unknown")
    if is_open:
        return f"Market is open. Next close: {next_close}."
    return f"Market is closed. Next open: {next_open}."


def summarize_event(event: Dict[str, Any]) -> str:
    event_type = event.get("type", "event")
    ts = event.get("ts", "unknown time")
    payload = event.get("payload") or {}
    if event_type == "agent_operator_journal":
        return f"{ts} - operator journal\n{payload.get('summary', 'No summary.')}"
    if event_type == "agent_operator_cycle":
        return f"{ts} - operator cycle\n{payload.get('summary', 'No summary.')}"
    if event_type in {"research", "periodic_research"}:
        reply = payload.get("reply")
        if reply:
            return f"{ts} - research\n{reply}"
        research = payload.get("research") or {}
        best = research.get("best") or {}
        validation = best.get("validation") or {}
        return (
            f"{ts} - research\n"
            f"Best: {research.get('best_strategy_id', 'unknown')}. "
            f"Validation return: {validation.get('total_return_pct', 'n/a')}, "
            f"win rate: {validation.get('win_rate', 'n/a')}, "
            f"trades: {validation.get('trades', 'n/a')}."
        )
    if event_type == "research_started":
        return (
            f"{ts} - research started\n"
            f"{payload.get('symbols_per_run', 'n/a')} symbols per run, "
            f"{payload.get('max_variants', 'n/a')} max variants, "
            f"{payload.get('ai_strategy_ideas', 'n/a')} AI ideas."
        )
    if event_type == "research_error":
        return f"{ts} - research error\n{payload.get('error', 'Unknown error')}"
    if event_type == "autonomy_cycle":
        return f"{ts} - autonomy cycle\n{payload.get('summary', 'No summary.')}"
    if event_type == "autonomy_error":
        return f"{ts} - autonomy error\n{payload.get('error', 'Unknown error')}"
    if event_type == "order":
        result = payload.get("result") or {}
        request = payload.get("request") or {}
        return (
            f"{ts} - order\n"
            f"{request.get('side', result.get('side', ''))} "
            f"{request.get('qty') or request.get('notional') or result.get('qty') or result.get('notional') or ''} "
            f"{request.get('symbol') or result.get('symbol') or ''}. "
            f"Status: {result.get('status', 'submitted')}."
        )
    if event_type == "clock":
        return f"{ts} - clock\n{summarize_clock(payload.get('clock') or {})}"
    return f"{ts} - {event_type}\n{payload.get('reply') or payload.get('summary') or 'Recorded.'}"


def summarize_events(rows: list[Dict[str, Any]]) -> str:
    if not rows:
        return "No recent actions recorded yet."
    lines = [f"Recent actions ({len(rows)}):"]
    for index, event in enumerate(reversed(rows), start=1):
        lines.append(f"\n{index}. {summarize_event(event)}")
    return "\n".join(lines)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/chat")


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat() -> str:
    return CHAT_HTML


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "app": settings.app_name,
        "paper": settings.alpaca_paper,
        "alpaca_configured": settings.alpaca_ready,
        "openai_configured": settings.openai_ready,
        "admin_token_configured": bool(settings.admin_token),
        "autonomy_enabled": settings.autonomy_enabled,
        "autonomy_running": autonomy_engine.status()["running"],
    }


@app.get("/state", dependencies=[Depends(require_auth)])
def state() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(client.state)
    append_event(settings.data_dir, "state", {"ok": True})
    return result


@app.get("/clock", dependencies=[Depends(require_auth)])
def clock() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(client.clock)
    append_event(settings.data_dir, "clock", {"ok": True, "clock": result})
    return {"ok": True, "reply": summarize_clock(result), "clock": result}


@app.get("/uploads", dependencies=[Depends(require_auth)])
def uploads() -> Dict[str, Any]:
    records = load_uploads(settings.data_dir)
    return {"uploads": [record.__dict__ for record in records[-25:]]}


@app.get("/events", dependencies=[Depends(require_auth)])
def events(limit: int = 20) -> Dict[str, Any]:
    limit = max(1, min(limit, 100))
    rows = load_events(settings.data_dir, limit=limit)
    return {
        "ok": True,
        "reply": summarize_events(rows),
        "events": rows,
    }


@app.get("/metrics", dependencies=[Depends(require_auth)])
def metrics() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(lambda: build_metrics(client))
    append_event(settings.data_dir, "metrics", {"ok": True})
    return {"ok": True, "metrics": result}


@app.get("/screen", dependencies=[Depends(require_auth)])
def screen() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(
        lambda: screen_symbols(
            client,
            settings.autonomy_symbols or None,
            max_symbols_per_cycle=settings.autonomy_screen_symbols_per_cycle,
        )
    )
    append_event(settings.data_dir, "screen", {"summary": result})
    return {
        "ok": True,
        "reply": (
            f"Screened {result.get('symbols_checked', 0)} symbols and found "
            f"{len(result.get('candidates', []))} candidates. "
            f"Top candidate: {result.get('candidates', [{}])[0].get('symbol', 'n/a') if result.get('candidates') else 'n/a'}."
        ),
        "screen": result,
    }


@app.get("/autonomy/status", dependencies=[Depends(require_auth)])
def autonomy_status() -> Dict[str, Any]:
    return {"ok": True, "reply": "Autonomy status loaded.", "autonomy": autonomy_engine.status()}


@app.post("/autonomy/start", dependencies=[Depends(require_auth)])
def autonomy_start() -> Dict[str, Any]:
    result = autonomy_engine.start(alpaca)
    append_event(settings.data_dir, "autonomy_start", result)
    return {"ok": True, "reply": "Autonomy started.", **result}


@app.post("/autonomy/stop", dependencies=[Depends(require_auth)])
def autonomy_stop() -> Dict[str, Any]:
    result = autonomy_engine.stop()
    append_event(settings.data_dir, "autonomy_stop", result)
    return {"ok": True, "reply": "Autonomy stopped.", **result}


@app.post("/autonomy/cycle", dependencies=[Depends(require_auth)])
def autonomy_cycle() -> Dict[str, Any]:
    client = alpaca()
    if settings.agent_operator_enabled:
        result = api_result(lambda: autonomy_engine.run_operator_cycle(client))
        return {"ok": True, "reply": result["summary"], "operator": result}
    result = api_result(lambda: autonomy_engine.run_cycle(client))
    return {"ok": True, "reply": result["summary"], "autonomy": result}


@app.post("/agent/cycle", dependencies=[Depends(require_auth)])
def agent_cycle() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(lambda: autonomy_engine.run_operator_cycle(client))
    return {"ok": True, "reply": result["summary"], "operator": result}


@app.post("/research", dependencies=[Depends(require_auth)])
def research() -> Dict[str, Any]:
    job = research_jobs.start()
    status = job["status"]
    if job["started"]:
        reply = (
            "Research started in the background. "
            f"Testing up to {settings.autonomy_research_max_variants} variants "
            f"on {settings.autonomy_research_symbols_per_run} selected symbols, "
            f"including up to {settings.autonomy_ai_strategy_ideas} AI lab ideas. "
            "Ask `research status` for progress."
        )
    else:
        reply = (
            "Research is already running. "
            f"Started at {status.get('started_at')}. Ask `research status` for progress."
        )
    return {"ok": True, "reply": reply, "research_job": status}


@app.get("/research/status", dependencies=[Depends(require_auth)])
def research_status() -> Dict[str, Any]:
    status = research_jobs.status()
    progress = status.get("progress") or {}
    progress_text = progress.get("message") or progress.get("stage") or "No progress update yet."
    if status.get("running"):
        reply = (
            "Research is still running. "
            f"Started at {status.get('started_at')}. "
            f"{progress_text}"
        )
    elif status.get("error"):
        reply = f"Research failed: {status.get('error')}"
    elif status.get("result"):
        reply = status["result"].get("reply") or "Research completed."
    else:
        reply = "No background research job has run since this process started."
    return {"ok": True, "reply": reply, "research_job": status}


@app.post("/upload", dependencies=[Depends(require_auth)])
async def upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    raw = await file.read()
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")
    upload_id, path = new_upload_path(settings.data_dir, file.filename or "upload")
    Path(path).write_bytes(raw)
    summary = parse_upload(file.filename or "upload", raw, file.content_type or "")
    record = UploadRecord(
        upload_id=upload_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "",
        path=str(path),
        created_at=utc_now(),
        kind=summary.get("kind", "unknown"),
        summary=summary,
    )
    save_upload_record(settings.data_dir, record)
    append_event(settings.data_dir, "upload", {"upload_id": upload_id, "summary": summary})
    return {"ok": True, "upload": record.__dict__}


@app.post("/order", dependencies=[Depends(require_auth)])
def order(req: OrderRequest) -> Dict[str, Any]:
    client = alpaca()

    def submit() -> Dict[str, Any]:
        return client.place_order(
            symbol=req.symbol,
            side=req.side,
            qty=req.qty,
            notional=req.notional,
            order_type=req.order_type,
            time_in_force=req.time_in_force or settings.default_time_in_force,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            extended_hours=(
                settings.extended_hours
                if req.extended_hours is None
                else req.extended_hours
            ),
        )

    result = api_result(submit)
    append_event(settings.data_dir, "order", {"request": req.model_dump(), "result": result})
    return {"ok": True, "order": result}


@app.post("/cancel-all", dependencies=[Depends(require_auth)])
def cancel_all() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(client.cancel_all_orders)
    append_event(settings.data_dir, "cancel_all_orders", {"result": result})
    return {"ok": True, "result": result}


@app.post("/close-all", dependencies=[Depends(require_auth)])
def close_all() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(client.close_all_positions)
    append_event(settings.data_dir, "close_all_positions", {"result": result})
    return {"ok": True, "result": result}


@app.post("/run", dependencies=[Depends(require_auth)])
def run(dry_run: bool = False) -> Dict[str, Any]:
    client = alpaca()
    upload_record = latest_upload(settings.data_dir)
    result = api_result(
        lambda: run_once(
            upload=upload_record,
            alpaca=client,
            default_qty=settings.default_order_qty,
            time_in_force=settings.default_time_in_force,
            extended_hours=settings.extended_hours,
            dry_run=dry_run,
        )
    )
    append_event(settings.data_dir, "run", {"dry_run": dry_run, "result": result})
    return result


@app.post("/query", dependencies=[Depends(require_auth)])
def query(req: QueryRequest) -> Dict[str, Any]:
    upload_record = latest_upload(settings.data_dir)
    context = {
        "recent_upload": upload_record.__dict__ if upload_record else None,
        "paper": settings.alpaca_paper,
    }
    parsed = llm_parse(settings, req.message, context)
    action = parsed.get("action", "reply")
    args = parsed.get("args") or {}

    if not req.execute:
        return {"ok": True, "parsed": parsed, "executed": False}

    if action == "state":
        raw_state = state()
        result = {"ok": True, "reply": summarize_state(raw_state), "state": raw_state}
    elif action == "clock":
        result = clock()
    elif action == "metrics":
        result = metrics()
    elif action == "events":
        result = events()
    elif action == "screen":
        result = screen()
    elif action == "autonomy_start":
        result = autonomy_start()
    elif action == "autonomy_stop":
        result = autonomy_stop()
    elif action == "autonomy_status":
        result = autonomy_status()
    elif action == "autonomy_cycle":
        result = autonomy_cycle()
    elif action == "agent_cycle":
        result = agent_cycle()
    elif action == "research":
        result = research()
    elif action == "research_status":
        result = research_status()
    elif action == "cancel_all_orders":
        result = cancel_all()
    elif action == "close_all_positions":
        result = close_all()
    elif action == "run":
        result = run(dry_run=False)
    elif action == "analyze":
        result = {
            "latest_upload": upload_record.__dict__ if upload_record else None,
            "reply": llm_reply(settings, req.message, context),
        }
    elif action == "place_order":
        merged = {
            "symbol": args.get("symbol"),
            "side": args.get("side"),
            "qty": args.get("qty") or settings.default_order_qty,
            "notional": args.get("notional"),
            "order_type": args.get("order_type") or "market",
            "time_in_force": args.get("time_in_force") or settings.default_time_in_force,
            "limit_price": args.get("limit_price"),
            "stop_price": args.get("stop_price"),
            "extended_hours": args.get("extended_hours", settings.extended_hours),
        }
        if not merged["symbol"] or not merged["side"]:
            result = {"ok": False, "reply": "I could not identify a symbol and side."}
        else:
            order_result = order(OrderRequest(**merged))
            result = {
                **order_result,
                "reply": summarize_order(order_result.get("order") or {}),
            }
    else:
        result = {
            "ok": True,
            "reply": args.get("text") or llm_reply(settings, req.message, context),
        }

    append_event(
        settings.data_dir,
        "query",
        {"message": req.message, "parsed": parsed, "result": result},
    )
    return {"ok": True, "parsed": parsed, "result": result}
