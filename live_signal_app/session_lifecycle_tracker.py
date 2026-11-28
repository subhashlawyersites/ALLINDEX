from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
APP_DIR = Path(__file__).resolve().parent
LIFECYCLE_LOG_PATH = APP_DIR / "session_lifecycle_events.json"

MANDATORY_FIELDS = (
    "session_date",
    "trade_id",
    "event_type",
    "event_ts_ist",
    "recorded_at_ist",
    "direction",
    "level_pct",
    "price",
    "message",
    "index",
)


def _load_events(path: Path = LIFECYCLE_LOG_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return [row for row in data["events"] if isinstance(row, dict)]
    except Exception:
        pass
    return []


def _save_events(events: list[dict[str, Any]], path: Path = LIFECYCLE_LOG_PATH) -> None:
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")


def format_event_ts_ist(raw_ts: str | None, session_date: str, fallback_dt: datetime) -> str:
    txt = str(raw_ts or "").strip()
    if not txt:
        return fallback_dt.strftime("%Y-%m-%d %H:%M IST")
    if "IST" in txt:
        return txt
    if len(txt) >= 10 and txt[4] == "-" and txt[7] == "-":
        return f"{txt[:16]} IST" if len(txt) >= 16 else f"{txt} IST"
    return f"{session_date} {txt[:5]} IST"


def next_trade_id(index_key: str, session_date: str, events: list[dict[str, Any]] | None = None) -> str:
    idx_up = index_key.upper()
    rows = events if events is not None else _load_events()
    existing = {
        str(ev.get("trade_id", ""))
        for ev in rows
        if ev.get("index") == idx_up and ev.get("session_date") == session_date and ev.get("trade_id")
    }
    seq = len(existing) + 1
    candidate = f"{idx_up}-{session_date}-T{seq}"
    while candidate in existing:
        seq += 1
        candidate = f"{idx_up}-{session_date}-T{seq}"
    return candidate


def append_lifecycle_event(
    *,
    index_key: str,
    event_type: str,
    event_ts_ist: str | None,
    message: str,
    session_date: str | None = None,
    trade_id: str | None = None,
    direction: str | None = None,
    level_pct: float | int | None = None,
    price: float | int | None = None,
) -> dict[str, Any]:
    now_ist = datetime.now(IST)
    raw_ts = str(event_ts_ist or "").strip()
    inferred_date = raw_ts[:10] if len(raw_ts) >= 10 and raw_ts[4] == "-" and raw_ts[7] == "-" else ""
    sess_d = (session_date or inferred_date or now_ist.strftime("%Y-%m-%d")).strip()

    all_events = [ev for ev in _load_events() if str(ev.get("session_date", "")) == sess_d]
    formatted_event_ts = format_event_ts_ist(raw_ts, sess_d, now_ist)

    record: dict[str, Any] = {
        "session_date": sess_d,
        "trade_id": trade_id,
        "event_type": str(event_type).upper(),
        "event_ts_ist": formatted_event_ts,
        "recorded_at_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "direction": direction.upper() if isinstance(direction, str) and direction.strip() else None,
        "level_pct": level_pct,
        "price": float(price) if price is not None else None,
        "message": message,
        "index": index_key.upper(),
    }
    all_events.append(record)
    _save_events(all_events)
    return record


def get_session_lifecycle_events(session_date: str | None = None, index_key: str | None = None) -> list[dict[str, Any]]:
    events = _load_events()
    if not events:
        return []
    target_date = (session_date or str(events[-1].get("session_date") or datetime.now(IST).strftime("%Y-%m-%d"))).strip()
    filtered = [ev for ev in events if str(ev.get("session_date", "")) == target_date]
    if index_key:
        idx_up = index_key.upper()
        filtered = [ev for ev in filtered if str(ev.get("index", "")).upper() == idx_up]
    return filtered
