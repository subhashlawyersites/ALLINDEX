from collections import defaultdict
from pathlib import Path
import numpy as np
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

def backtest_df(df: pd.DataFrame, index_name: str, fast_span: int = 9, slow_span: int = 21, target_r: float = 0.70) -> list[dict]:
    work = df.copy()
    ts = pd.to_datetime(work["timestamp_ist"], errors="coerce")
    if ts.isna().all() and "epoch" in work.columns:
        ts = pd.to_datetime(work["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    work["ts"] = ts
    work = work.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    work["session_date"] = work["ts"].dt.date
    work["time_str"] = work["ts"].dt.strftime("%H:%M")
    work["weekday"] = work["ts"].dt.day_name()

    work["ema_fast"] = work["close"].ewm(span=fast_span, adjust=False).mean()
    work["ema_slow"] = work["close"].ewm(span=slow_span, adjust=False).mean()

    trades = []
    for _, day_df in work.groupby("session_date", sort=True):
        rows = day_df.to_dict("records")
        in_pos = False
        trade = None

        for i in range(1, len(rows)):
            prev_r = rows[i - 1]
            cur_r = rows[i]
            t_str = cur_r["time_str"]

            if in_pos and trade is not None:
                # Track intraday favorable/adverse excursion
                if trade["direction"] == "CALL":
                    fav = float(cur_r["high"]) - trade["entry_price"]
                    adv = trade["entry_price"] - float(cur_r["low"])
                    hit_sl = float(cur_r["low"]) <= trade["sl_price"]
                    hit_tgt = float(cur_r["high"]) >= trade["target_price"]
                else:
                    fav = trade["entry_price"] - float(cur_r["low"])
                    adv = float(cur_r["high"]) - trade["entry_price"]
                    hit_sl = float(cur_r["high"]) >= trade["sl_price"]
                    hit_tgt = float(cur_r["low"]) <= trade["target_price"]

                trade["mfe_pts"] = max(trade["mfe_pts"], fav)
                trade["mae_pts"] = max(trade["mae_pts"], adv)

                exit_price = None
                exit_reason = None
                if hit_sl and hit_tgt:
                    exit_price = trade["sl_price"]
                    exit_reason = "EXIT_SL"
                elif hit_sl:
                    exit_price = trade["sl_price"]
                    exit_reason = "EXIT_SL"
                elif hit_tgt:
                    exit_price = trade["target_price"]
                    exit_reason = "EXIT_TARGET"
                elif t_str >= "15:15" or i == len(rows) - 1:
                    exit_price = float(cur_r["close"])
                    exit_reason = "EXIT_EOD_1515"

                if exit_price is not None:
                    raw_pts = (exit_price - trade["entry_price"]) if trade["direction"] == "CALL" else (trade["entry_price"] - exit_price)
                    risk_pts = max(trade["risk_pts"], 1e-6)
                    r_val = round(raw_pts / risk_pts, 4)
                    trade.update({
                        "exit_time": t_str,
                        "exit_price": round(exit_price, 2),
                        "exit_reason": exit_reason,
                        "points": round(raw_pts, 2),
                        "r_val": r_val,
                        "mfe_pts": round(trade["mfe_pts"], 2),
                        "mae_pts": round(trade["mae_pts"], 2),
                    })
                    trades.append(trade)
                    in_pos = False
                    trade = None

            if not in_pos and "09:30" <= t_str <= "14:45":
                bull_cross = (prev_r["ema_fast"] <= prev_r["ema_slow"]) and (cur_r["ema_fast"] > cur_r["ema_slow"])
                bear_cross = (prev_r["ema_fast"] >= prev_r["ema_slow"]) and (cur_r["ema_fast"] < cur_r["ema_slow"])
                if bull_cross or bear_cross:
                    direction = "CALL" if bull_cross else "PUT"
                    entry_price = float(cur_r["close"])
                    # Swing/candle risk distance with sensible minimum floor
                    swing_ref = float(prev_r["low"]) if direction == "CALL" else float(prev_r["high"])
                    raw_risk = abs(entry_price - swing_ref)
                    min_floor = max(entry_price * 0.0012, 10.0)
                    risk_pts = max(raw_risk, min_floor)
                    if direction == "CALL":
                        sl_price = entry_price - risk_pts
                        target_price = entry_price + (risk_pts * target_r)
                    else:
                        sl_price = entry_price + risk_pts
                        target_price = entry_price - (risk_pts * target_r)

                    in_pos = True
                    trade = {
                        "index": index_name,
                        "session_date": str(cur_r["session_date"]),
                        "weekday": cur_r["weekday"],
                        "entry_time": t_str,
                        "direction": direction,
                        "entry_price": round(entry_price, 2),
                        "risk_pts": round(risk_pts, 2),
                        "sl_price": round(sl_price, 2),
                        "target_price": round(target_price, 2),
                        "mfe_pts": 0.0,
                        "mae_pts": 0.0,
                    }

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
        t_rows = backtest_df(df, name)
        all_trades.extend(t_rows)
        print(f"\n=== {name} (ALL MON-FRI) ===")
        print("  OVERALL  :", summarize(t_rows))
        by_day = defaultdict(list)
        for tr in t_rows:
            by_day[tr["weekday"]].append(tr)
        for wd in WEEKDAY_ORDER:
            print(f"  {wd:9s}:", summarize(by_day[wd]))

        by_time = defaultdict(list)
        for tr in t_rows:
            by_time[tr["entry_time"]].append(tr)
        ranked_times = sorted(by_time.items(), key=lambda kv: sum(float(x["r_val"]) for x in kv[1]))
        print("  Worst entry slots:", ", ".join(f"{k}({sum(float(x['r_val']) for x in v):+.2f}R)" for k, v in ranked_times[:3]))
        print("  Best  entry slots:", ", ".join(f"{k}({sum(float(x['r_val']) for x in v):+.2f}R)" for k, v in ranked_times[-3:]))

    if all_trades:
        out_csv = REPORT_DIR / "all_indices_1y_trades.csv"
        pd.DataFrame(all_trades).to_csv(out_csv, index=False)
        print(f"\nSaved {len(all_trades)} total trades -> {out_csv}")
