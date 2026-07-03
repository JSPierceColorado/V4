import json
import os
import re
from typing import Any, Dict, Optional

from config import Settings


ORDER_RE = re.compile(
    r"\b(?P<side>buy|sell)\s+"
    r"(?:(?P<qty>\d+(?:\.\d+)?)\s+(?:shares?|share|qty|quantity)\s+(?:of\s+)?)?"
    r"(?P<symbol>[A-Z]{1,5}(?:\.[A-Z])?)"
    r"(?:.*?\b(?P<type>market|limit|stop|stop-limit|stop limit)\b)?"
    r"(?:.*?\b(?:at|limit|price)\s+\$?(?P<price>\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)


def _json_from_text(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def rule_parse(message: str) -> Dict[str, Any]:
    lowered = message.lower()
    if any(phrase in lowered for phrase in ("cancel all", "cancel orders")):
        return {"action": "cancel_all_orders", "args": {}}
    if any(phrase in lowered for phrase in ("close all", "liquidate", "flatten")):
        return {"action": "close_all_positions", "args": {}}
    if any(word in lowered for word in ("state", "account", "positions", "buying power")):
        return {"action": "state", "args": {}}
    if "run" in lowered or "use uploaded" in lowered or "strongest" in lowered:
        return {"action": "run", "args": {}}
    if "analyze" in lowered or "summarize" in lowered:
        return {"action": "analyze", "args": {}}

    match = ORDER_RE.search(message)
    if match:
        groups = match.groupdict()
        order_type = (groups.get("type") or "").lower().replace(" ", "-") or "market"
        price = groups.get("price")
        if price and order_type == "market":
            order_type = "limit"
        return {
            "action": "place_order",
            "args": {
                "symbol": groups["symbol"].upper(),
                "side": groups["side"].lower(),
                "qty": float(groups["qty"]) if groups.get("qty") else None,
                "order_type": order_type,
                "limit_price": float(price) if price else None,
            },
        }
    return {"action": "reply", "args": {"message": message}}


def llm_parse(settings: Settings, message: str, context: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.openai_ready:
        return rule_parse(message)

    try:
        from openai import OpenAI
    except ImportError:
        return rule_parse(message)

    client = OpenAI(api_key=settings.openai_api_key)
    system = (
        "You are the command parser for a paper-trading FastAPI app. "
        "Return only JSON with keys action and args. "
        "Allowed actions: state, analyze, run, place_order, cancel_all_orders, "
        "close_all_positions, reply. "
        "Only choose place_order when the user clearly asks to place a paper order. "
        "For place_order args include symbol, side, qty, notional, order_type, "
        "time_in_force, limit_price, stop_price, extended_hours when known."
    )
    payload = {
        "user_message": message,
        "recent_upload": context.get("recent_upload"),
        "defaults": {
            "default_order_qty": settings.default_order_qty,
            "default_time_in_force": settings.default_time_in_force,
        },
    }
    try:
        response = client.responses.create(
            model=settings.openai_model,
            input=f"{system}\n\n{json.dumps(payload, default=str)}",
        )
        parsed = _json_from_text(getattr(response, "output_text", "") or "")
        if parsed and parsed.get("action"):
            return parsed
    except Exception:
        pass
    return rule_parse(message)


def llm_reply(settings: Settings, message: str, context: Dict[str, Any]) -> str:
    if not settings.openai_ready:
        return (
            "I can handle direct commands now: state, cancel all orders, close all "
            "positions, run latest upload, or buy/sell SYMBOL. Set OPENAI_API_KEY "
            "to enable richer natural-language replies."
        )
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.openai_model,
            input=(
                "You are v4, a concise paper-trading assistant. "
                "Do not give financial advice; explain what the app can inspect or do. "
                f"Context: {json.dumps(context, default=str)[:12000]}\n\n"
                f"User: {message}"
            ),
        )
        return getattr(response, "output_text", "") or "No model reply was returned."
    except Exception as exc:
        return f"OpenAI reply failed, but the app is still running: {exc}"
