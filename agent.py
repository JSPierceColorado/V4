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


def friendly_openai_error(exc: Exception) -> str:
    text = str(exc)
    if "insufficient_quota" in text or "exceeded your current quota" in text:
        return (
            "OpenAI quota is exhausted or billing is not active. I can still use "
            "rule-based commands and Alpaca controls, but natural-language reasoning "
            "needs API billing/credits."
        )
    if "model_not_found" in text or "does not exist" in text:
        return (
            "The configured OpenAI model is not available to this API key. Set "
            "OPENAI_MODEL to gpt-5.2, gpt-5-mini, or another model your account can use."
        )
    return f"OpenAI request failed, but the app is still running: {exc}"


def rule_parse(message: str) -> Dict[str, Any]:
    lowered = message.lower()
    if any(phrase in lowered for phrase in ("cancel all", "cancel orders")):
        return {"action": "cancel_all_orders", "args": {}}
    if any(phrase in lowered for phrase in ("close all", "liquidate", "flatten")):
        return {"action": "close_all_positions", "args": {}}
    if any(word in lowered for word in ("metrics", "dashboard", "graph", "graphs", "chart", "charts", "performance")):
        return {"action": "metrics", "args": {}}
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

    rule_result = rule_parse(message)
    if rule_result.get("action") != "reply":
        return rule_result

    try:
        from openai import OpenAI
    except ImportError:
        return rule_parse(message)

    client = OpenAI(api_key=settings.openai_api_key)
    system = (
        "You are v4, a concise paper-trading assistant and command parser. "
        "Return only JSON with keys action and args. "
        "Allowed actions: state, metrics, analyze, run, place_order, cancel_all_orders, "
        "close_all_positions, reply. "
        "Only choose place_order when the user clearly asks to place a paper order. "
        "For place_order args include symbol, side, qty, notional, order_type, "
        "time_in_force, limit_price, stop_price, extended_hours when known. "
        "For reply, put the final user-facing answer in args.text. "
        "Write like a capable, direct trading copilot. Be warm, but avoid pretending "
        "to be human. Do not lead with disclaimers unless safety requires it. "
        "For casual questions about thinking, say that you can reason over the "
        "available data, inspect the paper account, run checks, and act through "
        "the app's tools."
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
    except Exception as exc:
        fallback = rule_parse(message)
        fallback["warning"] = friendly_openai_error(exc)
        return fallback
    return rule_result


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
                "You are v4, a concise paper-trading copilot connected to an Alpaca "
                "paper-trading app. Be natural, capable, and direct. You can reason "
                "over uploaded CSV/PDF context, account state, positions, orders, and "
                "market data made available by the app. Do not claim to have placed "
                "orders unless tool results show that happened. Avoid financial advice; "
                "frame trade ideas as paper-trading experiments and explain next actions clearly. "
                f"Context: {json.dumps(context, default=str)[:12000]}\n\n"
                f"User: {message}"
            ),
        )
        return getattr(response, "output_text", "") or "No model reply was returned."
    except Exception as exc:
        return friendly_openai_error(exc)
