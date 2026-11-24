from datetime import datetime
import json
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

ACTIVE_TRADES_PATH = APP_DIR / "active_trades.json"
EVENTS_LOG_PATH = APP_DIR / "critical_events.json"
LIVE_TRADES_CSV = REPORT_DIR / "live_monitored_trades.csv"

LIVE_COLUMNS = [
    "index", "session_date", "weekday", "entry_time", "exit_time",
    "direction", "entry_price", "sl_price", "target_price", "exit_price",
    "exit_reason", "points", "r_val", "source_tag"
]


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_event(index_key: str, event_type: str, message: str, severity: str = "warn") -> dict:
    events = _load_json(EVENTS_LOG_PATH, [])
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    ev = {
        "id": f"{int(datetime.now(IST).timestamp() * 1000)}_{len(events) + 1}",
        "time": now_ist,
        "index": index_key.upper(),
        "type": event_type,
        "severity": severity,
        "message": message,
    }
    if not events or events[-1].get("type") != event_type or events[-1].get("message") != message or events[-1].get("index") != index_key.upper():
        events.append(ev)
        events = events[-100:]
        _save_json(EVENTS_LOG_PATH, events)
    return ev


def get_recent_events(limit: int = 25) -> list[dict]:
    events = _load_json(EVENTS_LOG_PATH, [])
    return list(reversed(events[-limit:]))


def get_active_trades() -> dict:
    data = _load_json(ACTIVE_TRADES_PATH, {})
    return data if isinstance(data, dict) else {}


def start_monitored_trade(
    index_key: str,
    direction: str,
    entry_price: float,
    sl_price: float,
    target_price: Optional[float] = None,
    session_date: Optional[str] = None,
    weekday: Optional[str] = None,
    entry_time: Optional[str] = None,
) -> dict:
    key = index_key.upper()
    d = direction.upper()
    e_val = float(entry_price)
    s_val = float(sl_price)
    risk = abs(e_val - s_val) if abs(e_val - s_val) > 0 else 1.0
    if target_price is None:
        t_val = (e_val + 0.60 * risk) if d == "CALL" else (e_val - 0.60 * risk)
    else:
        t_val = float(target_price)

    now_ist = datetime.now(IST)
    trades = get_active_trades()
    rec = {
        "index": key,
        "direction": d,
        "session_date": session_date or now_ist.strftime("%Y-%m-%d"),
        "weekday": weekday or now_ist.strftime("%A"),
        "entry_time": entry_time or now_ist.strftime("%H:%M"),
        "entry_price": round(e_val, 2),
        "sl_price": round(s_val, 2),
        "target_price": round(t_val, 2),
        "risk_pts": round(risk, 2),
        "opened_at": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "milestones_hit": [],
    }
    trades[key] = rec
    _save_json(ACTIVE_TRADES_PATH, trades)
    record_event(key, "TRADE_MONITOR_STARTED", f"Monitoring {d} entry={rec['entry_price']} SL={rec['sl_price']} Target={rec['target_price']}", "good")
    return rec


def _append_closed_trade_to_csv(rec: dict, exit_price: float, exit_reason: str, exit_time: str) -> dict:
    d = rec["direction"].upper()
    e_val = float(rec["entry_price"])
    s_val = float(rec["sl_price"])
    x_val = float(exit_price)
    risk = float(rec.get("risk_pts") or abs(e_val - s_val) or 1.0)

    pts = (x_val - e_val) if d == "CALL" else (e_val - x_val)
    r_val = pts / risk

    row = {
        "index": rec["index"],
        "session_date": rec["session_date"],
        "weekday": rec["weekday"],
        "entry_time": rec["entry_time"],
        "exit_time": exit_time,
        "direction": d,
        "entry_price": round(e_val, 2),
        "sl_price": round(s_val, 2),
        "target_price": round(float(rec["target_price"]), 2),
        "exit_price": round(x_val, 2),
        "exit_reason": exit_reason,
        "points": round(pts, 2),
        "r_val": round(r_val, 2),
        "source_tag": "LIVE_MONITOR",
    }

    existing = pd.read_csv(LIVE_TRADES_CSV) if LIVE_TRADES_CSV.exists() else pd.DataFrame(columns=LIVE_COLUMNS)
    updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    updated.to_csv(LIVE_TRADES_CSV, index=False)
    return row


