import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Blueprint, redirect, request
from fyers_apiv3 import fyersModel

IST = ZoneInfo("Asia/Kolkata")
fyers_auth_bp = Blueprint("fyers_auth", __name__)


def _token_file_path() -> Path:
    mount = (os.getenv("DATA_MOUNT_PATH") or "").strip()
    if mount and Path(mount).exists():
        return Path(mount) / "fyers_token.json"
    return Path(__file__).resolve().parent / ".fyers_token.json"


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
    path = _token_file_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            token = (data.get("access_token") or "").strip()
            if token:
                os.environ["FYERS_ACCESS_TOKEN"] = token
                return token
        except Exception:
            pass
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

    session = _build_session()
    session.set_token(auth_code)
    resp = session.generate_token()

    access_token = (resp.get("access_token") or "").strip() if isinstance(resp, dict) else ""
    if not access_token:
        return {"ok": False, "error": f"Token exchange failed: {resp}"}, 400

    save_access_token(access_token)
    return redirect("/")
