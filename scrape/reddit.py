"""Scrape r/melbourne (and r/MelbourneActivities) using Reddit's public JSON endpoints.

No API key / app registration needed. Adding `.json` to any Reddit URL returns the
same data the API serves.

This scraper is deliberately polite:
  - Random jitter between requests (3-6s)
  - Random pause between suburbs (10-25s)
  - Honors `Retry-After` header on 429
  - Exponential backoff on transient errors
  - Hard-fails on 401/403 (we've been blocked — stop hammering)
  - Single warmup hit at session start to look like a normal user
  - Rolling RPM stats so you can spot trouble

Usage:
    uv run python -m scrape.reddit Fitzroy        # one suburb, prints summary
    uv run python -m scrape.reddit --all          # all suburbs in suburb_list.txt
    uv run python -m scrape.reddit --all --force  # re-scrape even if cached
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from pathlib import Path

import requests
from dotenv import load_dotenv

# Windows console default is cp1252 which chokes on non-Latin1 chars in print().
# Reconfigure to UTF-8 so we can log Reddit content (and accents) safely.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
SUBURB_LIST = DATA / "suburb_list.txt"

SEARCH_URL = "https://www.reddit.com/r/{sub}/search.json"
COMMENTS_URL = "https://www.reddit.com/r/{sub}/comments/{post_id}.json"
WARMUP_URL = "https://www.reddit.com/r/melbourne.json"
SUBREDDITS = ["melbourne", "MelbourneActivities"]

# Cross-suburb meta-threads where every suburb gets named in the comments.
# These are the gold posts that don't show up in per-suburb searches because
# the suburb name isn't in the title.
META_QUERIES = [
    "best suburb",
    "worst suburb",
    "favourite suburb",
    "underrated suburb",
    "overrated suburb",
    "your suburb in",                  # "your suburb in 3 words"
    "describe your suburb",
    "tell me about your suburb",
    "suburb stereotypes",
    "stereotypes about Melbourne",
    "moving to Melbourne where",
    "moving to Melbourne avoid",
    "ranking suburbs",
    "hidden gem",
    "weirdest suburb",
    "most embarrassing suburb",
]
META_THREAD_MIN_COMMENTS = 30
META_THREAD_COMMENT_LIMIT = 500
META_OUT = "_meta.json"

POSTS_PER_SUBURB = 15  # top posts per subreddit search (was 25 — 15 is plenty)
COMMENTS_PER_POST = 15  # top comments per post

# Search aliases. "Melbourne" the suburb (the CBD) is rarely called "Melbourne"
# on r/melbourne — locals say CBD, the city, etc. So we search for those instead.
# Keys are SAL suburb names; values are list of search queries to OR together.
SUBURB_SEARCH_ALIASES: dict[str, list[str]] = {
    "Melbourne": ['"Melbourne CBD"', '"the CBD"', '"city centre"'],
}

# Per-request jitter: uniform random delay AFTER each request.
SLEEP_MIN = 3.0
SLEEP_MAX = 6.0

# Pause between suburbs to look like a human moving on to a new search.
SUBURB_PAUSE_MIN = 10.0
SUBURB_PAUSE_MAX = 25.0

# Backoff schedule for transient errors (seconds). Last entry is also the cap.
BACKOFF_SCHEDULE = [10, 30, 60]

# Hard-fail status codes — we've been blocked, no point retrying.
HARD_FAIL_CODES = {401, 403}


class RedditBlockedError(RuntimeError):
    pass


class RedditClient:
    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.request_times: deque[float] = deque(maxlen=120)  # for rolling RPM

    def _sleep_jitter(self) -> None:
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    def _record_request(self) -> None:
        self.request_times.append(time.time())

    def rpm(self) -> float:
        """Requests in the last 60s."""
        if not self.request_times:
            return 0.0
        cutoff = time.time() - 60.0
        return sum(1 for t in self.request_times if t >= cutoff)

    def warmup(self) -> None:
        """One innocuous request to look like a user landing on r/melbourne first."""
        try:
            print("[reddit] warmup: GET r/melbourne homepage")
            resp = self.session.get(WARMUP_URL, timeout=20)
            self._record_request()
            if resp.status_code in HARD_FAIL_CODES:
                raise RedditBlockedError(f"warmup blocked with {resp.status_code}")
            self._sleep_jitter()
        except requests.RequestException as e:
            print(f"[reddit] warmup failed (non-fatal): {e}")

    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        for attempt, backoff in enumerate([0] + BACKOFF_SCHEDULE):
            if backoff:
                time.sleep(backoff + random.uniform(0, 5))
            try:
                resp = self.session.get(url, params=params, timeout=20)
                self._record_request()

                if resp.status_code in HARD_FAIL_CODES:
                    raise RedditBlockedError(
                        f"BLOCKED ({resp.status_code}) on {url} — stopping run"
                    )

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else 60.0
                    wait += random.uniform(2, 8)  # add jitter
                    print(f"[reddit]   429 rate-limited (attempt {attempt+1}); "
                          f"Retry-After={retry_after}, sleeping {wait:.1f}s")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except RedditBlockedError:
                raise
            except (requests.RequestException, ValueError) as e:
                print(f"[reddit]   error on attempt {attempt+1}: {e!r}")
                if attempt == len(BACKOFF_SCHEDULE):
                    raise
        raise RuntimeError("unreachable")

    def search(self, subreddit: str, query: str, limit: int) -> list[dict]:
        url = SEARCH_URL.format(sub=subreddit)
        params = {
            "q": query,
            "restrict_sr": "on",
            "sort": "top",
            "t": "all",
            "limit": limit,
        }
        data = self.get_json(url, params=params)
        self._sleep_jitter()
        children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
        return [c["data"] for c in children]

    def comments(self, subreddit: str, post_id: str, limit: int, deep: bool = False) -> list[dict]:
        """Fetch comments. If deep=True, walks the full reply tree (depth-limited
        by Reddit's response). Otherwise just top-level comments."""
        url = COMMENTS_URL.format(sub=subreddit, post_id=post_id)
        data = self.get_json(url, params={"limit": limit, "sort": "top"})
        self._sleep_jitter()
        if not isinstance(data, list) or len(data) < 2:
            return []
        children = data[1].get("data", {}).get("children", [])

        def walk(node: dict, out: list[dict]) -> None:
            if node.get("kind") != "t1":
                return
            d = node.get("data", {})
            body = d.get("body", "")
            if body and body not in ("[removed]", "[deleted]") and d.get("author") != "AutoModerator":
                out.append({
                    "body": body,
                    "score": d.get("score", 0),
                    "author": d.get("author", "[unknown]"),
                })
            if deep:
                replies = d.get("replies")
                if isinstance(replies, dict):
                    for child in replies.get("data", {}).get("children", []):
                        walk(child, out)

        comments: list[dict] = []
        for c in children:
            walk(c, comments)
        return comments


def scrape_meta_threads(client: "RedditClient") -> dict:
    """Search r/melbourne for cross-suburb meta-threads, fetch full comment trees,
    return a structure ready to dump as JSON."""
    threads: list[dict] = []
    seen_ids: set[str] = set()
    for q in META_QUERIES:
        print(f"[reddit-meta] searching r/melbourne: {q!r}")
        try:
            results = client.search("melbourne", q, limit=15)
        except RedditBlockedError:
            raise
        except Exception as e:
            print(f"[reddit-meta]   search failed: {e}")
            continue
        for p in results:
            pid = p.get("id")
            if not pid or pid in seen_ids:
                continue
            if (p.get("num_comments") or 0) < META_THREAD_MIN_COMMENTS:
                continue
            seen_ids.add(pid)
            print(f"[reddit-meta]   thread score={p.get('score')} comments={p.get('num_comments')}: {p.get('title', '')[:80]}")
            try:
                comments = client.comments("melbourne", pid, limit=META_THREAD_COMMENT_LIMIT, deep=True)
            except RedditBlockedError:
                raise
            except Exception as e:
                print(f"[reddit-meta]   comments fetch failed: {e}")
                comments = []
            threads.append({
                "id": pid,
                "title": p.get("title", ""),
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "url": f"https://www.reddit.com{p.get('permalink', '')}",
                "comments": comments,
            })
    total_comments = sum(len(t["comments"]) for t in threads)
    print(f"[reddit-meta] total: {len(threads)} threads, {total_comments} comments")
    return {"queries": META_QUERIES, "threads": threads}


def collect_suburb(client: RedditClient, suburb: str) -> dict:
    """Search across SUBREDDITS for the suburb, collect top posts + their top comments.
    Uses SUBURB_SEARCH_ALIASES if present (e.g. Melbourne -> CBD/city centre)."""
    queries = SUBURB_SEARCH_ALIASES.get(suburb, [f'"{suburb}"'])
    print(f"[reddit] {suburb}: searching {len(SUBREDDITS)} subreddit(s) "
          f"with {len(queries)} query/queries (rpm={client.rpm():.0f})")
    all_posts: list[dict] = []
    seen_ids: set[str] = set()
    for query in queries:
        for sub in SUBREDDITS:
            try:
                results = client.search(sub, query, POSTS_PER_SUBURB)
            except RedditBlockedError:
                raise
            except Exception as e:
                print(f"[reddit]   {sub} search failed for {query!r}: {e}")
                continue
            for p in results:
                pid = p.get("id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                all_posts.append({
                    "id": pid,
                    "subreddit": p.get("subreddit", sub),
                    "title": p.get("title", ""),
                    "selftext": p.get("selftext", ""),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "url": f"https://www.reddit.com{p.get('permalink', '')}",
                })
    all_posts.sort(key=lambda x: x["score"], reverse=True)
    top_posts = all_posts[:POSTS_PER_SUBURB]
    print(f"[reddit]   {len(all_posts)} unique posts; fetching comments for top {len(top_posts)}")

    for post in top_posts:
        try:
            post["comments"] = client.comments(post["subreddit"], post["id"], COMMENTS_PER_POST)
        except RedditBlockedError:
            raise
        except Exception as e:
            print(f"[reddit]   post {post['id']} comments failed: {e}")
            post["comments"] = []

    total_comments = sum(len(p["comments"]) for p in top_posts)
    print(f"[reddit]   total: {len(top_posts)} posts, {total_comments} comments")
    return {"suburb": suburb, "posts": top_posts}


