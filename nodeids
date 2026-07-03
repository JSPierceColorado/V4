from typing import Any, Dict, List, Optional

from alpaca_rest import AlpacaRest
from storage import UploadRecord


def best_candidate(upload: UploadRecord) -> Optional[Dict[str, Any]]:
    candidates = upload.summary.get("top_candidates") or []
    if not candidates:
        symbols = upload.summary.get("symbols") or []
        return {"symbol": symbols[0], "score": None, "row": {}} if symbols else None
    return candidates[0]


def latest_midpoint(alpaca: AlpacaRest, symbol: str) -> Optional[float]:
    quote = alpaca.latest_quote(symbol)
    latest = quote.get("quote") or quote
    bid = latest.get("bp") or latest.get("bid_price")
    ask = latest.get("ap") or latest.get("ask_price")
    try:
        bid_f = float(bid)
        ask_f = float(ask)
    except (TypeError, ValueError):
        return None
    if bid_f <= 0 or ask_f <= 0:
        return None
    return round((bid_f + ask_f) / 2, 2)


def run_once(
    *,
    upload: Optional[UploadRecord],
    alpaca: AlpacaRest,
    default_qty: float,
    time_in_force: str,
    extended_hours: bool,
    dry_run: bool = False,
) -> Dict[str, Any]:
    if upload is None:
        return {"ok": False, "reason": "No uploaded CSV/PDF/text file is available yet."}

    candidate = best_candidate(upload)
    if not candidate:
        return {"ok": False, "reason": "The latest upload did not contain a ticker symbol."}

    symbol = str(candidate["symbol"]).upper()
    midpoint = latest_midpoint(alpaca, symbol)
    order_type = "limit" if midpoint else "market"
    order_args = {
        "symbol": symbol,
        "side": "buy",
        "qty": default_qty,
        "order_type": order_type,
        "time_in_force": time_in_force,
        "limit_price": midpoint,
        "extended_hours": extended_hours,
    }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "candidate": candidate,
            "order_args": order_args,
        }

    order = alpaca.place_order(**order_args)
    return {
        "ok": True,
        "candidate": candidate,
        "order_args": order_args,
        "order": order,
    }
