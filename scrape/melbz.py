"""Scrape suburb profile pages from melbz.com.au.

Each suburb has a page at https://melbz.com.au/{slug}/ with sections like:
  - "What X Is Actually Like" (the vibe gold)
  - "Who Lives Here"
  - "Eating and Drinking"
  - "Is X Right for You?"
  - "Living in X — The Full Picture"
  - "Verdict"

We extract the structured (heading, content) pairs from <main>, stopping
before the related-articles tail. Cache per-suburb to data/raw/melbz/{suburb}.json.

Usage:
    uv run python -m scrape.melbz Brunswick           # one
    uv run python -m scrape.melbz --all               # everything in suburb_list.txt
    uv run python -m scrape.melbz Brunswick --force   # ignore cache
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw" / "melbz"
SUBURB_LIST = DATA / "suburb_list.txt"
USER_AGENT = "Mozilla/5.0 (compatible; melb-map/0.1; hobby project)"

BASE_URL = "https://melbz.com.au/{slug}/"

# Sentinel headings that mark the start of related-articles tail.
# When we hit one, we stop collecting sections.
TAIL_SENTINELS = (
    "the british expat",
    "study cafes",
    "indoor things to do",
    "best ramen",
    "best pubs",
    "best bars",
    "best fish and chips",
    "quiet cafes",
    "how safe is",
    "commute reality",
    "things to do this",
    "best cafes",
    "winter ",
    "summer ",
)

# Sections we don't need (low-signal "see also" blocks)
SKIP_HEADINGS_PREFIX = (
    "suburbs near",
)

SLEEP_MIN = 2.0
SLEEP_MAX = 5.0


def slug_for(suburb: str) -> str:
    """Convert 'Carlton North' -> 'carlton-north'. Special-cases handled in
    SUBURB_SLUG_OVERRIDES if needed."""
    return SUBURB_SLUG_OVERRIDES.get(
        suburb,
        suburb.lower().replace(" ", "-"),
    )


# Manual overrides for suburbs whose MELBZ slug differs from a naïve transform.
# Empty by default — populate as we discover mismatches.
SUBURB_SLUG_OVERRIDES: dict[str, str] = {
    "Melbourne": "melbourne-cbd",  # MELBZ likely uses CBD-aware slug; auto-fallback if 404
}


def is_tail(heading: str) -> bool:
    h = heading.lower().strip()
    return any(h.startswith(s) for s in TAIL_SENTINELS)


def is_skip(heading: str) -> bool:
    h = heading.lower().strip()
    return any(h.startswith(s) for s in SKIP_HEADINGS_PREFIX)


def extract_sections(html: str) -> dict:
    """Parse a MELBZ suburb page and return {title, sections: [{heading, content}]}."""
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main")
    if main is None:
        # Fallback: use whole body
        main = soup.body or soup

    h1 = main.find("h1")
    title = h1.get_text(strip=True) if h1 else ""

    sections: list[dict] = []
    current_h2: str | None = None
    current_buf: list[str] = []

    def flush():
        nonlocal current_h2, current_buf
        if current_h2 is not None and current_buf:
            text = "\n".join(s.strip() for s in current_buf if s.strip())
            if text:
                sections.append({"heading": current_h2, "content": text})
        current_h2 = None
        current_buf = []

    # Walk descendants in document order
    for el in main.descendants:
        name = getattr(el, "name", None)
        if name == "h2":
            heading_text = el.get_text(strip=True)
            flush()
            if is_tail(heading_text):
                # Stop entirely — we've hit related-articles tail
                current_h2 = None
                break
            if is_skip(heading_text):
                current_h2 = None
                continue
            current_h2 = heading_text
        elif name in ("p", "li", "h3", "h4") and current_h2 is not None:
            txt = el.get_text(" ", strip=True)
            if txt:
                # Tag h3 with a slight indicator
                if name == "h3":
                    current_buf.append(f"### {txt}")
                else:
                    current_buf.append(txt)
    flush()

    return {"title": title, "sections": sections}


def fetch_suburb(suburb: str, force: bool = False) -> dict | None:
    RAW.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    cache_path = RAW / f"{safe}.json"
    if cache_path.exists() and not force:
        print(f"[melbz] {suburb}: cached")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    slug_attempts = [slug_for(suburb)]
    # Add fallback: pure naïve transform if override differs
    naive = suburb.lower().replace(" ", "-")
    if naive not in slug_attempts:
        slug_attempts.append(naive)

    last_status = None
    for slug in slug_attempts:
        url = BASE_URL.format(slug=slug)
        print(f"[melbz] {suburb}: GET {url}")
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        except requests.RequestException as e:
            print(f"[melbz]   request failed: {e}")
            return None
        last_status = resp.status_code
        if resp.status_code == 200:
            break
        print(f"[melbz]   HTTP {resp.status_code} for slug {slug!r}")

    if last_status != 200:
        # Save a stub so we know we tried
        stub = {"suburb": suburb, "url": url, "status": last_status, "title": "", "sections": []}
        cache_path.write_text(json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8")
        return stub

    data = extract_sections(resp.text)
    data["suburb"] = suburb
    data["url"] = url
    data["status"] = 200
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    n_sections = len(data.get("sections", []))
    total_chars = sum(len(s["content"]) for s in data.get("sections", []))
    print(f"[melbz]   {n_sections} sections, {total_chars} chars total")
    return data


def load_suburb_list() -> list[str]:
    return [s.strip() for s in SUBURB_LIST.read_text(encoding="utf-8").splitlines() if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*", help="suburb names; skip if --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.all:
        suburbs = load_suburb_list()
    elif args.suburbs:
        suburbs = args.suburbs
    else:
        parser.error("provide a suburb or --all")
        return 2

    n_ok = 0
    n_404 = 0
    for i, s in enumerate(suburbs, 1):
        print(f"[melbz] [{i}/{len(suburbs)}] {s}")
        result = fetch_suburb(s, force=args.force)
        if result is None:
            continue
        if result.get("status") == 200 and result.get("sections"):
            n_ok += 1
        else:
            n_404 += 1
        if i < len(suburbs):
            wait = random.uniform(SLEEP_MIN, SLEEP_MAX)
            time.sleep(wait)

    print(f"[melbz] done: {n_ok} ok, {n_404} missing/empty, total {len(suburbs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
