from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import pandas as pd

COMMON_RULES = {
    "timeframe": "15m",
    "fast_sma": 2,
    "slow_sma": 10,
    "primary_target_r": 0.60,
    "target_pct_steps": [50, 60, 70, 80, 90, 100],
    "blocked_times": ["10:30", "10:45", "13:15"],
    "entry_cutoff": "14:00",
    "eod_exit_time": "15:15",
    "max_trades_per_day": 2,
    "gap_down_threshold_pct": -0.75,
}

INDEX_CONFIGS = {
    "NIFTY50": {
        "name": "NIFTY 50",
        "symbol": "NSE:NIFTY50-INDEX",
        "csv_file": "nifty50_15m_1y.csv",
        "step": 50,
        "ref_1y_r": "+14.2R",
        "best_days": ["Tuesday", "Wednesday"],
        "avoid_weekdays": [],
        "caution_weekdays": ["Friday"],
        "day_note": "Primary anchor index; monitor 15m closed candles within cutoff.",
    },
    "SENSEX": {
        "name": "SENSEX",
        "symbol": "BSE:SENSEX-INDEX",
        "csv_file": "sensex_15m_1y.csv",
        "step": 100,
        "ref_1y_r": "+11.8R",
        "best_days": ["Tuesday", "Thursday"],
        "avoid_weekdays": [],
        "caution_weekdays": ["Monday"],
        "day_note": "Follow standard 15m SMA(2)/SMA(10) and cutoff rules.",
    },
    "BANKNIFTY": {
        "name": "BANKNIFTY",
        "symbol": "NSE:NIFTYBANK-INDEX",
        "csv_file": "banknifty_15m_1y.csv",
        "step": 100,
        "ref_1y_r": "+8.6R",
        "best_days": ["Wednesday", "Thursday"],
        "avoid_weekdays": ["Monday"],
        "caution_weekdays": ["Tuesday"],
        "day_note": "Avoid BANKNIFTY on Mondays due to historical chop/whipsaw.",
    },
    "FINNIFTY": {
        "name": "FINNIFTY",
        "symbol": "NSE:FINNIFTY-INDEX",
        "csv_file": "finnifty_15m_1y.csv",
        "step": 50,
        "ref_1y_r": "+7.9R",
        "best_days": ["Tuesday", "Wednesday"],
        "avoid_weekdays": ["Thursday"],
        "caution_weekdays": ["Monday"],
        "day_note": "Avoid FINNIFTY on Thursdays due to historical chop/whipsaw.",
    },
}


@dataclass
class SignalState:
    index_key: str
    index_name: str
    symbol: str
    session_date: str
    weekday: str
    last_bar_time: str
    last_close: float
    sma2: float
    sma10: float
    status: str
    status_reason: str
    armed_dir: Optional[str]
    entry_price: Optional[float]
    stop_price: Optional[float]
    target_price: Optional[float]
    trades_today: int
    avoid_today: bool
    gap_excluded: bool
    day_note: str
    ref_1y_r: str
    best_days: list[str]
    avoid_weekdays: list[str]
    caution_weekdays: list[str]
    target_ladder: dict
    strike_ladder: dict
    monitored_trade: Optional[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_candles_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "datetime" not in out.columns:
        if "timestamp_ist" in out.columns:
            cleaned_ts = out["timestamp_ist"].astype(str).str.replace(r"\s+IST$", "", regex=True).str.strip()
            parsed = pd.to_datetime(cleaned_ts, errors="coerce")
            if hasattr(parsed.dt, "tz") and parsed.dt.tz is not None:
                parsed = parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            out["datetime"] = parsed
        elif "epoch" in out.columns:
            out["datetime"] = (
                pd.to_datetime(pd.to_numeric(out["epoch"], errors="coerce"), unit="s", utc=True)
                .dt.tz_convert("Asia/Kolkata")
                .dt.tz_localize(None)
            )
        else:
            raise KeyError("Expected timestamp_ist, epoch, or datetime column in candle data")
    else:
        parsed = pd.to_datetime(out["datetime"], errors="coerce")
        if hasattr(parsed.dt, "tz") and parsed.dt.tz is not None:
            parsed = parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        out["datetime"] = parsed
    return out.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)


