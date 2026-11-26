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


def _load_local_trades_df(mode_key: str = "refined") -> pd.DataFrame:
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

    if not df.empty and "data_source" not in df.columns:
        df["data_source"] = "LOCAL"

    if LIVE_TRADES_CSV.exists():
        try:
            live_df = pd.read_csv(LIVE_TRADES_CSV)
            if not live_df.empty:
                live_df["data_source"] = "LOCAL_LIVE"
                df = pd.concat([df, live_df], ignore_index=True)
        except Exception:
            pass

    return df


def _load_supabase_trades_df() -> pd.DataFrame:
    cols = [
        "index", "session_date", "weekday", "entry_time", "exit_time",
        "direction", "entry_price", "sl_price", "target_price", "exit_price",
        "exit_reason", "points", "r_val", "data_source"
    ]
    try:
        from db_store import get_db_connection
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select index_name, session_date, weekday, bar_time, direction,
                           entry_price, sl_price, target_price, last_close, status, payload
                    from public.signals
                    order by session_date desc, bar_time desc
                    limit 2000
                    """
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return pd.DataFrame(columns=cols)

    records = []
    for r in rows or []:
        idx_name, sess_date, wkday, bar_time, direction, entry_p, sl_p, tgt_p, last_c, status, payload = r
        p_dict = payload if isinstance(payload, dict) else {}
        if not direction:
            continue
        ep = float(entry_p if entry_p is not None else (p_dict.get("entry_price") or 0.0))
        xp = float(p_dict.get("exit_price") if p_dict.get("exit_price") is not None else (last_c or ep))
        pts = float(p_dict.get("points") if p_dict.get("points") is not None else ((xp - ep) if str(direction).upper() == "CALL" else (ep - xp)))
        r_val = float(p_dict.get("r_val") or 0.0)
        records.append({
            "index": str(idx_name or "").upper(),
            "session_date": str(sess_date or ""),
            "weekday": str(wkday or p_dict.get("weekday") or ""),
            "entry_time": str(p_dict.get("entry_time") or bar_time or ""),
            "exit_time": str(p_dict.get("exit_time") or bar_time or ""),
            "direction": str(direction or "").upper(),
            "entry_price": ep,
            "sl_price": float(sl_p if sl_p is not None else (p_dict.get("sl_price") or ep)),
            "target_price": float(tgt_p if tgt_p is not None else (p_dict.get("target_price") or ep)),
            "exit_price": xp,
            "exit_reason": str(p_dict.get("exit_reason") or status or ""),
            "points": pts,
            "r_val": r_val,
            "data_source": "SUPABASE",
        })
    return pd.DataFrame(records, columns=cols) if records else pd.DataFrame(columns=cols)


def _sanitize_and_dedup_trades_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    work = df.copy()
    ex_s = work.get("exit_reason", pd.Series("", index=work.index)).astype(str).str.strip().str.upper()
    ep = pd.to_numeric(work.get("entry_price"), errors="coerce").fillna(0.0)
    sp = pd.to_numeric(work.get("sl_price"), errors="coerce").fillna(0.0)
    hhmm = work.get("entry_time", pd.Series("", index=work.index)).astype(str).map(_normalize_hhmm)
    bad_hhmm = hhmm.str.match(r"^\d{2}:\d{2}$") & ((hhmm < "09:15") | (hhmm > "15:30"))
    valid_mask = ~ex_s.isin(["AVOID_DAY", "CONFIRMED_CALL", "CONFIRMED_PUT"]) & (ep > 0.0) & (sp > 0.0) & (ep != sp) & (~bad_hhmm)
    work = work[valid_mask].copy()
    if work.empty:
        return work

    src_rank = {"LOCAL_LIVE": 3, "LOCAL": 2, "SUPABASE": 1}
    work["_src_rank"] = work.get("data_source", "LOCAL").astype(str).str.upper().map(src_rank).fillna(1)
    work["_dedup_idx"] = work.get("index", "").astype(str).str.strip().str.upper()
    work["_dedup_date"] = pd.to_datetime(work.get("session_date", ""), format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d").fillna(work.get("session_date", "").astype(str))
    work["_dedup_dir"] = work.get("direction", "").astype(str).str.strip().str.upper()
    work["_dedup_entry"] = pd.to_numeric(work.get("entry_price"), errors="coerce").round(2)
    work["_non_null_score"] = work.notna().sum(axis=1)
    work = work.sort_values(["_src_rank", "_non_null_score"], ascending=True).drop_duplicates(
        subset=["_dedup_idx", "_dedup_date", "_dedup_dir", "_dedup_entry"],
        keep="last",
    )
    drop_cols = [c for c in work.columns if c.startswith("_dedup_") or c in ("_src_rank", "_non_null_score")]
    return work.drop(columns=drop_cols).reset_index(drop=True)


def _load_trades_df(mode_key: str = "refined", source: str = "both") -> pd.DataFrame:
    src_key = str(source or "both").strip().lower()
    if src_key == "local":
        df = _load_local_trades_df(mode_key)
    elif src_key == "supabase":
        df = _load_supabase_trades_df()
    else:
        local_df = _load_local_trades_df(mode_key)
        sb_df = _load_supabase_trades_df()
        frames = [f for f in (local_df, sb_df) if f is not None and not f.empty]
        df = pd.concat(frames, ignore_index=True) if frames else local_df
    return _sanitize_and_dedup_trades_df(df)


def _normalize_hhmm(val: str) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    parts = s.split(":")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return s


def _resolve_date_bounds(
    df_dates: pd.Series,
    preset: str = "",
    from_date: str = "",
    to_date: str = "",
) -> tuple[str, str]:
    p = str(preset or "").strip().lower()
    eff_from = str(from_date or "").strip()
    eff_to = str(to_date or "").strip()

    valid_dates = sorted(d for d in df_dates.dropna().astype(str).unique() if d and d.lower() != "nan")
    anchor_str = eff_to or (valid_dates[-1] if valid_dates else "")
    anchor_ts = pd.to_datetime(anchor_str, errors="coerce")

    if p and p != "all" and pd.notna(anchor_ts):
        if not eff_to:
            eff_to = anchor_ts.strftime("%Y-%m-%d")
        if not eff_from:
            if p == "today":
                eff_from = anchor_ts.strftime("%Y-%m-%d")
            elif p == "7d":
                eff_from = (anchor_ts - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
            elif p == "30d":
                eff_from = (anchor_ts - pd.Timedelta(days=29)).strftime("%Y-%m-%d")
            elif p == "mtd":
                eff_from = anchor_ts.replace(day=1).strftime("%Y-%m-%d")

    return eff_from, eff_to


def _compute_target_achieved_pct(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    entry = pd.to_numeric(df.get("entry_price"), errors="coerce")
    target = pd.to_numeric(df.get("target_price"), errors="coerce")
    exit_p = pd.to_numeric(df.get("exit_price"), errors="coerce")
    direction = df.get("direction", pd.Series("", index=df.index)).astype(str).str.upper()

    target_dist = (target - entry).abs()
    fallback_move = pd.Series(0.0, index=df.index, dtype=float)
    is_call = direction == "CALL"
    is_put = direction == "PUT"
    fallback_move = fallback_move.where(~is_call, (exit_p - entry).clip(lower=0.0))
    fallback_move = fallback_move.where(~is_put, (entry - exit_p).clip(lower=0.0))

    if "mfe_pts" in df.columns:
        mfe = pd.to_numeric(df["mfe_pts"], errors="coerce")
        fav_pts = mfe.where(mfe.notna() & (mfe >= 0), fallback_move)
    else:
        fav_pts = fallback_move

    pct = (fav_pts / target_dist.replace(0, float("nan"))) * 100.0
    return pct.fillna(0.0).round(1)


def _target_bucket_label(pct_val: float) -> str:
    try:
        v = float(pct_val)
    except Exception:
        return "<50%"
    if v < 50.0:
        return "<50%"
    if v < 60.0:
        return "50-59%"
    if v < 70.0:
        return "60-69%"
    if v < 80.0:
        return "70-79%"
    if v < 100.0:
        return "80-99%"
    return "100%+"


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
    from_date: str = "",
    to_date: str = "",
    from_time: str = "",
    to_time: str = "",
    preset: str = "",
    min_target_pct: float | None = None,
    target_bucket: str = "",
    source: str = "both",
    min_points: float | None = None,
    max_points: float | None = None,
    min_r_pct: float | None = None,
    max_r_pct: float | None = None,
) -> dict:
    mode_key = "refined" if str(mode).lower() == "refined" else "baseline"
    source_key = str(source or "both").strip().lower()
    if source_key not in ("both", "local", "supabase"):
        source_key = "both"
    all_df = _load_trades_df(mode_key, source=source_key)
    scope_upper = scope.upper()

    if not all_df.empty:
        all_parsed = pd.to_datetime(all_df["session_date"], format="ISO8601", errors="coerce")
        all_df["_iso_date"] = all_parsed.dt.strftime("%Y-%m-%d").fillna(all_df["session_date"].astype(str))
        all_df["_hhmm"] = all_df["entry_time"].astype(str).map(_normalize_hhmm)
        all_df["_target_pct"] = _compute_target_achieved_pct(all_df)
        all_df["_target_bucket"] = all_df["_target_pct"].map(_target_bucket_label)
    else:
        all_df["_iso_date"] = pd.Series(dtype=str)
        all_df["_hhmm"] = pd.Series(dtype=str)
        all_df["_target_pct"] = pd.Series(dtype=float)
        all_df["_target_bucket"] = pd.Series(dtype=str)

    if scope_upper != "ALL" and scope_upper in INDEX_CONFIGS:
        df = all_df[all_df["index"].astype(str).str.upper() == scope_upper].copy()
    else:
        df = all_df.copy()

    if df.empty:
        df["_iso_date"] = pd.Series(dtype=str)
        df["_hhmm"] = pd.Series(dtype=str)

    eff_from_date, eff_to_date = _resolve_date_bounds(
        df["_iso_date"] if "_iso_date" in df.columns else pd.Series(dtype=str),
        preset=preset,
        from_date=from_date,
        to_date=to_date,
    )
    eff_from_time = _normalize_hhmm(from_time)
    eff_to_time = _normalize_hhmm(to_time)

    filtered = df.copy()
    if not filtered.empty:
        if weekday_filter:
            filtered = filtered[filtered["weekday"].astype(str).str.lower() == weekday_filter.lower()]
        if direction_filter:
            filtered = filtered[filtered["direction"].astype(str).str.upper() == direction_filter.upper()]
        if exit_filter:
            filtered = filtered[filtered["exit_reason"].astype(str).str.upper() == exit_filter.upper()]
        if eff_from_date:
            filtered = filtered[filtered["_iso_date"] >= eff_from_date]
        if eff_to_date:
            filtered = filtered[filtered["_iso_date"] <= eff_to_date]
        if eff_from_time:
            filtered = filtered[filtered["_hhmm"] >= eff_from_time]
        if eff_to_time:
            filtered = filtered[filtered["_hhmm"] <= eff_to_time]
        if min_target_pct is not None:
            filtered = filtered[filtered["_target_pct"] >= float(min_target_pct)]
        if target_bucket and str(target_bucket).strip().upper() != "ALL":
            filtered = filtered[filtered["_target_bucket"].astype(str).str.upper() == str(target_bucket).strip().upper()]
        pts_s = pd.to_numeric(filtered.get("points"), errors="coerce")
        r_pct_s = pd.to_numeric(filtered.get("r_val"), errors="coerce") * 100.0
        if min_points is not None:
            filtered = filtered[pts_s >= float(min_points)]
            pts_s = pd.to_numeric(filtered.get("points"), errors="coerce")
            r_pct_s = pd.to_numeric(filtered.get("r_val"), errors="coerce") * 100.0
        if max_points is not None:
            filtered = filtered[pts_s <= float(max_points)]
            pts_s = pd.to_numeric(filtered.get("points"), errors="coerce")
            r_pct_s = pd.to_numeric(filtered.get("r_val"), errors="coerce") * 100.0
        if min_r_pct is not None:
            filtered = filtered[r_pct_s >= float(min_r_pct)]
            r_pct_s = pd.to_numeric(filtered.get("r_val"), errors="coerce") * 100.0
        if max_r_pct is not None:
            filtered = filtered[r_pct_s <= float(max_r_pct)]

    chrono_cols = [c for c in ["_iso_date", "_hhmm", "index"] if c in filtered.columns]
    chrono = filtered.sort_values(chrono_cols or ["session_date", "entry_time"], ascending=True).reset_index(drop=True)

    sort_col_key = sort_by if sort_by in SORT_COLUMNS else "session_date"
    sort_cols = SORT_COLUMNS[sort_col_key]
    asc = str(sort_dir).lower() == "asc"
    if sort_col_key == "session_date":
        effective_cols = [c for c in ["_iso_date", "_hhmm", "index"] if c in filtered.columns]
        log_df = filtered.sort_values(effective_cols, ascending=[asc] * len(effective_cols)).reset_index(drop=True)
    else:
        tie_cols = [c for c in ["_iso_date", "_hhmm", "index"] if c in filtered.columns and c not in sort_cols]
        full_cols = sort_cols + tie_cols
        log_df = filtered.sort_values(
            full_cols,
            ascending=[asc] + [False] * (len(full_cols) - 1),
        ).reset_index(drop=True)

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
        sub = all_df[all_df["index"].astype(str).str.upper() == k]
        if weekday_filter:
            sub = sub[sub["weekday"].astype(str).str.lower() == weekday_filter.lower()]
        if direction_filter:
            sub = sub[sub["direction"].astype(str).str.upper() == direction_filter.upper()]
        if exit_filter:
            sub = sub[sub["exit_reason"].astype(str).str.upper() == exit_filter.upper()]
        if not sub.empty:
            if eff_from_date:
                sub = sub[sub["_iso_date"] >= eff_from_date]
            if eff_to_date:
                sub = sub[sub["_iso_date"] <= eff_to_date]
            if eff_from_time:
                sub = sub[sub["_hhmm"] >= eff_from_time]
            if eff_to_time:
                sub = sub[sub["_hhmm"] <= eff_to_time]
            if min_target_pct is not None:
                sub = sub[sub["_target_pct"] >= float(min_target_pct)]
            if target_bucket and str(target_bucket).strip().upper() != "ALL":
                sub = sub[sub["_target_bucket"].astype(str).str.upper() == str(target_bucket).strip().upper()]
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
            "target_achieved_pct": round(float(r.get("_target_pct", 0.0)), 1),
            "target_bucket": str(r.get("_target_bucket", "<50%")),
            "data_source": str(r.get("data_source") or ("SUPABASE" if source_key == "supabase" else "LOCAL")),
            "outcome": "WIN" if pts_v > 0 else "LOSS",
        })

    bucket_order = ["<50%", "50-59%", "60-69%", "70-79%", "80-99%", "100%+"]
    target_levels = []
    for b_name in bucket_order:
        b_sub = filtered[filtered["_target_bucket"] == b_name] if not filtered.empty and "_target_bucket" in filtered.columns else pd.DataFrame()
        b_n = int(len(b_sub))
        target_levels.append({
            "bucket": b_name,
            "trades": b_n,
            "win_pct": round(float((b_sub["points"] > 0).mean() * 100.0), 1) if b_n else 0.0,
            "avg_r": round(float(b_sub["r_val"].mean()), 2) if b_n else 0.0,
            "cum_r": round(float(b_sub["r_val"].sum()), 2) if b_n else 0.0,
            "cum_pts": round(float(b_sub["points"].sum()), 2) if b_n else 0.0,
        })

    return {
        "scope": scope_upper,
        "mode": mode_key,
        "source": source_key,
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
        "target_levels": target_levels,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_rows": total_filtered,
            "total_pages": total_pages,
            "rows": trades_page,
        },
    }
