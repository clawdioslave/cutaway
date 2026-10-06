"""What the channel actually did, so the next change is argued from numbers.

Views on their own flatter whatever you published first and whatever you linked to hardest.
What this is for is comparing like with like: two episodes cut two different ways, released
the same way, read side by side. `--tag` groups them so you can ask a specific question —
"did cutting on real events beat cutting on motion" — rather than staring at a list.

Retention is the measure that really decides a short, and it lives in the YouTube Analytics
API, which is a separate API and a separate scope from the one uploading uses. Until that is
switched on this reports what the Data API knows: views, likes, comments, and the rate
between them.
"""
from __future__ import annotations

import datetime
import re
import sys

import youtube

API = "https://www.googleapis.com/youtube/v3"


def _requests():
    import requests
    return requests


def uploads_playlist(tok: str) -> str:
    r = _requests().get(f"{API}/channels", params={"part": "contentDetails", "mine": "true"},
                        headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
    items = r.json().get("items") or []
    if not items:
        sys.exit("this account has no YouTube channel")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def video_ids(tok: str, playlist: str, most: int) -> list:
    out, page = [], None
    while len(out) < most:
        p = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if page:
            p["pageToken"] = page
        r = _requests().get(f"{API}/playlistItems", params=p,
                            headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        if r.status_code >= 300:
            sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
        d = r.json()
        out += [i["contentDetails"]["videoId"] for i in d.get("items", [])]
        page = d.get("nextPageToken")
        if not page:
            break
    return out[:most]


def details(tok: str, ids: list) -> list:
    rows = []
    for n in range(0, len(ids), 50):
        r = _requests().get(f"{API}/videos",
                            params={"part": "snippet,statistics,contentDetails",
                                    "id": ",".join(ids[n:n + 50])},
                            headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        if r.status_code >= 300:
            sys.exit(f"HTTP {r.status_code} {r.text[:300]}")
        for v in r.json().get("items", []):
            st = v.get("statistics", {})
            rows.append({
                "id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"][:10],
                "views": int(st.get("viewCount") or 0),
                "likes": int(st.get("likeCount") or 0),
                "comments": int(st.get("commentCount") or 0),
                "duration": v.get("contentDetails", {}).get("duration", ""),
            })
    return rows


def _age(day: str) -> float:
    """Whole days since publication. Returns 0 for anything younger than a day — a rate
    computed over a few hours is not a rate, and printing one invites a wrong conclusion."""
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        return 0.0
    return float((datetime.date.today() - d).days)


def report(most: int = 25, group: str = "") -> None:
    tok = youtube.access_token()
    rows = details(tok, video_ids(tok, uploads_playlist(tok), most))
    if not rows:
        print("nothing published yet."); return
    rows.sort(key=lambda r: r["published"], reverse=True)

    print(f"{'published':<11}{'views':>7}{'/day':>7}{'likes':>7}{'eng%':>7}  title")
    print("─" * 78)
    young = 0
    for r in rows:
        age = _age(r["published"])
        if age < 1:
            young += 1
            rate = "    new"
        else:
            rate = f"{r['views'] / age:>7.1f}"
        eng = (r["likes"] + r["comments"]) / r["views"] * 100 if r["views"] else 0.0
        print(f"{r['published']:<11}{r['views']:>7}{rate}{r['likes']:>7}"
              f"{eng:>6.1f}%  {r['title'][:40]}")
    if young:
        print(f"\n{young} of these are less than a day old. Nothing here is settled yet — "
              f"a short's first day is mostly which feed it happened to land in.")

    if group:
        print()
        buckets: dict = {}
        for r in rows:
            key = "other"
            for part in [g.strip() for g in group.split(",") if g.strip()]:
                label, pattern = (part.split("=", 1) + [part])[:2]
                if re.search(pattern, r["title"], re.I):
                    key = label
                    break
            buckets.setdefault(key, []).append(r)
        print(f"{'group':<14}{'n':>4}{'views':>8}{'median':>8}{'/day':>8}")
        print("─" * 42)
        for key, rs in buckets.items():
            per = sorted(r["views"] / _age(r["published"]) for r in rs if _age(r["published"]) >= 1)
            med = f"{per[len(per) // 2]:>8.1f}" if per else "     new"
            print(f"{key:<14}{len(rs):>4}{sum(x['views'] for x in rs):>8}"
                  f"{sorted(x['views'] for x in rs)[len(rs) // 2]:>8}{med}")
        print("\nmedian views a day is the fairer column — it does not reward whatever "
              "has been up longest.")

    print("\nRetention needs the YouTube Analytics API (a separate API and scope). "
          "Without it, none of the above says whether anyone watched to the end.")


ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def retention(days: int = 28, most: int = 25, group: str = "") -> None:
    """How far into each episode people actually get.

    Views say what the algorithm did with a short. This says what a person did once it
    reached them, which is the only half you control. averageViewPercentage is the number
    to argue from: on a vertical under thirty seconds, anything under about half means the
    opening is not earning the rest.
    """
    tok = youtube.access_token()
    rows = details(tok, video_ids(tok, uploads_playlist(tok), most))
    if not rows:
        print("nothing published yet."); return
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    got = []
    for r in rows:
        q = {"ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
             "metrics": "views,averageViewDuration,averageViewPercentage",
             "filters": f"video=={r['id']}"}
        resp = _requests().get(ANALYTICS, params=q,
                               headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        if resp.status_code == 403:
            sys.exit("the Analytics API refused this token — run `cutaway setup youtube` "
                     "again to re-consent with the analytics scope added.")
        if resp.status_code >= 300:
            sys.exit(f"HTTP {resp.status_code} {resp.text[:300]}")
        data = (resp.json().get("rows") or [[0, 0, 0]])[0]
        got.append({**r, "a_views": int(data[0]), "secs": float(data[1]),
                    "pct": float(data[2])})

    if not any(r["a_views"] for r in got):
        print(f"The Analytics API answered, and has nothing yet.\n")
        print("YouTube takes a day or two to process analytics, so anything published\n"
              "recently reports zero even when the Data API already shows views on it.\n"
              "Run `cutaway stats` for live view counts; come back to this one tomorrow.")
        return

    print(f"last {days} days\n")
    print(f"{'published':<11}{'views':>7}{'avg sec':>9}{'% seen':>8}  title")
    print("─" * 78)
    for r in sorted(got, key=lambda x: x["published"], reverse=True):
        print(f"{r['published']:<11}{r['a_views']:>7}{r['secs']:>9.0f}{r['pct']:>7.0f}%"
              f"  {r['title'][:38]}")

    if group:
        print()
        buckets: dict = {}
        for r in got:
            key = "other"
            for part in [g.strip() for g in group.split(",") if g.strip()]:
                label, pattern = (part.split("=", 1) + [part])[:2]
                if re.search(pattern, r["title"], re.I):
                    key = label; break
            buckets.setdefault(key, []).append(r)
        print(f"{'group':<14}{'n':>4}{'views':>8}{'avg sec':>9}{'% seen':>8}")
        print("─" * 44)
        for key, rs in buckets.items():
            seen = [r for r in rs if r["a_views"] > 0]
            if not seen:
                print(f"{key:<14}{len(rs):>4}{'—':>8}{'—':>9}{'no data':>8}"); continue
            print(f"{key:<14}{len(rs):>4}{sum(r['a_views'] for r in seen):>8}"
                  f"{sum(r['secs'] for r in seen) / len(seen):>9.0f}"
                  f"{sum(r['pct'] for r in seen) / len(seen):>7.0f}%")
        print("\nCompare the % seen column, not the views. Views measure the algorithm; "
              "percent seen measures the edit.")
