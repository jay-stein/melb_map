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
  "tags": [7 to 12 short, vivid phrases],
  "vibe": "2-3 full sentences capturing the suburb's character with personality",
  "lore": [3 to 6 specific stories, landmarks, recurring drama, businesses, or in-jokes that locals would recognise],
  "history": "1-2 sentences on the suburb's founding, etymology, or key historical arc — only what gives modern character, not a textbook recap. Prefer the EMELBOURNE source when present; fall back to WIKIPEDIA. If both are thin or missing, return an empty string.",
  "quotes": [3 to 5 verbatim Reddit lines that exemplify the suburb's character],
  "primary_category": "one of: hipster, posh, student, family, nightlife, industrial, sleepy, multicultural, unknown",
  "mascot": {
    "name": "an absurd character name with title (e.g. 'Warren P. Toadfish, ESQ.')",
    "tagline": "one snappy line in the mascot's voice",
    "description": "1-2 sentences, sarcastic and a bit bitchy, in the spirit of a knowing local roasting their own neighbourhood. Pick ONE central anthropomorphic figure (animal/creature/object) with AT MOST TWO distinguishing items (one outfit + one prop), each tied to suburb lore. Don't over-explain; the snark is the point.",
    "image_prompt": "a single self-contained sentence under 40 words. Format strictly: '[creature/figure] wearing [ONE outfit], holding/with [ONE prop], single character portrait, plain white background, no other characters or scenery, simple flat 2D cartoon illustration, comic style.' No backgrounds. No additional characters."
  },
  "flag": {
    "colors": ["2-3 hex colors, e.g. '#1B3A5F', '#F4D35E', '#FFFFFF'"],
    "emblem": "single concrete object/animal/symbol that goes on the flag, in 1-3 words (e.g. 'toadfish', 'banh mi roll', 'Range Rover key', 'tomato')",
    "style": "one of: horizontal tricolor, vertical tricolor, horizontal bicolor, diagonal split, quartered, canton-and-field",
    "description": "1-2 sentences explaining the flag's design and what each color/emblem references about the suburb",
    "image_prompt": "a single self-contained sentence suitable as input to a flag image generator. Must specify the band layout, the colors (use names not hex), the emblem in the center, and end with 'minimalist flat civic flag, vexillographic design, simple, plain white background, no text, no shadows, rectangular 3:2 ratio'. Keep under 50 words."
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
- 3-6 specific items each in 1-2 sentences. The kind of stuff locals tell newcomers.
- Real businesses (Franco Cozzo, Greville Records), real landmarks (the dog-shaped clouds painted on a wall), recurring news stories (the truck-bridge collision saga), persistent neighbourhood characters or dramas.
- If the corpus has thin lore, return fewer items rather than padding with generic stuff.

HISTORY GUIDELINES:
- 1-2 sentences. Distil the EMELBOURNE entry (or WIKIPEDIA fallback) into a tight blurb covering ONE or TWO of: founding date, name etymology, key development phase, defining historical industry/migration wave.
- Pick the angle that connects most to the suburb's MODERN character. e.g. "Brick-making and quarries shaped Brunswick from the 1840s; the wave of post-WWII Italian and Greek migration left the Sydney Road texture you still see today" — explains the present.
- Avoid textbook-style "X was incorporated in Y, became part of Z council in 1928, etc." — keep it character-relevant.
- If EMELBOURNE is missing AND WIKIPEDIA only says something like "X is a suburb N km from CBD with population Y", return an empty string. Don't pad with generic facts.

QUOTES GUIDELINES:
- 3-5 lines lifted VERBATIM from the corpus. Do not paraphrase, invent, or polish.
- Pick lines that show personality and humour: hot takes, jokes, weird observations.
- Trim to 1-2 sentences each. Strip leading/trailing whitespace.
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
- The mascot is a quirky anthropomorphic character. Think hoodmaps-meets-Mascot-Tournament, not corporate-mascot.
- Pick ONE central creature/figure (an animal, an object, a stereotype-figure). Add AT MOST TWO distinguishing items: ONE outfit (e.g. coat, hat, monocle, hi-vis vest) and ONE prop (e.g. coffee cup, croissant, key, briefcase). Both items must callback to specific suburb lore from the corpus.
- Resist the urge to pile on props or scene elements. The image will be a single-character portrait — no background, no other figures. Simpler beats busier.
- Give it a ridiculous formal name with title and a one-line catchphrase in its voice.
- DESCRIPTION TONE: 1-2 sentences max. SARCASTIC, BITCHY, knowing — the voice of a local who loves their suburb but won't stop dragging it. Strong example: "Felix the Tuxedo Cat clocks in to his startup in a tiny puffer jacket, business card already in paw, eight different oat-milk receipts in the other pocket." Weak example: "Felix is a friendly cat who lives in Carlton and enjoys the cafes and university atmosphere." Avoid earnest set-up; just hit the joke.
- The image_prompt must end with the standard suffix and explicitly say "single character portrait, plain white background, no other characters or scenery". Keep it under 40 words total.
- Don't be cruel; punch at quirks, not people. No racial caricatures.

FLAG GUIDELINES:
- This is a CIVIC FLAG, designed using real vexillography principles: simple, recognisable from a distance, 2-3 colours max, ONE small symbol.
- Pick a colour palette that ties to the suburb (e.g. posh = navy + gold, hipster = burgundy + cream, multicultural-ethnic-area = adopt a relevant culture's palette respectfully).
- The emblem is ONE concrete thing — an animal, an object, an iconic feature — chosen from the suburb's lore. Not a logo or letters.
- Style is the band layout. Don't invent new styles outside the list given in the schema.
- The image_prompt must result in a flag image — flat colours, no shadows, no gradients, no text, no national flag references. Vexillographic and clean.

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
    parsed["tags"] = list(parsed.get("tags", []))[:12]
    parsed["lore"] = list(parsed.get("lore", []))[:6]
    parsed["history"] = str(parsed.get("history", "")).strip()
    parsed["quotes"] = list(parsed.get("quotes", []))[:5]
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
    # Drop legacy field if model emits it
    parsed.pop("food_and_drink", None)
    mascot = parsed.get("mascot") or {}
    parsed["mascot"] = {
        "name": str(mascot.get("name", "")).strip(),
        "tagline": str(mascot.get("tagline", "")).strip(),
        "description": str(mascot.get("description", "")).strip(),
        "image_prompt": str(mascot.get("image_prompt", "")).strip(),
    }
    flag = parsed.get("flag") or {}
    parsed["flag"] = {
        "colors": list(flag.get("colors", []))[:3],
        "emblem": str(flag.get("emblem", "")).strip(),
        "style": str(flag.get("style", "")).strip(),
        "description": str(flag.get("description", "")).strip(),
        "image_prompt": str(flag.get("image_prompt", "")).strip(),
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
