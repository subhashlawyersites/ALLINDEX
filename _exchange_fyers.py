import os
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

env_path = Path(".env")
load_dotenv(dotenv_path=env_path, override=True)

client_id = (os.getenv("FYERS_APP_ID") or os.getenv("FYERS_CLIENT_ID") or "").strip()
secret_key = (os.getenv("FYERS_SECRET_KEY") or "").strip()
redirect_uri = "https://allindex.onrender.com/fyers/callback"

if not client_id:
    client_id = input("Enter FYERS_APP_ID: ").strip()
if not secret_key:
    secret_key = input("Enter FYERS_SECRET_KEY: ").strip()

redirected_input = input("Paste the full https://allindex.onrender.com/fyers/callback?... URL: ").strip()
if "auth_code=" in redirected_input:
    auth_code = parse_qs(urlparse(redirected_input).query)["auth_code"][0]
else:
    auth_code = redirected_input

session = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code",
)
session.set_token(auth_code)
resp = session.generate_token()

if not isinstance(resp, dict) or resp.get("s") != "ok" or not resp.get("access_token"):
    raise RuntimeError(f"Token exchange failed: {resp}")

access_token = resp["access_token"].strip()

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
updated = []
found = False
for line in lines:
    if line.strip().startswith("FYERS_ACCESS_TOKEN="):
        updated.append(f"FYERS_ACCESS_TOKEN={access_token}")
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(f"FYERS_ACCESS_TOKEN={access_token}")

env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
subprocess.run(["clip"], input=access_token, text=True, check=True)
print(f"Success! Saved FYERS_ACCESS_TOKEN (length={len(access_token)}) to .env and copied to clipboard.")