def build_target_ladder(
    direction: Optional[str],
    entry: Optional[float],
    sl: Optional[float],
    current_price: Optional[float],
) -> dict:
    if not direction or entry is None or sl is None or entry == sl:
        return {
            "entry": entry,
            "sl": sl,
            "r_points": 0.0,
            "progress_pct": 0.0,
            "milestone_label": "No active entry/SL",
            "levels": [],
        }

    r_pts = abs(entry - sl)
    sign = 1.0 if direction == "CALL" else -1.0
    primary_r = COMMON_RULES["primary_target_r"]
    levels = []
    for pct in COMMON_RULES["target_pct_steps"]:
        r_mult = round(pct / 100.0, 2)
        lvl_price = round(entry + sign * r_mult * r_pts, 2)
        levels.append({
            "pct": pct,
            "r_mult": r_mult,
            "price": lvl_price,
            "is_primary": (abs(r_mult - primary_r) < 1e-9),
        })

    cur = current_price if current_price is not None else entry
    signed_r = ((cur - entry) * sign) / r_pts if r_pts > 0 else 0.0
    clamped = max(-1.0, min(1.0, signed_r))
    progress_pct = round(((clamped + 1.0) / 2.0) * 100.0, 1)

    reached = [f"{l['pct']}%" for l in levels if ((cur - entry) * sign) >= (l["r_mult"] * r_pts)]
    if signed_r <= -1.0:
        milestone_label = "At or beyond Stop Loss (-1.0R)"
    elif reached:
        milestone_label = f"Reached: {', '.join(reached)}"
    else:
        milestone_label = f"Current progress: {round(signed_r, 2)}R (Target: {primary_r}R)"

    return {
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "r_points": round(r_pts, 2),
        "progress_pct": progress_pct,
        "milestone_label": milestone_label,
        "levels": levels,
    }


