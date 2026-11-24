import json
import os
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_local_env() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_db_connection():
    _load_local_env()
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url or "[YOUR-DATABASE-PASSWORD]" in db_url:
        raise RuntimeError("DATABASE_URL is missing or still contains [YOUR-DATABASE-PASSWORD].")
    try:
        import psycopg
        return psycopg.connect(db_url)
    except ImportError:
        import psycopg2
        return psycopg2.connect(db_url)


def upsert_signal(state: dict[str, Any]) -> None:
    idx_name = str(state.get("index_key") or state.get("index") or "").strip()
    sess_date = str(state.get("session_date") or "").strip()
    bar_time = str(state.get("last_bar_time") or state.get("bar_time") or "").strip()
    status = str(state.get("status") or "WATCHING").strip()
    if not (idx_name and sess_date and bar_time):
        return

    sig_dir = state.get("confirmed_dir") or state.get("armed_dir") or state.get("direction")
    if sig_dir not in ("CALL", "PUT"):
        sig_dir = None

    sig_uid = f"{idx_name}_{sess_date}_{bar_time}_{status}"
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.signals (
                    signal_uid, index_name, session_date, weekday, bar_time,
                    direction, status, status_reason, last_close, sma2, sma10,
                    entry_price, sl_price, target_price, trades_today,
                    avoid_today, gap_excluded, payload
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                on conflict (signal_uid)
                do update set
                    direction = excluded.direction,
                    status = excluded.status,
                    status_reason = excluded.status_reason,
                    last_close = excluded.last_close,
                    sma2 = excluded.sma2,
                    sma10 = excluded.sma10,
                    entry_price = excluded.entry_price,
                    sl_price = excluded.sl_price,
                    target_price = excluded.target_price,
                    trades_today = excluded.trades_today,
                    avoid_today = excluded.avoid_today,
                    gap_excluded = excluded.gap_excluded,
                    payload = excluded.payload,
                    updated_at = now()
                """,
                (
                    sig_uid,
                    idx_name,
                    sess_date,
                    str(state.get("weekday") or ""),
                    bar_time,
                    sig_dir,
                    status,
                    state.get("status_reason"),
                    state.get("last_close"),
                    state.get("sma2"),
                    state.get("sma10"),
                    state.get("entry_price"),
                    state.get("stop_price") or state.get("sl_price"),
                    state.get("target_price"),
                    int(state.get("trades_today") or 0),
                    bool(state.get("avoid_today", False)),
                    bool(state.get("gap_excluded", False)),
                    json.dumps(state, default=str),
                ),
            )
    conn.close()


def insert_alert(
    index_name: str,
    session_date: str,
    alert_time: str,
    alert_type: str,
    message: str,
    direction: Optional[str] = None,
    r_level: Optional[float] = None,
    trigger_price: Optional[float] = None,
    delivered: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    d_val = direction if direction in ("CALL", "PUT") else None
    uid = f"{index_name}_{session_date}_{alert_time}_{alert_type}_{d_val or 'NONE'}"
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.alerts (
                    alert_uid, index_name, session_date, alert_time, alert_type,
                    direction, r_level, trigger_price, message, delivered, metadata
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                on conflict (alert_uid)
                do update set
                    r_level = excluded.r_level,
                    trigger_price = excluded.trigger_price,
                    message = excluded.message,
                    delivered = excluded.delivered,
                    metadata = excluded.metadata
                """,
                (
                    uid,
                    index_name,
                    session_date,
                    alert_time,
                    alert_type,
                    d_val,
                    r_level,
                    trigger_price,
                    message,
                    bool(delivered),
                    json.dumps(metadata or {}, default=str),
                ),
            )
    conn.close()


def upsert_live_trade(trade: dict[str, Any]) -> None:
    idx_name = str(trade["index"]).strip()
    sess_date = str(trade["session_date"]).strip()
    entry_time = str(trade["entry_time"]).strip()
    direction = str(trade["direction"]).strip()
    uid = str(trade.get("trade_uid") or f"{idx_name}_{sess_date}_{entry_time}_{direction}")

    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into public.live_trades (
                    trade_uid, index_name, session_date, weekday, entry_time, exit_time,
                    direction, entry_price, sl_price, target_price, exit_price,
                    exit_reason, points, r_val, trade_status, notes
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (trade_uid)
                do update set
                    exit_time = excluded.exit_time,
                    exit_price = excluded.exit_price,
                    exit_reason = excluded.exit_reason,
                    points = excluded.points,
                    r_val = excluded.r_val,
                    trade_status = excluded.trade_status,
                    notes = excluded.notes,
                    updated_at = now()
                """,
                (
                    uid,
                    idx_name,
                    sess_date,
                    str(trade.get("weekday") or ""),
                    entry_time,
                    trade.get("exit_time"),
                    direction,
                    float(trade["entry_price"]),
                    float(trade["sl_price"]),
                    float(trade["target_price"]),
                    None if trade.get("exit_price") in (None, "") else float(trade["exit_price"]),
                    trade.get("exit_reason"),
                    float(trade.get("points") or 0.0),
                    float(trade.get("r_val") or 0.0),
                    str(trade.get("trade_status") or ("CLOSED" if trade.get("exit_time") else "OPEN")),
                    trade.get("notes"),
                ),
            )
    conn.close()
