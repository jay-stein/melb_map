"""Fetch Wikipedia summary extracts for each suburb (fallback history source).

Uses Wikipedia's REST summary endpoint, which returns a clean 3-5 sentence
intro paragraph per article — perfect for our 1-2 sentence history blurb.

Title resolution: most Melbourne suburbs disambiguate as "{Suburb},_Victoria".
Edge cases (e.g. CBD) are listed in WIKIPEDIA_TITLE_OVERRIDES.

Usage:
    uv run python -m scrape.wikipedia Reservoir
    uv run python -m scrape.wikipedia --all
    uv run python -m scrape.wikipedia Reservoir --force
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw" / "wikipedia"
SUBURB_LIST = DATA / "suburb_list.txt"

USER_AGENT = "melb-map (educational hobby project; contact via github)"
SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
ARTICLE_URL = "https://en.wikipedia.org/wiki/{title}"

SLEEP_MIN = 5.0
SLEEP_MAX = 8.0

# Non-default title overrides for suburbs whose Wikipedia article isn't
# "{Suburb},_Victoria". Extend as we discover mismatches.
WIKIPEDIA_TITLE_OVERRIDES: dict[str, str] = {
    "Melbourne": "Melbourne_central_business_district",
}


def title_for(suburb: str) -> str:
    if suburb in WIKIPEDIA_TITLE_OVERRIDES:
        return WIKIPEDIA_TITLE_OVERRIDES[suburb]
    return f"{suburb.replace(' ', '_')},_Victoria"


def fetch_summary(title: str) -> requests.Response:
    url = SUMMARY_URL.format(title=quote(title, safe=",_"))
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def fetch_suburb(suburb: str, force: bool = False) -> dict | None:
    RAW.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    cache_path = RAW / f"{safe}.json"
    if cache_path.exists() and not force:
        print(f"[wikipedia] {suburb}: cached")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    titles_to_try = [title_for(suburb)]
    # Fallback: bare suburb name (no ",_Victoria" suffix) — handles disambig edge cases
    bare = suburb.replace(" ", "_")
    if bare not in titles_to_try:
        titles_to_try.append(bare)

    last_status = None
    for title in titles_to_try:
        print(f"[wikipedia] {suburb}: GET summary for {title!r}")
        try:
            resp = fetch_summary(title)
        except requests.RequestException as e:
            print(f"[wikipedia]   request failed: {e}")
            return None
        last_status = resp.status_code
        if resp.status_code == 200:
            payload = resp.json()
            data = {
                "suburb": suburb,
                "title": payload.get("title", ""),
                "extract": payload.get("extract", "").strip(),
                "url": ARTICLE_URL.format(title=quote(title, safe=",_")),
                "wikidata_id": payload.get("wikibase_item", ""),
                "status": 200,
            }
            cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[wikipedia]   {len(data['extract'])} chars extract")
            return data
        print(f"[wikipedia]   HTTP {resp.status_code} for {title!r}")

    stub = {
        "suburb": suburb,
        "missing": True,
        "status": last_status,
        "reason": f"all title variants returned {last_status}",
    }
    cache_path.write_text(json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[wikipedia]   no article found — stubbed")
    return stub


def load_suburb_list() -> list[str]:
    return [s.strip() for s in SUBURB_LIST.read_text(encoding="utf-8").splitlines() if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*")
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

    n_ok = n_missing = n_fail = 0
    for i, s in enumerate(suburbs, 1):
        print(f"[wikipedia] [{i}/{len(suburbs)}] {s}")
        result = fetch_suburb(s, force=args.force)
        if result is None:
            n_fail += 1
        elif result.get("missing"):
            n_missing += 1
        else:
            n_ok += 1
        if i < len(suburbs) and result is not None:
            time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"[wikipedia] done: {n_ok} ok, {n_missing} missing, {n_fail} failed, total {len(suburbs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
