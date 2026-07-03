import csv
import io
from pathlib import Path
from typing import Any, Dict, List

from storage import extract_symbols, pick_score_column, pick_symbol_column


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_csv_bytes(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows: List[Dict[str, str]] = [dict(row) for row in reader if row]
    headers = reader.fieldnames or []
    symbol_col = pick_symbol_column(headers)
    score_col = pick_score_column(headers)

    candidates = []
    if symbol_col:
        for row in rows:
            symbol = str(row.get(symbol_col, "")).strip().upper()
            if not symbol:
                continue
            candidates.append(
                {
                    "symbol": symbol,
                    "score": _to_float(row.get(score_col)) if score_col else None,
                    "row": row,
                }
            )
    else:
        for symbol in extract_symbols(text):
            candidates.append({"symbol": symbol, "score": None, "row": {}})

    if score_col:
        candidates.sort(
            key=lambda item: item["score"] if item["score"] is not None else float("-inf"),
            reverse=True,
        )

    return {
        "kind": "csv",
        "headers": headers,
        "row_count": len(rows),
        "symbol_column": symbol_col,
        "score_column": score_col,
        "symbols": [item["symbol"] for item in candidates[:100]],
        "top_candidates": candidates[:25],
        "text_preview": text[:2000],
    }


def parse_pdf_bytes(raw: bytes) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support requires pypdf") from exc

    reader = PdfReader(io.BytesIO(raw))
    page_texts = []
    for page in reader.pages[:25]:
        page_texts.append(page.extract_text() or "")
    text = "\n".join(page_texts)
    symbols = extract_symbols(text)
    return {
        "kind": "pdf",
        "page_count": len(reader.pages),
        "symbols": symbols[:100],
        "top_candidates": [
            {"symbol": symbol, "score": None, "row": {}} for symbol in symbols[:25]
        ],
        "text_preview": text[:3000],
    }


def parse_upload(filename: str, raw: bytes, content_type: str = "") -> Dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv" or "csv" in content_type:
        return parse_csv_bytes(raw)
    if suffix == ".pdf" or "pdf" in content_type:
        return parse_pdf_bytes(raw)
    text = raw.decode("utf-8", errors="replace")
    symbols = extract_symbols(text)
    return {
        "kind": "text",
        "symbols": symbols[:100],
        "top_candidates": [
            {"symbol": symbol, "score": None, "row": {}} for symbol in symbols[:25]
        ],
        "text_preview": text[:3000],
    }
