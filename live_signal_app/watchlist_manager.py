from __future__ import annotations

import calendar
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SELECTION_FILE = DATA_DIR / "watchlist_selection.json"
MASTER_CACHE_FILE = DATA_DIR / "mcx_sym_master_cache.json"
QUOTES_CACHE_FILE = DATA_DIR / "watchlist_quotes_cache.json"
MCX_MASTER_URL = "https://public.fyers.in/sym_details/MCX_COM_sym_master.json"

MAX_WATCHLIST_ITEMS = 5
WATCHLIST_QUOTE_TTL_SEC = 60.0


CATALOG: list[dict[str, Any]] = [
    {"id": "GOLD_NEAR", "label": "GOLD (Near Month)", "exchange": "MCX", "root": "GOLD", "kind": "mcx_near_fut"},
    {"id": "GOLDPETAL_NEAR", "label": "GOLDPETAL (Near Month)", "exchange": "MCX", "root": "GOLDPETAL", "kind": "mcx_near_fut"},
    {"id": "CRUDEOIL_NEAR", "label": "CRUDEOIL (Near Month)", "exchange": "MCX", "root": "CRUDEOIL", "kind": "mcx_near_fut"},
    {"id": "CRUDEOILM_NEAR", "label": "CRUDEOIL M (Near Month)", "exchange": "MCX", "root": "CRUDEOILM", "kind": "mcx_near_fut"},
    {"id": "BULLDEX_NEAR", "label": "BULLDEX (Near Month)", "exchange": "MCX", "root": "MCXBULLDEX", "kind": "mcx_near_fut"},
    {"id": "SILVER_NEAR", "label": "SILVER (Near Month)", "exchange": "MCX", "root": "SILVER", "kind": "mcx_near_fut"},
    {"id": "NIFTY50", "label": "NIFTY 50", "exchange": "NSE", "symbol": "NSE:NIFTY50-INDEX", "kind": "static"},
    {"id": "BANKNIFTY", "label": "BANKNIFTY", "exchange": "NSE", "symbol": "NSE:NIFTYBANK-INDEX", "kind": "static"},
    {"id": "SENSEX", "label": "SENSEX", "exchange": "BSE", "symbol": "BSE:SENSEX-INDEX", "kind": "static"},
    {"id": "FINNIFTY", "label": "FINNIFTY", "exchange": "NSE", "symbol": "NSE:FINNIFTY-INDEX", "kind": "static"},
]

DEFAULT_SELECTION = [
    "GOLD_NEAR",
    "CRUDEOIL_NEAR",
    "CRUDEOILM_NEAR",
    "BULLDEX_NEAR",
    "SILVER_NEAR",
]

_LOCK = threading.Lock()
_MASTER_MEM: dict[str, Any] = {"date": "", "records": []}


def _month_code_yy_mmm(dt: datetime) -> str:
    yy = dt.strftime("%y")
    mmm = calendar.month_abbr[dt.month].upper()
    return f"{yy}{mmm}"


