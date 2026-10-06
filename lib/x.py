#!/usr/bin/env python3
"""Post to X as the account whose keys live in ~/.config/cutaway/x.json.

  cutaway post --check                       # verify the keys; prints the @handle
  cutaway post --text "..." [--video clip.mp4 | --image shot.jpg] [--dry-run]
  cutaway post --from-draft draft.md         # first fenced block = text; 'clip:'/'image:' lines = media
  cutaway post --text "..." --reply-to https://x.com/someone/status/123

OAuth 1.0a user context: API v2 for the post, v2 chunked upload for media, v1.1 upload as
a fallback for the tiers that do not have the v2 endpoint. Replying to someone else's post
needs a paid tier — the free tier returns 403 for that one call and nothing else.
"""
import argparse, json, os, pathlib, re, sys, time

VENV_SITE = pathlib.Path.home() / ".config" / "cutaway" / ".venv"
CREDS = pathlib.Path(os.environ.get("CUTAWAY_HOME", pathlib.Path.home() / ".config" / "cutaway")) / "x.json"

def _deps():
    try:
        import requests  # noqa
        from requests_oauthlib import OAuth1  # noqa
    except ImportError:
        # allow running with the plain interpreter by adding the project venv's site-packages
        for sp in VENV_SITE.glob("lib/python*/site-packages"):
            sys.path.insert(0, str(sp))
    import requests
    from requests_oauthlib import OAuth1
    return requests, OAuth1

def auth():
    if not CREDS.exists():
        raise SystemExit(f"no credentials at {CREDS} — run: cutaway setup x")
    c = json.load(open(CREDS))
    _, OAuth1 = _deps()
    return OAuth1(c["api_key"], c["api_secret"], c["access_token"], c["access_secret"])

def me(requests, a):
    r = requests.get("https://api.x.com/2/users/me", auth=a, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"credential check failed: HTTP {r.status_code} {r.text[:300]}")
    d = r.json()["data"]; return d["username"], d["name"], d["id"]

def upload_media(requests, a, path: pathlib.Path):
    size = path.stat().st_size
    is_video = path.suffix.lower() in (".mp4", ".mov")
    mime = "video/mp4" if is_video else ("image/png" if path.suffix.lower() == ".png" else "image/jpeg")
    category = "tweet_video" if is_video else "tweet_image"
    base = "https://api.x.com/2/media/upload"
    r = requests.post(f"{base}/initialize", auth=a, json={"media_type": mime, "total_bytes": size, "media_category": category}, timeout=60)
    if r.status_code == 404:  # v2 upload not available on this tier/version → v1.1
        return upload_media_v1(requests, a, path, mime, category, size)
    if r.status_code >= 300:
        raise SystemExit(f"media initialize failed: HTTP {r.status_code} {r.text[:300]}")
    media_id = r.json()["data"]["id"]
    chunk = 4 * 1024 * 1024
    with open(path, "rb") as fh:
        idx = 0
        while True:
            buf = fh.read(chunk)
            if not buf: break
            r = requests.post(f"{base}/{media_id}/append", auth=a, data={"segment_index": idx}, files={"media": buf}, timeout=300)
            if r.status_code >= 300:
                raise SystemExit(f"media append failed: HTTP {r.status_code} {r.text[:300]}")
            idx += 1
    r = requests.post(f"{base}/{media_id}/finalize", auth=a, timeout=60)
    if r.status_code >= 300:
        raise SystemExit(f"media finalize failed: HTTP {r.status_code} {r.text[:300]}")
    info = r.json().get("data", {}).get("processing_info")
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(info.get("check_after_secs", 3))
        r = requests.get(base, auth=a, params={"command": "STATUS", "media_id": media_id}, timeout=60)
        info = r.json().get("data", {}).get("processing_info")
    if info and info.get("state") == "failed":
        raise SystemExit(f"media processing failed: {info}")
    return media_id

def upload_media_v1(requests, a, path, mime, category, size):
    base = "https://upload.twitter.com/1.1/media/upload.json"
    r = requests.post(base, auth=a, data={"command": "INIT", "media_type": mime, "total_bytes": size, "media_category": category}, timeout=60)
    if r.status_code >= 300: raise SystemExit(f"v1 INIT failed: HTTP {r.status_code} {r.text[:300]}")
    media_id = r.json()["media_id_string"]
    with open(path, "rb") as fh:
        idx = 0
        while True:
            buf = fh.read(4 * 1024 * 1024)
            if not buf: break
            r = requests.post(base, auth=a, data={"command": "APPEND", "media_id": media_id, "segment_index": idx}, files={"media": buf}, timeout=300)
            if r.status_code >= 300: raise SystemExit(f"v1 APPEND failed: HTTP {r.status_code} {r.text[:300]}")
            idx += 1
    r = requests.post(base, auth=a, data={"command": "FINALIZE", "media_id": media_id}, timeout=60)
    info = r.json().get("processing_info")
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(info.get("check_after_secs", 3))
        r = requests.get(base, auth=a, params={"command": "STATUS", "media_id": media_id}, timeout=60)
        info = r.json().get("processing_info")
    if info and info.get("state") == "failed": raise SystemExit(f"v1 processing failed: {info}")
    return media_id

