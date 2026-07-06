import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9]{0,4}(?:\.[A-Z])?\b")
COMMON_FALSE_SYMBOLS = {
    "A",
    "AI",
    "API",
    "CSV",
    "ETF",
    "GET",
    "HTTP",
    "JSON",
    "LLM",
    "PDF",
    "POST",
    "SEC",
    "THE",
    "USD",
}


@dataclass(frozen=True)
class UploadRecord:
    upload_id: str
    filename: str
    content_type: str
    path: str
    created_at: str
    kind: str
    summary: Dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_data_dirs(data_dir: str) -> Dict[str, Path]:
    root = Path(data_dir)
    uploads = root / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    uploads.mkdir(parents=True, exist_ok=True)
    return {"root": root, "uploads": uploads}


def append_event(data_dir: str, event_type: str, payload: Dict[str, Any]) -> None:
    paths = ensure_data_dirs(data_dir)
    event = {
        "ts": utc_now(),
        "type": event_type,
        "payload": payload,
    }
    with (paths["root"] / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, default=str, sort_keys=True) + "\n")


def load_events(data_dir: str, limit: int = 25) -> List[Dict[str, Any]]:
    paths = ensure_data_dirs(data_dir)
    event_path = paths["root"] / "events.jsonl"
    if not event_path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with event_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events[-limit:]


def save_upload_record(data_dir: str, record: UploadRecord) -> None:
    paths = ensure_data_dirs(data_dir)
    index_path = paths["root"] / "uploads.jsonl"
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def load_uploads(data_dir: str) -> List[UploadRecord]:
    paths = ensure_data_dirs(data_dir)
    index_path = paths["root"] / "uploads.jsonl"
    if not index_path.exists():
        return []
    records: List[UploadRecord] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(UploadRecord(**json.loads(line)))
    return records


def latest_upload(data_dir: str) -> Optional[UploadRecord]:
    records = load_uploads(data_dir)
    return records[-1] if records else None


def new_upload_path(data_dir: str, filename: str) -> tuple[str, Path]:
    paths = ensure_data_dirs(data_dir)
    suffix = Path(filename).suffix.lower()
    upload_id = f"up_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    return upload_id, paths["uploads"] / f"{upload_id}{suffix}"


def extract_symbols(text: str) -> List[str]:
    found = []
    seen = set()
    for match in SYMBOL_RE.finditer(text.upper()):
        symbol = match.group(0)
        if symbol in COMMON_FALSE_SYMBOLS or symbol in seen:
            continue
        seen.add(symbol)
        found.append(symbol)
    return found


def pick_symbol_column(headers: Iterable[str]) -> Optional[str]:
    normalized = {h.lower().strip(): h for h in headers}
    for candidate in ("symbol", "ticker", "asset", "stock"):
        if candidate in normalized:
            return normalized[candidate]
    return None


def pick_score_column(headers: Iterable[str]) -> Optional[str]:
    lower_map = {h.lower().strip(): h for h in headers}
    preferred = ("score", "rank", "rating", "strength", "signal")
    for name in preferred:
        if name in lower_map:
            return lower_map[name]
    for header in headers:
        lower = header.lower()
        if "score" in lower or "rank" in lower:
            return header
    return None
