from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any, Dict, Iterable, List

from alpaca_rest import AlpacaRest


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
    symbols: Iterable[str],
    *,
    min_price: float = 2.0,
    min_dollar_vol_m: float = 1.0,
    max_results: int = 50,
) -> Dict[str, Any]:
    unique = []
    seen = set()
    for symbol in symbols:
        token = symbol.strip().upper()
        if token and token not in seen:
            seen.add(token)
            unique.append(token)

    start = (datetime.now(timezone.utc) - timedelta(days=390)).date().isoformat()
    candidates: List[Dict[str, Any]] = []
    rejected = 0
    for chunk in _chunks(unique, 50):
        response = alpaca.stock_bars(chunk, start=start)
        bars_by_symbol = response.get("bars") or {}
        for symbol, bars in bars_by_symbol.items():
            if not bars:
                rejected += 1
                continue
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
            ret_20d_pct = ((close / closes[-21]) - 1) * 100 if len(closes) > 21 and closes[-21] else 0.0
            row = {
                "symbol": symbol,
                "close": round(close, 4),
                "sma_50": round(_sma(closes, 50) or 0.0, 4),
                "sma_200": round(_sma(closes, 200) or 0.0, 4),
                "pos_52w": round(pos_52w, 4),
                "dollar_vol_m": round(dollar_vol_m, 2),
                "volume_ratio": round(volume / avg_volume_20, 4) if avg_volume_20 else 0.0,
                "ret_20d_pct": round(ret_20d_pct, 2),
                "bars": len(bars),
            }
            row["score"] = _score(row)
            candidates.append(row)

    candidates.sort(key=lambda item: item["score"], reverse=True)
    return {
        "ok": True,
        "symbols_checked": len(unique),
        "rejected": rejected,
        "candidates": candidates[:max_results],
    }
