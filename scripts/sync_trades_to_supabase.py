from pathlib import Path
import sys
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "live_signal_app"))
from db_store import get_db_connection, upsert_live_trade

hist_csv = BASE_DIR / "reports" / "refined_trades_1y.csv"
live_csv = BASE_DIR / "reports" / "live_monitored_trades.csv"

conn = get_db_connection()
with conn:
    with conn.cursor() as cur:
        hist_count = 0
        if hist_csv.exists():
            hdf = pd.read_csv(hist_csv)
            for _, r in hdf.iterrows():
                cur.execute(
                    """
                    insert into public.historical_trades (
                        index_name, session_date, weekday, entry_time, exit_time,
                        direction, entry_price, sl_price, target_price, exit_price,
                        exit_reason, points, r_val, source_variant
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (index_name, session_date, entry_time, direction, source_variant)
                    do update set
                        exit_time = excluded.exit_time,
                        exit_price = excluded.exit_price,
                        exit_reason = excluded.exit_reason,
                        points = excluded.points,
                        r_val = excluded.r_val
                    """,
                    (
                        str(r["index"]).strip(),
                        str(r["session_date"]).strip(),
                        str(r["weekday"]).strip(),
                        str(r["entry_time"]).strip(),
                        None if pd.isna(r.get("exit_time")) else str(r["exit_time"]).strip(),
                        str(r["direction"]).strip(),
                        float(r["entry_price"]),
                        float(r["sl_price"]),
                        float(r["target_price"]),
                        None if pd.isna(r.get("exit_price")) else float(r["exit_price"]),
                        None if pd.isna(r.get("exit_reason")) else str(r["exit_reason"]).strip(),
                        float(r["points"]),
                        float(r["r_val"]),
                        "3_REFINED_ALL_INDICES",
                    ),
                )
                hist_count += 1
conn.close()

live_count = 0
if live_csv.exists():
    ldf = pd.read_csv(live_csv)
    for idx_i, r in ldf.iterrows():
        row_d = {k: (None if pd.isna(v) else v) for k, v in r.to_dict().items()}
        row_d.setdefault("trade_uid", f"{row_d['index']}_{row_d['session_date']}_{row_d['entry_time']}_{row_d['direction']}_{idx_i}")
        upsert_live_trade(row_d)
        live_count += 1

conn = get_db_connection()
with conn.cursor() as cur:
    cur.execute("select count(*) from public.historical_trades")
    db_hist = cur.fetchone()[0]
    cur.execute("select count(*) from public.live_trades")
    db_live = cur.fetchone()[0]
    cur.execute("select count(*) from public.signals")
    db_sig = cur.fetchone()[0]
    cur.execute("select count(*) from public.alerts")
    db_alt = cur.fetchone()[0]
conn.close()

print(f"Supabase sync complete -> historical_trades={db_hist} (from {hist_count} CSV rows), live_trades={db_live} (from {live_count} CSV rows), signals={db_sig}, alerts={db_alt}")
