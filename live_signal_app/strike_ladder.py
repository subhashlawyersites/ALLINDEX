from typing import Optional
from fyers_auth import get_fyers_client

STRIKE_STEPS = {
    "NIFTY50": 50,
    "SENSEX": 100,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
}

OPTION_PREFIXES = {
    "NIFTY50": "NSE:NIFTY",
    "SENSEX": "BSE:SENSEX",
    "BANKNIFTY": "NSE:BANKNIFTY",
    "FINNIFTY": "NSE:FINNIFTY",
}

TARGET_PCTS = [50, 60, 70, 80, 90, 100]


def build_target_ladder(
    direction: Optional[str],
    entry_price: Optional[float],
    sl_price: Optional[float],
    current_price: Optional[float],
) -> dict:
    """
    Computes 50%, 60% (primary strategy target), 70%, 80%, 90%, and 100% R target levels
    plus realtime progress from SL (-1.0R / 0% bar) toward 1.0R (100% bar).
    """
    if not direction or entry_price is None or sl_price is None:
        return {
            "active": False,
            "direction": direction,
            "entry": entry_price,
            "sl": sl_price,
            "risk_pts": None,
            "progress_pct": 0.0,
            "milestone_label": "Waiting for signal",
            "levels": [],
        }

    is_call = direction.upper() == "CALL"
    risk_pts = (entry_price - sl_price) if is_call else (sl_price - entry_price)
    if risk_pts <= 0:
        risk_pts = abs(entry_price - sl_price) or 1.0

    levels = []
    for pct in TARGET_PCTS:
        mult = pct / 100.0
        lvl_price = (entry_price + mult * risk_pts) if is_call else (entry_price - mult * risk_pts)
        levels.append({
            "pct": pct,
            "r_mult": round(mult, 2),
            "price": round(lvl_price, 2),
            "is_primary": pct == 60,
        })

    # Map current_price relative to SL (-1.0R -> 0%) and +1.0R (100%)
    progress_pct = 0.0
    milestone_label = "No live price"
    if current_price is not None and risk_pts > 0:
        if is_call:
            r_progress = (current_price - sl_price) / (2.0 * risk_pts)
        else:
            r_progress = (sl_price - current_price) / (2.0 * risk_pts)
        progress_pct = round(max(0.0, min(100.0, r_progress * 100.0)), 1)

        if current_price <= sl_price if is_call else current_price >= sl_price:
            milestone_label = "At / Below SL (-1.0R)"
        elif progress_pct >= 100.0:
            milestone_label = "100% Target Reached (+1.0R)"
        elif progress_pct >= 90.0:
            milestone_label = "90% Target Zone"
        elif progress_pct >= 80.0:
            milestone_label = "80% Target Zone"
        elif progress_pct >= 70.0:
            milestone_label = "70% Target Zone"
        elif progress_pct >= 60.0:
            milestone_label = "60% Primary Target Hit (+0.60R)"
        elif progress_pct >= 50.0:
            milestone_label = "50% Target Zone"
        else:
            milestone_label = "Active (0%–50% Zone)"

    return {
        "active": True,
        "direction": direction.upper(),
        "entry": round(float(entry_price), 2),
        "sl": round(float(sl_price), 2),
        "risk_pts": round(float(risk_pts), 2),
        "progress_pct": progress_pct,
        "milestone_label": milestone_label,
        "levels": levels,
    }


def build_strike_ladder(index_key: str, spot_price: float, direction: Optional[str] = None) -> dict:
    """
    Builds ITM1, ATM, and OTM1..OTM10 strike levels for the given index and spot price.
    Attempts live Fyers quote lookup if connected, while remaining usable offline.
    """
    key = index_key.upper()
    step = STRIKE_STEPS.get(key, 50)
    if not spot_price or spot_price <= 0:
        return {"index": key, "step": step, "atm": None, "rows": [], "quote_source": "CALCULATED_ONLY"}

    atm = int(round(float(spot_price) / step) * step)
    opt_dir = (direction or "CALL").upper()
    is_put = opt_dir == "PUT"

    # For CALL: ITM1 is below ATM (-1*step), OTM1..OTM10 are above ATM (+1..+10*step)
    # For PUT:  ITM1 is above ATM (+1*step), OTM1..OTM10 are below ATM (-1..-10*step)
    specs = [("ITM1", -1 if not is_put else 1), ("ATM", 0)]
    for i in range(1, 11):
        offset_step = i if not is_put else -i
        specs.append((f"OTM{i}", offset_step))

    rows = []
    symbol_map = {}
    prefix = OPTION_PREFIXES.get(key)
    opt_suffix = "PE" if is_put else "CE"

    for label, mult in specs:
        strike = atm + (mult * step)
        fyers_sym = f"{prefix}{strike}{opt_suffix}" if prefix else None
        if fyers_sym:
            symbol_map[fyers_sym] = label
        rows.append({
            "label": label,
            "strike": strike,
            "option_type": opt_suffix,
            "symbol": fyers_sym,
            "ltp": None,
        })

    quote_source = "CALCULATED_ONLY"
    client = get_fyers_client()
    if client is not None and symbol_map:
        try:
            q_resp = client.quotes({"symbols": ",".join(symbol_map.keys())})
            if isinstance(q_resp, dict) and q_resp.get("s") == "ok" and q_resp.get("d"):
                ltp_by_sym = {}
                for item in q_resp["d"]:
                    sym = item.get("n") or item.get("symbol")
                    v = item.get("v") or {}
                    if sym and "lp" in v:
                        ltp_by_sym[sym] = v["lp"]
                if ltp_by_sym:
                    quote_source = "FYERS_QUOTES"
                    for r in rows:
                        if r["symbol"] in ltp_by_sym:
                            r["ltp"] = ltp_by_sym[r["symbol"]]
        except Exception:
            pass

    return {
        "index": key,
        "direction": opt_dir,
        "spot": round(float(spot_price), 2),
        "step": step,
        "atm": atm,
        "quote_source": quote_source,
        "rows": rows,
    }
