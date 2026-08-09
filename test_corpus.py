"""Corpus QA scan: detect malformed or leaky rounds in data/street_themes.json,
plus theme-label casing and icon coverage checks."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape.street_themes import canon_theme, title_case
import streets

corpus = json.loads(Path("data/street_themes.json").read_text(encoding="utf-8"))
suburbs = json.loads(Path("data/suburbs.json").read_text(encoding="utf-8"))
streets.init(corpus, suburbs)
issues = 0


def report(suburb, theme, msg):
    global issues
    issues += 1
    print(f"  [{suburb} / {theme}] {msg}")


seen_themes = set()
for suburb, entry in sorted(corpus.items()):
    for p in entry["puzzles"]:
        theme = p["theme"]
        seen_themes.add(theme)
        # casing: labels must be canonical title case
        if theme != title_case(theme):
            report(suburb, theme, f"theme label not title-cased: '{theme}'")
        # consolidation: every label must already be a canonical family name
        if theme != canon_theme(theme):
            report(suburb, theme, f"theme not canonical (fold into {canon_theme(theme)})")
        # every theme must have a proper icon (not the generic fallback)
        if streets.theme_icon(theme) == "🧩":
            report(suburb, theme, f"no icon mapped for theme")
        if not p.get("background") or len(p["background"]) < 20:
            report(suburb, theme, "background missing/too short")
        if not p.get("reveal"):
            report(suburb, theme, "reveal missing")
        if len(p.get("rounds", [])) != 5:
            report(suburb, theme, f"rounds = {len(p.get('rounds', []))} (want 5)")
        seen_namesakes = set()
        for r in p.get("rounds", []):
            street = r.get("street", "")
            namesake = r.get("namesake", "")
            clue = r.get("clue", "")
            options = r.get("options", [])
            explainer = r.get("explainer", "")
            tidbit = r.get("tidbit", "")
            if not street or not namesake or not clue:
                report(suburb, theme, f"round {street}: empty street/namesake/clue")
            if namesake and namesake in seen_namesakes:
                report(suburb, theme, f"round {street}: duplicate namesake {namesake}")
            seen_namesakes.add(namesake)
            if len(options) != 3:
                report(suburb, theme, f"round {street}: {len(options)} options (want 3)")
            elif namesake not in options:
                report(suburb, theme, f"round {street}: namesake not in options")
            if len(options) == 3 and len(set(options)) != 3:
                report(suburb, theme, f"round {street}: duplicate options")
            if namesake in options and sum(1 for o in options if o == namesake) > 1:
                report(suburb, theme, f"round {street}: namesake appears twice in options")
            if len(explainer) < 50:
                report(suburb, theme, f"round {street}: explainer too short ({len(explainer)} chars)")
            if not tidbit:
                report(suburb, theme, f"round {street}: no tidbit")
            # leak checks: clue must not contain street name or suburb name
            if street and re.search(rf"\b{re.escape(street.split()[0])}\b", clue, re.I) and \
               len(street.split()[0]) > 3:
                report(suburb, theme, f"round {street}: street name leaked in clue")
            if re.search(rf"\b{re.escape(suburb)}\b", clue, re.I) and len(suburb) > 3:
                report(suburb, theme, f"round {street}: suburb name leaked in clue")
            if re.search(rf"\b{re.escape(suburb)}\b", explainer, re.I) and len(suburb) > 3:
                report(suburb, theme, f"round {street}: suburb name leaked in explainer")
            # distractor sanity: options should differ from the answer's phrasing
            for o in options:
                if o != namesake and o.lower() in namesake.lower():
                    report(suburb, theme, f"round {street}: option '{o}' is substring of namesake")

if issues == 0:
    print(f"CLEAN — no issues found ({len(seen_themes)} themes, all title-cased with icons)")
else:
    print(f"{issues} issue(s) found")
sys.exit(1 if issues else 0)
