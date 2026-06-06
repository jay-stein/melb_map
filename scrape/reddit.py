"""Scrape r/melbourne (and r/MelbourneActivities) by parsing old.reddit.com HTML.

No API key / app registration needed. As of 2026-05, Reddit 403s the unauthenticated
`.json` endpoints (all UAs/IPs), but still serves normal browser HTML — including
old.reddit.com's clean, parseable search and comment pages. So we fetch HTML and
parse it with BeautifulSoup. The output shape is identical to the old JSON path, so
nothing downstream (summarize.py) needed to change. See memory: reddit-json-api-blocked.

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
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
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

# old.reddit.com serves clean, parseable HTML (the www/new .json API is 403'd).
BASE = "https://old.reddit.com"
SEARCH_URL = BASE + "/r/{sub}/search"
COMMENTS_URL = BASE + "/r/{sub}/comments/{post_id}/"
LISTING_URL = BASE + "/r/{sub}/{sort}/"
WARMUP_URL = BASE + "/r/melbourne"
SUBREDDITS = ["melbourne", "MelbourneActivities"]

# Browser-ish Accept headers help old.reddit serve the full HTML page. A polite
# descriptive UA (from .env REDDIT_USER_AGENT) works fine for HTML.
DEFAULT_USER_AGENT = "melb-map/0.1 (Melbourne suburb character map; hobby project)"
HTML_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}
# How many comments to ask old.reddit to render per post page. We request a big
# page once, then slice to the caller's limit (so enough top-level comments load).
COMMENT_PAGE_LIMIT = 500

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
    # History-flavoured meta queries — quirky historical anecdotes / urban
    # legends / "used to be" stories about Melbourne suburbs.
    "Melbourne history",
    "weird Melbourne fact",
    "Melbourne urban legend",
    "old Melbourne",
    "Melbourne ghost",
    "TIL Melbourne",
    "Melbourne used to be",
    "demolished Melbourne",
    "historic Melbourne pub",
]
META_THREAD_MIN_COMMENTS = 30
META_THREAD_COMMENT_LIMIT = 500
META_OUT = "_meta.json"

POSTS_PER_SUBURB = 15  # top posts per subreddit search (was 25 — 15 is plenty)
COMMENTS_PER_POST = 15  # top comments per post

# Per-suburb history-flavoured query templates. Each {} gets the suburb name
# substituted. Run as an ADDITIONAL search pass after the main suburb query so
# we pull in folklore / "used to be" / ghost / demolished-landmark threads
# that the plain suburb-name search misses.
HISTORY_QUERY_TEMPLATES = [
    'history of "{suburb}"',
    '"{suburb}" used to be',
    'old "{suburb}"',
    '"{suburb}" ghost',
    '"{suburb}" demolished',
]
HISTORY_POSTS_PER_QUERY = 3   # top hits per history query
HISTORY_SUBREDDITS = ["melbourne"]  # skip MelbourneActivities for history

# Optional probe: r/MelbourneHistory may or may not exist. If alive, scrape its
# top posts as additional meta-threads.
MELB_HISTORY_SUBREDDIT = "MelbourneHistory"
MELB_HISTORY_TOP_POSTS = 30

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


def _parse_int(text: str | None) -> int:
    """Pull the first integer out of strings like '4,487 points' / '188 comments'."""
    if not text:
        return 0
    m = re.search(r"-?\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else 0


def _id_from_fullname(fullname: str | None) -> str:
    """'t3_abc123' -> 'abc123'."""
    if not fullname:
        return ""
    return fullname.split("_", 1)[1] if "_" in fullname else fullname


class RedditClient:
    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent or DEFAULT_USER_AGENT
        self.session.headers.update(HTML_HEADERS)
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

    def get_html(self, url: str, params: dict | None = None) -> str:
        """GET a page, returning HTML text. Same politeness/backoff/hard-fail
        handling as the old JSON fetcher."""
        for attempt, backoff in enumerate([0] + BACKOFF_SCHEDULE):
            if backoff:
                time.sleep(backoff + random.uniform(0, 5))
            try:
                resp = self.session.get(url, params=params, timeout=25)
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
                return resp.text

            except RedditBlockedError:
                raise
            except requests.RequestException as e:
                print(f"[reddit]   error on attempt {attempt+1}: {e!r}")
                if attempt == len(BACKOFF_SCHEDULE):
                    raise
        raise RuntimeError("unreachable")

    def search(self, subreddit: str, query: str, limit: int) -> list[dict]:
        """Parse old.reddit search results. Returns post dicts with the same keys
        the old JSON path produced (permalink is a path, e.g. '/r/.../comments/..').
        selftext isn't in search HTML — it's filled later from the post page."""
        url = SEARCH_URL.format(sub=subreddit)
        params = {"q": query, "restrict_sr": "on", "sort": "top", "t": "all"}
        html = self.get_html(url, params=params)
        self._sleep_jitter()
        soup = BeautifulSoup(html, "lxml")
        out: list[dict] = []
        for r in soup.select("div.search-result-link"):
            pid = _id_from_fullname(r.get("data-fullname"))
            a = r.select_one("a.search-title")
            if not pid or a is None:
                continue
            sub_el = r.select_one(".search-subreddit-link")
            sub_name = sub_el.get_text(strip=True).removeprefix("r/") if sub_el else subreddit
            score_el = r.select_one(".search-score")
            comments_el = r.select_one(".search-comments")
            out.append({
                "id": pid,
                "subreddit": sub_name,
                "title": a.get_text(strip=True),
                "selftext": "",
                "score": _parse_int(score_el.get_text() if score_el else ""),
                "num_comments": _parse_int(comments_el.get_text() if comments_el else ""),
                "permalink": urlparse(a.get("href", "")).path,
            })
            if len(out) >= limit:
                break
        return out

    def listing(self, subreddit: str, sort: str = "top", t: str = "all",
                limit: int = 25) -> list[dict]:
        """Parse a subreddit listing page (e.g. /r/X/top/). Returns [] if the sub
        doesn't exist / has no link posts — which doubles as an existence probe."""
        url = LISTING_URL.format(sub=subreddit, sort=sort)
        try:
            html = self.get_html(url, params={"t": t, "limit": limit})
        except RedditBlockedError:
            raise
        except requests.RequestException:
            return []
        self._sleep_jitter()
        soup = BeautifulSoup(html, "lxml")
        out: list[dict] = []
        for thing in soup.select("#siteTable div.thing.link"):
            pid = _id_from_fullname(thing.get("data-fullname"))
            a = thing.select_one("a.title")
            if not pid or a is None:
                continue
            score_el = thing.select_one(".score.unvoted")
            comments_el = thing.select_one("a.comments")
            permalink = thing.get("data-permalink") or urlparse(a.get("href", "")).path
            out.append({
                "id": pid,
                "title": a.get_text(strip=True),
                "score": _parse_int(score_el.get("title") or score_el.get_text() if score_el else ""),
                "num_comments": _parse_int(comments_el.get_text() if comments_el else ""),
                "permalink": permalink,
            })
            if len(out) >= limit:
                break
        return out

    def fetch_post(self, subreddit: str, post_id: str, limit: int,
                   deep: bool = False) -> dict:
        """Fetch a post's comment page. Returns {'selftext': str, 'comments': [...]}.
        deep=True walks the full rendered reply tree; otherwise top-level only.
        Comments are sliced to `limit`."""
        url = COMMENTS_URL.format(sub=subreddit, post_id=post_id)
        html = self.get_html(url, params={"limit": COMMENT_PAGE_LIMIT, "sort": "top"})
        self._sleep_jitter()
        soup = BeautifulSoup(html, "lxml")

        selftext = ""
        self_md = soup.select_one("#siteTable div.thing.self div.usertext-body div.md")
        if self_md:
            selftext = self_md.get_text("\n", strip=True)

        if deep:
            nodes = soup.select("div.commentarea div.comment")
        else:
            area = soup.select_one("div.commentarea div.sitetable.nestedlisting")
            nodes = area.find_all("div", class_="comment", recursive=False) if area else \
                soup.select("div.commentarea div.comment")

        comments: list[dict] = []
        for c in nodes:
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
            score = _parse_int(score_el.get("title") or score_el.get_text() if score_el else "")
            comments.append({"body": body, "score": score, "author": author})
            if len(comments) >= limit:
                break
        return {"selftext": selftext, "comments": comments}

    def comments(self, subreddit: str, post_id: str, limit: int,
                 deep: bool = False) -> list[dict]:
        """Convenience wrapper: just the comment list from fetch_post."""
        return self.fetch_post(subreddit, post_id, limit, deep=deep)["comments"]


