from pathlib import Path
import math
import sys
import pandas as pd

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from signal_engine import INDEX_CONFIGS
from test_rule_filters import BAD_TIMES_DEFAULT, INDICES, simulate_variant

DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
LIVE_TRADES_CSV = REPORT_DIR / "live_monitored_trades.csv"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

SORT_COLUMNS = {
    "session_date": ["session_date", "entry_time"],
    "index": ["index", "session_date", "entry_time"],
    "direction": ["direction", "session_date", "entry_time"],
    "entry_time": ["entry_time", "session_date"],
    "exit_reason": ["exit_reason", "session_date", "entry_time"],
    "points": ["points", "session_date", "entry_time"],
    "r_val": ["r_val", "session_date", "entry_time"],
}


def _load_trades_df(mode_key: str = "refined") -> pd.DataFrame:
    report_file = REPORT_DIR / ("refined_trades_1y.csv" if mode_key == "refined" else "sma2_sma10_color60_1y_trades.csv")

    if report_file.exists():
        df = pd.read_csv(report_file)
    else:
        frames = []
        for idx_name, fname in INDICES.items():
            p = DATA_DIR / fname
            if not p.exists():
                continue
            raw = pd.read_csv(p)
            sim = simulate_variant(
                raw,
                idx_name,
                target_mult=0.60,
                latest_entry="14:00" if mode_key == "refined" else "15:14",
                blocked_times=BAD_TIMES_DEFAULT if mode_key == "refined" else set(),
                allowed_weekdays=None,
                drop_bad_index_days=(mode_key == "refined"),
            )
            if not sim.empty:
                frames.append(sim)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[
            "index", "session_date", "weekday", "entry_time", "exit_time",
            "direction", "entry_price", "sl_price", "target_price", "exit_price",
            "exit_reason", "points", "r_val"
        ])

    # Merge any newly closed trades from live/paper trade_monitor so charts & log include them
    if LIVE_TRADES_CSV.exists():
        try:
            live_df = pd.read_csv(LIVE_TRADES_CSV)
            if not live_df.empty:
                df = pd.concat([df, live_df], ignore_index=True)
        except Exception:
            pass

    return df


