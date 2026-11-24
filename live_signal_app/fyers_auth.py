import hashlib
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
DATA_DIR = BASE_DIR / "data"

load_dotenv(ENV_PATH)


def _upsert_env(updates: dict[str, str]) -> None:
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={val}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = v


def get_fyers_config() -> dict:
    load_dotenv(ENV_PATH, override=True)
    return {
        "client_id": os.getenv("FYERS_CLIENT_ID", "").strip(),
        "secret_key": os.getenv("FYERS_SECRET_KEY", "").strip(),
        "redirect_uri": os.getenv("FYERS_REDIRECT_URI", "https://127.0.0.1/").strip(),
        "has_token": bool(os.getenv("FYERS_ACCESS_TOKEN", "").strip()),
    }


def _make_client(client_id: str, token_str: str):
    from fyers_apiv3 import fyersModel

    return fyersModel.FyersModel(
        client_id=client_id,
        is_async=False,
        token=token_str,
        log_path=str(DATA_DIR),
    )


def get_fyers_client():
    load_dotenv(ENV_PATH, override=True)
    client_id = os.getenv("FYERS_CLIENT_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not client_id or not access_token:
        return None
    try:
        auth_token = access_token if ":" in access_token else f"{client_id}:{access_token}"
        return _make_client(client_id, auth_token)
    except Exception:
        return None


def test_fyers_connection() -> dict:
    load_dotenv(ENV_PATH, override=True)
    client_id = os.getenv("FYERS_CLIENT_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not client_id or not access_token:
        return {
            "connected": False,
            "message": "Missing FYERS_CLIENT_ID or FYERS_ACCESS_TOKEN in .env",
        }

    candidates = []
    prefixed = access_token if ":" in access_token else f"{client_id}:{access_token}"
    candidates.append(prefixed)
    if access_token != prefixed:
        candidates.append(access_token)

    last_resp = None
    for tok in candidates:
        try:
            client = _make_client(client_id, tok)
            resp = client.get_profile()
            last_resp = resp
            if isinstance(resp, dict) and resp.get("s") == "ok":
                data = resp.get("data") or {}
                name = data.get("name") or data.get("fy_id") or "Fyers User"
                return {
                    "connected": True,
                    "message": f"Connected as {name}",
                    "profile": data,
                }
        except Exception as exc:
            last_resp = str(exc)

    return {
        "connected": False,
        "message": f"Fyers check failed: {last_resp}",
    }


def save_or_authorize_fyers(
    client_id: str = "",
    secret_key: str = "",
    redirect_uri: str = "",
    auth_code: str = "",
    direct_token: str = "",
) -> dict:
    cfg = get_fyers_config()
    cid = (client_id or cfg["client_id"]).strip()
    sec = (secret_key or cfg["secret_key"]).strip()
    redir = (redirect_uri or cfg["redirect_uri"] or "https://127.0.0.1/").strip()

    env_updates = {}
    if cid:
        env_updates["FYERS_CLIENT_ID"] = cid
    if sec:
        env_updates["FYERS_SECRET_KEY"] = sec
    if redir:
        env_updates["FYERS_REDIRECT_URI"] = redir

    if direct_token.strip():
        env_updates["FYERS_ACCESS_TOKEN"] = direct_token.strip()
        _upsert_env(env_updates)
        status = test_fyers_connection()
        return {
            "ok": status["connected"],
            "message": status["message"],
            "status": status,
        }

    if auth_code.strip():
        if not cid or not sec:
            return {
                "ok": False,
                "message": "FYERS_CLIENT_ID and FYERS_SECRET_KEY are required to exchange auth_code.",
            }
        try:
            from fyers_apiv3 import fyersModel

            app_hash = hashlib.sha256(f"{cid}:{sec}".encode("utf-8")).hexdigest()
            session = fyersModel.SessionModel(
                client_id=cid,
                secret_key=sec,
                redirect_uri=redir,
                response_type="code",
                grant_type="authorization_code",
            )
            session.set_token(auth_code.strip())
            token_resp = session.generate_token()
            if isinstance(token_resp, dict) and token_resp.get("access_token"):
                env_updates["FYERS_ACCESS_TOKEN"] = token_resp["access_token"]
                _upsert_env(env_updates)
                status = test_fyers_connection()
                return {
                    "ok": status["connected"],
                    "message": f"Token generated & saved. {status['message']}",
                    "app_hash": app_hash[:12] + "...",
                    "status": status,
                }
            return {
                "ok": False,
                "message": f"Token exchange response: {token_resp}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": f"Auth code exchange error: {exc}",
            }

    if env_updates:
        _upsert_env(env_updates)
    status = test_fyers_connection()
    return {
        "ok": status["connected"],
        "message": f"Credentials saved. {status['message']}",
        "status": status,
    }
