import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from dotenv import load_dotenv
from signal_engine import INDEX_CONFIGS

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
load_dotenv(BASE_DIR / ".env")

IST = ZoneInfo("Asia/Kolkata")


def _load_csv_fallback(index_key: str) -> pd.DataFrame:
    csv_path = DATA_DIR / INDEX_CONFIGS[index_key]["csv_file"]
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing fallback CSV: {csv_path}")
    return pd.read_csv(csv_path)


def fetch_latest_candles(index_key: str, lookback_days: int = 10) -> tuple[pd.DataFrame, str]:
    """
    Attempts to fetch recent completed 15m candles from Fyers API v3 using credentials
    in C:\AllIndicesBacktest\.env, falling back cleanly to local CSVs if unavailable.
    """
    cfg = INDEX_CONFIGS[index_key]
    client_id = os.getenv("FYERS_CLIENT_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()

    if client_id and access_token:
        try:
            from fyers_apiv3 import fyersModel

            auth_token = access_token if ":" in access_token else f"{client_id}:{access_token}"
            fyers = fyersModel.FyersModel(client_id=client_id, is_async=False, token=auth_token, log_path=str(DATA_DIR))

            now_ist = datetime.now(IST)
            # Request up to previous completed minute so partial bars are avoided
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
            resp = fyers.history(data=payload)
            if isinstance(resp, dict) and resp.get("s") == "ok" and resp.get("candles"):
                cols = ["epoch", "open", "high", "low", "close", "volume"]
                raw = [c[:6] for c in resp["candles"]]
                df = pd.DataFrame(raw, columns=cols)
                df["epoch"] = pd.to_numeric(df["epoch"], errors="coerce")
                df = df.dropna(subset=["epoch"]).drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
                df["timestamp_ist"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d %H:%M:%S%z")
                return df, "FYERS_LIVE_15M"
        except Exception:
            pass

    return _load_csv_fallback(index_key), "LOCAL_CSV_1Y"
