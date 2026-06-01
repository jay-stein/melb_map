"""Summarise a suburb's Reddit corpus into quirky tags via DeepSeek.

Uses the OpenAI SDK pointed at DeepSeek's OpenAI-compatible endpoint.
DeepSeek auto-caches identical prompt prefixes server-side, so we keep the
SYSTEM prompt byte-identical across all 75 calls.

Usage:
    uv run python -m scrape.summarize Fitzroy           # single, prints JSON
    uv run python -m scrape.summarize --all             # every cached suburb
    uv run python -m scrape.summarize --all --force     # redo even if in suburbs.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
MELBZ_DIR = RAW / "melbz"
EMELBOURNE_DIR = RAW / "emelbourne"
WIKIPEDIA_DIR = RAW / "wikipedia"
META_PATH = RAW / "_meta.json"
SUBURBS_OUT = DATA / "suburbs.json"
SUBURB_LIST = DATA / "suburb_list.txt"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# Cap on meta-mention comments injected per suburb (cost / context control)
MAX_META_MENTIONS = 30

# Curated fallback nicknames for suburbs where the locals' shorthand is so
# common it would feel wrong missing, but doesn't always show up verbatim in
# the corpus. Only used when the LLM returns empty for `nickname` — LLM-
# extracted nicknames from corpus win when present. Keep this list TIGHT;
# every entry adds visual chrome to the map.
KNOWN_NICKNAMES: dict[str, str] = {
    "Fitzroy": "Fitzy",
    "St Kilda": "St K",
    "Footscray": "Scray",
    "Williamstown": "Willy",
    "Prahran": "Pran",
    "Reservoir": "Rezza",
    "Melbourne": "the CBD",
}

CATEGORIES = [
    "hipster", "posh", "student", "family",
    "nightlife", "industrial", "sleepy", "multicultural", "unknown",
]

SYSTEM_PROMPT = """You profile Melbourne suburbs by extracting their cultural quirks from r/melbourne posts and comments.

Your job is to read the corpus the user gives you for ONE suburb and return a JSON object capturing what makes that suburb distinctive — the things locals joke about, complain about, or recognise instantly. Think hoodmaps.com style: specific, observational, funny, a little affectionate. Be DETAILED and SPECIFIC — readers want texture, not generic suburb descriptions.

The corpus may contain UP TO FIVE sections (any or all may appear):
- MELBZ.COM.AU PROFILE: a curated suburb guide with sections like "What X Is Actually Like", "Who Lives Here", "Eating and Drinking", "Verdict". This is the highest-quality structured content — use it for accurate facts (boundaries, transport, demographics) and for character cues. The "What X Is Actually Like" and "Verdict" sections are gold.
- REDDIT POSTS / COMMENTS: discussions where this suburb is the main topic. Raw, often funny, occasionally exaggerated — great for vibes and one-liners.
- META MENTIONS: snippets pulled from cross-suburb Reddit threads ("best suburb", "your suburb in 3 words", etc.) where this suburb was named in passing. These capture how outsiders see the suburb — the stereotypes, the offhand jokes. Often the gold.
- EMELBOURNE: a curated University of Melbourne encyclopaedia entry with the suburb's founding, etymology, and historical arc. Use this as the PRIMARY source for the `history` field — it's reliable scholarly writing.
- WIKIPEDIA: the lead paragraph of the suburb's Wikipedia article. Used as a FALLBACK for `history` when EMELBOURNE is absent. Often very thin (just "X is a suburb of Melbourne, N km from CBD") — if so, leave `history` empty rather than padding.

When sources disagree (e.g. MELBZ says "great public transport" but Reddit complains about it), trust REDDIT for vibes/character and MELBZ for facts. For history, prefer EMELBOURNE; never use REDDIT or MELBZ for the `history` field. Quote ONLY from Reddit (verbatim quotes from MELBZ/EMELBOURNE/WIKIPEDIA are off — Reddit voices are what we want in `quotes`).

