from typing import Any, Dict, Optional


TERMINAL_ORDER_STATUSES = {
    "canceled",
    "expired",
    "filled",
    "rejected",
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def duplicate_order_reason(
    state: Dict[str, Any],
    *,
    symbol: str,
    side: str,
) -> Optional[str]:
    target = str(symbol or "").strip().upper()
    normalized_side = str(side or "").strip().lower()
    if not target or normalized_side not in {"buy", "sell"}:
        return None

    for order in state.get("open_orders") or []:
        order_symbol = str(order.get("symbol", "")).strip().upper()
        status = str(order.get("status", "open")).strip().lower()
        if order_symbol == target and status not in TERMINAL_ORDER_STATUSES:
            return f"{target} already has an open order; skipping duplicate order entry."

    if normalized_side == "buy":
        for position in state.get("positions") or []:
            position_symbol = str(position.get("symbol", "")).strip().upper()
            if position_symbol == target and _float(position.get("qty")) > 0:
                return f"{target} is already held; skipping duplicate buy entry."

    return None
