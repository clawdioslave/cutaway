#!/bin/zsh
# One-time: store the four X API keys in a locked file. Paste each when asked; nothing is echoed.
#
# Get them at https://developer.x.com/en/portal/dashboard
#   → create a project and an app
#   → User authentication settings: turn on OAuth 1.0a, set permissions to Read and write
#   → Keys and tokens: copy the API key/secret, then generate an access token/secret
# The free tier posts up to 17 times a day, which is plenty. It cannot reply to other
# people's posts — that one call returns 403 until you are on a paid tier.
set -e
D="${CUTAWAY_HOME:-$HOME/.config/cutaway}"
F="$D/x.json"
mkdir -p "$D"; chmod 700 "$D"
echo "Paste each value and press Enter (input is hidden)."
read -s "API_KEY?API Key (Consumer Key): "; echo
read -s "API_SECRET?API Key Secret (Consumer Secret): "; echo
read -s "ACCESS_TOKEN?Access Token: "; echo
read -s "ACCESS_SECRET?Access Token Secret: "; echo
python3 - "$F" "$API_KEY" "$API_SECRET" "$ACCESS_TOKEN" "$ACCESS_SECRET" <<'PY'
import json, sys, os
f, *vals = sys.argv[1:]
vals = [v.strip() for v in vals]
if not all(vals): raise SystemExit("one of the values was empty — run this again")
json.dump({"api_key": vals[0], "api_secret": vals[1], "access_token": vals[2], "access_secret": vals[3]}, open(f, "w"))
os.chmod(f, 0o600); print(f"stored → {f}")
PY
echo "Now verify:  cutaway post --check"