def evaluate_index_signal(
    df: pd.DataFrame,
    index_key: str,
    monitored_trade: Optional[dict] = None,
    strike_ladder: Optional[dict] = None,
) -> SignalState:
    cfg = INDEX_CONFIGS[index_key]
    work = _normalize_candles_df(df)
    work["sma2"] = work["close"].rolling(COMMON_RULES["fast_sma"]).mean()
    work["sma10"] = work["close"].rolling(COMMON_RULES["slow_sma"]).mean()

    now_ist = datetime.now(IST)
    last_row = work.iloc[-1]
    last_dt: datetime = last_row["datetime"]
    session_date = last_dt.strftime("%Y-%m-%d")
    weekday = last_dt.strftime("%A")
    last_bar_time = last_dt.strftime("%H:%M")
    last_close = round(float(last_row["close"]), 2)

    closed_work = work[work["datetime"].apply(lambda d: is_15m_bar_closed(d, now_ist))].copy()
    if closed_work.empty:
        closed_work = work.iloc[:-1].copy() if len(work) > 1 else work.copy()

    closed_work["sma2"] = closed_work["close"].rolling(COMMON_RULES["fast_sma"]).mean()
    closed_work["sma10"] = closed_work["close"].rolling(COMMON_RULES["slow_sma"]).mean()
    last_closed_row = closed_work.iloc[-1]
    sma2_val = round(float(last_closed_row["sma2"]), 2) if pd.notna(last_closed_row["sma2"]) else last_close
    sma10_val = round(float(last_closed_row["sma10"]), 2) if pd.notna(last_closed_row["sma10"]) else last_close

    avoid_today = weekday in cfg["avoid_weekdays"]
    day_df = closed_work[closed_work["datetime"].dt.strftime("%Y-%m-%d") == session_date].copy()
    prev_all_df = closed_work[closed_work["datetime"].dt.strftime("%Y-%m-%d") < session_date].copy()

    gap_excluded = False
    gap_thresh = COMMON_RULES["gap_down_threshold_pct"]
    if len(day_df) >= 1:
        first_open = float(day_df.iloc[0]["open"])
        prev_days = work[work["datetime"].dt.strftime("%Y-%m-%d") < session_date]
        if len(prev_days) >= 1 and first_open > 0:
            prev_close = float(prev_days.iloc[-1]["close"])
            gap_pct = ((first_open - prev_close) / prev_close) * 100.0
            if gap_pct <= gap_thresh:
                gap_excluded = True

    armed_dir: Optional[str] = None
    ref_sl: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    trades_today = 0
    confirmed_dir: Optional[str] = None
    confirmed_entry: Optional[float] = None
    confirmed_sl: Optional[float] = None
    confirmed_target: Optional[float] = None
    status = "WATCHING"
    status_reason = "Monitoring 15m closed candles for SMA(2)/SMA(10) arm and candle confirmation."

    primary_r = float(COMMON_RULES["primary_target_r"])
    blocked_slots = set(COMMON_RULES["blocked_times"])
    cutoff = COMMON_RULES["entry_cutoff"]
    eod_time = COMMON_RULES["eod_exit_time"]
    max_trades = int(COMMON_RULES["max_trades_per_day"])

    for idx in range(0, len(day_df)):
        cur_r = day_df.iloc[idx]
        bar_num = idx + 1
        bar_hm = cur_r["datetime"].strftime("%H:%M")
        o = float(cur_r["open"])
        h = float(cur_r["high"])
        l = float(cur_r["low"])
        c = float(cur_r["close"])
        sma2 = cur_r["sma2"]
        sma10 = cur_r["sma10"]

        prev_r = day_df.iloc[idx - 1] if idx >= 1 else (prev_all_df.iloc[-1] if len(prev_all_df) >= 1 else None)
        if prev_r is not None:
            prev_sma2 = prev_r["sma2"]
            prev_sma10 = prev_r["sma10"]
            if pd.notna(prev_sma2) and pd.notna(prev_sma10) and pd.notna(sma2) and pd.notna(sma10):
                p_diff = float(prev_sma2) - float(prev_sma10)
                c_diff = float(sma2) - float(sma10)
                bull_cross = (p_diff <= 0) and (c_diff > 0)
                bear_cross = (p_diff >= 0) and (c_diff < 0)
                if bull_cross:
                    armed_dir = "CALL"
                    ref_sl = float(prev_r["low"])
                elif bear_cross:
                    armed_dir = "PUT"
                    ref_sl = float(prev_r["high"])

        if armed_dir is not None and ref_sl is not None and pd.notna(sma10):
            if avoid_today or gap_excluded:
                continue
            if bar_hm in blocked_slots:
                if confirmed_dir is None:
                    status = "BLOCKED_SLOT"
                    status_reason = f"Armed {armed_dir} candle at {bar_hm} IST is in blocked chop slot ({', '.join(COMMON_RULES['blocked_times'])})."
                continue
            if bar_hm > cutoff:
                if confirmed_dir is None:
                    status = "CUTOFF_1400"
                    status_reason = f"Armed {armed_dir} candle at {bar_hm} IST is past {cutoff} IST entry cutoff."
                continue
            if trades_today >= max_trades:
                if confirmed_dir is None:
                    status = "MAX_TRADES"
                    status_reason = f"Daily trade cap ({max_trades}) reached for {session_date}."
                continue

            if bar_num >= 4 and ("09:45" <= bar_hm <= cutoff):
                body_low = min(o, c)
                body_high = max(o, c)
                lower_wick = body_low - l
                upper_wick = h - body_high
                sma10_f = float(sma10)

                confirmed = False
                sig_entry = round(c, 2)
                sig_sl = round(ref_sl, 2)
                r_pts = 0.0
                sig_tgt = None

                if armed_dir == "CALL":
                    risk_pts = c - ref_sl
                    if (c > o) and (l > sma10_f) and (lower_wick > upper_wick) and (risk_pts > 0):
                        confirmed = True
                        r_pts = max(1.0, risk_pts)
                        sig_tgt = round(c + primary_r * risk_pts, 2)
                elif armed_dir == "PUT":
                    risk_pts = ref_sl - c
                    if (c < o) and (h < sma10_f) and (upper_wick > lower_wick) and (risk_pts > 0):
                        confirmed = True
                        r_pts = max(1.0, risk_pts)
                        sig_tgt = round(c - primary_r * risk_pts, 2)

                if confirmed and sig_tgt is not None:
                    trades_today += 1
                    if confirmed_dir is None:
                        entry_price = sig_entry
                        stop_price = sig_sl
                        target_price = sig_tgt
                        confirmed_dir = armed_dir
                        confirmed_entry = sig_entry
                        confirmed_sl = sig_sl
                        confirmed_target = sig_tgt
                        status = f"CONFIRMED_{armed_dir}"
                        status_reason = f"15m confirmed {armed_dir} signal at {bar_hm} IST (Target {primary_r}R)."
                    armed_dir = None
                    ref_sl = None

    if avoid_today:
        status = "AVOID_DAY"
        status_reason = f"{index_key} on {weekday} is flagged as an avoid day ({cfg['day_note']})."
    elif gap_excluded:
        status = "GAP_EXCLUDED"
        status_reason = f"Session open gap triggered exclusion rule (<= {gap_thresh}%)."

    ladder_dir = monitored_trade["direction"] if monitored_trade else (confirmed_dir or armed_dir)
    ladder_entry = monitored_trade["entry_price"] if monitored_trade else (confirmed_entry if confirmed_entry is not None else entry_price)
    ladder_sl = monitored_trade["sl_price"] if monitored_trade else (confirmed_sl if confirmed_sl is not None else stop_price)
    ladder = build_target_ladder(ladder_dir, ladder_entry, ladder_sl, last_close)

    return SignalState(
        index_key=index_key,
        index_name=cfg["name"],
        symbol=cfg["symbol"],
        session_date=session_date,
        weekday=weekday,
        last_bar_time=last_bar_time,
        last_close=last_close,
        sma2=sma2_val,
        sma10=sma10_val,
        status=status,
        status_reason=status_reason,
        armed_dir=armed_dir,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        trades_today=trades_today,
        avoid_today=avoid_today,
        gap_excluded=gap_excluded,
        day_note=cfg["day_note"],
        ref_1y_r=cfg["ref_1y_r"],
        best_days=cfg["best_days"],
        avoid_weekdays=cfg["avoid_weekdays"],
        caution_weekdays=cfg["caution_weekdays"],
        target_ladder=ladder,
        strike_ladder=strike_ladder or {},
        monitored_trade=monitored_trade,
    )

from datetime import timedelta

def is_15m_bar_closed(bar_dt: datetime, now_ist: datetime, grace_sec: int = 2) -> bool:
    "Return True only after the 15-minute bar window has finished."
    bar_end = bar_dt.replace(second=0, microsecond=0) + timedelta(minutes=15)
    now_naive = now_ist.replace(tzinfo=None) if getattr(now_ist, "tzinfo", None) else now_ist
    bar_naive = bar_end.replace(tzinfo=None) if getattr(bar_end, "tzinfo", None) else bar_end
    return now_naive >= (bar_naive + timedelta(seconds=grace_sec))