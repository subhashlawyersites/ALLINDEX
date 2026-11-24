from collections import defaultdict
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

INDICES = {
    "NIFTY50": "nifty50_15m_1y.csv",
    "SENSEX": "sensex_15m_1y.csv",
    "BANKNIFTY": "banknifty_15m_1y.csv",
    "FINNIFTY": "finnifty_15m_1y.csv",
}

WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

def run_sma2_sma10_color60(df: pd.DataFrame, index_name: str, target_mult: float = 0.60) -> list[dict]:
    work = df.copy()
    ts = pd.to_datetime(work["timestamp_ist"], errors="coerce")
    if ts.isna().all() and "epoch" in work.columns:
        ts = pd.to_datetime(work["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    work["ts"] = ts
    work = work.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    work["session_date"] = work["ts"].dt.date
    work["time_str"] = work["ts"].dt.strftime("%H:%M")
    work["weekday"] = work["ts"].dt.day_name()

    # Continuous SMAs across sessions
    work["sma2"] = work["close"].rolling(2).mean()
    work["sma10"] = work["close"].rolling(10).mean()

    trades = []
    prev_session_close = None

    for sess_date, day_df in work.groupby("session_date", sort=True):
        rows = day_df.to_dict("records")
        if not rows:
            continue

        first_open = float(rows[0]["open"])
        gap_excluded = False
        if prev_session_close is not None and prev_session_close > 0:
            gap_pct = (first_open - prev_session_close) / prev_session_close
            if gap_pct <= -0.0075:
                gap_excluded = True

        trades_today = 0
        armed_dir = None
        ref_sl = None
        in_trade = False
        active_trade = None

        for idx_in_day, r in enumerate(rows):
            bar_num = idx_in_day + 1  # 1-based bar within session
            t_str = r["time_str"]
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            sma2 = r["sma2"]
            sma10 = r["sma10"]

            prev_sma2 = rows[idx_in_day - 1]["sma2"] if idx_in_day > 0 else None
            prev_sma10 = rows[idx_in_day - 1]["sma10"] if idx_in_day > 0 else None
            prev_low = float(rows[idx_in_day - 1]["low"]) if idx_in_day > 0 else None
            prev_high = float(rows[idx_in_day - 1]["high"]) if idx_in_day > 0 else None

            # 1. Manage open trade first
            if in_trade and active_trade is not None:
                d = active_trade["direction"]
                sl_p = active_trade["sl_price"]
                tgt_p = active_trade["target_price"]

                if d == "CALL":
                    fav = h - active_trade["entry_price"]
                    adv = active_trade["entry_price"] - l
                    hit_sl = l <= sl_p
                    hit_tgt = h >= tgt_p
                else:
                    fav = active_trade["entry_price"] - l
                    adv = h - active_trade["entry_price"]
                    hit_sl = h >= sl_p
                    hit_tgt = l <= tgt_p

                active_trade["mfe_pts"] = max(active_trade["mfe_pts"], fav)
                active_trade["mae_pts"] = max(active_trade["mae_pts"], adv)

                exit_price = None
                exit_reason = None
                if hit_sl and hit_tgt:
                    exit_price = sl_p
                    exit_reason = "EXIT_SL_AMBIGUOUS"
                elif hit_sl:
                    exit_price = sl_p
                    exit_reason = "EXIT_SL"
                elif hit_tgt:
                    exit_price = tgt_p
                    exit_reason = "EXIT_TARGET"
                elif t_str >= "15:15" or idx_in_day == len(rows) - 1:
                    exit_price = c
                    exit_reason = "EXIT_EOD_1515"

                if exit_price is not None:
                    raw_pts = (exit_price - active_trade["entry_price"]) if d == "CALL" else (active_trade["entry_price"] - exit_price)
                    r_val = round(raw_pts / active_trade["risk_pts"], 4)
                    active_trade.update({
                        "exit_time": t_str,
                        "exit_price": round(exit_price, 2),
                        "exit_reason": exit_reason,
                        "points": round(raw_pts, 2),
                        "r_val": r_val,
                        "mfe_pts": round(active_trade["mfe_pts"], 2),
                        "mae_pts": round(active_trade["mae_pts"], 2),
                    })
                    trades.append(active_trade)
                    in_trade = False
                    active_trade = None

            # 2. Crossover arming on closed bar (when SMA2 & SMA10 are valid)
            if (
                prev_sma2 is not None and prev_sma10 is not None
                and pd.notna(prev_sma2) and pd.notna(prev_sma10)
                and pd.notna(sma2) and pd.notna(sma10)
                and prev_low is not None and prev_high is not None
            ):
                bull_cross = (prev_sma2 <= prev_sma10) and (sma2 > sma10)
                bear_cross = (prev_sma2 >= prev_sma10) and (sma2 < sma10)
                if bull_cross and not in_trade:
                    armed_dir = "CALL"
                    ref_sl = prev_low
                elif bear_cross and not in_trade:
                    armed_dir = "PUT"
                    ref_sl = prev_high

            # 3. Entry confirmation (bar_num >= 4, 09:45 <= t_str <= 15:14, not gap_excluded, trades_today < 2)
            if (
                not in_trade
                and not gap_excluded
                and trades_today < 2
                and bar_num >= 4
                and "09:45" <= t_str <= "15:14"
                and armed_dir is not None
                and ref_sl is not None
                and pd.notna(sma10)
            ):
                body_low = min(o, c)
                body_high = max(o, c)
                lower_wick = body_low - l
                upper_wick = h - body_high

                confirmed = False
                if armed_dir == "CALL":
                    risk_pts = c - ref_sl
                    if (c > o) and (l > sma10) and (lower_wick > upper_wick) and (risk_pts > 0):
                        confirmed = True
                        target_price = c + target_mult * risk_pts
                elif armed_dir == "PUT":
                    risk_pts = ref_sl - c
                    if (c < o) and (h < sma10) and (upper_wick > lower_wick) and (risk_pts > 0):
                        confirmed = True
                        target_price = c - target_mult * risk_pts

                if confirmed:
                    in_trade = True
                    trades_today += 1
                    active_trade = {
                        "index": index_name,
                        "session_date": str(sess_date),
                        "weekday": r["weekday"],
                        "entry_time": t_str,
                        "direction": armed_dir,
                        "entry_price": round(c, 2),
                        "risk_pts": round(risk_pts, 2),
                        "sl_price": round(ref_sl, 2),
                        "target_price": round(target_price, 2),
                        "mfe_pts": 0.0,
                        "mae_pts": 0.0,
                    }
                    # Reset arm once trade opens
                    armed_dir = None
                    ref_sl = None

        prev_session_close = float(rows[-1]["close"])

    return trades

def summarize(rows: list[dict]) -> str:
    if not rows:
        return "trades=  0 | win=  0.0% | avg_r=+0.00R | cum_r= +0.00R | pts=    +0.0"
    n = len(rows)
    wins = sum(1 for r in rows if float(r["points"]) > 0)
    r_vals = [float(r["r_val"]) for r in rows]
    pts_vals = [float(r["points"]) for r in rows]
    return (
        f"trades={n:3d} | win={wins/n*100:5.1f}% | "
        f"avg_r={sum(r_vals)/n:+5.2f}R | cum_r={sum(r_vals):+6.2f}R | pts={sum(pts_vals):+8.1f}"
    )

if __name__ == "__main__":
    all_trades = []
    for name, fname in INDICES.items():
        path = DATA_DIR / fname
        if not path.exists():
            print(f"[SKIP] Missing {path}")
            continue
        df = pd.read_csv(path)
        t_rows = run_sma2_sma10_color60(df, name)
        all_trades.extend(t_rows)
        print(f"\n=== {name} | SMA2/SMA10 COLOR60 (MON-FRI) ===")
        print("  OVERALL  :", summarize(t_rows))
        by_day = defaultdict(list)
        for tr in t_rows:
            by_day[tr["weekday"]].append(tr)
        for wd in WEEKDAY_ORDER:
            print(f"  {wd:9s}:", summarize(by_day[wd]))

    if all_trades:
        out_csv = REPORT_DIR / "sma2_sma10_color60_1y_trades.csv"
        pd.DataFrame(all_trades).to_csv(out_csv, index=False)
        print(f"\nSaved {len(all_trades)} trades -> {out_csv}")