OUTPUT FORMAT (strict JSON, nothing else):
{
  "nickname": "a widely-used 1-3 word locals' nickname for this suburb IF prevalent in the corpus (e.g. 'Fitzy' for Fitzroy, 'St K' for St Kilda, 'Scray' for Footscray, 'Swappers Crossing' for Hoppers Crossing). Must appear in the corpus OR be a well-documented Melbourne nickname. Return empty string if no clear nickname exists — don't invent one.",
  "tags": [7 to 12 short, vivid phrases],
  "vibe": "2-3 full sentences capturing the suburb's character with personality",
  "lore": [5 to 8 specific items, mix of PRESENT-DAY stories/landmarks/in-jokes AND HISTORICAL CURIOSITIES (the building that used to be a brothel, the train line they tore up in 1962, the urban legend about the haunted tram, demolished landmarks, ghost stories). Reddit history-flavoured threads are gold for this — surface them.],
  "history": "1-2 sentences on the suburb's founding, etymology, or key historical arc — only what gives modern character, not a textbook recap. Prefer the EMELBOURNE source when present; fall back to WIKIPEDIA. If both are thin or missing, return an empty string.",
  "top_quote": "the SINGLE funniest or most memorable verbatim Reddit line from the corpus — picked from the same pool as `quotes`. The line that made you laugh hardest, the most-Melbourne hot take, the niche observation that captures the suburb in one sentence. Empty string if nothing's truly standout.",
  "quotes": [5 to 10 verbatim Reddit lines that exemplify the suburb's character — hot takes, jokes, weird observations. Include the obscure-funny ones, not just the obvious picks.],
  "primary_category": "one of: hipster, posh, student, family, nightlife, industrial, sleepy, multicultural, unknown",
  "mascot": {
    "name": "a Melbourne archetype with a name (e.g. 'Dave the Brunswick Sparkie, 38', 'Stephanie from Toorak'). A warm character title is fine if the suburb doesn't fit a clean archetype.",
    "tagline": "one snappy line in the mascot's voice",
    "description": "1-2 sentences. WARM, AFFECTIONATE, observational — the voice of someone fond of their suburb describing a local you'd actually meet. Pick a recognisable Melbourne archetype (tradie, brunch dad, international student, AFL grandma, footy-club bartender, etc.) and ground them in one or two specific suburb details from the corpus. Skip surreal anthropomorphism unless the suburb really calls for it.",
    "image_prompt": "a single self-contained sentence under 40 words. Format: '[person archetype with clothing description] holding/with [ONE prop], single character portrait, plain white background, no other characters or scenery, simple flat 2D cartoon illustration, comic style.' No backgrounds. No additional characters."
  }
}

TAG GUIDELINES:
- 7-12 tags. Concrete, specific, sensory. "$9 oat lattes" beats "expensive cafes". "Tradies in hi-vis at 6am" beats "working class".
- Capture different facets: businesses, demographics, smells, traffic, fashion, gripes, in-jokes, weather quirks.
- Specific landmarks if they recur: "Franco Cozzo store", "the bridge that trucks keep hitting".
- Mix tones: gripes, affection, niche references, observational humour. Variety is the goal.
- Playful and observational, never mean-spirited or punching down. Avoid slurs, mocking poverty/race/disability.
- Lowercase except proper nouns. No periods. No emoji.

VIBE GUIDELINES:
- 2-3 full sentences. Real paragraph, not a one-liner. Captures the suburb's character, contradictions, and any recent shifts (gentrification, demographic flux, etc.).
- Strong vibe example: "Inner-north hipster cliché that refuses to die, but somehow still cool. Empty shopfronts and Soviet-style queues for $6 croissants coexist on the same block. Locals complain about it daily, then move there anyway."
- Weak vibe: "A residential suburb in inner Melbourne with cafes and bars."

LORE GUIDELINES:
- 5-8 specific items each in 1-2 sentences. The kind of stuff locals tell newcomers.
- MIX present-day (real businesses like Franco Cozzo, Greville Records; recurring news stories like the truck-bridge collision saga; neighbourhood characters or dramas; landmarks) WITH historical curiosities (the building that used to be a brothel, the train line they tore up in 1962, the demolished pub, the urban legend, the ghost story). Reddit history-flavoured threads ("history of X", "X used to be", "old X", "TIL Melbourne") are gold for the historical items — surface them.
- Prefer Reddit folk-history over the scholarly EMELBOURNE entry for these items; EMELBOURNE feeds the separate `history` field.
- If the corpus has thin lore, return fewer items rather than padding with generic stuff.

HISTORY GUIDELINES:
- 1-2 sentences. Distil the EMELBOURNE entry (or WIKIPEDIA fallback) into a tight blurb covering ONE or TWO of: founding date, name etymology, key development phase, defining historical industry/migration wave.
- Pick the angle that connects most to the suburb's MODERN character. e.g. "Brick-making and quarries shaped Brunswick from the 1840s; the wave of post-WWII Italian and Greek migration left the Sydney Road texture you still see today" — explains the present.
- Avoid textbook-style "X was incorporated in Y, became part of Z council in 1928, etc." — keep it character-relevant.
- If EMELBOURNE is missing AND WIKIPEDIA only says something like "X is a suburb N km from CBD with population Y", return an empty string. Don't pad with generic facts.

QUOTES GUIDELINES:
- 5-10 lines lifted VERBATIM from the corpus. Do not paraphrase, invent, or polish.
- Pick lines that show personality and humour: hot takes, jokes, weird observations, niche local references, the obscure-funny ones — not just the obvious picks. The Reddit comments are full of comedy gold, surface MORE not less.
- Trim to 1-2 sentences each. Strip leading/trailing whitespace.
- `top_quote` is your single best pick — the line that made you laugh hardest, the most-Melbourne hot take, the niche observation that captures the suburb in one sentence. Verbatim, picked from the same corpus pool. If nothing's truly standout, leave empty. It's fine for `top_quote` to also appear in `quotes`.
- If nothing's quotable, fewer is fine.

CATEGORY GUIDELINES:
- hipster: Fitzroy/Brunswick-style — vintage, third-wave coffee, art, indie
- posh: Toorak/Brighton — old money, expensive cars, private schools
- student: Carlton/Parkville-style — university adjacent, cheap eats, share houses
- family: quiet residential, schools, parks, family demographics dominant
- nightlife: pubs, clubs, late-night activity central to identity
- industrial: warehouses, light industry, trucks, "up-and-coming" gentrifiers
- sleepy: little discussed, mostly residential, low cultural footprint
- multicultural: signature ethnic food/community/culture (Footscray Vietnamese, Caulfield Jewish, etc.)
- unknown: corpus is too thin to characterise

MASCOT GUIDELINES:
- The mascot is a RECOGNISABLE MELBOURNE ARCHETYPE, drawn cartoon-style. Think "the person you'd actually meet in this suburb" — the tradie in hi-vis, the brunch dad on his second flat white, the international student wheeling a suitcase, the AFL grandma in club colours, the footy-club bartender. Not surreal anthropomorphic croissants.
- Pick ONE archetype rooted in the suburb's actual demographic / vibe. Add ONE outfit detail and ONE prop, both pulled from specific suburb lore in the corpus. Keep it visually simple — single-character portrait, no background, no other figures.
- Give them a relatable first-name-based name with archetype/age (e.g. "Dave the Brunswick Sparkie, 38", "Stephanie from Toorak, school-run mum") and a one-line catchphrase in their voice.
- DESCRIPTION TONE: 1-2 sentences max. WARM, AFFECTIONATE, observational — fond, not mocking. The voice of someone who LIVES there and is showing you their neighbour. Strong example: "Dave the Brunswick Sparkie, 38, hi-vis vest and Volley sneakers, holding a 6am long black from his go-to cafe on Lygon — knows every shortcut to the freeway and which kebab shop stays open after midnight." Weak example: "Dave is a friendly tradie who lives in Brunswick." — too generic, no texture. Aim for specific + affectionate.
- The image_prompt must end with the standard suffix and explicitly say "single character portrait, plain white background, no other characters or scenery". Keep it under 40 words total. Describe a person, not an anthropomorphised object/animal.
- Skip surreal anthropomorphism (talking croissants, monocled toadfish) unless the suburb really calls for it — default to relatable humans.
- Don't be cruel; punch at quirks, not people. No racial caricatures.

If the corpus is sparse or generic (under ~5 substantive comments), be honest: use "unknown" category, return fewer tags / lore items, and let the mascot be a vague placeholder that acknowledges the thin coverage. Never fabricate specific local references."""


def load_melbz(suburb: str) -> dict | None:
    safe = suburb.replace(" ", "_").replace("/", "_")
    path = MELBZ_DIR / f"{safe}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != 200 or not data.get("sections"):
        return None
    return data


def load_emelbourne(suburb: str) -> dict | None:
    """Returns the parsed eMelbourne entry, or None if missing/empty."""
    safe = suburb.replace(" ", "_").replace("/", "_")
    path = EMELBOURNE_DIR / f"{safe}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("missing") or not data.get("body"):
        return None
    return data


def load_wikipedia(suburb: str) -> dict | None:
    safe = suburb.replace(" ", "_").replace("/", "_")
    path = WIKIPEDIA_DIR / f"{safe}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("missing") or not data.get("extract"):
        return None
    return data


def build_user_prompt(corpus: dict, meta_mentions: list[dict] | None = None,
                      melbz: dict | None = None, emelbourne: dict | None = None,
                      wikipedia: dict | None = None) -> str:
    suburb = corpus["suburb"]
    posts = corpus.get("posts", [])
    lines = [f"Suburb: {suburb}", ""]

    # MELBZ first — it's the highest signal-to-noise (curated suburb profile)
    if melbz:
        lines.append("=== MELBZ.COM.AU PROFILE (curated suburb guide — high signal) ===")
        if melbz.get("title"):
            lines.append(f"# {melbz['title']}")
        for sec in melbz.get("sections", []):
            heading = sec.get("heading", "").strip()
            content = sec.get("content", "").strip()
            if not content:
                continue
            # Skip "Living in X — The Full Picture" — it's mostly internal links
            if "the full picture" in heading.lower():
                continue
            lines.append(f"## {heading}")
            # Cap each section to keep prompt manageable
            if len(content) > 1500:
                content = content[:1500] + "..."
            lines.append(content)
            lines.append("")
        lines.append("")

    lines.append("=== REDDIT POSTS (r/melbourne) ===")
    for p in posts:
        lines.append(f"[score {p['score']}] {p['title']}")
        if p.get("selftext"):
            text = p["selftext"].strip()
            if len(text) > 800:
                text = text[:800] + "..."
            lines.append(text)
        lines.append("")
    lines.append("=== REDDIT COMMENTS ===")
    for p in posts:
        for c in p.get("comments", []):
            body = c["body"].strip()
            if len(body) > 500:
                body = body[:500] + "..."
            lines.append(f"[score {c['score']}] {body}")

    if meta_mentions:
        lines.append("")
        lines.append("=== META MENTIONS (cross-suburb threads where this suburb was named) ===")
        for m in meta_mentions:
            body = m["body"].strip()
            if len(body) > 500:
                body = body[:500] + "..."
            ctx = m.get("thread_title", "")
            lines.append(f"[score {m['score']}] (in: {ctx[:60]}) {body}")

    if emelbourne and emelbourne.get("body"):
        lines.append("")
        lines.append("=== EMELBOURNE (curated University of Melbourne history entry — primary history source) ===")
        meta = emelbourne.get("meta_line", "").strip()
        if meta:
            lines.append(meta)
        lines.append(emelbourne["body"].strip())
        author = emelbourne.get("author", "").strip()
        if author:
            lines.append(f"— {author}")

    if wikipedia and wikipedia.get("extract"):
        lines.append("")
        lines.append("=== WIKIPEDIA (lead paragraph — fallback history source) ===")
        title = wikipedia.get("title", "").strip()
        if title:
            lines.append(f"# {title}")
        lines.append(wikipedia["extract"].strip())

    return "\n".join(lines)


def load_meta_threads() -> list[dict]:
    """Load _meta.json if present, return list of threads with comments. Empty if not."""
    if not META_PATH.exists():
        return []
    data = json.loads(META_PATH.read_text(encoding="utf-8"))
    return data.get("threads", [])


# Mention-scan terms for suburbs that aren't called by their SAL name in
# free text. For Melbourne (CBD), we scan for CBD-related terms.
SUBURB_MENTION_TERMS: dict[str, list[str]] = {
    "Melbourne": ["CBD", "Melbourne CBD", "city centre", "the city"],
}


def find_meta_mentions(suburb: str, threads: list[dict], all_suburbs: list[str]) -> list[dict]:
    """Find comments in meta threads that mention this suburb (case-insensitive,
    word boundary). Avoid double-matching by checking longer suburb names first
    and excluding mentions that are actually about a longer-name suburb."""
    import re

    # Pick the terms to scan for. Most suburbs use their SAL name; some (e.g.
    # Melbourne the CBD) need alias terms.
    terms = SUBURB_MENTION_TERMS.get(suburb, [suburb])
    target_pats = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in terms]

    # Avoid double-matching: if "Carlton" matches inside "Carlton North", skip
    # unless the plain term ALSO appears outside the longer phrase.
    longer_overlaps: list[str] = []
    for term in terms:
        longer_overlaps += [s for s in all_suburbs if s != suburb and term.lower() in s.lower() and s != term]
    longer_pats = [re.compile(rf"\b{re.escape(s)}\b", re.IGNORECASE) for s in set(longer_overlaps)]

    hits: list[dict] = []
    for thread in threads:
        for c in thread.get("comments", []):
            body = c.get("body", "")
            if not any(p.search(body) for p in target_pats):
                continue
            if longer_pats:
                stripped = body
                for lp in longer_pats:
                    stripped = lp.sub(" ", stripped)
                if not any(p.search(stripped) for p in target_pats):
                    continue
            hits.append({
                "body": body,
                "score": c.get("score", 0),
                "thread_title": thread.get("title", ""),
            })
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:MAX_META_MENTIONS]


def summarise(client: OpenAI, corpus: dict, meta_mentions: list[dict] | None = None,
              melbz: dict | None = None, emelbourne: dict | None = None,
              wikipedia: dict | None = None) -> dict:
    user_prompt = build_user_prompt(
        corpus, meta_mentions=meta_mentions, melbz=melbz,
        emelbourne=emelbourne, wikipedia=wikipedia,
    )

    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content
    parsed = json.loads(raw)

    # Validate / coerce
    cat = parsed.get("primary_category", "unknown")
    if cat not in CATEGORIES:
        cat = "unknown"
    parsed["primary_category"] = cat
    parsed["nickname"] = str(parsed.get("nickname", "")).strip()
    # Fallback: if LLM left nickname empty but we have a curated one for this
    # suburb, use that. LLM-extracted from corpus always wins when present.
    if not parsed["nickname"]:
        parsed["nickname"] = KNOWN_NICKNAMES.get(corpus.get("suburb", ""), "")
    parsed["tags"] = list(parsed.get("tags", []))[:12]
    parsed["lore"] = list(parsed.get("lore", []))[:8]
    parsed["history"] = str(parsed.get("history", "")).strip()
    parsed["top_quote"] = str(parsed.get("top_quote", "")).strip()
    parsed["quotes"] = list(parsed.get("quotes", []))[:10]
    parsed["vibe"] = str(parsed.get("vibe", "")).strip()
    # Determine which source the LLM was working from for `history`.
    # Set deterministically from corpus presence — don't trust the LLM to self-report.
    if parsed["history"]:
        if emelbourne and emelbourne.get("body"):
            parsed["history_source"] = "emelbourne"
            parsed["history_source_url"] = emelbourne.get("url", "")
            parsed["history_source_author"] = emelbourne.get("author", "")
        elif wikipedia and wikipedia.get("extract"):
            parsed["history_source"] = "wikipedia"
            parsed["history_source_url"] = wikipedia.get("url", "")
            parsed["history_source_author"] = ""
        else:
            parsed["history_source"] = None
            parsed["history_source_url"] = ""
            parsed["history_source_author"] = ""
    else:
        parsed["history_source"] = None
        parsed["history_source_url"] = ""
        parsed["history_source_author"] = ""
    # Drop legacy fields if the model emits them
    parsed.pop("food_and_drink", None)
    parsed.pop("flag", None)
    mascot = parsed.get("mascot") or {}
    parsed["mascot"] = {
        "name": str(mascot.get("name", "")).strip(),
        "tagline": str(mascot.get("tagline", "")).strip(),
        "description": str(mascot.get("description", "")).strip(),
        "image_prompt": str(mascot.get("image_prompt", "")).strip(),
    }
    return parsed


def make_client() -> OpenAI:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY not set in .env")
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def load_corpus(suburb: str) -> dict:
    safe = suburb.replace(" ", "_").replace("/", "_")
    path = RAW / f"{safe}.json"
    if not path.exists():
        raise FileNotFoundError(f"no cached corpus for {suburb} ({path}) — run scrape.reddit first")
    return json.loads(path.read_text(encoding="utf-8"))


def load_existing_output() -> dict:
    if SUBURBS_OUT.exists():
        return json.loads(SUBURBS_OUT.read_text(encoding="utf-8"))
    return {}


def save_output(data: dict) -> None:
    SUBURBS_OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_suburb_list() -> list[str]:
    return [line.strip() for line in SUBURB_LIST.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*", help="one or more suburb names; skip if --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    client = make_client()

    if args.all:
        suburbs = load_suburb_list()
    elif args.suburbs:
        suburbs = args.suburbs
    else:
        parser.error("provide a suburb or --all")
        return 2

    output = load_existing_output()
    meta_threads = load_meta_threads()
    all_suburbs = load_suburb_list()
    if meta_threads:
        n_thread_comments = sum(len(t.get("comments", [])) for t in meta_threads)
        print(f"[summarize] loaded {len(meta_threads)} meta threads, "
              f"{n_thread_comments} comments — will scan for mentions")
    else:
        print(f"[summarize] no _meta.json — running without meta-mentions "
              f"(run `uv run python -m scrape.reddit --meta` to generate)")

    for i, suburb in enumerate(suburbs, 1):
        if suburb in output and not args.force:
            print(f"[summarize] [{i}/{len(suburbs)}] {suburb}: already in suburbs.json, skipping")
            continue
        try:
            corpus = load_corpus(suburb)
        except FileNotFoundError as e:
            print(f"[summarize] [{i}/{len(suburbs)}] {suburb}: {e}")
            continue
        n_posts = len(corpus.get("posts", []))
        n_comments = sum(len(p.get("comments", [])) for p in corpus.get("posts", []))
        meta_mentions = find_meta_mentions(suburb, meta_threads, all_suburbs) if meta_threads else []
        melbz = load_melbz(suburb)
        emelb = load_emelbourne(suburb)
        wiki = load_wikipedia(suburb)
        n_melbz = len(melbz.get("sections", [])) if melbz else 0
        emelb_chars = len(emelb["body"]) if emelb else 0
        wiki_chars = len(wiki["extract"]) if wiki else 0
        print(f"[summarize] [{i}/{len(suburbs)}] {suburb}: "
              f"{n_posts} posts, {n_comments} comments, {len(meta_mentions)} meta, "
              f"{n_melbz} MELBZ sections, {emelb_chars}c eMelb, {wiki_chars}c wiki")
        try:
            result = summarise(client, corpus, meta_mentions=meta_mentions, melbz=melbz,
                               emelbourne=emelb, wikipedia=wiki)
        except Exception as e:
            print(f"[summarize]   FAILED: {e}")
            continue
        output[suburb] = result
        save_output(output)  # incremental save — interrupt-safe
        print(f"[summarize]   {result['primary_category']}: {len(result['tags'])} tags, "
              f"{len(result.get('lore', []))} lore items")
        if not args.all:
            print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