def scrape_meta_threads(client: "RedditClient") -> dict:
    """Search r/melbourne for cross-suburb meta-threads, fetch full comment trees,
    return a structure ready to dump as JSON. Also probes r/MelbourneHistory
    (if it exists) for top historical-folklore threads."""
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
                "source_subreddit": "melbourne",
            })

    # Probe r/MelbourneHistory — if it exists, scrape its top posts as additional
    # meta-threads. Quirky historical anecdotes / urban legends / "used to be" lore.
    # listing() returns [] for a non-existent/empty sub, so it doubles as the probe.
    print(f"[reddit-meta] probing r/{MELB_HISTORY_SUBREDDIT}")
    try:
        hist_posts = client.listing(MELB_HISTORY_SUBREDDIT, sort="top", t="all",
                                    limit=MELB_HISTORY_TOP_POSTS)
    except RedditBlockedError:
        raise
    except Exception as e:
        print(f"[reddit-meta]   probe failed (skipping): {e}")
        hist_posts = []
    if hist_posts:
        print(f"[reddit-meta]   r/{MELB_HISTORY_SUBREDDIT} alive — {len(hist_posts)} top posts")
        for p in hist_posts:
            pid = p.get("id")
            if not pid or pid in seen_ids:
                continue
            # Lower min-comments bar for history sub (smaller community)
            if (p.get("num_comments") or 0) < 5:
                continue
            seen_ids.add(pid)
            print(f"[reddit-meta]   r/{MELB_HISTORY_SUBREDDIT} score={p.get('score')} comments={p.get('num_comments')}: {p.get('title', '')[:80]}")
            try:
                post = client.fetch_post(MELB_HISTORY_SUBREDDIT, pid,
                                         limit=META_THREAD_COMMENT_LIMIT, deep=True)
            except RedditBlockedError:
                raise
            except Exception as e:
                print(f"[reddit-meta]   comments fetch failed: {e}")
                post = {"selftext": "", "comments": []}
            threads.append({
                "id": pid,
                "title": p.get("title", ""),
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "url": f"https://www.reddit.com{p.get('permalink', '')}",
                "comments": post["comments"],
                "source_subreddit": MELB_HISTORY_SUBREDDIT,
                "selftext": post["selftext"],
            })
    else:
        print(f"[reddit-meta]   r/{MELB_HISTORY_SUBREDDIT} not found / empty — skipping")

    total_comments = sum(len(t["comments"]) for t in threads)
    print(f"[reddit-meta] total: {len(threads)} threads, {total_comments} comments")
    return {"queries": META_QUERIES, "threads": threads}


