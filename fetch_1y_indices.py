from datetime import date, timedelta
from pathlib import Path
import time
import pandas as pd
from dotenv import load_dotenv
import os
from fyers_apiv3 import fyersModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ENV_PATH = BASE_DIR / ".env"

INDEX_SYMBOLS = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
}

def get_client() -> fyersModel.FyersModel:
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    client_id = (os.getenv("FYERS_CLIENT_ID") or "").strip().strip('"').strip("'")
    token = (os.getenv("FYERS_ACCESS_TOKEN") or "").strip().strip('"').strip("'")
    if not client_id or not token:
        raise RuntimeError("Set FYERS_CLIENT_ID and FYERS_ACCESS_TOKEN in C:\\AllIndicesBacktest\\.env first.")
    return fyersModel.FyersModel(client_id=client_id, token=token, log_path="")

def fetch_1y_15m(symbol: str, days_total: int = 365, chunk_days: int = 95) -> pd.DataFrame:
    client = get_client()
    end_d = date.today()
    start_limit = end_d - timedelta(days=days_total)
    cur_end = end_d
    frames = []

    while cur_end > start_limit:
        cur_start = max(start_limit, cur_end - timedelta(days=chunk_days))
        resp = client.history({
            "symbol": symbol,
            "resolution": "15",
            "date_format": "1",
            "range_from": cur_start.strftime("%Y-%m-%d"),
            "range_to": cur_end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        })
        if resp.get("s") == "ok" and resp.get("candles"):
            df_chunk = pd.DataFrame(
                resp["candles"], columns=["epoch", "open", "high", "low", "close", "volume"]
            )
            frames.append(df_chunk)
        cur_end = cur_start - timedelta(days=1)
        time.sleep(0.25)

    if not frames:
        raise RuntimeError(f"No candles returned for {symbol} (check Fyers token/auth).")

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["epoch"]).sort_values("epoch").reset_index(drop=True)
    out["timestamp_ist"] = pd.to_datetime(out["epoch"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    out["weekday"] = out["timestamp_ist"].dt.day_name()
    return out

if __name__ == "__main__":
    for name, sym in INDEX_SYMBOLS.items():
        try:
            df = fetch_1y_15m(sym, days_total=365)
            out_path = DATA_DIR / f"{name.lower()}_15m_1y.csv"
            df.to_csv(out_path, index=False)
            print(f"[OK] {name} ({sym}): {len(df)} bars | {df['timestamp_ist'].min()} -> {df['timestamp_ist'].max()}")
        except Exception as exc:
            print(f"[ERR] {name} ({sym}): {exc}")
