import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from fyers_auth import load_access_token
from signal_engine import INDEX_CONFIGS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
load_dotenv(BASE_DIR / ".env")

IST = ZoneInfo("Asia/Kolkata")

_CACHE_LOCK = threading.Lock()
_HISTORY_CACHE: dict[str, tuple[float, pd.DataFrame, str]] = {}
_QUOTE_CACHE: dict[str, tuple[float, dict]] = {}
_SESSION_CANDLES: dict[str, pd.DataFrame] = {}

_RATE_LIMIT_UNTIL = 0.0
_LAST_RATE_LOG = 0.0
_LAST_FYERS_CALL_MONO = 0.0

HISTORY_TTL_SEC = 120.0
QUOTE_TTL_SEC = 10.0
RATE_BACKOFF_SEC = 90.0
MIN_FYERS_INTERVAL_SEC = 1.6

HISTORY_DISK_CACHE_FILE = DATA_DIR / "fyers_history_cache.json"


def _is_rate_limited(resp: object) -> bool:
    if not isinstance(resp, dict):
        return False
    code = resp.get("code")
    msg = str(resp.get("message") or "").lower()
    return (
        code in (-360, 1015, "1015", "-360")
        or "50 requests/minute" in msg
        or "5 requests/second" in msg
        or "too many requests" in msg
    )


def _note_rate_limit(context_label: str, resp: object) -> None:
    global _RATE_LIMIT_UNTIL, _LAST_RATE_LOG
    now = time.monotonic()
    _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, now + RATE_BACKOFF_SEC)
    if now - _LAST_RATE_LOG >= 15.0:
        _LAST_RATE_LOG = now
        print(f"[Fyers rate-limit backoff {int(RATE_BACKOFF_SEC)}s] {context_label}: {resp}")


def _acquire_fyers_slot() -> bool:
    global _LAST_FYERS_CALL_MONO
    now = time.monotonic()
    if now < _RATE_LIMIT_UNTIL:
        return False
    if (now - _LAST_FYERS_CALL_MONO) < MIN_FYERS_INTERVAL_SEC:
        return False
    _LAST_FYERS_CALL_MONO = now
    return True


def _is_equity_session_open(now_ist: datetime) -> bool:
    if now_ist.weekday() >= 5:
        return False
    mins = now_ist.hour * 60 + now_ist.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


def _merge_live_quote_into_df(df: pd.DataFrame, live_quote: dict, now_ist: datetime) -> pd.DataFrame:
    if df is None or df.empty or not isinstance(live_quote, dict) or live_quote.get("ltp") is None:
        return df
    if not _is_equity_session_open(now_ist):
        return df
    try:
        ltp = float(live_quote["ltp"])
        if ltp <= 0:
            return df
        out = df.copy()
        idx_last = out.index[-1]
        cur_high = float(out.loc[idx_last, "high"])
        cur_low = float(out.loc[idx_last, "low"])
        out.loc[idx_last, "close"] = ltp
        out.loc[idx_last, "high"] = max(cur_high, ltp)
        out.loc[idx_last, "low"] = min(cur_low, ltp)
        if "timestamp_ist" in out.columns and live_quote.get("updated_ist"):
            out.loc[idx_last, "timestamp_ist"] = now_ist.strftime("%Y-%m-%d %H:%M:%S%z")
        return out
    except Exception:
        return df


