"""Simulated full-game test for streets.py — plays games with wrong answers,
hints, exhausted attempts and clean sweeps, mirroring the real UI flow
(solve a round, press Next, repeat)."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streets

corpus = json.loads(Path("data/street_themes.json").read_text(encoding="utf-8"))
suburbs = json.loads(Path("data/suburbs.json").read_text(encoding="utf-8"))
streets.init(corpus, suburbs)

random.seed(42)
failures = 0


def check(cond, msg):
    global failures
    if not cond:
        failures += 1
        print(f"  FAIL: {msg}")


def opt_of(st, name):
    r = st["rounds"][st["idx"]]
    return "opt-" + str(r["options"].index(name))


def wrong_of(st):
    r = st["rounds"][st["idx"]]
    return [o for o in r["options"] if o != r["namesake"]][0]


def solve_round(st, mode):
    """mode: 'clean' | 'second_try' | 'hint_clean' | 'hint_second' | 'fail'"""
    if mode in ("hint_clean", "hint_second", "hint_fail"):
        st, _, _ = streets.apply_action(st, "hint")
    if mode == "clean":
        st, fb, solved = streets.apply_action(st, opt_of(st, st["rounds"][st["idx"]]["namesake"]))
    elif mode == "hint_clean":
        st, fb, solved = streets.apply_action(st, opt_of(st, st["rounds"][st["idx"]]["namesake"]))
    else:
        st, _, _ = streets.apply_action(st, opt_of(st, wrong_of(st)))
        if mode == "fail":
            st, fb, solved = streets.apply_action(st, opt_of(st, wrong_of(st)))
        else:
            st, fb, solved = streets.apply_action(st, opt_of(st, st["rounds"][st["idx"]]["namesake"]))
    return st, fb, solved


for game_no in range(6):
    st = streets.new_game()
    check(0 <= st["idx"] < 5 and not st["done"], "fresh state")
    check(len(st["rounds"]) == 5, "5 rounds")
    check(all(len(r["options"]) == 3 for r in st["rounds"]), "3 options per round")

    st, fb, solved = solve_round(st, "fail")
    check(solved and st["results"][0]["state"] == "fail" and st["points"] == 0,
          "exhausted round scores 0")
    check(fb is not None, "fail feedback shown")
    ui = streets._round_ui(st, [], fb, solved)
    check("disabled" in str(ui), "options locked after solve")
    st, _, _ = streets.apply_action(st, "next")
    check(st["idx"] == 1 and not st["done"], "next advances round")

    st, _, solved = solve_round(st, "hint_clean")
    check(solved and st["results"][1]["state"] == "hint_first" and st["points"] == 50,
          "hint + first try = 50")
    st, _, _ = streets.apply_action(st, "next")

    st, _, solved = solve_round(st, "clean")
    check(solved and st["results"][2]["state"] == "first" and st["points"] == 150,
          "clean first try = 100")
    st, _, _ = streets.apply_action(st, "next")

    st, _, solved = solve_round(st, "second_try")
    check(solved and st["results"][3]["state"] == "second" and st["points"] == 200,
          "second try = 50")
    st, _, _ = streets.apply_action(st, "next")

    st, _, solved = solve_round(st, "hint_second")
    check(solved and st["results"][4]["state"] == "hint_second" and st["points"] == 225,
          "hint + second try = 25")
    st, _, _ = streets.apply_action(st, "next")
    check(st["done"], "game done after round 5 next")

    grid = streets.emoji_grid(st)
    check("Streetwise 225/500" in grid and "melb-map" in grid, "share grid header/footer")
    check(grid.splitlines()[1] == "⬛🟦🟩🟨🟦", f"grid row: {grid.splitlines()[1]}")

    st2, _, _ = streets.apply_action(st, "play-again")
    check(not st2["done"] and st2["points"] == 0 and len(st2["results"]) == 0,
          "play-again resets")

    # UI renders without error at every stage
    streets._round_ui(st, streets._solved_cards(st))
    streets._round_ui(st2, [])
    streets._round_ui(st, streets._solved_cards(st), fb, True)

# clean-sweep game: all first-try, no hints -> 500
st = streets.new_game()
for _ in range(5):
    st, _, _ = solve_round(st, "clean")
    if st["idx"] < 4:
        st, _, _ = streets.apply_action(st, "next")
check(st["points"] == 500 and st["results"][0]["state"] == "first", "clean sweep = 500")

# everything-fail game -> 0, all fails
st = streets.new_game()
for _ in range(5):
    st, _, _ = solve_round(st, "fail")
    st, _, _ = streets.apply_action(st, "next")
check(st["done"] and st["points"] == 0 and all(r["state"] == "fail" for r in st["results"]),
      "all-fail game = 0")

# --- theme select ---------------------------------------------------------- #
themes = streets.available_themes()
check(len(themes) >= 5, f"at least 5 themes listed ({len(themes)})")
check(all(n >= 1 for _, _, n in themes), "every theme has >= 1 puzzle")
check(all(len(i) > 0 for _, i, _ in themes), "every theme has an icon")

# theme-filtered games only produce puzzles of that theme
for label, _, _ in random.sample(themes, 3):
    for _ in range(3):
        g = streets.new_game(label)
        check(g["theme"] == label, f"new_game('{label}') returns theme {g['theme']}")

# random trigger from the select screen
sel = {"screen": "select"}
g, _, _ = streets.apply_action(sel, "random")
check("screen" not in g and len(g["rounds"]) == 5, "random trigger starts a game")

# theme trigger
g, _, _ = streets.apply_action(sel, "theme:Native Flora")
check(g["theme"] == "Native Flora", "theme trigger filters correctly")

# back to select
back, _, _ = streets.apply_action(g, "themes")
check(back == {"screen": "select"}, "themes trigger returns to select")

# play-again keeps the theme
g2, _, _ = streets.apply_action(g, "play-again")
check(g2["theme"] == "Native Flora", "play-again keeps the chosen theme")

# select UI renders with the right chiclet ids
ui = streets._select_ui()
ui_text = str(ui)
check("'type': 'streets-chiclet'" in ui_text and "'index': 'random'" in ui_text,
      "select UI has random chiclet")
check("'index': 'native_flora'" in ui_text, "select UI has theme chiclet slugs")

# --- celebration finale ---------------------------------------------------- #
check(streets._celebration_copy(5) == ("Perfect! 🎉", "5/5 streets — absolutely flawless."),
      "5/5 copy is the big fuss")
check(streets._celebration_copy(3)[0] == "Nice work!", "3/5 copy")
check(streets._celebration_copy(0)[0] == "Brutal!", "0/5 copy")
check(len(streets._confetti_pieces()) == 42, "42 confetti pieces")
check(len(streets._balloons()) == 8, "8 balloons")
g_check = streets.new_game()
check(len(g_check.get("all_streets", [])) >= 5, "game state carries all_streets")
finale_ui = streets._round_ui(st, streets._solved_cards(st))
finale_text = str(finale_ui)
check("sw-trophy" in finale_text, "trophy in finale")
check("sw-confetti" in finale_text, "confetti in finale")
check("sw-balloon" in finale_text, "balloons in finale")
check("sw-reveal-card" in finale_text, "reveal card in finale")
check("all_streets" in finale_text or "themed streets" in finale_text,
      "finale shows the full themed-street list")

print(f"{'PASS' if failures == 0 else 'FAIL'} — {failures} failures")
sys.exit(1 if failures else 0)
