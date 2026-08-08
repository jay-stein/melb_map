"""Build the True/False question bank for the Melbourne City Facts quiz.

Reads the city-wide facts from data/fun_facts.json and asks DeepSeek to
write a CONVINCINGLY FALSE variant of each (same style, wrong central
claim). The result is data/quiz_questions.json: a shuffled list of
{"text": str, "truth": bool} — the true statements plus their false
doppelgangers, ready for the Suburb Detective page's City Facts mode.

Run (after scrape.mine_fun_facts):
    uv run python -u -m scrape.mine_city_quiz
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
FUN_FACTS_JSON = ROOT / "data" / "fun_facts.json"
QUIZ_JSON = ROOT / "data" / "quiz_questions.json"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

FALSE_FACTS_PROMPT = """You are building a TRUE/FALSE trivia quiz about Melbourne, Australia.

I will give you a list of TRUE facts about Melbourne. For each one, write a CONVINCINGLY FALSE variant:

RULES for the false variant:
- Keep the exact sentence structure, style and tone of the original.
- Change the CENTRAL factual claim to something wrong yet plausible: a different place name, a different year, a different number, a different landmark, a different institution, or the opposite claim where that works.
- It must be clearly false to anyone who knows the real fact, but believable to someone who doesn't. No absurd claims.
- Do NOT negate mechanically ("not", "never", "wasn't") — that reads as obviously fake. Change the details instead.
- Do NOT mention "false", "actually", "really", or any meta wording.
- If a fact genuinely cannot be plausibly falsified, return null for it.

Output STRICT JSON, an array of objects in the same order as the input facts:
[{"false": "false variant of fact 1"}, {"false": null}, ...]
"""

MAX_BATCH = 50


def load_city_facts() -> list[str]:
    corpus = json.loads(FUN_FACTS_JSON.read_text(encoding="utf-8"))
    return [f.strip() for f in corpus.get("city", []) if f.strip()]


def _batches(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("[quiz] DEEPSEEK_API_KEY not set — aborting")
        return 1
    client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    facts = load_city_facts()
    print(f"[quiz] {len(facts)} city facts")

    false_map: dict[str, str] = {}
    for i, chunk in enumerate(_batches(facts, MAX_BATCH)):
        numbered = "\n".join(f"{j}. {f}" for j, f in enumerate(chunk))
        for attempt in range(2):
            try:
                resp = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": FALSE_FACTS_PROMPT},
                        {"role": "user", "content": f"Facts:\n{numbered}"},
                    ],
                    temperature=0.7,
                    max_tokens=4000,
                )
                text = resp.choices[0].message.content.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1]
                    if text.endswith("```"):
                        text = text[:-3]
                parsed = json.loads(text)
                got = 0
                for j, f in enumerate(chunk):
                    if j < len(parsed) and isinstance(parsed[j], dict):
                        v = parsed[j].get("false")
                        if isinstance(v, str) and v.strip():
                            false_map[f] = v.strip()
                            got += 1
                print(f"[quiz]   batch {i + 1}: {got}/{len(chunk)} false variants")
                break
            except Exception as e:
                print(f"[quiz]   batch {i + 1} attempt {attempt + 1} failed: {e}")

    if not false_map:
        print("[quiz] no false variants generated — aborting")
        return 1

    questions: list[dict] = []
    for f in facts:
        questions.append({"text": f, "truth": True})
        if f in false_map:
            questions.append({"text": false_map[f], "truth": False})
    random.Random(20260808).shuffle(questions)

    QUIZ_JSON.write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")
    true_n = sum(1 for q in questions if q["truth"])
    false_n = len(questions) - true_n
    print(f"[quiz] wrote {QUIZ_JSON}: {len(questions)} statements "
          f"({true_n} true, {false_n} false)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
