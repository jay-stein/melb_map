"""Surgically expand the `quotes` field for suburbs in suburbs.json.

Re-extracts ~15 verbatim r/melbourne quotes per suburb from the cached Reddit
corpus (per-suburb posts/comments + cross-suburb meta mentions) and merges ONLY
the `quotes` and `top_quote` fields back into suburbs.json. Everything else
(vibe, tags, lore, mascot, history, ...) is left untouched, so the existing
output you like is preserved.

Verbatim-checked: a returned quote is kept only if it actually appears in the
corpus text (normalised), guarding against paraphrase/hallucination.

Run:
    uv run python -u -m scrape.expand_quotes Fitzroy        # one suburb (test)
    uv run python -u -m scrape.expand_quotes --all          # every suburb in suburbs.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from scrape.summarize import (
    DEEPSEEK_MODEL,
    SUBURBS_OUT,
    build_user_prompt,
    find_meta_mentions,
    load_corpus,
    load_meta_threads,
    load_suburb_list,
    make_client,
)

TARGET_QUOTES = 15

# Byte-identical across all calls -> DeepSeek auto prompt-caching.
QUOTES_SYSTEM_PROMPT = """You extract the most characterful VERBATIM quotes about a Melbourne suburb from r/melbourne posts and comments.

RULES:
- Every quote MUST appear word-for-word in the input. Do NOT paraphrase, summarise, invent, translate, clean up grammar, or merge lines. Copy exactly.
- Pick lines with personality and humour: hot takes, jokes, weird observations, niche local references, affectionate gripes, the obscure-funny ones. Surface MORE not less — the obvious AND the obscure.
- Trim each to 1-2 sentences. Strip leading/trailing whitespace and any "[score N]" prefixes.
- Skip slurs and mean-spirited lines that punch down (race, poverty, disability). Playful and observational is fine.
- If the corpus is thin, return fewer rather than padding with generic lines.

Return ONLY a JSON object:
{
  "top_quote": "the single funniest or most-memorable verbatim line — the one that captures the suburb best (may also appear in quotes); empty string if nothing stands out",
  "quotes": ["up to ~15 verbatim lines, most characterful first"]
}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def expand_one(client, suburb: str, meta_threads: list, all_suburbs: list[str]) -> dict | None:
    try:
        corpus = load_corpus(suburb)
    except FileNotFoundError:
        print(f"[quotes] {suburb}: no cached Reddit corpus — skipping")
        return None
    meta_mentions = find_meta_mentions(suburb, meta_threads, all_suburbs) if meta_threads else []
    # Reddit-only blob: pass corpus + meta mentions, no melbz/emelbourne/wikipedia.
    user_prompt = build_user_prompt(corpus, meta_mentions=meta_mentions)

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": QUOTES_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt + f"\n\nReturn up to {TARGET_QUOTES} quotes."},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=2000,
    )
    parsed = json.loads(resp.choices[0].message.content)
    raw_quotes = [str(q).strip() for q in (parsed.get("quotes") or []) if str(q).strip()]
    top_quote = str(parsed.get("top_quote") or "").strip()

    # Verbatim guard: keep only quotes that actually appear in the corpus blob.
    haystack = _norm(user_prompt)
    kept, dropped = [], 0
    seen = set()
    for q in raw_quotes:
        nq = _norm(q)
        if nq in haystack and nq not in seen:
            kept.append(q)
            seen.add(nq)
        else:
            dropped += 1
    if top_quote and _norm(top_quote) not in haystack:
        top_quote = kept[0] if kept else ""

    kept = kept[:TARGET_QUOTES]  # hard cap (the model occasionally overshoots)

    print(f"[quotes] {suburb}: {len(kept)} kept"
          + (f" ({dropped} dropped as non-verbatim)" if dropped else ""))
    return {"quotes": kept, "top_quote": top_quote}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*", help="suburb names; skip if --all")
    parser.add_argument("--all", action="store_true", help="every suburb in suburbs.json")
    args = parser.parse_args()

    if not SUBURBS_OUT.exists():
        print("[quotes] suburbs.json not found — run scrape.summarize first")
        return 1
    data = json.loads(SUBURBS_OUT.read_text(encoding="utf-8"))

    if args.all:
        targets = list(data.keys())
    elif args.suburbs:
        targets = args.suburbs
    else:
        parser.error("provide a suburb or --all")

    client = make_client()
    meta_threads = load_meta_threads()
    all_suburbs = load_suburb_list()
    if meta_threads:
        print(f"[quotes] loaded {len(meta_threads)} meta threads for mention scan")

    updated = 0
    for i, suburb in enumerate(targets, 1):
        if suburb not in data:
            print(f"[quotes] [{i}/{len(targets)}] {suburb}: not in suburbs.json — skipping")
            continue
        print(f"[quotes] [{i}/{len(targets)}] {suburb}")
        result = expand_one(client, suburb, meta_threads, all_suburbs)
        if result is None:
            continue
        if not result["quotes"]:
            print(f"[quotes] {suburb}: no verbatim quotes returned — keeping existing")
            continue
        data[suburb]["quotes"] = result["quotes"]
        data[suburb]["top_quote"] = result["top_quote"]
        updated += 1
        # Save after each suburb so a mid-run interruption keeps progress.
        SUBURBS_OUT.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"[quotes] done: updated {updated}/{len(targets)} suburbs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
