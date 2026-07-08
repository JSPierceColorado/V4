from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List

from alpaca_rest import AlpacaError, AlpacaRest


def _is_rate_limit_error(message: str) -> bool:
    lowered = message.lower()
    return "429" in lowered or "too many requests" in lowered or "rate limit" in lowered


def _is_transient_data_error(message: str) -> bool:
    lowered = message.lower()
    transient_markers = (
        "status=500",
        "status=502",
        "status=503",
        "status=504",
        "upstream error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "timeout",
        "temporarily unavailable",
        "connection aborted",
        "connection reset",
    )
    return any(marker in lowered for marker in transient_markers)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _sma(values: List[float], window: int) -> float | None:
    clean = [value for value in values if value > 0]
    if not clean:
        return None
    return mean(clean[-window:]) if len(clean) >= window else mean(clean)


def _ret_pct(values: List[float], days: int) -> float:
    if len(values) <= days:
        return 0.0
    previous = values[-days - 1]
    latest = values[-1]
    return ((latest / previous) - 1) * 100 if previous > 0 and latest > 0 else 0.0


def _atr_pct(highs: List[float], lows: List[float], closes: List[float], window: int = 14) -> float:
    ranges = []
    for index in range(1, len(closes)):
        high = highs[index]
        low = lows[index]
        previous_close = closes[index - 1]
        if high <= 0 or low <= 0 or previous_close <= 0:
            continue
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    latest = closes[-1] if closes else 0.0
    if not ranges or latest <= 0:
        return 0.0
    return mean(ranges[-window:]) / latest * 100


def _gap_pct(opens: List[float], closes: List[float]) -> float:
    if len(opens) < 1 or len(closes) < 2:
        return 0.0
    previous_close = closes[-2]
    latest_open = opens[-1]
    return ((latest_open / previous_close) - 1) * 100 if previous_close > 0 and latest_open > 0 else 0.0


def _score(row: Dict[str, Any]) -> float:
    score = 0.0
    close = row["close"]
    sma50 = row.get("sma_50")
    sma200 = row.get("sma_200")
    if sma50 and close > sma50:
        score += 18
    if sma200 and close > sma200:
        score += 18
    score += max(0.0, min(1.0, row.get("pos_52w") or 0.0)) * 25
    score += max(-10.0, min(20.0, row.get("ret_20d_pct") or 0.0))
    score += max(0.0, min(12.0, (row.get("volume_ratio") or 0.0) * 4))
    score += max(0.0, min(7.0, (row.get("dollar_vol_m") or 0.0) / 10))
    return round(score, 2)


def screen_symbols(
    alpaca: AlpacaRest,
    symbols: Iterable[str] | None,
    *,
    min_price: float = 2.0,
    min_dollar_vol_m: float = 1.0,
    max_results: int = 0,
    max_symbols_per_cycle: int = 0,
    offset: int = 0,
) -> Dict[str, Any]:
    if symbols is None:
        symbols = alpaca.active_tradable_us_equity_symbols()

    unique = []
    seen = set()
    for symbol in symbols:
        token = symbol.strip().upper()
        if token and token not in seen:
            seen.add(token)
            unique.append(token)

    universe_symbols = len(unique)
    normalized_offset = offset % universe_symbols if universe_symbols else 0
    if max_symbols_per_cycle > 0 and universe_symbols > max_symbols_per_cycle:
        rotated = unique[normalized_offset:] + unique[:normalized_offset]
        unique = rotated[:max_symbols_per_cycle]
        next_offset = (normalized_offset + len(unique)) % universe_symbols
    else:
        next_offset = 0

    start = (datetime.now(timezone.utc) - timedelta(days=390)).date().isoformat()
    candidates: List[Dict[str, Any]] = []
    rejected = 0
    warnings: List[str] = []
    rate_limited = False
    data_errors = 0
    for chunk in _chunks(unique, 50):
        try:
            response = alpaca.stock_bars(chunk, start=start)
        except AlpacaError as exc:
            message = str(exc)
            if _is_rate_limit_error(message):
                rate_limited = True
                warnings.append(message)
                break
            if _is_transient_data_error(message):
                data_errors += 1
                warnings.append(message)
                continue
            raise
        bars_by_symbol = response.get("bars") or {}
        for symbol, bars in bars_by_symbol.items():
            if not bars:
                rejected += 1
                continue
            opens = [_float(bar.get("o")) for bar in bars]
            highs = [_float(bar.get("h")) for bar in bars]
            lows = [_float(bar.get("l")) for bar in bars]
            closes = [_float(bar.get("c")) for bar in bars]
            volumes = [_float(bar.get("v")) for bar in bars]
            latest = bars[-1]
            close = _float(latest.get("c"))
            volume = _float(latest.get("v"))
            dollar_vol_m = close * volume / 1_000_000 if close and volume else 0.0
            if close < min_price or dollar_vol_m < min_dollar_vol_m:
                rejected += 1
                continue

            low_52w = min(closes[-252:]) if closes else close
            high_52w = max(closes[-252:]) if closes else close
            pos_52w = (close - low_52w) / (high_52w - low_52w) if high_52w > low_52w else 0.5
            avg_volume_20 = mean(volumes[-20:]) if volumes else 0.0
            dollar_values = [
                c * v / 1_000_000
                for c, v in zip(closes, volumes)
                if c > 0 and v > 0
            ]
            dollar_vol_20d_m = mean(dollar_values[-20:]) if dollar_values else 0.0
            range_20_pct = (
                ((max(highs[-20:]) / min(lows[-20:])) - 1) * 100
                if highs[-20:] and lows[-20:] and min(lows[-20:]) > 0
                else 0.0
            )
            row = {
                "symbol": symbol,
                "close": round(close, 4),
                "sma_20": round(_sma(closes, 20) or 0.0, 4),
                "sma_50": round(_sma(closes, 50) or 0.0, 4),
                "sma_200": round(_sma(closes, 200) or 0.0, 4),
                "pos_52w": round(pos_52w, 4),
                "dollar_vol_m": round(dollar_vol_m, 2),
                "dollar_vol_20d_m": round(dollar_vol_20d_m, 2),
                "volume_ratio": round(volume / avg_volume_20, 4) if avg_volume_20 else 0.0,
                "ret_1d_pct": round(_ret_pct(closes, 1), 2),
                "ret_5d_pct": round(_ret_pct(closes, 5), 2),
                "ret_10d_pct": round(_ret_pct(closes, 10), 2),
                "ret_20d_pct": round(_ret_pct(closes, 20), 2),
                "ret_60d_pct": round(_ret_pct(closes, 60), 2),
                "atr_14_pct": round(_atr_pct(highs, lows, closes), 2),
                "gap_pct": round(_gap_pct(opens, closes), 2),
                "range_20_pct": round(range_20_pct, 2),
                "bars": len(bars),
            }
            row["score"] = _score(row)
            candidates.append(row)

    candidates.sort(key=lambda item: item["score"], reverse=True)
    selected = candidates if max_results <= 0 else candidates[:max_results]
    return {
        "ok": True,
        "universe_symbols": universe_symbols,
        "symbols_checked": len(unique),
        "screen_offset": normalized_offset,
        "next_screen_offset": next_offset,
        "rate_limited": rate_limited,
        "data_errors": data_errors,
        "warnings": warnings,
        "rejected": rejected,
        "candidates": selected,
    }
