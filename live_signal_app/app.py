from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
from flask import Flask, Response, jsonify, render_template, request
from fyers_auth import fyers_auth_bp
import pandas as pd
from analytics_data import build_historical_analytics
from data_provider import fetch_latest_candles
from fyers_auth import get_fyers_config, save_or_authorize_fyers, test_fyers_connection
from signal_engine import COMMON_RULES, INDEX_CONFIGS, evaluate_index_signal
from strike_ladder import build_strike_ladder, build_target_ladder
from trade_monitor import (
    close_monitored_trade,
    evaluate_monitored_trades_against_market,
    get_active_trades,
    get_recent_events,
    record_event,
    start_monitored_trade,
)

IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__)
app.register_blueprint(fyers_auth_bp)



FEED_STATE_FILE = Path(__file__).resolve().parent / ".index_feed_state.json"
DEFAULT_FEED_STATE = {
    "NIFTY50": True,
    "SENSEX": False,
    "BANKNIFTY": False,
    "FINNIFTY": False,
}


def get_feed_states() -> dict[str, bool]:
    state = dict(DEFAULT_FEED_STATE)
    if FEED_STATE_FILE.exists():
        try:
            saved = json.loads(FEED_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for k in DEFAULT_FEED_STATE:
                    if k in saved:
                        state[k] = bool(saved[k])
        except Exception:
            pass
    return state


def set_index_feed_enabled(index_key: str, enabled: bool) -> dict[str, bool]:
    state = get_feed_states()
    k = index_key.upper()
    if k in state:
        state[k] = bool(enabled)
        try:
            FEED_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            FEED_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass
    return state



ACTIVE_FEED_FILE = Path(__file__).resolve().parent / ".active_feed_index.json"
_ACTIVE_FEED_KEY = "NIFTY50"


def get_active_feed_index() -> str:
    global _ACTIVE_FEED_KEY
    if ACTIVE_FEED_FILE.exists():
        try:
            saved = json.loads(ACTIVE_FEED_FILE.read_text(encoding="utf-8")).get("active_index", "")
            if isinstance(saved, str) and saved.upper() in INDEX_CONFIGS:
                _ACTIVE_FEED_KEY = saved.upper()
        except Exception:
            pass
    return _ACTIVE_FEED_KEY


def set_active_feed_index(index_key: str) -> str:
    global _ACTIVE_FEED_KEY
    k = index_key.upper()
    if k in INDEX_CONFIGS:
        _ACTIVE_FEED_KEY = k
        try:
            ACTIVE_FEED_FILE.write_text(json.dumps({"active_index": k}), encoding="utf-8")
        except Exception:
            pass
    return _ACTIVE_FEED_KEY


def _build_all_states() -> tuple[list[dict], dict[str, str], dict]:
    states = []
    sources = {}
    price_snaps = {}

    active_feed_key = get_active_feed_index()
    for key in INDEX_CONFIGS:
        feed_on = (key == active_feed_key)
        df, src = fetch_latest_candles(key, allow_fyers=feed_on)
        st = evaluate_index_signal(df, key).to_dict()
        live_q = (df.attrs or {}).get("live_quote") if (feed_on and hasattr(df, "attrs")) else {}
        live_ltp = live_q.get("ltp") if isinstance(live_q, dict) else None
        st["live_quote"] = live_q or {}
        st["live_ltp"] = float(live_ltp) if live_ltp is not None else float(st.get("last_close") or 0.0)
        st["data_source"] = src
        st["feed_enabled"] = feed_on
        st["feed_state_label"] = "FEED RECEIVING" if feed_on else "FEED BLOCKED TEMPORARILY"

        last_row = df.iloc[-1] if not df.empty else {}
        spot_now = st["live_ltp"] if (feed_on and src == "FYERS_LIVE_15M" and live_ltp is not None) else float(last_row.get("close", st["last_close"]))
        st["spot_price"] = spot_now
        price_snaps[key] = {
            "high": max(float(last_row.get("high", spot_now)), spot_now),
            "low": min(float(last_row.get("low", spot_now)), spot_now),
            "close": spot_now,
            "time_str": (live_q.get("updated_ist") if isinstance(live_q, dict) and live_q.get("updated_ist") else st["last_bar_time"]),
        }

        if st["avoid_today"]:
            record_event(key, "AVOID_WEEKDAY", f"{st['weekday']} is avoidable for {key}", "warn")
        elif st["gap_excluded"]:
            record_event(key, "GAP_EXCLUDED", f"Session gap <= -0.75% on {key}", "bad")
        elif st["status"].startswith("CONFIRMED_"):
            record_event(key, st["status"], f"{key} confirmed {st['status']} @ {st['entry_price']}", "good")

        sources[key] = src
        states.append(st)
    evaluate_monitored_trades_against_market(price_snaps)
    active_map = get_active_trades()

    for st in states:
        k = st["index_key"]
        mon = active_map.get(k)
        st["monitored_trade"] = mon
        ref_entry = mon["entry_price"] if mon else st["entry_price"]
        ref_sl = mon["sl_price"] if mon else st["stop_price"]
        ref_dir = mon["direction"] if mon else (st["armed_dir"] or ("PUT" if st["status"].endswith("PUT") else "CALL"))

        st["target_ladder"] = build_target_ladder(
            direction=ref_dir,
            entry_price=ref_entry,
            sl_price=ref_sl,
            current_price=st.get("spot_price", st["last_close"]),
        )
        st["strike_ladder"] = build_strike_ladder(
            index_key=k,
            spot_price=st.get("spot_price", st["last_close"]),
            direction=ref_dir,
        )

    fyers_status = test_fyers_connection()
    return states, sources, fyers_status


@app.route("/")
def combined_dashboard():
    states, sources, fyers_status = _build_all_states()
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    return render_template(
        "combined.html",
        page_title="Combined Signal Command Center",
        active_nav="combined",
        states=states,
        sources=sources,
        fyers_status=fyers_status,
        events=get_recent_events(15),
        rules=COMMON_RULES,
        now_str=now_str,
        active_feed_index=get_active_feed_index(),
    )


@app.route("/auth", methods=["GET", "POST"])
def auth_page():
    auth_result = None
    if request.args.get("fyers_auth") == "ok" and not auth_result:
        auth_result = {"ok": True, "message": "Fresh Fyers access token exchanged and saved."}
    if request.method == "POST":
        auth_result = save_or_authorize_fyers(
            client_id=request.form.get("client_id", ""),
            secret_key=request.form.get("secret_key", ""),
            redirect_uri=request.form.get("redirect_uri", ""),
            auth_code=request.form.get("auth_code", ""),
            direct_token=request.form.get("direct_token", ""),
        )
    cfg = get_fyers_config()
    fyers_status = test_fyers_connection()
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    return render_template(
        "auth.html",
        page_title="Fyers Authorization & Token Setup",
        active_nav="auth",
        cfg=cfg,
        fyers_status=fyers_status,
        auth_result=auth_result,
        now_str=now_str,
        active_feed_index=get_active_feed_index(),
    )


@app.route("/<index_slug>")
def single_index_page(index_slug: str):
    key = index_slug.upper()
    if key not in INDEX_CONFIGS:
        states, sources, fyers_status = _build_all_states()
        return render_template(
            "combined.html",
            page_title="Combined Signal Command Center",
            active_nav="combined",
            states=states,
            sources=sources,
            fyers_status=fyers_status,
            events=get_recent_events(15),
            rules=COMMON_RULES,
            now_str=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        ), 404

    states, sources, fyers_status = _build_all_states()
    state = next(item for item in states if item["index_key"] == key)
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    return render_template(
        "index_page.html",
        page_title=f"{state['index_name']} Live Signal & Rules",
        active_nav=key.lower(),
        state=state,
        source=sources[key],
        fyers_status=fyers_status,
        events=[ev for ev in get_recent_events(25) if ev["index"] == key][:10],
        rules=COMMON_RULES,
        now_str=now_str,
        all_index_keys=list(INDEX_CONFIGS.keys()),
    )


@app.route("/api/fyers/connect", methods=["POST"])
def api_fyers_connect():
    payload = request.get_json(silent=True) or {}
    if payload.get("auth_code") or payload.get("direct_token") or payload.get("client_id"):
        res = save_or_authorize_fyers(
            client_id=payload.get("client_id", ""),
            secret_key=payload.get("secret_key", ""),
            redirect_uri=payload.get("redirect_uri", ""),
            auth_code=payload.get("auth_code", ""),
            direct_token=payload.get("direct_token", ""),
        )
        return jsonify(res)
    status = test_fyers_connection()
    return jsonify({"ok": status["connected"], "message": status["message"], "status": status})


@app.route("/api/monitor/<index_slug>/start", methods=["POST"])
def api_monitor_start(index_slug: str):
    key = index_slug.upper()
    if key not in INDEX_CONFIGS:
        return jsonify({"error": "Unknown index"}), 404
    body = request.get_json(silent=True) or {}
    rec = start_monitored_trade(
        index_key=key,
        direction=body.get("direction", "CALL"),
        entry_price=float(body["entry_price"]),
        sl_price=float(body["sl_price"]),
        target_price=float(body["target_price"]) if body.get("target_price") is not None else None,
        session_date=body.get("session_date"),
        weekday=body.get("weekday"),
        entry_time=body.get("entry_time"),
    )
    return jsonify({"ok": True, "monitored_trade": rec})


@app.route("/api/monitor/<index_slug>/close", methods=["POST"])
def api_monitor_close(index_slug: str):
    key = index_slug.upper()
    if key not in INDEX_CONFIGS:
        return jsonify({"error": "Unknown index"}), 404
    body = request.get_json(silent=True) or {}
    closed = close_monitored_trade(
        index_key=key,
        exit_price=float(body["exit_price"]),
        exit_reason=body.get("exit_reason", "MANUAL_EXIT"),
        exit_time=body.get("exit_time"),
    )
    if not closed:
        return jsonify({"ok": False, "message": "No active monitored trade for index"}), 404
    return jsonify({"ok": True, "closed_trade": closed})


@app.route("/api/events")
def api_events():
    idx = (request.args.get("index") or "").upper()
    evs = get_recent_events(40)
    if idx in INDEX_CONFIGS:
        evs = [e for e in evs if e["index"] == idx]
    return jsonify({"events": evs[:20]})


@app.route("/api/chart/<index_slug>")
def api_chart(index_slug: str):
    key = index_slug.upper()
    if key not in INDEX_CONFIGS:
        return jsonify({"error": "Unknown index"}), 404

    df, src = fetch_latest_candles(key)
    work = df.copy()
    ts = pd.to_datetime(work["timestamp_ist"], errors="coerce")
    if ts.isna().all() and "epoch" in work.columns:
        ts = pd.to_datetime(work["epoch"], unit="s", utc=True).dt.tz_convert(IST)
    work["ts"] = ts
    work = work.dropna(subset=["ts"]).sort_values("ts").tail(50).reset_index(drop=True)
    work["sma2"] = work["close"].rolling(2).mean()
    work["sma10"] = work["close"].rolling(10).mean()

    bars = [
        {
            "time": r["ts"].strftime("%d %b %H:%M"),
            "open": round(float(r["open"]), 2),
            "high": round(float(r["high"]), 2),
            "low": round(float(r["low"]), 2),
            "close": round(float(r["close"]), 2),
            "sma2": round(float(r["sma2"]), 2) if pd.notna(r["sma2"]) else None,
            "sma10": round(float(r["sma10"]), 2) if pd.notna(r["sma10"]) else None,
        }
        for _, r in work.iterrows()
    ]
    return jsonify({"index": key, "source": src, "bars": bars})


@app.route("/api/analytics/<scope>")
def api_analytics(scope: str):
    mode = request.args.get("mode", "refined")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 15))
    weekday = request.args.get("weekday", "")
    direction = request.args.get("direction", "")
    exit_reason = request.args.get("exit_reason", "")
    sort_by = request.args.get("sort_by", "session_date")
    sort_dir = request.args.get("sort_dir", "desc")
    data = build_historical_analytics(
        scope=scope,
        mode=mode,
        page=page,
        per_page=per_page,
        weekday_filter=weekday,
        direction_filter=direction,
        exit_filter=exit_reason,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return jsonify(data)






@app.route("/api/stream")
def api_stream():
    import json as _json
    import time as _time

    def _event_generator():
        while True:
            try:
                states, sources, fyers_status = _build_all_states()
                payload = {
                    "updated_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
                    "fyers": fyers_status,
                    "sources": sources,
                    "events": get_recent_events(15),
                    "indices": states,
                }
                yield f"data: {_json.dumps(payload, default=str)}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
            _time.sleep(1.0)

    return Response(
        _event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )



@app.route("/api/feed/active", methods=["GET", "POST"])
def api_feed_active():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        target = str(body.get("index_key") or body.get("index") or "").upper()
        if target not in INDEX_CONFIGS:
            return jsonify({"ok": False, "error": "Invalid index_key"}), 400
        active = set_active_feed_index(target)
        return jsonify({"ok": True, "active_index": active})
    return jsonify({"ok": True, "active_index": get_active_feed_index()})

@app.route("/api/status")
def api_status():
    states, sources, fyers_status = _build_all_states()
    return jsonify({
        "updated_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "fyers": fyers_status,
        "rules": COMMON_RULES,
        "sources": sources,
        "events": get_recent_events(15),
        "indices": states,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
