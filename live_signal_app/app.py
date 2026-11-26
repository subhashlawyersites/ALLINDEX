import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from analytics_data import build_historical_analytics
from data_provider import fetch_latest_candles
from fyers_auth import fyers_auth_bp, get_fyers_config, save_or_authorize_fyers, test_fyers_connection
from db_store import enqueue_alert_insert, enqueue_signal_upsert
from signal_engine import COMMON_RULES, INDEX_CONFIGS, evaluate_index_signal, is_15m_bar_closed
from strike_ladder import build_strike_ladder, build_target_ladder
from trade_monitor import (
    close_monitored_trade,
    evaluate_monitored_trades_against_market,
    get_active_trades,
    start_monitored_trade,
    get_recent_events,
    record_event,
)

IST = ZoneInfo("Asia/Kolkata")

app = Flask(__name__)
app.register_blueprint(fyers_auth_bp)

_FEED_LOCK = threading.Lock()
ACTIVE_FEED_FILE = Path(__file__).resolve().parent / ".active_feed_index.json"
_ACTIVE_FEED_KEY = "NIFTY50"


def get_active_feed_index() -> str:
    global _ACTIVE_FEED_KEY
    with _FEED_LOCK:
        if _ACTIVE_FEED_KEY in INDEX_CONFIGS:
            return _ACTIVE_FEED_KEY
        if ACTIVE_FEED_FILE.exists():
            try:
                saved = str((json.loads(ACTIVE_FEED_FILE.read_text(encoding="utf-8")) or {}).get("active_index") or "").strip().upper()
                if saved in INDEX_CONFIGS:
                    _ACTIVE_FEED_KEY = saved
                    return _ACTIVE_FEED_KEY
            except Exception:
                pass
        _ACTIVE_FEED_KEY = "NIFTY50"
        return _ACTIVE_FEED_KEY


def set_active_feed_index(index_key: str) -> str:
    global _ACTIVE_FEED_KEY
    k = str(index_key or "").strip().upper()
    if k not in INDEX_CONFIGS:
        k = "NIFTY50"
    with _FEED_LOCK:
        _ACTIVE_FEED_KEY = k
        try:
            ACTIVE_FEED_FILE.write_text(json.dumps({"active_index": k}), encoding="utf-8")
        except Exception:
            pass
    return _ACTIVE_FEED_KEY


_LAST_ENQUEUED_SIGNAL_SIG: dict[str, str] = {}


def _maybe_enqueue_signal_and_alert(st: dict, event_type: str | None = None, message: str | None = None) -> None:
    idx_key = str(st.get("index_key") or "").strip()
    sess_date = str(st.get("session_date") or "").strip()
    bar_time = str(st.get("last_bar_time") or "").strip()
    status = str(st.get("status") or "").strip()
    sig_key = f"{idx_key}|{sess_date}|{bar_time}|{status}"
    if idx_key and sess_date and bar_time and _LAST_ENQUEUED_SIGNAL_SIG.get(idx_key) != sig_key:
        _LAST_ENQUEUED_SIGNAL_SIG[idx_key] = sig_key
        enqueue_signal_upsert(st)

    if event_type and message and idx_key and sess_date:
        if status.endswith("_PUT"):
            sig_dir = "PUT"
        elif status.endswith("_CALL"):
            sig_dir = "CALL"
        else:
            sig_dir = st.get("confirmed_dir") or st.get("armed_dir") or st.get("direction")
        if sig_dir not in ("CALL", "PUT"):
            sig_dir = None
        enqueue_alert_insert(
            index_name=idx_key,
            session_date=sess_date,
            alert_time=bar_time or datetime.now(IST).strftime("%H:%M"),
            alert_type=event_type,
            message=message,
            direction=sig_dir,
            trigger_price=st.get("entry_price") or st.get("last_close"),
            delivered=False,
            metadata={"status": status, "data_source": st.get("data_source")},
        )


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
        spot_now = (
            st["live_ltp"]
            if (feed_on and src == "FYERS_LIVE_15M" and live_ltp is not None)
            else float(last_row.get("close", st["last_close"]))
        )
        st["spot_price"] = spot_now
        price_snaps[key] = {
            "high": max(float(last_row.get("high", spot_now)), spot_now),
            "low": min(float(last_row.get("low", spot_now)), spot_now),
            "close": spot_now,
            "time_str": (
                live_q.get("updated_ist")
                if isinstance(live_q, dict) and live_q.get("updated_ist")
                else st["last_bar_time"]
            ),
        }

        _ev_type = None
        _ev_msg = None
        if st["avoid_today"]:
            _ev_type = "AVOID_WEEKDAY"
            _ev_msg = f"{st['weekday']} is avoidable for {key}"
            record_event(key, _ev_type, _ev_msg, "warn")
        elif st["gap_excluded"]:
            _ev_type = "GAP_EXCLUDED"
            _ev_msg = f"Session gap <= -0.75% on {key}"
            record_event(key, _ev_type, _ev_msg, "bad")
        elif st["status"].startswith("CONFIRMED_"):
            _ev_type = st["status"]
            _ev_msg = f"{key} confirmed {st['status']} @ {st['entry_price']}"
            record_event(key, _ev_type, _ev_msg, "good")
            conf_dir = st.get("armed_dir") or ("PUT" if st["status"].endswith("PUT") else "CALL")
            entry_p = st.get("entry_price")
            sl_p = st.get("stop_price")
            if entry_p is not None and sl_p is not None and key not in get_active_trades():
                start_monitored_trade(
                    index_key=key,
                    direction=conf_dir,
                    entry_price=float(entry_p),
                    sl_price=float(sl_p),
                    session_date=st.get("session_date"),
                    weekday=st.get("weekday"),
                    entry_time=st.get("last_bar_time"),
                )
        _maybe_enqueue_signal_and_alert(st, _ev_type, _ev_msg)
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


