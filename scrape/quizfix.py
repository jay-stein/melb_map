"""Filter bad false-variants from quiz_questions.json and retry them.

A false statement is bad if it is identical/near-identical to its true
original (matched by similarity) or uses meta wording ("actually",
"however", "this is false") that gives it away. Bad ones are regenerated
in batches with an explicit corrective prompt. Rewrites quiz_questions.json
as a shuffled list of {"text": str, "truth": bool}.
"""
from __future__ import annotations

import difflib
import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
QUIZ_JSON = ROOT / "data" / "quiz_questions.json"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

BANNED = [
    "but it is actually", "but is actually", "however", "contrary to",
    "not actually", "isn't actually", "not really", "in reality",
    "this is false", "falsely", "supposedly", "claims to be",
]

RETRY_PROMPT = """You are fixing mistakes in a TRUE/FALSE Melbourne trivia quiz.

Each item has a TRUE fact and the BROKEN false variant a previous attempt produced.
The broken variant is either identical to the true fact, too similar to it, or uses
meta wording ("actually", "however", "this is false") that gives it away as fake.

For EACH item write a NEW false variant:
- Same sentence structure and tone as the TRUE fact.
- Change the CENTRAL detail to something wrong yet plausible: different place,
  year, number, landmark, institution, or reversed claim. Never negate with
  "not"/"never"/"isn't". Never use meta wording like "actually" or "however".
- Clearly false to anyone who knows the truth, believable to someone who doesn't.
- If a fact genuinely cannot be fixed, return null for that item.

Output STRICT JSON, an array of objects in the same order as the items:
[{"false": "new false variant"}, {"false": null}, ...]"""

MAX_BATCH = 20


def load() -> list[dict]:
    return json.loads(QUIZ_JSON.read_text(encoding="utf-8"))


def save(qs: list[dict]) -> None:
    QUIZ_JSON.write_text(json.dumps(qs, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY not set")
        return 1
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    qs = load()
    trues = [q["text"] for q in qs if q["truth"]]
    false_texts = [q["text"] for q in qs if not q["truth"]]
    true_set = set(trues)
    good_false: list[str] = []
    bad: list[tuple[str, str]] = []  # (best-matching true, bad false)

    for f in false_texts:
        if f in true_set:
            bad.append((f, f))
            continue
        best_t, best_r = None, 0.0
        for t in trues:
            r = difflib.SequenceMatcher(None, t.lower(), f.lower()).ratio()
            if r > best_r:
                best_t, best_r = t, r
        if best_r > 0.94:
            bad.append((best_t, f))
            continue
        if any(b in f.lower() for b in BANNED):
            bad.append((best_t, f))
            continue
        good_false.append(f)

    print(f"[quizfix] {len(good_false)} good false variants kept, {len(bad)} to regenerate")

    fixed: list[str] = []
    used: set[str] = set(good_false) | set(trues)  # never collide with true facts
    for b_i in range(0, len(bad), MAX_BATCH):
        chunk = bad[b_i:b_i + MAX_BATCH]
        numbered = "\n".join(
            f"{j}. TRUE: {t}\n   BROKEN FALSE: {bf}" for j, (t, bf) in enumerate(chunk)
        )
        got = 0
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": RETRY_PROMPT},
                        {"role": "user", "content": f"Items:\n{numbered}"},
                    ],
                    temperature=0.7,
                    max_tokens=6000,
                )
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1]
                    if text.endswith("```"):
                        text = text[:-3]
                parsed = json.loads(text)
                for j, (t, _bf) in enumerate(chunk):
                    if j < len(parsed) and isinstance(parsed[j], dict):
                        v = parsed[j].get("false")
                        if isinstance(v, str) and v.strip() and v.strip() not in used:
                            fixed.append(v.strip())
                            used.add(v.strip())
                            got += 1
                break
            except Exception as e:
                print(f"[quizfix]   batch {b_i // MAX_BATCH + 1} attempt {attempt + 1} failed: {e}")
        print(f"[quizfix]   batch {b_i // MAX_BATCH + 1}: {got}/{len(chunk)} fixed")

    rebuilt: list[dict] = (
        [{"text": t, "truth": True} for t in trues]
        + [{"text": f, "truth": False} for f in good_false]
        + [{"text": f, "truth": False} for f in fixed]
    )
    random.Random(20260808).shuffle(rebuilt)
    save(rebuilt)
    n_true = sum(1 for q in rebuilt if q["truth"])
    n_false = len(rebuilt) - n_true
    print(f"[quizfix] wrote {QUIZ_JSON}: {len(rebuilt)} statements ({n_true} true, {n_false} false)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