def write_cache(suburb: str, corpus: dict) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    path = RAW / f"{safe}.json"
    path.write_text(json.dumps(corpus, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def cached_path(suburb: str) -> Path:
    safe = suburb.replace(" ", "_").replace("/", "_")
    return RAW / f"{safe}.json"


def load_suburb_list() -> list[str]:
    return [line.strip() for line in SUBURB_LIST.read_text(encoding="utf-8").splitlines() if line.strip()]


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("suburb", nargs="?", help="single suburb name (skip if --all/--meta)")
    parser.add_argument("--all", action="store_true", help="scrape every suburb in suburb_list.txt")
    parser.add_argument("--meta", action="store_true",
                        help="scrape cross-suburb meta-threads to data/raw/_meta.json")
    parser.add_argument("--force", action="store_true", help="re-scrape even if cached")
    args = parser.parse_args()

    user_agent = os.getenv("REDDIT_USER_AGENT") or "melb-map (hobby project)"
    client = RedditClient(user_agent)

    # Meta-thread mode: independent of per-suburb scraping
    if args.meta:
        meta_path = RAW / META_OUT
        if meta_path.exists() and not args.force:
            print(f"[reddit-meta] {meta_path} exists, skipping (use --force to redo)")
            return 0
        client.warmup()
        try:
            meta = scrape_meta_threads(client)
        except RedditBlockedError as e:
            print(f"[reddit-meta] STOPPED: {e}")
            return 1
        RAW.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[reddit-meta] wrote {meta_path}")
        return 0

    if args.all:
        suburbs = load_suburb_list()
    elif args.suburb:
        suburbs = [args.suburb]
    else:
        parser.error("provide a suburb, --all, or --meta")
        return 2

    # Skip warmup for single-suburb test runs (already cached likely)
    if len(suburbs) > 1:
        client.warmup()

    start = time.time()
    completed = 0
    skipped = 0

    try:
        for i, suburb in enumerate(suburbs, 1):
            path = cached_path(suburb)
            if path.exists() and not args.force:
                print(f"[reddit] [{i}/{len(suburbs)}] {suburb}: cached, skipping")
                skipped += 1
                continue
            elapsed = time.time() - start
            todo = len(suburbs) - i + 1
            done = i - 1 - skipped
            avg = elapsed / done if done else 0
            eta = avg * todo if avg else 0
            print(f"[reddit] [{i}/{len(suburbs)}] {suburb} "
                  f"(elapsed={fmt_duration(elapsed)}, eta={fmt_duration(eta) if eta else '?'})")
            corpus = collect_suburb(client, suburb)
            path = write_cache(suburb, corpus)
            print(f"[reddit]   wrote {path}")
            completed += 1

            # Pause before next suburb (unless last)
            if i < len(suburbs):
                pause = random.uniform(SUBURB_PAUSE_MIN, SUBURB_PAUSE_MAX)
                print(f"[reddit]   pause {pause:.1f}s before next suburb")
                time.sleep(pause)
    except RedditBlockedError as e:
        print(f"[reddit] STOPPED: {e}")
        print(f"[reddit] completed {completed} suburbs before block. Re-run later to resume from cache.")
        return 1

    print(f"[reddit] done: {completed} scraped, {skipped} cached, total {fmt_duration(time.time()-start)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