def build_historical_analytics(
    scope: str = "all",
    mode: str = "refined",
    page: int = 1,
    per_page: int = 15,
    weekday_filter: str = "",
    direction_filter: str = "",
    exit_filter: str = "",
    sort_by: str = "session_date",
    sort_dir: str = "desc",
) -> dict:
    mode_key = "refined" if str(mode).lower() == "refined" else "baseline"
    all_df = _load_trades_df(mode_key)
    scope_upper = scope.upper()

    if scope_upper != "ALL" and scope_upper in INDEX_CONFIGS:
        df = all_df[all_df["index"].str.upper() == scope_upper].copy()
    else:
        df = all_df.copy()

    filtered = df.copy()
    if weekday_filter:
        filtered = filtered[filtered["weekday"].str.lower() == weekday_filter.lower()]
    if direction_filter:
        filtered = filtered[filtered["direction"].str.upper() == direction_filter.upper()]
    if exit_filter:
        filtered = filtered[filtered["exit_reason"].str.upper() == exit_filter.upper()]

    chrono = filtered.sort_values(["session_date", "entry_time"]).reset_index(drop=True)

    sort_col_key = sort_by if sort_by in SORT_COLUMNS else "session_date"
    sort_cols = SORT_COLUMNS[sort_col_key]
    asc = str(sort_dir).lower() == "asc"
    if sort_col_key == "session_date":
        log_df = filtered.sort_values(sort_cols, ascending=[asc, asc]).reset_index(drop=True)
    else:
        log_df = filtered.sort_values(sort_cols, ascending=[asc] + [False] * (len(sort_cols) - 1)).reset_index(drop=True)

    total_trades = int(len(chrono))
    wins = int((chrono["points"] > 0).sum()) if total_trades else 0
    losses = int(total_trades - wins)
    win_rate = round((wins / total_trades) * 100.0, 1) if total_trades else 0.0
    loss_rate = round((losses / total_trades) * 100.0, 1) if total_trades else 0.0

    cum_pts = round(float(chrono["points"].sum()), 2) if total_trades else 0.0
    cum_r = round(float(chrono["r_val"].sum()), 2) if total_trades else 0.0
    avg_r = round(float(chrono["r_val"].mean()), 2) if total_trades else 0.0

    curve_labels = []
    cum_pts_series = []
    cum_r_series = []
    running_pts = 0.0
    running_r = 0.0
    peak_pts = 0.0
    for idx_i, row in chrono.iterrows():
        running_pts = round(running_pts + float(row["points"]), 2)
        running_r = round(running_r + float(row["r_val"]), 2)
        peak_pts = max(peak_pts, running_pts)
        curve_labels.append(f"#{idx_i+1} {row['session_date']} {row['entry_time']}")
        cum_pts_series.append(running_pts)
        cum_r_series.append(running_r)

    best_session = None
    worst_session = None
    if total_trades:
        sess_grp = (
            chrono.groupby("session_date", as_index=False)
            .agg(pts=("points", "sum"), r_sum=("r_val", "sum"), trades=("points", "count"))
            .sort_values("pts", ascending=False)
        )
        top_s = sess_grp.iloc[0]
        bot_s = sess_grp.iloc[-1]
        best_session = {
            "date": str(top_s["session_date"]),
            "points": round(float(top_s["pts"]), 2),
            "cum_r": round(float(top_s["r_sum"]), 2),
            "trades": int(top_s["trades"]),
        }
        worst_session = {
            "date": str(bot_s["session_date"]),
            "points": round(float(bot_s["pts"]), 2),
            "cum_r": round(float(bot_s["r_sum"]), 2),
            "trades": int(bot_s["trades"]),
        }

    weekday_stats = []
    best_wd = None
    worst_wd = None
    for wd in WEEKDAYS:
        sub = chrono[chrono["weekday"] == wd]
        w_pts = round(float(sub["points"].sum()), 2) if not sub.empty else 0.0
        w_r = round(float(sub["r_val"].sum()), 2) if not sub.empty else 0.0
        w_cnt = int(len(sub))
        w_win = round(float((sub["points"] > 0).mean() * 100.0), 1) if w_cnt else 0.0
        weekday_stats.append({
            "weekday": wd,
            "trades": w_cnt,
            "win_pct": w_win,
            "points": w_pts,
            "cum_r": w_r,
        })
    if weekday_stats:
        sorted_by_pts = sorted(weekday_stats, key=lambda x: x["points"], reverse=True)
        best_wd = sorted_by_pts[0]
        worst_wd = sorted_by_pts[-1]

    exit_reasons = ["EXIT_TARGET", "EXIT_SL", "EXIT_SL_AMBIGUOUS", "EXIT_EOD_1515", "MANUAL_EXIT"]
    exit_stats = []
    for er in exit_reasons:
        sub = chrono[chrono["exit_reason"] == er]
        exit_stats.append({
            "exit_reason": er,
            "count": int(len(sub)),
            "points": round(float(sub["points"].sum()), 2) if not sub.empty else 0.0,
            "cum_r": round(float(sub["r_val"].sum()), 2) if not sub.empty else 0.0,
        })

    call_cnt = int((chrono["direction"] == "CALL").sum()) if total_trades else 0
    put_cnt = int((chrono["direction"] == "PUT").sum()) if total_trades else 0
    call_pct = round((call_cnt / total_trades) * 100.0, 1) if total_trades else 0.0
    put_pct = round((put_cnt / total_trades) * 100.0, 1) if total_trades else 0.0

    comparison = []
    for k, cfg in INDEX_CONFIGS.items():
        sub = all_df[all_df["index"].str.upper() == k]
        if weekday_filter:
            sub = sub[sub["weekday"].str.lower() == weekday_filter.lower()]
        if direction_filter:
            sub = sub[sub["direction"].str.upper() == direction_filter.upper()]
        if exit_filter:
            sub = sub[sub["exit_reason"].str.upper() == exit_filter.upper()]
        c_n = int(len(sub))
        c_win = round(float((sub["points"] > 0).mean() * 100.0), 1) if c_n else 0.0
        c_avg_r = round(float(sub["r_val"].mean()), 2) if c_n else 0.0
        c_cum_r = round(float(sub["r_val"].sum()), 2) if c_n else 0.0
        c_cum_pts = round(float(sub["points"].sum()), 2) if c_n else 0.0
        avoid_str = ", ".join(cfg["avoid_weekdays"]) if cfg["avoid_weekdays"] else "None"
        comparison.append({
            "index_key": k,
            "index_name": cfg["name"],
            "avoid_days": avoid_str,
            "trades": c_n,
            "win_pct": c_win,
            "avg_r": c_avg_r,
            "cum_r": c_cum_r,
            "cum_pts": c_cum_pts,
        })

    per_page = max(5, min(50, int(per_page)))
    total_filtered = int(len(log_df))
    total_pages = max(1, math.ceil(total_filtered / per_page))
    page = max(1, min(total_pages, int(page)))
    start_i = (page - 1) * per_page
    end_i = start_i + per_page

    trades_page = []
    for _, r in log_df.iloc[start_i:end_i].iterrows():
        pts_v = float(r["points"])
        trades_page.append({
            "index": r["index"],
            "session_date": str(r["session_date"]),
            "weekday": r["weekday"],
            "direction": r["direction"],
            "entry_time": r["entry_time"],
            "exit_time": r["exit_time"],
            "entry_price": round(float(r["entry_price"]), 2),
            "sl_price": round(float(r["sl_price"]), 2),
            "target_price": round(float(r["target_price"]), 2),
            "exit_price": round(float(r["exit_price"]), 2),
            "exit_reason": r["exit_reason"],
            "points": round(pts_v, 2),
            "r_val": round(float(r["r_val"]), 2),
            "outcome": "WIN" if pts_v > 0 else "LOSS",
        })

    return {
        "scope": scope_upper,
        "mode": mode_key,
        "sort_by": sort_col_key,
        "sort_dir": "asc" if asc else "desc",
        "summary": {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "loss_rate": loss_rate,
            "cum_pts": cum_pts,
            "peak_pts": round(peak_pts, 2),
            "cum_r": cum_r,
            "avg_r": avg_r,
            "call_cnt": call_cnt,
            "put_cnt": put_cnt,
            "call_pct": call_pct,
            "put_pct": put_pct,
            "best_session": best_session,
            "worst_session": worst_session,
            "best_weekday": best_wd,
            "worst_weekday": worst_wd,
        },
        "curve": {
            "labels": curve_labels,
            "cum_pts": cum_pts_series,
            "cum_r": cum_r_series,
        },
        "weekdays": weekday_stats,
        "exits": exit_stats,
        "comparison": comparison,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_rows": total_filtered,
            "total_pages": total_pages,
            "rows": trades_page,
        },
    }
