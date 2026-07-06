from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import Settings
from storage import utc_now
from v4_doctrine import STRATEGY_DSL_GUIDE, V4_DOCTRINE


def _compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    account = state.get("account") or {}
    positions = state.get("positions") or []
    open_orders = state.get("open_orders") or []
    return {
        "clock": state.get("clock") or {},
        "account": {
            "equity": account.get("equity"),
            "buying_power": account.get("buying_power"),
            "cash": account.get("cash"),
        },
        "positions": [
            {
                "symbol": pos.get("symbol"),
                "qty": pos.get("qty"),
                "market_value": pos.get("market_value"),
                "unrealized_plpc": pos.get("unrealized_plpc"),
            }
            for pos in positions[:20]
        ],
        "open_orders": [
            {
                "symbol": order.get("symbol"),
                "side": order.get("side"),
                "status": order.get("status"),
            }
            for order in open_orders[:20]
        ],
    }


def _top_candidates(screen: Optional[Dict[str, Any]], limit: int = 15) -> List[Dict[str, Any]]:
    candidates = (screen or {}).get("candidates") or []
    return [
        {
            "symbol": row.get("symbol"),
            "close": row.get("close"),
            "score": row.get("score"),
            "ret_5d_pct": row.get("ret_5d_pct"),
            "ret_20d_pct": row.get("ret_20d_pct"),
            "ret_60d_pct": row.get("ret_60d_pct"),
            "volume_ratio": row.get("volume_ratio"),
            "dollar_vol_20d_m": row.get("dollar_vol_20d_m"),
            "atr_14_pct": row.get("atr_14_pct"),
            "range_20_pct": row.get("range_20_pct"),
        }
        for row in candidates[:limit]
    ]


def _response_text(settings: Settings, prompt: str, *, search_context_size: str = "medium") -> str:
    if not settings.openai_ready:
        return ""
    try:
        from openai import OpenAI
    except ImportError:
        return ""

    client = OpenAI(api_key=settings.openai_api_key)
    tool_variants = (
        [{"type": "web_search", "search_context_size": search_context_size}],
        [{"type": "web_search_preview", "search_context_size": search_context_size}],
    )
    last_error: Optional[Exception] = None
    for tools in tool_variants:
        try:
            response = client.responses.create(
                model=settings.openai_model,
                tools=tools,
                input=prompt,
            )
            return getattr(response, "output_text", "") or ""
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"web intelligence request failed: {last_error}")


def market_brief(
    settings: Settings,
    *,
    state: Dict[str, Any],
    screen: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not settings.openai_ready:
        return {
            "ok": True,
            "skipped": True,
            "reason": "openai_not_configured",
            "reply": "Market brief skipped because OPENAI_API_KEY is not configured.",
        }
    payload = {
        "timestamp": utc_now(),
        "doctrine": V4_DOCTRINE,
        "account_state": _compact_state(state),
        "top_screen_candidates": _top_candidates(screen),
    }
    prompt = (
        "You are v4's market intelligence analyst. Search the web for current market "
        "context that could matter to a US equity paper-trading agent today. "
        "Synthesize regime, sector themes, catalysts, risks, and what the agent should "
        "research or avoid next. Do not give investment advice; frame this as paper "
        "trading research guidance.\n\n"
        f"Context:\n{json.dumps(payload, default=str)[:20000]}"
    )
    try:
        text = _response_text(settings, prompt, search_context_size="medium")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reply": str(exc)}
    return {"ok": True, "reply": text, "brief": text, "generated_at": utc_now()}


def symbol_research(
    settings: Settings,
    *,
    symbol: str,
    state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        return {"ok": False, "error": "symbol_required", "reply": "A symbol is required."}
    if not settings.openai_ready:
        return {
            "ok": True,
            "skipped": True,
            "reason": "openai_not_configured",
            "reply": "Symbol research skipped because OPENAI_API_KEY is not configured.",
        }
    prompt = (
        f"Search the web for current, trade-relevant context on {symbol}. "
        "Return a concise paper-trading research note with catalysts, risks, sentiment, "
        "near-term events, and which strategy families might be worth testing. "
        "Do not recommend a trade directly.\n\n"
        f"Account context:\n{json.dumps(_compact_state(state or {}), default=str)[:12000]}"
    )
    try:
        text = _response_text(settings, prompt, search_context_size="medium")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reply": str(exc)}
    return {"ok": True, "symbol": symbol, "reply": text, "research": text, "generated_at": utc_now()}


def strategy_ideas(
    settings: Settings,
    *,
    state: Dict[str, Any],
    screen: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not settings.openai_ready:
        return {
            "ok": True,
            "skipped": True,
            "reason": "openai_not_configured",
            "reply": "Strategy ideas skipped because OPENAI_API_KEY is not configured.",
        }
    payload = {
        "timestamp": utc_now(),
        "doctrine": V4_DOCTRINE,
        "strategy_dsl": STRATEGY_DSL_GUIDE,
        "account_state": _compact_state(state),
        "top_screen_candidates": _top_candidates(screen, limit=25),
    }
    prompt = (
        "Search the web for current US equity market themes and propose strategy theses "
        "for v4 to test. Include simple and weird ideas. Return prose plus a small JSON "
        "block of DSL-style candidate concepts if useful. These are research ideas only; "
        "the backtester must validate them before deployment.\n\n"
        f"Context:\n{json.dumps(payload, default=str)[:30000]}"
    )
    try:
        text = _response_text(settings, prompt, search_context_size="high")
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reply": str(exc)}
    return {"ok": True, "reply": text, "ideas": text, "generated_at": utc_now()}
