import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Blueprint, redirect, request
from fyers_apiv3 import fyersModel

IST = ZoneInfo("Asia/Kolkata")
fyers_auth_bp = Blueprint("fyers_auth", __name__)


def _candidate_token_paths() -> list[Path]:
    paths: list[Path] = []
    for env_key in ("RENDER_DISK_PATH", "PERSISTENT_STORAGE_PATH", "DATA_DIR"):
        mount = (os.getenv(env_key) or "").strip()
        if mount:
            paths.append(Path(mount) / "fyers_token.json")
    if Path("/var/data").exists():
        paths.append(Path("/var/data") / "fyers_token.json")
    paths.append(Path(__file__).resolve().parent / ".fyers_token.json")
    deduped: list[Path] = []
    for p in paths:
        if p not in deduped:
            deduped.append(p)
    return deduped


def _token_file_path() -> Path:
    return _candidate_token_paths()[0]


def get_fyers_credentials() -> dict:
    client_id = (os.getenv("FYERS_APP_ID") or os.getenv("FYERS_CLIENT_ID") or "").strip()
    secret_key = (os.getenv("FYERS_SECRET_KEY") or "").strip()
    redirect_uri = (
        os.getenv("FYERS_REDIRECT_URI")
        or os.getenv("REDIRECTED_URI")
        or "https://allindex.onrender.com/fyers/callback"
    ).strip()
    return {
        "client_id": client_id,
        "secret_key": secret_key,
        "redirect_uri": redirect_uri,
    }


def save_access_token(access_token: str) -> None:
    token = (access_token or "").strip()
    if not token:
        return
    os.environ["FYERS_ACCESS_TOKEN"] = token
    payload = {
        "access_token": token,
        "saved_at_ist": datetime.now(IST).isoformat(),
        "session_date": datetime.now(IST).strftime("%Y-%m-%d"),
    }
    path = _token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_access_token() -> str:
    for path in _candidate_token_paths():
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                token = (data.get("access_token") or "").strip()
                if token:
                    os.environ["FYERS_ACCESS_TOKEN"] = token
                    return token
            except Exception:
                continue
    return (os.getenv("FYERS_ACCESS_TOKEN") or "").strip()


def _build_session() -> fyersModel.SessionModel:
    creds = get_fyers_credentials()
    if not creds["client_id"] or not creds["secret_key"]:
        raise RuntimeError("Missing FYERS_APP_ID or FYERS_SECRET_KEY in environment.")
    return fyersModel.SessionModel(
        client_id=creds["client_id"],
        secret_key=creds["secret_key"],
        redirect_uri=creds["redirect_uri"],
        response_type="code",
        grant_type="authorization_code",
    )


@fyers_auth_bp.get("/fyers/login")
def fyers_login():
    session = _build_session()
    return redirect(session.generate_authcode())


@fyers_auth_bp.get("/fyers/callback")
def fyers_callback():
    auth_code = (request.args.get("auth_code") or "").strip()
    if not auth_code:
        return {"ok": False, "error": f"No auth_code returned: {dict(request.args)}"}, 400

    try:
        session = _build_session()
        session.set_token(auth_code)
        resp = session.generate_token()
    except Exception as exc:
        return {"ok": False, "error": f"Token exchange exception: {exc}"}, 400

    access_token = (resp.get("access_token") or "").strip() if isinstance(resp, dict) else ""
    if not access_token:
        return {"ok": False, "error": f"Token exchange failed: {resp}"}, 400

    save_access_token(access_token)
    return redirect("/auth?fyers_auth=ok")


def get_fyers_config() -> dict:
    creds = get_fyers_credentials()
    token = load_access_token()
    return {
        "client_id": creds["client_id"],
        "secret_key": creds["secret_key"],
        "redirect_uri": creds["redirect_uri"],
        "access_token": token,
        "has_credentials": bool(creds["client_id"] and creds["secret_key"]),
        "has_token": bool(token),
    }


def save_or_authorize_fyers(
    client_id: str = "",
    secret_key: str = "",
    redirect_uri: str = "",
    auth_code: str = "",
    access_token: str = "",
    direct_token: str = "",
) -> dict:
    creds = get_fyers_credentials()
    cid = (client_id or creds["client_id"] or "").strip()
    sec = (secret_key or creds["secret_key"] or "").strip()
    ruri = (redirect_uri or creds["redirect_uri"] or "").strip()

    if cid:
        os.environ["FYERS_APP_ID"] = cid
        os.environ["FYERS_CLIENT_ID"] = cid
    if sec:
        os.environ["FYERS_SECRET_KEY"] = sec
    if ruri:
        os.environ["FYERS_REDIRECT_URI"] = ruri

    tok = (direct_token or access_token or "").strip()
    code = (auth_code or "").strip()

    if code and not tok:
        if not cid or not sec:
            return {"ok": False, "error": "Missing FYERS_APP_ID or FYERS_SECRET_KEY"}
        session = fyersModel.SessionModel(
            client_id=cid,
            secret_key=sec,
            redirect_uri=ruri,
            response_type="code",
            grant_type="authorization_code",
        )
        session.set_token(code)
        resp = session.generate_token()
        if isinstance(resp, dict):
            tok = (resp.get("access_token") or "").strip()
        if not tok:
            return {"ok": False, "error": f"Token exchange failed: {resp}"}

    if tok:
        save_access_token(tok)
        return {"ok": True, "access_token": tok}

    return {"ok": False, "error": "No auth_code or access_token provided"}


def test_fyers_connection() -> dict:
    creds = get_fyers_credentials()
    token = load_access_token()
    if not creds["client_id"] or not token:
        return {"ok": False, "connected": False, "message": "Missing FYERS_APP_ID or FYERS_ACCESS_TOKEN"}
    auth_token = token if ":" in token else f"{creds['client_id']}:{token}"
    try:
        client = fyersModel.FyersModel(
            client_id=creds["client_id"],
            is_async=False,
            token=auth_token,
            log_path="",
        )
        profile = client.get_profile()
        if isinstance(profile, dict) and profile.get("s") == "ok":
            return {"ok": True, "connected": True, "message": "Fyers connection active", "profile": profile.get("data", {})}
        return {"ok": False, "connected": False, "message": f"Fyers check failed: {profile}"}
    except Exception as exc:
        return {"ok": False, "connected": False, "message": f"Fyers check error: {exc}"}


def get_fyers_client(log_path: str = ""):
    creds = get_fyers_credentials()
    token = load_access_token()
    if not creds["client_id"] or not token:
        return None
    auth_token = token if ":" in token else f"{creds['client_id']}:{token}"
    try:
        return fyersModel.FyersModel(
            client_id=creds["client_id"],
            is_async=False,
            token=auth_token,
            log_path=log_path,
        )
    except Exception:
        return None
