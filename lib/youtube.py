#!/usr/bin/env python3
"""Upload a finished vertical to YouTube, with the title, description and audience set.

  cutaway upload short.mp4 --title "..." --desc "..." [--private] [--tags a,b]
  cutaway upload --check          # confirm the connection and print the channel

Uses the YouTube Data API's resumable upload directly over HTTPS, so the only thing this
needs installed is `requests`. Your tokens are stored in ~/.config/cutaway/youtube.json
and never leave the machine.

One-time setup (Google makes everyone do this for their own uploads):
  1. console.cloud.google.com → new project
  2. APIs & Services → Library → enable "YouTube Data API v3"
  3. APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app
  4. cutaway setup youtube      and paste the client ID and secret

A new Cloud project is in "testing" mode, which is fine for uploading to your own channel —
add your own Google account as a test user on the OAuth consent screen. The quota allows
about six uploads a day, which is more than a daily series needs.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import socket
import sys
import threading
import urllib.parse
import webbrowser

HOME = pathlib.Path(os.environ.get("CUTAWAY_HOME", pathlib.Path.home() / ".config" / "cutaway"))
CREDS = HOME / "youtube.json"
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"


def _requests():
    try:
        import requests
    except ImportError:
        sys.exit("this needs `requests`:  python3 -m pip install --user requests")
    return requests


def _store() -> dict:
    if not CREDS.exists():
        sys.exit(f"no YouTube credentials at {CREDS} — run: cutaway setup youtube")
    return json.loads(CREDS.read_text())


def _save(d: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)
    CREDS.write_text(json.dumps(d, indent=2) + "\n")
    CREDS.chmod(0o600)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _consent(store: dict) -> dict:
    """Open the browser once, catch the redirect on localhost, keep the refresh token."""
    requests = _requests()
    port = _free_port()
    redirect = f"http://localhost:{port}"
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got
            self.wfile.write(
                b"<body style='font:16px -apple-system;padding:3rem'>"
                + (b"<h2>Connected.</h2><p>You can close this tab and go back to the terminal.</p>"
                   if ok else b"<h2>Not connected.</h2><p>Nothing was saved. Try again.</p>")
                + b"</body>")

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": store["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
    print("Opening your browser to connect your YouTube channel.")
    print("If it does not open, paste this:\n\n  " + url + "\n")
    webbrowser.open(url)
    for _ in range(300):
        if got:
            break
        threading.Event().wait(1)
    srv.server_close()
    if "code" not in got:
        sys.exit("no authorisation came back — nothing was changed")

    r = requests.post(TOKEN, data={
        "code": got["code"], "client_id": store["client_id"], "client_secret": store["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"token exchange failed: HTTP {r.status_code} {r.text[:300]}")
    tok = r.json()
    store["refresh_token"] = tok["refresh_token"]
    _save(store)
    print("Connected. The refresh token is stored; you will not be asked again.")
    return store


def access_token() -> str:
    requests = _requests()
    store = _store()
    if not store.get("refresh_token"):
        store = _consent(store)
    r = requests.post(TOKEN, data={
        "refresh_token": store["refresh_token"], "client_id": store["client_id"],
        "client_secret": store["client_secret"], "grant_type": "refresh_token"}, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"could not refresh the token: HTTP {r.status_code} {r.text[:300]}\n"
                 f"Delete {CREDS} and run `cutaway setup youtube` again to reconnect.")
    return r.json()["access_token"]


def check() -> None:
    requests = _requests()
    tok = access_token()
    r = requests.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
    items = r.json().get("items") or []
    if not items:
        sys.exit("connected, but this Google account has no YouTube channel")
    s = items[0]["snippet"]
    print(f"OK — uploading to “{s['title']}”  (channel {items[0]['id']})")


def upload(path: pathlib.Path, title: str, desc: str, tags: list, private: bool,
           made_for_kids: bool = False) -> str:
    requests = _requests()
    if not path.exists():
        sys.exit(f"no such file: {path}")
    if len(title) > 100:
        sys.exit(f"the title is {len(title)} characters; YouTube allows 100")
    tok = access_token()
    meta = {
        "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "20"},
        "status": {"privacyStatus": "private" if private else "public",
                   "selfDeclaredMadeForKids": made_for_kids},
    }
    size = path.stat().st_size
    r = requests.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                      headers={"Authorization": f"Bearer {tok}",
                               "X-Upload-Content-Length": str(size),
                               "X-Upload-Content-Type": "video/*"},
                      json=meta, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"upload could not start: HTTP {r.status_code} {r.text[:400]}")
    session = r.headers.get("Location")
    if not session:
        sys.exit("upload could not start: no session URL came back")

    sent = 0
    chunk = 8 * 1024 * 1024
    with open(path, "rb") as fh:
        while sent < size:
            buf = fh.read(chunk)
            end = sent + len(buf) - 1
            p = requests.put(session, data=buf, timeout=600, headers={
                "Content-Length": str(len(buf)),
                "Content-Range": f"bytes {sent}-{end}/{size}"})
            if p.status_code in (200, 201):
                vid = p.json()["id"]
                print(f"\n{'private' if private else 'public'}: https://youtube.com/watch?v={vid}")
                return vid
            if p.status_code != 308:
                sys.exit(f"\nupload failed at {sent}: HTTP {p.status_code} {p.text[:300]}")
            rng = p.headers.get("Range")
            sent = int(rng.split("-")[1]) + 1 if rng else end + 1
            print(f"\r  {sent / size * 100:5.1f}%", end="", flush=True)
    sys.exit("\nupload ended without YouTube returning a video id")


def setup(from_file: str = "") -> None:
    """Store the client ID and secret, then do the one browser round trip.

    `--from` takes the JSON Google hands you when you create the client, so the secret goes
    from the download straight into the locked config without being read aloud, pasted into
    a terminal, or passing through anybody's scrollback.
    """
    if from_file:
        p = pathlib.Path(from_file).expanduser()
        if not p.exists():
            sys.exit(f"no such file: {p}")
        try:
            blob = json.loads(p.read_text())
        except json.JSONDecodeError:
            sys.exit(f"{p} is not JSON — download it again from the OAuth client dialog")
        inner = blob.get("installed") or blob.get("web") or blob
        cid, sec = inner.get("client_id", ""), inner.get("client_secret", "")
        if not cid or not sec:
            sys.exit(f"{p} has no client_id/client_secret — is it the right download?")
        print(f"read the client from {p.name} (…{cid[-18:]})")
    else:
        print(__doc__.split("One-time setup")[1].strip() if "One-time setup" in __doc__ else "")
        cid = input("\nOAuth client ID: ").strip()
        sec = input("Client secret:   ").strip()
        if not cid or not sec:
            sys.exit("both values are needed — nothing was saved")
    _save({"client_id": cid, "client_secret": sec})
    print(f"stored → {CREDS}")
    _consent(_store())
    check()
