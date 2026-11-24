import os
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=ENV_PATH, override=True)

client_id = (os.getenv("FYERS_CLIENT_ID") or "").strip()
secret_key = (os.getenv("FYERS_SECRET_KEY") or "").strip()
redirect_uri = (os.getenv("REDIRECTED_URI") or os.getenv("FYERS_REDIRECT_URI") or "http://127.0.0.1:8765").strip()

if not client_id:
    client_id = input("Enter FYERS_CLIENT_ID: ").strip()
if not secret_key:
    secret_key = input("Enter FYERS_SECRET_KEY: ").strip()

session = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code",
)

auth_url = session.generate_authcode()
print("\n1. Open this URL in your browser and log in to FYERS:\n")
print(auth_url)
print("\n2. After login, copy the full redirected URL from the browser address bar and paste it below.")
redirected_input = input("Paste redirected URL (or auth_code): ").strip()

if "auth_code=" in redirected_input:
    auth_code = parse_qs(urlparse(redirected_input).query)["auth_code"][0]
else:
    auth_code = redirected_input

session.set_token(auth_code)
resp = session.generate_token()
if not isinstance(resp, dict) or resp.get("s") != "ok" or not resp.get("access_token"):
    raise RuntimeError(f"Token exchange failed: {resp}")

access_token = resp["access_token"].strip()

with open(ENV_PATH, "w", encoding="utf-8") as f:
    f.write(f"FYERS_CLIENT_ID={client_id}\n")
    f.write(f"FYERS_ACCESS_TOKEN={access_token}\n")
    f.write(f"REDIRECTED_URI={redirect_uri}\n")
    f.write(f"FYERS_SECRET_KEY={secret_key}\n")

print("\nUpdated FYERS_ACCESS_TOKEN in C:\\AllIndicesBacktest\\.env")
