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
SCOPE = ("https://www.googleapis.com/auth/youtube"
         " https://www.googleapis.com/auth/youtube.upload"
         " https://www.googleapis.com/auth/youtube.readonly"
         " https://www.googleapis.com/auth/yt-analytics.readonly")
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
    srv.timeout = 1

    def serve():
        # a browser may hit the port for a favicon or a preflight before the real redirect;
        # one handle_request() would swallow that and leave the actual code unanswered
        while not ("code" in got or "error" in got):
            srv.handle_request()
    threading.Thread(target=serve, daemon=True).start()

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": store["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
    print("Opening your browser to connect your YouTube channel.")
    print("If it does not open, paste this:\n\n  " + url + "\n")
    webbrowser.open(url)
    for _ in range(300):
        if "code" in got or "error" in got:
            break
        threading.Event().wait(1)
    srv.server_close()
    if "code" not in got:
        why = got.get("error", "no authorisation came back")
        sys.exit(f"{why} — nothing was changed. If it says access_denied, check that the app's "
                 f"scopes are registered under Data Access and you are listed as a test user.")

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
           made_for_kids: bool = False, category: str = "20") -> str:
    """category 20 is Gaming — set "youtube_category" in config for anything else."""
    requests = _requests()
    if not path.exists():
        sys.exit(f"no such file: {path}")
    if len(title) > 100:
        sys.exit(f"the title is {len(title)} characters; YouTube allows 100")
    tok = access_token()
    meta = {
        "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": category},
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
    # Keep a working refresh token until the new consent actually succeeds. Re-running
    # setup to add a scope must not be able to break the nightly release by timing out.
    prior = json.loads(CREDS.read_text()) if CREDS.exists() else {}
    store = {"client_id": cid, "client_secret": sec}
    if prior.get("client_id") == cid and prior.get("refresh_token"):
        store["refresh_token"] = prior["refresh_token"]
    _save(store)
    print(f"stored → {CREDS}")
    fresh = dict(store); fresh.pop("refresh_token", None)
    _consent(fresh)          # on success this writes the new token; on failure the old one stands
    check()


# ── playlists ────────────────────────────────────────────────────────────────────────────────
# A series needs a shelf of its own on the channel: one place where the episodes sit in order
# and autoplay into each other. That is where a viewer who liked one becomes a viewer who
# watched six. Needs the full `youtube` scope.

def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def my_playlists(tok: str) -> list:
    """All the channel's playlists as [(id, title), ...]."""
    requests = _requests()
    out, page = [], None
    while True:
        p = {"part": "snippet", "mine": "true", "maxResults": 50}
        if page:
            p["pageToken"] = page
        r = requests.get(f"{API}/playlists", params=p, headers=_hdr(tok), timeout=30)
        if r.status_code == 403:
            sys.exit("the playlist call was refused — run `cutaway setup youtube` again to "
                     "re-consent with the full youtube scope added.")
        if r.status_code >= 300:
            sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
        d = r.json()
        out += [(i["id"], i["snippet"]["title"]) for i in d.get("items", [])]
        page = d.get("nextPageToken")
        if not page:
            return out


def ensure_playlist(tok: str, title: str, desc: str = "") -> str:
    """The playlist's id, creating it public if it is not there yet."""
    for pid, t in my_playlists(tok):
        if t.strip().lower() == title.strip().lower():
            return pid
    requests = _requests()
    r = requests.post(f"{API}/playlists", params={"part": "snippet,status"}, headers=_hdr(tok),
                      json={"snippet": {"title": title, "description": desc},
                            "status": {"privacyStatus": "public"}}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"could not create the playlist: HTTP {r.status_code} {r.text[:300]}")
    print(f"created playlist “{title}”")
    return r.json()["id"]


def playlist_videos(tok: str, pid: str) -> list:
    """Video ids already in the playlist, in order."""
    requests = _requests()
    out, page = [], None
    while True:
        p = {"part": "contentDetails", "playlistId": pid, "maxResults": 50}
        if page:
            p["pageToken"] = page
        r = requests.get(f"{API}/playlistItems", params=p, headers=_hdr(tok), timeout=30)
        if r.status_code == 404:
            # a playlist created seconds ago is not always readable yet; wait, then treat
            # a persistent 404 as empty rather than failing a sync that just created it
            import time
            for _ in range(4):
                time.sleep(3)
                r = requests.get(f"{API}/playlistItems", params=p, headers=_hdr(tok), timeout=30)
                if r.status_code != 404:
                    break
            if r.status_code == 404:
                return out
        if r.status_code >= 300:
            sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
        d = r.json()
        out += [i["contentDetails"]["videoId"] for i in d.get("items", [])]
        page = d.get("nextPageToken")
        if not page:
            return out


def add_to_playlist(tok: str, pid: str, video_id: str, position=None) -> None:
    requests = _requests()
    body = {"snippet": {"playlistId": pid,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    if position is not None:
        body["snippet"]["position"] = position
    r = requests.post(f"{API}/playlistItems", params={"part": "snippet"}, headers=_hdr(tok),
                      json=body, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"could not add {video_id}: HTTP {r.status_code} {r.text[:300]}")


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def chapter_number(title: str):
    """The N in 'Chapter N — …', read as a roman numeral, or None."""
    import re
    m = re.search(r"\bChapter\s+([IVXLC]+)\b", title)
    if not m:
        return None
    s, total = m.group(1), 0
    for i, ch in enumerate(s):
        val = _ROMAN[ch]
        if i + 1 < len(s) and val < _ROMAN[s[i + 1]]:
            total -= val
        else:
            total += val
    return total


def sync_playlist(title: str, desc: str = "") -> None:
    """Put every chapter on the channel into the playlist, in chapter order, once."""
    import stats
    tok = access_token()
    pid = ensure_playlist(tok, title, desc)
    have = set(playlist_videos(tok, pid))
    rows = stats.details(tok, stats.video_ids(tok, stats.uploads_playlist(tok), 200))
    chapters = [(chapter_number(r["title"]), r) for r in rows]
    chapters = sorted([(n, r) for n, r in chapters if n is not None], key=lambda x: x[0])
    added = 0
    for n, r in chapters:
        if r["id"] in have:
            continue
        add_to_playlist(tok, pid, r["id"])
        print(f"  + Chapter {n}: {r['title'][:50]}")
        added += 1
    print(f"{len(chapters)} chapters on the channel, {added} added, playlist has "
          f"{len(have) + added}: https://youtube.com/playlist?list={pid}")
