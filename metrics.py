from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from alpaca_rest import AlpacaRest


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def _money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _timestamp_label(value: Any) -> str:
    try:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return dt.strftime("%b %-d")
    except Exception:
        return str(value)


def _history_points(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    timestamps = history.get("timestamp") or []
    equity = history.get("equity") or []
    profit_loss = history.get("profit_loss") or []
    points = []
    for idx, value in enumerate(equity):
        eq = _float(value, default=float("nan"))
        if eq != eq:
            continue
        points.append(
            {
                "label": _timestamp_label(timestamps[idx]) if idx < len(timestamps) else str(idx + 1),
                "equity": eq,
                "profit_loss": _float(profit_loss[idx]) if idx < len(profit_loss) else None,
            }
        )
    return points


def _drawdown_points(equity_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    peak: Optional[float] = None
    points = []
    for point in equity_points:
        equity = _float(point.get("equity"))
        peak = equity if peak is None else max(peak, equity)
        drawdown = ((equity / peak) - 1) * 100 if peak else 0.0
        points.append({"label": point["label"], "drawdown": drawdown})
    return points


def _projected_points(equity_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not equity_points:
        return []
    latest = _float(equity_points[-1].get("equity"))
    recent = equity_points[-7:] if len(equity_points) >= 7 else equity_points
    if len(recent) >= 2:
        slope = (_float(recent[-1]["equity"]) - _float(recent[0]["equity"])) / (len(recent) - 1)
    else:
        slope = 0.0
    return [
        {"label": "Now", "equity": latest},
        {"label": "+7d", "equity": latest + slope * 7},
        {"label": "+30d", "equity": latest + slope * 30},
    ]


def _top_positions(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(
        positions,
        key=lambda pos: abs(_float(pos.get("market_value"))),
        reverse=True,
    )
    return [
        {
            "symbol": pos.get("symbol"),
            "market_value": _float(pos.get("market_value")),
            "unrealized_pl": _float(pos.get("unrealized_pl")),
            "unrealized_plpc": _float(pos.get("unrealized_plpc")) * 100,
        }
        for pos in ranked[:10]
    ]


def build_metrics(alpaca: AlpacaRest) -> Dict[str, Any]:
    state = alpaca.state()
    account = state["account"]
    positions = state["positions"]
    open_orders = state["open_orders"]
    history = alpaca.portfolio_history()

    equity = _float(account.get("equity"))
    last_equity = _float(account.get("last_equity"))
    cash = _float(account.get("cash"))
    buying_power = _float(account.get("buying_power"))
    long_market_value = sum(max(_float(pos.get("market_value")), 0.0) for pos in positions)
    short_market_value = abs(sum(min(_float(pos.get("market_value")), 0.0) for pos in positions))
    gross_market_value = long_market_value + short_market_value
    open_unrealized = sum(_float(pos.get("unrealized_pl")) for pos in positions)
    green = sum(1 for pos in positions if _float(pos.get("unrealized_pl")) > 0)
    red = sum(1 for pos in positions if _float(pos.get("unrealized_pl")) < 0)

    loser = min(positions, key=lambda pos: _float(pos.get("unrealized_plpc")), default=None)
    winner = max(positions, key=lambda pos: _float(pos.get("unrealized_plpc")), default=None)

    equity_points = _history_points(history)
    drawdown_points = _drawdown_points(equity_points)
    current_drawdown = drawdown_points[-1]["drawdown"] if drawdown_points else None
    max_drawdown = min((point["drawdown"] for point in drawdown_points), default=None)
    day_pl = equity - last_equity if last_equity else None
    day_pl_pct = ((equity / last_equity) - 1) * 100 if last_equity else None
    exposure_pct = (gross_market_value / equity) * 100 if equity else None
    cash_pct = (cash / equity) * 100 if equity else None
    margin_use_pct = (
        (_float(account.get("initial_margin")) / equity) * 100
        if equity and account.get("initial_margin") is not None
        else None
    )

    tiles = [
        {"label": "Equity", "value": _money(equity), "note": f"Status: {account.get('status', 'unknown')}"},
        {"label": "Day P/L", "value": _money(day_pl), "note": _pct(day_pl_pct)},
        {"label": "Buying Power", "value": _money(buying_power), "note": f"Cash: {_money(cash)}"},
        {"label": "Open Unrealized P/L", "value": _money(open_unrealized), "note": f"Positions: {len(positions)}"},
        {"label": "Exposure", "value": _pct(exposure_pct), "note": f"Cash: {_pct(cash_pct)}"},
        {"label": "Margin Use", "value": _pct(margin_use_pct), "note": f"Open orders: {len(open_orders)}"},
        {"label": "Drawdown", "value": _pct(current_drawdown), "note": f"Max: {_pct(max_drawdown)}"},
        {"label": "Red / Green", "value": f"{red} / {green}", "note": "Open positions"},
        {
            "label": "Largest Loser / Winner",
            "value": f"{loser.get('symbol') if loser else 'n/a'} / {winner.get('symbol') if winner else 'n/a'}",
            "note": (
                f"{_pct(_float(loser.get('unrealized_plpc')) * 100) if loser else 'n/a'} / "
                f"{_pct(_float(winner.get('unrealized_plpc')) * 100) if winner else 'n/a'}"
            ),
        },
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": (
            f"Equity {_money(equity)}. Day P/L {_money(day_pl)} ({_pct(day_pl_pct)}). "
            f"Open unrealized {_money(open_unrealized)} across {len(positions)} positions. "
            f"Exposure {_pct(exposure_pct)}."
        ),
        "tiles": tiles,
        "charts": {
            "equity": equity_points,
            "drawdown": drawdown_points,
            "daily_pl": [
                {"label": point["label"], "profit_loss": point.get("profit_loss")}
                for point in equity_points
                if point.get("profit_loss") is not None
            ],
            "projected_equity": _projected_points(equity_points),
            "top_positions": _top_positions(positions),
        },
        "raw": {
            "account": account,
            "positions_count": len(positions),
            "open_orders_count": len(open_orders),
        },
    }