def _load_disk_history_cache() -> dict:
    if HISTORY_DISK_CACHE_FILE.exists():
        try:
            data = json.loads(HISTORY_DISK_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_disk_history_cache(cache_obj: dict) -> None:
    try:
        HISTORY_DISK_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_DISK_CACHE_FILE.write_text(json.dumps(cache_obj), encoding="utf-8")
    except Exception:
        pass


def _load_csv_fallback(index_key: str) -> pd.DataFrame:
    csv_path = DATA_DIR / INDEX_CONFIGS[index_key]["csv_file"]
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing fallback CSV: {csv_path}")
    return pd.read_csv(csv_path)


def _candles_to_df(candles: list) -> pd.DataFrame:
    cols = ["epoch", "open", "high", "low", "close", "volume"]
    raw = [c[:6] for c in candles]
    df = pd.DataFrame(raw, columns=cols)
    df["epoch"] = pd.to_numeric(df["epoch"], errors="coerce")
    df = (
        df.dropna(subset=["epoch"])
        .drop_duplicates(subset=["epoch"])
        .sort_values("epoch")
        .reset_index(drop=True)
    )
    df["timestamp_ist"] = (
        pd.to_datetime(df["epoch"], unit="s", utc=True)
        .dt.tz_convert(IST)
        .dt.strftime("%Y-%m-%d %H:%M:%S%z")
    )
    return df


def fetch_latest_candles(index_key: str, lookback_days: int = 10, allow_fyers: bool = True) -> tuple[pd.DataFrame, str]:
    """
    Fetches recent 15m candles and live quotes from Fyers API v3 when allow_fyers=True
    and credentials are available, falling back to cached session bars or local CSVs.
    """
    cfg = INDEX_CONFIGS[index_key]
    client_id = (os.getenv("FYERS_APP_ID") or os.getenv("FYERS_CLIENT_ID") or "").strip()
    access_token = (load_access_token() or os.getenv("FYERS_ACCESS_TOKEN") or "").strip()

    if allow_fyers and client_id and access_token:
        now_mono = time.monotonic()
        with _CACHE_LOCK:
            cached_hist = _HISTORY_CACHE.get(index_key)
            cached_quote = _QUOTE_CACHE.get(cfg["symbol"])
            rate_limited = now_mono < _RATE_LIMIT_UNTIL

        if rate_limited and cached_hist is not None:
            _, df_cached, src_cached = cached_hist
            df_out = _merge_live_quote_into_df(df_cached, cached_quote[1] if cached_quote else {}, datetime.now(IST))
            if cached_quote is not None and cached_quote[1]:
                df_out.attrs["live_quote"] = dict(cached_quote[1])
            return df_out, src_cached

        if not rate_limited:
            try:
                from fyers_apiv3 import fyersModel

                auth_token = access_token.split(":", 1)[-1].strip()
                fyers = fyersModel.FyersModel(
                    client_id=client_id,
                    is_async=False,
                    token=auth_token,
                    log_path=str(DATA_DIR),
                )
                now_ist = datetime.now(IST)

                live_quote = {}
                if cached_quote is not None and (now_mono - cached_quote[0]) < QUOTE_TTL_SEC:
                    live_quote = dict(cached_quote[1])
                elif _acquire_fyers_slot():
                    try:
                        q_resp = fyers.quotes(data={"symbols": cfg["symbol"]})
                        if _is_rate_limited(q_resp):
                            _note_rate_limit(f"quotes {index_key}", q_resp)
                            if cached_quote is not None:
                                live_quote = dict(cached_quote[1])
                        elif isinstance(q_resp, dict) and q_resp.get("s") == "ok" and q_resp.get("d"):
                            v = q_resp["d"][0].get("v") or {}
                            live_quote = {
                                "symbol": cfg["symbol"],
                                "ltp": v.get("lp"),
                                "change": v.get("ch"),
                                "change_pct": v.get("chp"),
                                "high": v.get("high_price"),
                                "low": v.get("low_price"),
                                "prev_close": v.get("prev_close_price"),
                                "updated_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                            }
                            with _CACHE_LOCK:
                                _QUOTE_CACHE[cfg["symbol"]] = (time.monotonic(), dict(live_quote))
                    except Exception as q_exc:
                        print(f"[Fyers quotes error] {index_key} ({cfg['symbol']}): {q_exc}")
                elif cached_quote is not None:
                    live_quote = dict(cached_quote[1])

                if cached_hist is not None and (now_mono - cached_hist[0]) < HISTORY_TTL_SEC:
                    df_out = _merge_live_quote_into_df(cached_hist[1], live_quote, now_ist)
                    if live_quote:
                        df_out.attrs["live_quote"] = live_quote
                    return df_out, cached_hist[2]

                to_dt = now_ist - timedelta(minutes=1)
                from_dt = now_ist - timedelta(days=lookback_days)
                payload = {
                    "symbol": cfg["symbol"],
                    "resolution": "15",
                    "date_format": "1",
                    "range_from": from_dt.strftime("%Y-%m-%d"),
                    "range_to": to_dt.strftime("%Y-%m-%d"),
                    "cont_flag": "1",
                }
                today_str = now_ist.strftime("%Y-%m-%d")
                is_historical_only = payload["range_to"] < today_str
                hist_cache_key = f"{cfg['symbol']}_15_{payload['range_from']}_{payload['range_to']}"
                disk_cache = _load_disk_history_cache() if is_historical_only else {}

                if is_historical_only and hist_cache_key in disk_cache:
                    resp = disk_cache[hist_cache_key]
                elif _acquire_fyers_slot():
                    resp = fyers.history(data=payload)
                    if is_historical_only and isinstance(resp, dict) and resp.get("s") == "ok" and resp.get("candles"):
                        disk_cache[hist_cache_key] = resp
                        _save_disk_history_cache(disk_cache)
                else:
                    resp = None

                if _is_rate_limited(resp):
                    _note_rate_limit(f"history {index_key}", resp)
                    if cached_hist is not None:
                        df_out = _merge_live_quote_into_df(cached_hist[1], live_quote, now_ist)
                        if live_quote:
                            df_out.attrs["live_quote"] = live_quote
                        return df_out, cached_hist[2]
                elif isinstance(resp, dict) and resp.get("s") == "ok" and resp.get("candles"):
                    df_base = _candles_to_df(resp["candles"])
                    with _CACHE_LOCK:
                        _HISTORY_CACHE[index_key] = (time.monotonic(), df_base.copy(), "FYERS_LIVE_15M")
                        _SESSION_CANDLES[index_key] = df_base.copy()
                    df_out = _merge_live_quote_into_df(df_base, live_quote, now_ist)
                    if live_quote:
                        df_out.attrs["live_quote"] = live_quote
                    return df_out, "FYERS_LIVE_15M"
                elif resp is not None:
                    print(f"[Fyers history non-ok] {index_key} ({cfg['symbol']}): {resp}")
            except Exception as exc:
                print(f"[Fyers history error] {index_key} ({cfg['symbol']}): {exc}")

    with _CACHE_LOCK:
        sess_df = _SESSION_CANDLES.get(index_key)
    if sess_df is not None and not sess_df.empty:
        return sess_df.copy(), "SESSION_CACHED_15M"

    df_csv = _load_csv_fallback(index_key)
    with _CACHE_LOCK:
        if index_key not in _SESSION_CANDLES and df_csv is not None and not df_csv.empty:
            _SESSION_CANDLES[index_key] = df_csv.copy()
    return df_csv, "LOCAL_CSV_1Y"