def close_monitored_trade(index_key: str, exit_price: float, exit_reason: str = "MANUAL_EXIT", exit_time: Optional[str] = None) -> Optional[dict]:
    key = index_key.upper()
    trades = get_active_trades()
    rec = trades.pop(key, None)
    if not rec:
        return None
    _save_json(ACTIVE_TRADES_PATH, trades)
    ex_t = exit_time or datetime.now(IST).strftime("%H:%M")
    closed_row = _append_closed_trade_to_csv(rec, float(exit_price), exit_reason, ex_t)
    sev = "good" if closed_row["points"] >= 0 else "bad"
    record_event(
        key,
        exit_reason,
        f"Closed {rec['direction']} @ {closed_row['exit_price']} ({closed_row['points']:+.2f} pts / {closed_row['r_val']:+.2f}R)",
        sev,
    )
    return closed_row


def evaluate_monitored_trades_against_market(latest_prices: dict[str, dict]) -> list[dict]:
    """
    Checks active monitored trades against latest candle/spot data and auto-closes on
    SL, 0.60R Target, or 15:15 EOD exit while recording milestone alerts.
    """
    trades = get_active_trades()
    if not trades:
        return []

    changed = False
    newly_closed = []

    for key, rec in list(trades.items()):
        snap = latest_prices.get(key)
        if not snap:
            continue
        high_v = float(snap.get("high", snap.get("close", 0.0)))
        low_v = float(snap.get("low", snap.get("close", 0.0)))
        close_v = float(snap.get("close", 0.0))
        bar_t = str(snap.get("time_str", datetime.now(IST).strftime("%H:%M")))

        d = rec["direction"].upper()
        sl_v = float(rec["sl_price"])
        tgt_v = float(rec["target_price"])
        entry_v = float(rec["entry_price"])
        risk = max(0.01, float(rec.get("risk_pts") or abs(entry_v - sl_v) or 1.0))

        # Check milestones (50%, 60%, 80%, 100%)
        hit_set = set(rec.get("milestones_hit", []))
        for pct in [50, 60, 80, 100]:
            lvl = (entry_v + (pct / 100.0) * risk) if d == "CALL" else (entry_v - (pct / 100.0) * risk)
            reached = (high_v >= lvl) if d == "CALL" else (low_v <= lvl)
            if reached and pct not in hit_set:
                hit_set.add(pct)
                changed = True
                record_event(key, f"MILESTONE_{pct}PCT", f"{d} reached {pct}% R target level ({round(lvl, 2)})", "good")
        rec["milestones_hit"] = sorted(hit_set)

        # Exit evaluation: SL first (conservative), then 0.60R target, then EOD 15:15
        if d == "CALL":
            sl_hit = low_v <= sl_v
            tgt_hit = high_v >= tgt_v
            if sl_hit and tgt_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, sl_v, "EXIT_SL_AMBIGUOUS", bar_t))
                record_event(key, "EXIT_SL_AMBIGUOUS", f"Same-bar SL & Target hit; conservative SL exit @ {sl_v}", "bad")
                continue
            if sl_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, sl_v, "EXIT_SL", bar_t))
                record_event(key, "EXIT_SL", f"CALL Stop-Loss hit @ {sl_v}", "bad")
                continue
            if tgt_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, tgt_v, "EXIT_TARGET", bar_t))
                record_event(key, "EXIT_TARGET", f"CALL 0.60R Target hit @ {tgt_v}", "good")
                continue
        else:
            sl_hit = high_v >= sl_v
            tgt_hit = low_v <= tgt_v
            if sl_hit and tgt_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, sl_v, "EXIT_SL_AMBIGUOUS", bar_t))
                record_event(key, "EXIT_SL_AMBIGUOUS", f"Same-bar SL & Target hit; conservative SL exit @ {sl_v}", "bad")
                continue
            if sl_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, sl_v, "EXIT_SL", bar_t))
                record_event(key, "EXIT_SL", f"PUT Stop-Loss hit @ {sl_v}", "bad")
                continue
            if tgt_hit:
                trades.pop(key, None)
                changed = True
                newly_closed.append(_append_closed_trade_to_csv(rec, tgt_v, "EXIT_TARGET", bar_t))
                record_event(key, "EXIT_TARGET", f"PUT 0.60R Target hit @ {tgt_v}", "good")
                continue

        if bar_t >= "15:15":
            trades.pop(key, None)
            changed = True
            newly_closed.append(_append_closed_trade_to_csv(rec, close_v, "EXIT_EOD_1515", "15:15"))
            record_event(key, "EXIT_EOD_1515", f"EOD 15:15 square-off @ {close_v}", "warn")

    if changed:
        _save_json(ACTIVE_TRADES_PATH, trades)
    return newly_closed
