"""Strip fun-facts that duplicate the same suburb's page content.

fun_facts.json and suburbs.json were both mined from the same Reddit
threads, so a story can appear in both the quiz corpus and a suburb's
lore/history/vibe/tags. Two passes remove the overlap:

1. Heuristic pass: Jaccard word-overlap / shared rare words (same rules as
   docs/trivia.js's clue selector), with the suburb name excluded.
2. LLM pass: DeepSeek reads each suburb's remaining fun facts against its
   page content and flags any fact that retells a story already told there
   — catches heavily paraphrased duplicates the token maths miss.

Run (after scrape.mine_fun_facts):
    uv run python -u -m scrape.dedup_fun_facts
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
FUN_FACTS_JSON = ROOT / "data" / "fun_facts.json"
SUBURBS_JSON = ROOT / "data" / "suburbs.json"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

LLM_PROMPT = """You maintain a Melbourne suburb quiz.

A suburb has PAGE CONTENT shown on its public profile (lore items, history, vibe, tags).
It also has candidate FUN FACTS for the quiz. The quiz must never recycle page content,
and the same story must never appear twice.

For each candidate fun fact, decide: does it RETELl a story or claim ALREADY told in the
page content — even with completely different wording? ("named after the English racecourse
Ascot" in the fun fact vs "named after the English racecourse Ascot due to its ties to the
horse racing industry" in history is the SAME story — drop it.) Facts that add genuinely
new information — a different event, person, building, era or claim about the same subject —
must be kept.

Output STRICT JSON: {"drop": [indices of fun facts to drop, 0-based]} — empty array if none."""

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "is", "are", "was", "were", "it", "its", "this", "that",
    "suburb", "suburbs", "locals", "local", "melbourne", "city", "they", "their",
    "has", "have", "had", "been", "being", "you", "your", "who", "which", "what",
    "how", "when", "where", "not", "no", "so", "as", "be", "he", "she", "we",
    "them", "his", "her", "there", "here", "also", "very", "just", "one", "two",
    "some", "all", "more", "most", "into", "out", "up", "down", "over", "under",
    "about", "around", "between", "after", "before", "during", "since", "until",
    "because", "though", "although", "lore", "fact", "take", "history", "census",
    "known", "called", "says", "said", "like", "never", "ever", "even", "years",
    "year", "later", "early", "once", "named", "name", "opened", "built", "was",
    "has", "had", "now", "still", "back", "would", "could",
}

DUP_THRESHOLD = 0.33
DUP_SHARED = 3


def tokenize(text: str, exclude: set[str] | None = None) -> set[str]:
    clean = re.sub(r"<[^>]+>", " ", text.lower())
    clean = re.sub(r"&[a-z#0-9]+;", " ", clean)
    clean = re.sub(r"[^a-z\s]", " ", clean)
    return {w for w in clean.split() if len(w) >= 3 and w not in STOPWORDS and not (exclude and w in exclude)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def is_dup(a: set[str], b: set[str]) -> bool:
    return jaccard(a, b) >= DUP_THRESHOLD or len(a & b) >= DUP_SHARED


def llm_pass(client: OpenAI, suburbs: dict, facts: dict) -> int:
    """Ask DeepSeek to drop fun facts that retell a page story."""
    removed = 0
    for name, entry in suburbs.items():
        if not facts.get(name):
            continue
        page = []
        if entry.get("vibe"):
            page.append(f"VIBE: {entry['vibe']}")
        if entry.get("history"):
            page.append(f"HISTORY: {entry['history']}")
        for i, item in enumerate(entry.get("lore") or []):
            page.append(f"LORE {i}: {item}")
        for i, item in enumerate(entry.get("tags") or []):
            page.append(f"TAG {i}: {item}")
        candidates = facts[name]
        prompt = (
            "PAGE CONTENT:\n" + "\n".join(page) +
            "\n\nCANDIDATE FUN FACTS:\n" +
            "\n".join(f"{i}. {f}" for i, f in enumerate(candidates))
        )
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": LLM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=600,
            )
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            parsed = json.loads(text)
            drop = {int(i) for i in parsed.get("drop", []) if isinstance(i, int) or str(i).isdigit()}
            kept = [f for i, f in enumerate(candidates) if i not in drop]
            if len(kept) != len(candidates):
                removed += len(candidates) - len(kept)
                facts[name] = kept
        except Exception as e:
            print(f"[dedup]   llm pass failed for {name}: {e}")
    return removed


def main() -> int:
    facts = json.loads(FUN_FACTS_JSON.read_text(encoding="utf-8"))
    suburbs = json.loads(SUBURBS_JSON.read_text(encoding="utf-8"))

    removed = 0
    kept_total = 0
    for name, entry in suburbs.items():
        if name not in facts:
            continue
        # The suburb's own name appears in every fact and every page field —
        # it must not count as shared story evidence.
        exclude = {
            w for w in re.sub(r"[^a-z\s]", " ", (name + " " + (entry.get("nickname") or "")).lower()).split()
            if len(w) >= 3
        }
        page_texts: list[set[str]] = []
        for field in ("vibe", "history", "history_source_url"):
            if entry.get(field):
                page_texts.append(tokenize(str(entry[field]), exclude))
        for field in ("lore", "tags", "quotes", "top_quote"):
            for item in entry.get(field) or []:
                page_texts.append(tokenize(str(item), exclude))

        kept = []
        for f in facts[name]:
            ft = tokenize(f, exclude)
            if any(is_dup(ft, pt) for pt in page_texts if pt):
                removed += 1
                continue
            kept.append(f)
        facts[name] = kept
        kept_total += len(kept)

    # Drop empty buckets.
    facts = {k: v for k, v in facts.items() if v}
    total = sum(len(v) for v in facts.values())
    print(f"[dedup] heuristic pass: removed {removed}, kept {kept_total} suburb facts; "
          f"{len(facts)} keys, {total} facts")

    # LLM pass on the residue — catches paraphrased same-story retellings.
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        llm_removed = llm_pass(client, suburbs, facts)
        facts = {k: v for k, v in facts.items() if v}
        total = sum(len(v) for v in facts.values())
        print(f"[dedup] llm pass: removed {llm_removed}; corpus now {len(facts)} keys, {total} facts")
    else:
        print("[dedup] DEEPSEEK_API_KEY not set — skipping llm pass")

    FUN_FACTS_JSON.write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[dedup] wrote {FUN_FACTS_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
