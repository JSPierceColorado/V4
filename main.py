from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
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
