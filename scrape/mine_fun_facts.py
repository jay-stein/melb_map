"""Build the dedicated fun-facts corpus for the Suburb Detective quiz.

Quiz facts must NOT be recycled page content, so they live in their own
file — data/fun_facts.json — separate from the suburb profiles in
suburbs.json. Two sources:

1. Suburb-specific facts: moved from suburbs.json (recently generated from
   the gold fun-fact threads by the summariser) into {suburb: [facts]}.
2. City-wide facts: mined fresh from the hand-picked "best fun fact about
   Melbourne" threads in the meta corpus by DeepSeek into {"city": [facts]}.

Run (after scrape.mine_threads has run):
    uv run python -u -m scrape.mine_fun_facts
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from scrape.reddit import RAW, META_OUT

ROOT = Path(__file__).resolve().parent.parent
SUBURBS_JSON = ROOT / "data" / "suburbs.json"
FUN_FACTS_JSON = ROOT / "data" / "fun_facts.json"
SUBURB_LIST = ROOT / "data" / "suburb_list.txt"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# Threads that are predominantly CITY-wide facts (not suburb-specific).
CITY_THREAD_IDS = {
    "1hus1er", "6ai3lm", "p6m0nu", "wxt2vo", "18rxisg", "1450bs",
    "dhj71y", "n9ntdm", "9ua8lc",
}

FACTS_SYSTEM_PROMPT = """You mine clean, quiz-worthy fun facts about Melbourne city from Reddit comments.

Rules:
- Extract ONLY genuine, verifiable-sounding facts about the CITY of Melbourne or its landmarks/streets/parks/buildings/trams — NOT suburb-specific facts (those are handled elsewhere), NOT opinions, jokes, weather whinges, or personal anecdotes.
- Rewrite each fact as ONE punchy sentence, present tense or historical past tense, no "one time" or "apparently" filler.
- Keep the surprising detail: the Queen Vic Market carpark being built on graves, the Yarra waterfall being blown up, etc.
- Drop facts with URLs. Keep 25-120 word answers in original.
- Output STRICT JSON: an array of strings, e.g. ["...", "..."].
- If nothing is worth keeping, output []. Never fabricate — only facts stated or clearly implied in the comments."""

MAX_BATCH_COMMENTS = 70


def load_meta() -> dict:
    path = RAW / META_OUT
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"threads": []}


def _batch(comments: list[dict], size: int):
    for i in range(0, len(comments), size):
        yield comments[i:i + size]


def mine_city_facts(client: OpenAI, meta: dict) -> list[str]:
    comments: list[dict] = []
    for t in meta["threads"]:
        if t["id"] in CITY_THREAD_IDS:
            comments.extend(t["comments"])
    # De-dupe by body, drop junk, score-order so quality comes first.
    seen: set[str] = set()
    uniq: list[dict] = []
    for c in sorted(comments, key=lambda c: c.get("score", 0), reverse=True):
        body = (c.get("body") or "").strip()
        key = body.lower()
        if not body or body in ("[removed]", "[deleted]") or key in seen:
            continue
        if len(body) < 30:
            continue
        seen.add(key)
        uniq.append(c)
    print(f"[facts] {len(comments)} raw comments -> {len(uniq)} unique candidates")

    facts: list[str] = []
    for i, chunk in enumerate(_batch(uniq, MAX_BATCH_COMMENTS)):
        blob = "\n\n".join(f"- {c['body']}" for c in chunk)
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": FACTS_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Here are the comments:\n\n{blob}"},
                    ],
                    temperature=0.3,
                    max_tokens=4000,
                )
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1]
                    if text.endswith("```"):
                        text = text[:-3]
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    facts.extend(str(f).strip() for f in parsed if str(f).strip())
                    print(f"[facts]   batch {i + 1}: +{len(parsed)} facts")
                break
            except Exception as e:
                print(f"[facts]   batch {i + 1} attempt {attempt + 1} failed: {e}")
                if attempt == 0:
                    continue
    # Dedupe case-insensitively.
    out: list[str] = []
    seen_out: set[str] = set()
    for f in facts:
        key = f.lower()
        if key not in seen_out:
            seen_out.add(key)
            out.append(f)
    return out


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("[facts] DEEPSEEK_API_KEY not set — city-facts mining skipped")
        client = None
    else:
        client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    meta = load_meta()

    # 1. Move suburb-specific facts out of suburbs.json (idempotent: existing
    # corpus entries are preserved, only keys still in suburbs.json are moved).
    suburbs = json.loads(SUBURBS_JSON.read_text(encoding="utf-8"))
    existing_corpus = {}
    if FUN_FACTS_JSON.exists():
        existing_corpus = json.loads(FUN_FACTS_JSON.read_text(encoding="utf-8"))
    by_suburb: dict[str, list[str]] = {}
    for name, entry in suburbs.items():
        facts = [str(f).strip() for f in entry.pop("fun_facts", []) if str(f).strip()]
        if facts:
            by_suburb[name] = facts
    SUBURBS_JSON.write_text(json.dumps(suburbs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[facts] moved suburb facts for {len(by_suburb)} suburbs out of suburbs.json")

    # 2. Mine city-wide facts from the gold threads.
    city_facts = mine_city_facts(client, meta) if client else []
    print(f"[facts] {len(city_facts)} city-wide facts mined")

    merged = dict(existing_corpus)
    merged["city"] = city_facts
    merged.update({k: v for k, v in by_suburb.items() if v})
    FUN_FACTS_JSON.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in merged.values())
    print(f"[facts] wrote {FUN_FACTS_JSON}: {len(merged)} keys, {total} facts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