def collect_suburb(client: RedditClient, suburb: str) -> dict:
    """Search across SUBREDDITS for the suburb, collect top posts + their top comments.
    Uses SUBURB_SEARCH_ALIASES if present (e.g. Melbourne -> CBD/city centre).
    Then a second pass with history-flavoured queries to pull in folklore /
    'used to be' / ghost-story threads that plain-name searches miss.
    History posts get their own guaranteed slots so they aren't crowded out
    by higher-scoring main posts."""
    queries = SUBURB_SEARCH_ALIASES.get(suburb, [f'"{suburb}"'])
    print(f"[reddit] {suburb}: searching {len(SUBREDDITS)} subreddit(s) "
          f"with {len(queries)} query/queries (rpm={client.rpm():.0f})")
    main_posts: list[dict] = []
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
                main_posts.append({
                    "id": pid,
                    "subreddit": p.get("subreddit", sub),
                    "title": p.get("title", ""),
                    "selftext": p.get("selftext", ""),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "url": f"https://www.reddit.com{p.get('permalink', '')}",
                    "source_query": "main",
                })

    # Second pass: history-flavoured queries for folklore / "used to be" /
    # demolished landmarks / ghost stories. Caps each query at HISTORY_POSTS_PER_QUERY.
    history_posts: list[dict] = []
    for template in HISTORY_QUERY_TEMPLATES:
        query = template.format(suburb=suburb)
        for sub in HISTORY_SUBREDDITS:
            try:
                results = client.search(sub, query, HISTORY_POSTS_PER_QUERY)
            except RedditBlockedError:
                raise
            except Exception as e:
                print(f"[reddit]   {sub} history search failed for {query!r}: {e}")
                continue
            for p in results:
                pid = p.get("id")
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                history_posts.append({
                    "id": pid,
                    "subreddit": p.get("subreddit", sub),
                    "title": p.get("title", ""),
                    "selftext": p.get("selftext", ""),
                    "score": p.get("score", 0),
                    "num_comments": p.get("num_comments", 0),
                    "url": f"https://www.reddit.com{p.get('permalink', '')}",
                    "source_query": "history",
                })

    # Top main posts by score (cap at POSTS_PER_SUBURB) + ALL history posts.
    # History posts get guaranteed slots so the folklore content isn't drowned
    # by higher-scoring main posts. Comments will be fetched for both pools.
    main_posts.sort(key=lambda x: x["score"], reverse=True)
    history_posts.sort(key=lambda x: x["score"], reverse=True)
    top_posts = main_posts[:POSTS_PER_SUBURB] + history_posts
    print(f"[reddit]   {len(main_posts)} main posts (top {min(len(main_posts), POSTS_PER_SUBURB)} kept) "
          f"+ {len(history_posts)} history posts; fetching comments for all {len(top_posts)}")

    for post in top_posts:
        try:
            fetched = client.fetch_post(post["subreddit"], post["id"], COMMENTS_PER_POST)
            post["comments"] = fetched["comments"]
            # HTML search has no selftext; backfill it from the post page.
            if fetched["selftext"] and not post.get("selftext"):
                post["selftext"] = fetched["selftext"]
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
