from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from agent import llm_parse, llm_reply, rule_parse
from alpaca_rest import AlpacaError, AlpacaRest
from config import Settings, load_settings
from ingest import parse_upload
from storage import (
    UploadRecord,
    append_event,
    latest_upload,
    load_uploads,
    new_upload_path,
    save_upload_record,
    utc_now,
)
from strategy import run_once


settings = load_settings()
app = FastAPI(title="v4 Agentic Trader", version="0.1.0")


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
    .status { color: var(--muted); font-size: 13px; }
    main {
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
      min-height: calc(100vh - 58px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 16px;
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
      max-width: 980px;
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
    .composer {
      border-top: 1px solid var(--line);
      background: var(--panel);
      padding: 12px;
      display: grid;
      gap: 10px;
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
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      section.chat { min-height: 60vh; }
    }
  </style>
</head>
<body>
  <header>
    <h1>v4 Agentic Trader</h1>
    <div id="status" class="status">Checking service...</div>
  </header>
  <main>
    <aside>
      <div>
        <label for="token">Admin Token</label>
        <input id="token" type="password" placeholder="X-Admin-Token" />
        <div class="row" style="margin-top:8px">
          <button id="saveToken">Save</button>
          <button id="clearToken">Clear</button>
        </div>
        <div class="small">Stored only in this browser's localStorage.</div>
      </div>

      <div>
        <label for="file">Upload CSV or PDF</label>
        <input id="file" type="file" accept=".csv,.pdf,text/csv,application/pdf,text/plain" />
        <button id="uploadBtn" class="primary" style="width:100%;margin-top:8px">Upload</button>
      </div>

      <div>
        <label>Controls</label>
        <div class="row">
          <button id="stateBtn">State</button>
          <button id="uploadsBtn">Uploads</button>
        </div>
        <div class="row" style="margin-top:8px">
          <button id="dryRunBtn">Dry Run</button>
          <button id="runBtn" class="primary">Run</button>
        </div>
        <div class="row" style="margin-top:8px">
          <button id="cancelBtn" class="danger">Cancel Orders</button>
          <button id="closeBtn" class="danger">Close Positions</button>
        </div>
      </div>

      <div>
        <label>Quick Prompts</label>
        <div class="row">
          <button class="prompt">Show my account state and open positions.</button>
          <button class="prompt">Analyze the latest upload and tell me the best candidate.</button>
          <button class="prompt">Use the latest upload and run one paper trade.</button>
        </div>
      </div>
    </aside>

    <section class="chat">
      <div id="messages" class="messages"></div>
      <div class="composer">
        <textarea id="message" placeholder="Ask v4 what to inspect or do in the Alpaca paper account..."></textarea>
        <div class="row">
          <label class="checkline">
            <input id="execute" type="checkbox" checked />
            Execute actions
          </label>
          <button id="sendBtn" class="primary">Send</button>
        </div>
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
        const pre = document.createElement("pre");
        pre.textContent = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
        div.appendChild(pre);
      }
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
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
    async function callAndShow(label, fn) {
      addMessage("system", label);
      try {
        const data = await fn();
        addMessage("system", "Done.", data);
      } catch (err) {
        addMessage("error", err.message);
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
    $("uploadBtn").onclick = () => callAndShow("Uploading file...", async () => {
      const file = $("file").files[0];
      if (!file) throw new Error("Choose a CSV or PDF first.");
      const form = new FormData();
      form.append("file", file);
      return api("/upload", {method: "POST", headers: headers(false), body: form});
    });
    $("stateBtn").onclick = () => callAndShow("Fetching Alpaca state...", () => api("/state", {headers: headers()}));
    $("uploadsBtn").onclick = () => callAndShow("Fetching recent uploads...", () => api("/uploads", {headers: headers()}));
    $("dryRunBtn").onclick = () => callAndShow("Running latest upload as dry run...", () => api("/run?dry_run=true", {method: "POST", headers: headers()}));
    $("runBtn").onclick = () => callAndShow("Running latest upload and allowing paper execution...", () => api("/run", {method: "POST", headers: headers()}));
    $("cancelBtn").onclick = () => {
      if (confirm("Cancel all open Alpaca paper orders?")) {
        callAndShow("Canceling open orders...", () => api("/cancel-all", {method: "POST", headers: headers()}));
      }
    };
    $("closeBtn").onclick = () => {
      if (confirm("Close all Alpaca paper positions?")) {
        callAndShow("Closing all positions...", () => api("/close-all", {method: "POST", headers: headers()}));
      }
    };
    document.querySelectorAll(".prompt").forEach((btn) => {
      btn.onclick = () => { $("message").value = btn.textContent; $("message").focus(); };
    });
    $("sendBtn").onclick = async () => {
      const message = $("message").value.trim();
      if (!message) return;
      const execute = $("execute").checked;
      addMessage("user", message + (execute ? "" : "\\n\\n[parse only]"));
      $("message").value = "";
      try {
        const data = await api("/query", {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify({message, execute})
        });
        const reply = data.result?.reply || data.result?.reason || "v4 response";
        addMessage("system", reply, data);
      } catch (err) {
        addMessage("error", err.message);
      }
    };
    $("message").addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") $("sendBtn").click();
    });
    addMessage("system", "v4 is ready. Save your admin token, upload a CSV/PDF, then chat or use the controls.");
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
    }


@app.get("/state", dependencies=[Depends(require_auth)])
def state() -> Dict[str, Any]:
    client = alpaca()
    result = api_result(client.state)
    append_event(settings.data_dir, "state", {"ok": True})
    return result


@app.get("/uploads", dependencies=[Depends(require_auth)])
def uploads() -> Dict[str, Any]:
    records = load_uploads(settings.data_dir)
    return {"uploads": [record.__dict__ for record in records[-25:]]}


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
        result = state()
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
            result = order(OrderRequest(**merged))
    else:
        result = {"ok": True, "reply": llm_reply(settings, req.message, context)}

    append_event(
        settings.data_dir,
        "query",
        {"message": req.message, "parsed": parsed, "result": result},
    )
    return {"ok": True, "parsed": parsed, "result": result}
