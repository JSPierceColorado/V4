from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from alpaca_rest import AlpacaError

MARKET_TZ = ZoneInfo("America/New_York")


def coerce_is_open(value: Any) -> Optional[bool]:
    """Return a real bool for common API/test encodings, or None if unknown."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "open"}:
            return True
        if normalized in {"false", "0", "no", "n", "closed"}:
            return False
    return None


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_calendar_datetime(session_date: date, value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")

    parsed_dt = _parse_iso_datetime(raw)
    if parsed_dt is not None:
        return parsed_dt

    try:
        parsed_time = time.fromisoformat(raw)
    except ValueError:
        return None
    return datetime.combine(session_date, parsed_time, tzinfo=MARKET_TZ).astimezone(timezone.utc)


def _calendar_session_state(alpaca: Any, *, now: Optional[datetime] = None) -> Tuple[Optional[bool], Dict[str, Any]]:
    """
    Ask Alpaca's calendar for today's official regular session.

    Returns (is_open, metadata). is_open is None when no reliable calendar answer could
    be obtained. The calendar endpoint handles market holidays and early closes.
    """
    if not hasattr(alpaca, "calendar"):
        return None, {"calendar_source": "unavailable", "calendar_error": "alpaca.calendar missing"}

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    market_day = current.astimezone(MARKET_TZ).date()
    day = market_day.isoformat()

    try:
        rows = alpaca.calendar(start=day, end=day)
    except AlpacaError as exc:
        return None, {"calendar_source": "alpaca_calendar_error", "calendar_error": str(exc)}
    except Exception as exc:
        return None, {"calendar_source": "calendar_error", "calendar_error": str(exc)}

    if not rows:
        return False, {
            "calendar_source": "alpaca_calendar",
            "calendar_date": day,
            "calendar_reason": "no_regular_session",
        }

    row = rows[0] if isinstance(rows, list) else rows
    if not isinstance(row, dict):
        return None, {
            "calendar_source": "alpaca_calendar",
            "calendar_date": day,
            "calendar_error": "unexpected_calendar_shape",
        }

    session_date_raw = str(row.get("date") or day).strip()[:10]
    try:
        session_date = date.fromisoformat(session_date_raw)
    except ValueError:
        session_date = market_day

    open_at = _parse_calendar_datetime(session_date, row.get("open"))
    close_at = _parse_calendar_datetime(session_date, row.get("close"))
    if open_at is None or close_at is None:
        return None, {
            "calendar_source": "alpaca_calendar",
            "calendar_date": session_date.isoformat(),
            "calendar_error": "missing_or_invalid_open_close",
            "calendar_open_raw": row.get("open"),
            "calendar_close_raw": row.get("close"),
        }

    return open_at <= current < close_at, {
        "calendar_source": "alpaca_calendar",
        "calendar_date": session_date.isoformat(),
        "calendar_open": open_at.isoformat(),
        "calendar_close": close_at.isoformat(),
    }


def normalize_market_clock(
    alpaca: Any,
    raw_clock: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Normalize Alpaca clock responses for the autonomy engine.

    Alpaca's clock is the primary source. If it is missing/malformed, or if it reports
    closed while Alpaca's own calendar says the current time is inside today's regular
    session, use the calendar as a defensive fallback so the bot does not wait forever
    on a bad clock value.
    """
    clock: Dict[str, Any] = dict(raw_clock or {}) if isinstance(raw_clock, dict) else {}
    raw_is_open = clock.get("is_open")
    alpaca_is_open = coerce_is_open(raw_is_open)
    if alpaca_is_open is not None:
        clock["alpaca_is_open"] = alpaca_is_open
        clock["is_open"] = alpaca_is_open
        clock.setdefault("clock_source", "alpaca_clock")
    else:
        clock["is_open"] = False
        clock["clock_source"] = "invalid_or_missing_alpaca_clock"
        clock["clock_warning"] = f"Unrecognized Alpaca is_open value: {raw_is_open!r}"

    calendar_is_open, calendar_meta = _calendar_session_state(alpaca, now=now)
    clock.update(calendar_meta)

    if alpaca_is_open is None and calendar_is_open is not None:
        clock["is_open"] = calendar_is_open
        clock["clock_source"] = "alpaca_calendar_fallback"
        clock["clock_fallback_reason"] = "alpaca_clock_missing_or_malformed"
    elif alpaca_is_open is False and calendar_is_open is True:
        clock["is_open"] = True
        clock["clock_source"] = "alpaca_calendar_fallback"
        clock["clock_fallback_reason"] = "alpaca_clock_reported_closed_inside_regular_session"

    return clock


def get_market_clock(alpaca: Any, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_clock = None
    if isinstance(state, dict) and isinstance(state.get("clock"), dict):
        raw_clock = state.get("clock")
    if raw_clock is None:
        if not hasattr(alpaca, "clock"):
            raw_clock = {"is_open": True, "clock_source": "test_default"}
        else:
            try:
                raw_clock = alpaca.clock()
            except AlpacaError as exc:
                raw_clock = {
                    "is_open": None,
                    "clock_error": str(exc),
                    "clock_source": "alpaca_clock_error",
                }
    return normalize_market_clock(alpaca, raw_clock)


def is_market_open(clock: Dict[str, Any]) -> bool:
    return bool(coerce_is_open(clock.get("is_open")) is True)