def _parse_expiry_dt(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        ts = float(raw)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
    s = str(raw).strip()
    if s.isdigit():
        ts = float(s)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _load_mcx_master_records() -> list[dict[str, Any]]:
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")

    with _LOCK:
        if _MASTER_MEM["date"] == today_str and _MASTER_MEM["records"]:
            return list(_MASTER_MEM["records"])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if MASTER_CACHE_FILE.exists():
        try:
            cached = json.loads(MASTER_CACHE_FILE.read_text(encoding="utf-8"))
            if cached.get("date") == today_str and isinstance(cached.get("records"), list) and cached["records"]:
                with _LOCK:
                    _MASTER_MEM["date"] = today_str
                    _MASTER_MEM["records"] = cached["records"]
                return list(cached["records"])
        except Exception:
            pass

    records: list[dict[str, Any]] = []
    try:
        req = urllib.request.Request(MCX_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if isinstance(payload, dict):
            for sym_key, meta in payload.items():
                if not isinstance(meta, dict):
                    continue
                ticker = str(meta.get("symTicker") or sym_key or "").strip()
                if not ticker.startswith("MCX:"):
                    continue
                records.append({
                    "symbol": ticker,
                    "exSymName": str(meta.get("exSymName") or ""),
                    "symDetails": str(meta.get("symDetails") or ""),
                    "expiryDate": meta.get("expiryDate"),
                    "optType": str(meta.get("optType") or ""),
                })
        elif isinstance(payload, list):
            for meta in payload:
                if not isinstance(meta, dict):
                    continue
                ticker = str(meta.get("symTicker") or meta.get("symbol") or "").strip()
                if not ticker.startswith("MCX:"):
                    continue
                records.append({
                    "symbol": ticker,
                    "exSymName": str(meta.get("exSymName") or ""),
                    "symDetails": str(meta.get("symDetails") or ""),
                    "expiryDate": meta.get("expiryDate"),
                    "optType": str(meta.get("optType") or ""),
                })
    except Exception:
        records = []

    if records:
        try:
            MASTER_CACHE_FILE.write_text(
                json.dumps({"date": today_str, "records": records}),
                encoding="utf-8",
            )
        except Exception:
            pass
        with _LOCK:
            _MASTER_MEM["date"] = today_str
            _MASTER_MEM["records"] = records

    return records


def resolve_mcx_near_month(root: str) -> dict[str, str]:
    root_u = root.strip().upper().replace(" ", "")
    now_ist = datetime.now(IST)
    today_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

    candidates: list[tuple[datetime, str]] = []
    for rec in _load_mcx_master_records():
        sym = rec["symbol"]
        core = sym.split(":", 1)[-1]
        if not core.startswith(root_u) or not core.endswith("FUT"):
            continue
        suffix = core[len(root_u):-3]
        # Monthly futures typically end in 2 digits + 3 alpha letters (e.g., 26NOV, 26DEC)
        if len(suffix) < 5 or not suffix[:2].isdigit() or not suffix[2:].isalpha():
            continue
        exp_dt = _parse_expiry_dt(rec.get("expiryDate"))
        if exp_dt is not None and exp_dt >= today_start:
            candidates.append((exp_dt, sym))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        best_exp, best_sym = candidates[0]
        return {
            "symbol": best_sym,
            "expiry_date": best_exp.strftime("%Y-%m-%d"),
            "source": "mcx_master",
        }

    fallback_code = _month_code_yy_mmm(now_ist)
    return {
        "symbol": f"MCX:{root_u}{fallback_code}FUT",
        "expiry_date": "",
        "source": "month_fallback",
    }


def get_catalog() -> list[dict[str, Any]]:
    return [dict(item) for item in CATALOG]


def load_selected_ids() -> list[str]:
    valid_ids = {item["id"] for item in CATALOG}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SELECTION_FILE.exists():
        try:
            raw = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                cleaned: list[str] = []
                for x in raw:
                    sx = str(x).strip()
                    if sx in valid_ids and sx not in cleaned:
                        cleaned.append(sx)
                    if len(cleaned) >= MAX_WATCHLIST_ITEMS:
                        break
                if cleaned:
                    return cleaned
        except Exception:
            pass
    return list(DEFAULT_SELECTION[:MAX_WATCHLIST_ITEMS])


def save_selected_ids(selected_ids: list[str]) -> list[str]:
    valid_ids = {item["id"] for item in CATALOG}
    cleaned: list[str] = []
    for x in selected_ids:
        sx = str(x).strip()
        if sx in valid_ids and sx not in cleaned:
            cleaned.append(sx)
        if len(cleaned) >= MAX_WATCHLIST_ITEMS:
            break
    if not cleaned:
        cleaned = list(DEFAULT_SELECTION[:MAX_WATCHLIST_ITEMS])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SELECTION_FILE.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    return cleaned


def resolve_watchlist_items(selected_ids: list[str] | None = None) -> list[dict[str, Any]]:
    ids = load_selected_ids() if selected_ids is None else save_selected_ids(selected_ids)
    by_id = {item["id"]: item for item in CATALOG}
    resolved: list[dict[str, Any]] = []
    for item_id in ids[:MAX_WATCHLIST_ITEMS]:
        meta = by_id.get(item_id)
        if not meta:
            continue
        if meta["kind"] == "mcx_near_fut":
            info = resolve_mcx_near_month(meta["root"])
            resolved.append({
                "id": meta["id"],
                "label": meta["label"],
                "exchange": meta["exchange"],
                "kind": meta["kind"],
                "symbol": info["symbol"],
                "expiry_date": info["expiry_date"],
                "rollover_source": info["source"],
            })
        else:
            resolved.append({
                "id": meta["id"],
                "label": meta["label"],
                "exchange": meta["exchange"],
                "kind": meta["kind"],
                "symbol": meta["symbol"],
                "expiry_date": "",
                "rollover_source": "static",
            })
    return resolved


def _load_saved_quotes_cache() -> dict[str, dict[str, Any]]:
    if QUOTES_CACHE_FILE.exists():
        try:
            raw = json.loads(QUOTES_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("quotes"), dict):
                return raw["quotes"]
        except Exception:
            pass
    return {}


def _save_quotes_cache(quotes_map: dict[str, dict[str, Any]]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        QUOTES_CACHE_FILE.write_text(
            json.dumps({
                "saved_at_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
                "quotes": quotes_map,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


_MEM_QUOTE_CACHE: dict[str, Any] = {"mono": 0.0, "key": "", "quotes": {}}


def fetch_watchlist_quotes(selected_ids: list[str] | None = None) -> dict[str, Any]:
    import os
    from dotenv import load_dotenv
    from fyers_auth import load_access_token
    from fyers_apiv3 import fyersModel

    items = resolve_watchlist_items(selected_ids)
    now_ist_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    now_mono = time.monotonic()
    symbols = [it["symbol"] for it in items if it.get("symbol")]
    cache_key = ",".join(symbols)

    saved_quotes = _load_saved_quotes_cache()
    quotes_map: dict[str, dict[str, Any]] = {}
    served_from_cache = False

    with _LOCK:
        if (
            _MEM_QUOTE_CACHE["key"] == cache_key
            and _MEM_QUOTE_CACHE["quotes"]
            and (now_mono - float(_MEM_QUOTE_CACHE["mono"])) < WATCHLIST_QUOTE_TTL_SEC
        ):
            quotes_map = {
                sym: {**q, "feed_status": "CACHED_60S"}
                for sym, q in _MEM_QUOTE_CACHE["quotes"].items()
            }
            served_from_cache = True

    auth_token = ""
    client_id = ""
    try:
        load_dotenv(BASE_DIR / ".env")
        client_id = (os.getenv("FYERS_APP_ID") or os.getenv("FYERS_CLIENT_ID") or "").strip()
        raw_tok_obj = load_access_token()
        if isinstance(raw_tok_obj, dict):
            raw_tok = str(
                raw_tok_obj.get("access_token")
                or raw_tok_obj.get("token")
                or os.getenv("FYERS_ACCESS_TOKEN")
                or ""
            ).strip()
        else:
            raw_tok = str(raw_tok_obj or os.getenv("FYERS_ACCESS_TOKEN") or "").strip()
        auth_token = raw_tok.split(":", 1)[-1].strip() if raw_tok else ""
    except Exception:
        auth_token = ""
        client_id = ""

    live_connected = bool(auth_token and client_id)
    rate_limited = False

    if not served_from_cache and live_connected and symbols:
        try:
            fyers = fyersModel.FyersModel(
                client_id=client_id,
                is_async=False,
                token=auth_token,
                log_path="",
            )
            resp = fyers.quotes({"symbols": ",".join(symbols)})
            if isinstance(resp, dict) and str(resp.get("s")).lower() == "ok":
                fresh_map: dict[str, dict[str, Any]] = {}
                for row in resp.get("d") or []:
                    if not isinstance(row, dict):
                        continue
                    sym = str(row.get("n") or row.get("symbol") or "").strip()
                    v = row.get("v") if isinstance(row.get("v"), dict) else row
                    ltp = v.get("lp") if v.get("lp") is not None else v.get("ltp")
                    fresh_map[sym] = {
                        "ltp": ltp,
                        "change": v.get("ch"),
                        "change_pct": v.get("chp"),
                        "high": v.get("high_price"),
                        "low": v.get("low_price"),
                        "prev_close": v.get("prev_close_price"),
                        "feed_status": "LIVE",
                    }
                if fresh_map:
                    quotes_map = fresh_map
                    _save_quotes_cache(fresh_map)
                    with _LOCK:
                        _MEM_QUOTE_CACHE["mono"] = now_mono
                        _MEM_QUOTE_CACHE["key"] = cache_key
                        _MEM_QUOTE_CACHE["quotes"] = fresh_map
            else:
                msg = str((resp or {}).get("message") or "").lower()
                code = (resp or {}).get("code")
                if code in (-360, 1015, "1015", "-360") or "limit" in msg or "5,000" in msg:
                    rate_limited = True
        except Exception:
            pass

    rows: list[dict[str, Any]] = []
    for it in items:
        sym = it["symbol"]
        q = quotes_map.get(sym)
        if not q and sym in saved_quotes:
            base_q = dict(saved_quotes[sym])
            base_q["feed_status"] = "DAILY_LIMIT_CACHED" if rate_limited else "SAVED_CACHE"
            q = base_q
        if not q:
            q = {
                "ltp": None,
                "change": None,
                "change_pct": None,
                "high": None,
                "low": None,
                "prev_close": None,
                "feed_status": "DAILY_LIMIT" if rate_limited else ("NO_TOKEN" if not live_connected else "NO_QUOTE"),
            }
        rows.append({
            **it,
            "ltp": q.get("ltp"),
            "change": q.get("change"),
            "change_pct": q.get("change_pct"),
            "high": q.get("high"),
            "low": q.get("low"),
            "prev_close": q.get("prev_close"),
            "feed_status": q.get("feed_status"),
            "checked_at": now_ist_str,
        })

    return {
        "max_items": MAX_WATCHLIST_ITEMS,
        "selected_ids": [r["id"] for r in rows],
        "catalog": get_catalog(),
        "items": rows,
        "checked_at": now_ist_str,
        "fyers_connected": live_connected,
        "rate_limited": rate_limited,
        "cache_ttl_sec": int(WATCHLIST_QUOTE_TTL_SEC),
    }