@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "SMA2/SMA10 Index Decision Support",
        "short_name": "IndexSignals",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "description": "Multi-index SMA2/SMA10 signal monitoring and decision-support dashboard.",
        "icons": [],
    }
    return jsonify(manifest)


@app.route("/sw.js")
def service_worker():
    sw_code = """
const CACHE_NAME = "sma2-sma10-pwa-v1";
self.addEventListener("install", (event) => {
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});
self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/fyers/")) {
    return;
  }
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.status === 200 && url.origin === self.location.origin) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, clone)).catch(() => {});
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
""".strip()
    return Response(sw_code, mimetype="application/javascript")


@app.route("/", methods=["GET", "HEAD"])
def combined_dashboard():
    if request.method == "HEAD":
        return ("", 200)
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
    if request.args.get("fyers_auth") == "ok":
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
    active_feed = get_active_feed_index()
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
            active_feed_index=active_feed,
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
        active_feed_index=active_feed,
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

    df, src = fetch_latest_candles(key, allow_fyers=(key == get_active_feed_index()))
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
    from_date = request.args.get("from_date", "")
    to_date = request.args.get("to_date", "")
    from_time = request.args.get("from_time", "")
    to_time = request.args.get("to_time", "")
    preset = request.args.get("preset", "")
    target_bucket = request.args.get("target_bucket", "")
    source = request.args.get("source", "both")
    min_target_raw = request.args.get("min_target_pct", "").strip()
    min_target_pct = float(min_target_raw) if min_target_raw else None
    def _opt_flt(name: str) -> float | None:
        raw = request.args.get(name, "").strip()
        return float(raw) if raw else None
    min_points = _opt_flt("min_points")
    max_points = _opt_flt("max_points")
    min_r_pct = _opt_flt("min_r_pct")
    max_r_pct = _opt_flt("max_r_pct")
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
        from_date=from_date,
        to_date=to_date,
        from_time=from_time,
        to_time=to_time,
        preset=preset,
        min_target_pct=min_target_pct,
        target_bucket=target_bucket,
        source=source,
        min_points=min_points,
        max_points=max_points,
        min_r_pct=min_r_pct,
        max_r_pct=max_r_pct,
    )
    return jsonify(data)


_LAST_SWITCH_FETCH: dict[str, float] = {}
_SWITCH_COOLDOWN_SEC = 10.0


@app.route("/api/feed/active", methods=["GET", "POST"])
def api_feed_active():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        target = str(
            body.get("index_key") or body.get("index") or request.args.get("index_key") or ""
        ).strip().upper()
        if target not in INDEX_CONFIGS:
            return jsonify({"ok": False, "error": f"Unknown index: {target}"}), 400

        prev_active = get_active_feed_index()
        active = set_active_feed_index(target)

        now_mono = time.monotonic()
        last_ts = _LAST_SWITCH_FETCH.get(active, 0.0)
        hydrated = False
        source = None
        if (now_mono - last_ts) >= _SWITCH_COOLDOWN_SEC:
            _LAST_SWITCH_FETCH[active] = now_mono
            try:
                _, source = fetch_latest_candles(
                    active,
                    allow_fyers=True,
                    force_refresh=True,
                )
                hydrated = True
            except Exception as exc:
                print(f"[Feed switch catch-up warning] {active} (from {prev_active}): {exc}")

        return jsonify({
            "ok": True,
            "previous_index": prev_active,
            "active_index": active,
            "hydrated": hydrated,
            "source": source,
        })
    return jsonify({"ok": True, "active_index": get_active_feed_index()})


@app.route("/api/status")
def api_status():
    states, sources, fyers_status = _build_all_states()
    return jsonify({
        "updated_ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "fyers": fyers_status,
        "active_feed_index": get_active_feed_index(),
        "rules": COMMON_RULES,
        "sources": sources,
        "events": get_recent_events(15),
        "indices": states,
    })




@app.route("/watchlist")
def watchlist_page():
    return render_template("watchlist.html")


@app.route("/api/watchlist", methods=["GET", "POST"])
def api_watchlist():
    from watchlist_manager import MAX_WATCHLIST_ITEMS, fetch_watchlist_quotes
    selected = None
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get("selected_ids")
        if isinstance(raw_ids, list) and len(raw_ids) > MAX_WATCHLIST_ITEMS:
            return jsonify({"error": f"Maximum {MAX_WATCHLIST_ITEMS} instruments allowed."}), 400
        selected = raw_ids if isinstance(raw_ids, list) else []
    return jsonify(fetch_watchlist_quotes(selected))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=False)
