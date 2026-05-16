"""Scrape suburb history entries from eMelbourne (emelbourne.net.au).

eMelbourne is a curated Melbourne encyclopaedia from the University of Melbourne,
published 2008. Each suburb entry is a single tight paragraph (~200-300 words)
covering founding, etymology, and historical arc, with an author byline.

Two-stage flow:
1. Index page (EM00022b.htm) lists all suburb entries with non-sequential codes
   (e.g. Abbotsford = EM00024, Box Hill = EM00223). We must scrape it once to
   build a {suburb: url} map. Cached to data/raw/emelbourne/_index.json.
2. Per-suburb detail pages — extract title metadata line, body paragraphs, and
   author byline. Cached to data/raw/emelbourne/{suburb}.json.

Suburbs not present in the eMelbourne index are written as {"missing": true};
the summariser will fall back to Wikipedia for those.

Usage:
    uv run python -m scrape.emelbourne --rebuild-index    # one-off (or --force)
    uv run python -m scrape.emelbourne Carnegie           # one suburb
    uv run python -m scrape.emelbourne --all              # all in suburb_list.txt
    uv run python -m scrape.emelbourne Carnegie --force   # ignore cache
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw" / "emelbourne"
SUBURB_LIST = DATA / "suburb_list.txt"

INDEX_URL = "https://www.emelbourne.net.au/biogs/EM00022b.htm"
BASE = "https://www.emelbourne.net.au/biogs/"
INDEX_CACHE = RAW / "_index.json"
USER_AGENT = "melb-map (educational hobby project)"

# Be especially polite — small academic site
SLEEP_MIN = 4.0
SLEEP_MAX = 7.0

# Matches "(3163, 12 km SE, Glen Eira City)" — postcode, distance, LGA
META_LINE_RE = re.compile(r"^\s*\(\s*\d{4}\s*,\s*[^,]+,\s*[^)]+\)\s*$")


def fetch(url: str) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def build_index(force: bool = False) -> dict[str, str]:
    RAW.mkdir(parents=True, exist_ok=True)
    if INDEX_CACHE.exists() and not force:
        print(f"[emelbourne] index cached: {INDEX_CACHE}")
        return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))

    print(f"[emelbourne] GET {INDEX_URL}")
    resp = fetch(INDEX_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Find the "Suburbs & Localities" heading and grab links from the container
    # following it (the See-also block holding the alphabetical suburb list).
    heading = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if h.get_text(strip=True).lower() == "suburbs & localities":
            heading = h
            break
    if heading is None:
        raise RuntimeError("could not locate 'Suburbs & Localities' heading on index page")

    # Walk siblings until we find a container with the suburb links
    container = None
    sib = heading
    for _ in range(10):
        sib = sib.find_next_sibling()
        if sib is None:
            break
        if sib.name == "div" and sib.find("a", href=re.compile(r"EM\d+b\.htm")):
            container = sib
            break
    if container is None:
        raise RuntimeError("could not locate suburb-link container after heading")

    mapping: dict[str, str] = {}
    for a in container.find_all("a", href=re.compile(r"EM\d+b\.htm")):
        name = a.get_text(strip=True)
        if not name:
            continue
        mapping[name] = urljoin(BASE, a.get("href"))

    INDEX_CACHE.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[emelbourne] indexed {len(mapping)} suburb entries -> {INDEX_CACHE}")
    return mapping


def parse_detail(html: str) -> dict:
    """Extract {meta_line, body, author} from a detail page."""
    soup = BeautifulSoup(html, "lxml")

    # The detail page has the suburb h2 followed by:
    #   <p>(postcode, distance, lga)</p>
    #   <p>...body paragraph 1...</p>
    #   <p>...body paragraph 2... (optional)</p>
    #   <p>Author Name</p>
    #
    # Strategy: find the <p> matching META_LINE_RE; from that <p> walk forward
    # collecting siblings until we hit the page footer (we'll cap at the last
    # <p> in the same parent container).

    meta_p = None
    for p in soup.find_all("p"):
        if META_LINE_RE.match(p.get_text(strip=True)):
            meta_p = p
            break

    if meta_p is None:
        return {"meta_line": "", "body": "", "author": "", "raw_paragraphs": []}

    meta_line = meta_p.get_text(strip=True)

    # Collect subsequent <p> siblings within the same parent
    paragraphs: list[str] = []
    sib = meta_p.find_next_sibling()
    while sib is not None:
        if sib.name == "p":
            txt = sib.get_text(" ", strip=True)
            if txt:
                paragraphs.append(txt)
        sib = sib.find_next_sibling()

    if not paragraphs:
        return {"meta_line": meta_line, "body": "", "author": "", "raw_paragraphs": []}

    # Last paragraph is the author byline (typically 1-4 short words, no punctuation)
    author = ""
    body_paragraphs = paragraphs
    last = paragraphs[-1].strip()
    looks_like_byline = (
        len(last) < 80
        and last.count(".") == 0
        and last.count(",") <= 1
        and not last.endswith(("?", "!"))
    )
    if looks_like_byline:
        author = last
        body_paragraphs = paragraphs[:-1]

    body = "\n\n".join(body_paragraphs).strip()
    return {
        "meta_line": meta_line,
        "body": body,
        "author": author,
        "raw_paragraphs": paragraphs,
    }


def fetch_suburb(suburb: str, index: dict[str, str], force: bool = False) -> dict | None:
    RAW.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    cache_path = RAW / f"{safe}.json"
    if cache_path.exists() and not force:
        print(f"[emelbourne] {suburb}: cached")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    url = index.get(suburb)
    if not url:
        # Try a few simple variants before giving up
        variants = [suburb.replace("St ", "St. "), suburb.replace("St. ", "St ")]
        for v in variants:
            if v in index:
                url = index[v]
                break

    if not url:
        stub = {
            "suburb": suburb,
            "missing": True,
            "reason": "not in eMelbourne index (will fall back to Wikipedia)",
        }
        cache_path.write_text(json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[emelbourne]   missing from index — stubbed")
        return stub

    print(f"[emelbourne] {suburb}: GET {url}")
    try:
        resp = fetch(url)
    except requests.RequestException as e:
        print(f"[emelbourne]   request failed: {e}")
        return None

    if resp.status_code != 200:
        print(f"[emelbourne]   HTTP {resp.status_code}")
        return None

    parsed = parse_detail(resp.text)
    data = {"suburb": suburb, "url": url, "status": 200, **parsed}
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    body_len = len(parsed["body"])
    print(f"[emelbourne]   {body_len} chars body, author: {parsed['author'] or '(none)'}")
    return data


def load_suburb_list() -> list[str]:
    return [s.strip() for s in SUBURB_LIST.read_text(encoding="utf-8").splitlines() if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*", help="suburb names; skip if --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="re-fetch the suburb index page even if cached")
    args = parser.parse_args()

    index = build_index(force=args.rebuild_index)

    if args.all:
        suburbs = load_suburb_list()
    elif args.suburbs:
        suburbs = args.suburbs
    elif args.rebuild_index:
        # User just wanted to refresh the index
        return 0
    else:
        parser.error("provide a suburb, --all, or --rebuild-index")
        return 2

    n_ok = 0
    n_missing = 0
    n_fail = 0
    for i, s in enumerate(suburbs, 1):
        print(f"[emelbourne] [{i}/{len(suburbs)}] {s}")
        result = fetch_suburb(s, index, force=args.force)
        if result is None:
            n_fail += 1
        elif result.get("missing"):
            n_missing += 1
        else:
            n_ok += 1
        # Polite jitter only after a real network fetch (not when serving from cache)
        if i < len(suburbs) and result is not None and not result.get("missing"):
            wait = random.uniform(SLEEP_MIN, SLEEP_MAX)
            time.sleep(wait)

    print(f"[emelbourne] done: {n_ok} ok, {n_missing} missing-from-index, "
          f"{n_fail} failed, total {len(suburbs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