def parse_draft(path: pathlib.Path):
    txt = path.read_text()
    m = re.search(r"```(?:text)?\n(.*?)```", txt, re.S)
    text = (m.group(1) if m else txt).strip()
    media = []   # one video, or up to four images (every `image:` line counts)
    for key in ("clip", "video", "image", "screenshot"):
        for mm in re.finditer(rf"^{key}:\s*(.+)$", txt, re.M | re.I):
            cand = pathlib.Path(mm.group(1).strip().strip("`"))
            if cand.exists() and cand not in media: media.append(cand)
        if media and key in ("clip", "video"): break
    return text, media[:4]

def main():
    ap = argparse.ArgumentParser(prog="cutaway post", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--text"); ap.add_argument("--video"); ap.add_argument("--image")
    ap.add_argument("--from-draft"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reply-to", help="status URL or id to reply to")
    ap.add_argument("--delete", help="status URL or id to delete — permanent; prints the post first")
    a_ = ap.parse_args()
    requests, _ = _deps(); a = auth()
    user, name, uid = me(requests, a)
    if a_.check:
        print(f"OK — posting as @{user} ({name}), user id {uid}"); return
    if a_.delete:
        mm = re.search(r"(\d{6,})", a_.delete)
        if not mm: raise SystemExit(f"--delete: no status id found in {a_.delete!r}")
        did = mm.group(1)
        g = requests.get(f"https://api.x.com/2/tweets/{did}", params={"tweet.fields": "created_at,text"}, auth=a, timeout=30)
        d = (g.json() or {}).get("data")
        if not d: raise SystemExit(f"--delete: cannot read {did}: {g.status_code} {g.text[:200]}")
        print(f"deleting from @{user}, posted {d.get('created_at')}:\n---\n{d.get('text')}\n---")
        if a_.dry_run: print("dry run — not deleted"); return
        r = requests.delete(f"https://api.x.com/2/tweets/{did}", auth=a, timeout=30)
        if r.status_code >= 300: raise SystemExit(f"delete failed: HTTP {r.status_code} {r.text[:300]}")
        print("deleted:", r.json().get("data", {}).get("deleted"))
        return
    text, media = a_.text, None
    if a_.from_draft:
        text, media = parse_draft(pathlib.Path(a_.from_draft).expanduser())
    if a_.video: media = [pathlib.Path(a_.video).expanduser()]
    if a_.image: media = [pathlib.Path(a_.image).expanduser()]
    media = media or []
    if not text: raise SystemExit("nothing to post: give --text or --from-draft")
    weighted = len(re.sub(r"https?://\S+", "x" * 23, text))  # X counts every link as 23 chars (t.co)
    if weighted > 280: raise SystemExit(f"text is {weighted} chars as X counts it (>280); shorten it")
    reply_id = None
    if a_.reply_to:
        mm = re.search(r"(\d{6,})", a_.reply_to)
        if not mm: raise SystemExit(f"--reply-to: no status id found in {a_.reply_to!r}")
        reply_id = mm.group(1)
    print(f"as @{user}:\n---\n{text}\n---\nmedia: {', '.join(str(m) for m in media) or 'none'}"
          + (f"\nreplying to: {reply_id}" if reply_id else ""))
    if a_.dry_run: print("dry run — not posted"); return
    payload = {"text": text}
    if reply_id: payload["reply"] = {"in_reply_to_tweet_id": reply_id}
    if media:
        payload["media"] = {"media_ids": [upload_media(requests, a, m) for m in media]}
    r = requests.post("https://api.x.com/2/tweets", auth=a, json=payload, timeout=60)
    if r.status_code >= 300: raise SystemExit(f"post failed: HTTP {r.status_code} {r.text[:400]}")
    tid = r.json()["data"]["id"]
    print(f"{'replied' if reply_id else 'posted'}: https://x.com/{user}/status/{tid}")

if __name__ == "__main__":
    main()
