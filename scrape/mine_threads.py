"""Mine specific r/melbourne fun-fact / history threads into the meta corpus.

The general `--meta` sweep finds threads via search queries, but these
hand-picked "best fun fact about Melbourne", "fact about a suburb" and
"little-known history" threads are guaranteed gold and might have been
missed. This script fetches them by post ID and merges them into
data/raw/_meta.json (deduped by id), so the summariser picks them up as
META MENTIONS on the next run.

Fetch strategy: old.reddit.com now serves a login wall to unauthenticated
clients, so the primary fetch falls back to the Internet Archive's Wayback
Machine, which holds old.reddit snapshots of these threads — same HTML
structure, same parsers.

Run:
    uv run python -u -m scrape.mine_threads
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from scrape.reddit import RAW, META_OUT, RedditBlockedError, RedditClient

ROOT = Path(__file__).resolve().parent.parent

# Hand-picked gold threads (subreddit, post id, descriptive title for logging).
# All r/melbourne unless noted.
GOLD_THREADS = [
    ("melbourne", "1hus1er", "What's your best fun fact/tidbit about Melbourne?"),
    ("melbourne", "6ai3lm", "What are some fun facts about the Melbourne?"),
    ("melbourne", "p6m0nu", "What's the coolest fact or history thing you've heard about Melbourne?"),
    ("melbourne", "6bguxg", "Let's play a game — write down a fact about a suburb"),
    ("melbourne", "wxt2vo", "What is a fact about Melbourne that sounds made up but is true?"),
    ("melbourne", "18rxisg", "What's the most surprising thing about Melbourne?"),
    ("melbourne", "1450bs", "Care to share any historical facts about Melbourne?"),
    ("melbourne", "1hlyn4i", "Suggest an interesting suburb to explore"),
    ("melbourne", "1i3yrkd", "Tell me about the suburbs"),
    ("melbourne", "dhj71y", "It's the interesting Melbourne-related facts thread"),
    ("melbourne", "n9ntdm", "What's a little-known piece of Melbourne history?"),
    ("melbourne", "9ua8lc", "I am collecting any interesting or really weird facts about Melbourne"),
]

COMMENT_LIMIT = 500  # deep tree, same as the meta sweep

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_WEB = "https://web.archive.org/web"
CDX_FILTER = "filter=statuscode:200&collapse=digest"


def _wayback_comment_page(subreddit: str, pid: str) -> str | None:
    """Return archived old.reddit comment-page HTML for a post, or None."""
    pattern = f"old.reddit.com/r/{subreddit}/comments/{pid}*"
    try:
        r = requests.get(WAYBACK_CDX, params={
            "url": pattern, "output": "json", "limit": 20,
            "filter": "statuscode:200", "collapse": "digest",
        }, timeout=60)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"[mine]   wayback cdx failed: {e}")
        return None
    candidates = rows[1:] if rows else []
    if not candidates:
        return None
    # Prefer the biggest snapshot (usually the one with the most comments).
    candidates.sort(key=lambda row: int(row[6] or 0), reverse=True)
    ts, orig = candidates[0][1], candidates[0][2]
    if "old.reddit.com" not in orig:
        orig = f"https://old.reddit.com/r/{subreddit}/comments/{pid}/"
    url = f"{WAYBACK_WEB}/{ts}id_/{orig}"
    try:
        r = requests.get(url, timeout=90)
        if r.status_code != 200 or len(r.text) < 5000:
            print(f"[mine]   wayback fetch {r.status_code} len={len(r.text)} — trying next")
            return None
        return r.text
    except Exception as e:
        print(f"[mine]   wayback fetch failed: {e}")
        return None


def _parse_archived(html: str) -> tuple[str, list[dict]]:
    """Parse archived old.reddit HTML into (selftext, comments)."""
    soup = BeautifulSoup(html, "lxml")
    selftext = ""
    md = soup.select_one("#siteTable div.thing.self div.usertext-body div.md")
    if md:
        selftext = md.get_text("\n", strip=True)
    comments: list[dict] = []
    for c in soup.select("div.commentarea div.comment"):
        body_el = c.select_one("div.entry div.usertext-body div.md")
        if body_el is None:
            continue
        body = body_el.get_text("\n", strip=True)
        if not body or body in ("[removed]", "[deleted]"):
            continue
        author_el = c.select_one("div.entry a.author")
        author = author_el.get_text(strip=True) if author_el else "[unknown]"
        if author == "AutoModerator":
            continue
        score_el = c.select_one("div.entry span.score.unvoted")
        score = 0
        if score_el:
            t = score_el.get("title") or score_el.get_text(strip=True)
            import re
            m = re.search(r"-?\d[\d,]*", t)
            score = int(m.group(0).replace(",", "")) if m else 0
        comments.append({"body": body, "score": score, "author": author})
    return selftext, comments


def load_meta() -> dict:
    path = RAW / META_OUT
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"queries": ["curated gold threads"], "threads": []}


def save_meta(meta: dict) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / META_OUT
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    load_dotenv(ROOT / ".env")
    meta = load_meta()
    by_id = {t["id"]: t for t in meta["threads"]}
    client = RedditClient("melb-map (hobby project) by u/jay-stein")
    client.warmup()

    added = 0
    replaced = 0
    for subreddit, pid, title in GOLD_THREADS:
        existing = by_id.get(pid)
        if existing and existing.get("comments"):
            print(f"[mine] {pid} already in corpus with {len(existing['comments'])} comments — skipping")
            continue
        if existing:
            print(f"[mine] {pid} exists but empty ({len(existing.get('comments') or [])} comments) — refetching")
            meta["threads"] = [t for t in meta["threads"] if t["id"] != pid]
        print(f"[mine] r/{subreddit}/{pid} — {title[:70]}")

        # Primary: live old.reddit (may be login-walled).
        comments: list[dict] = []
        selftext = ""
        if not comments:
            try:
                post = client.fetch_post(subreddit, pid, limit=COMMENT_LIMIT, deep=True)
                comments = [c for c in post["comments"] if c.get("body")]
                selftext = post.get("selftext", "")
            except RedditBlockedError:
                comments = []
                print("[mine]   live old.reddit blocked — falling back to Wayback")
            except Exception as e:
                comments = []
                print(f"[mine]   live fetch failed: {e} — falling back to Wayback")

        # Fallback: Wayback Machine snapshot of the old.reddit page.
        if not comments:
            html = _wayback_comment_page(subreddit, pid)
            if html:
                selftext, comments = _parse_archived(html)
                print(f"[mine]   wayback: {len(comments)} comments")

        if not comments:
            print(f"[mine]   no comments found anywhere — skipping {pid}")
            continue

        meta["threads"].append({
            "id": pid,
            "title": title,
            "score": 0,
            "num_comments": len(comments),
            "url": f"https://www.reddit.com/r/{subreddit}/comments/{pid}/",
            "comments": comments,
            "source_subreddit": subreddit,
            "selftext": selftext,
        })
        added += 1
        print(f"[mine]   kept {len(comments)} comments")
        time.sleep(3)

    save_meta(meta)
    total_comments = sum(len(t["comments"]) for t in meta["threads"])
    print(f"[mine] done: {added} new threads, corpus now {len(meta['threads'])} threads, "
          f"{total_comments} comments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
