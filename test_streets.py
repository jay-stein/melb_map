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

print(f"{'PASS' if failures == 0 else 'FAIL'} — {failures} failures")
sys.exit(1 if failures else 0)
