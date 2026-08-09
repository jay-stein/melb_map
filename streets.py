"""Streetwise — guess the Melbourne suburb from its themed streets.

Each game is ONE suburb (5 rounds). A round shows a clue about the person or
thing one of the suburb's streets is named after; the player picks the
namesake from 3 options with 2 attempts. A hint costs points but reveals a
tidbit. Solving a round flips the street card and shows a short explainer.
After 5 rounds the theme and the suburb are revealed, with the suburb's
mascot/vibe as payoff, plus a shareable emoji grid.

Scoring: first-try 100, second-try 50, hint halves the round's value,
exhausted 0. Corpus: data/street_themes.json (built by scrape.street_themes)
— a per-suburb list of puzzles (multi-theme suburbs have several; the game
picks one at random per page load).

Self-contained game logic + Dash layout/callbacks, mirroring suburble.py.
app.py calls init(...) once and routes /streets to layout(). This module
never imports app (no cycle).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from dash import Input, Output, State, ALL, dcc, html, no_update

ROOT = Path(__file__).resolve().parent

ROUNDS_PER_GAME = 5
ATTEMPTS = 2
POINTS_FIRST = 100
POINTS_SECOND = 50

# Emoji states per round for the share grid.
_SQUARES = {
    "first": "🟩",        # correct on attempt 1
    "second": "🟨",       # correct on attempt 2
    "hint_first": "🟦",   # hint used, then correct on attempt 1
    "hint_second": "🟦",  # hint used, then correct on attempt 2
    "fail": "⬛",         # both attempts wrong, answer revealed
}

# Visual icon per theme for the theme-select chiclets. Keys are the canonical
# theme labels enforced by the corpus build (see THEME_CANON in
# scrape.street_themes.py); the lookup falls back to a case-insensitive match
# so a stray casing can never lose an icon.
THEME_ICONS: dict[str, str] = {
    "Literary Poets": "📜",
    "Native Flora": "🌿",
    "British Towns & Rivers": "🏰",
    "Prime Ministers": "🏛️",
    "Astronomy & Space": "🪐",
    "Precious Gemstones": "💎",
    "Wars & Battles": "🎖️",
    "Arthurian Legend": "🐉",
    "Elite English Schools": "🎓",
    "Aviation & Aircraft": "✈️",
    "Viticulture & Wine": "🍷",
    "Camera & Photography": "📷",
    "Renaissance Artists & Writers": "🎨",
    "Golf Courses": "⛳",
}

# Populated by init().
_CORPUS: dict[str, dict] = {}       # {suburb: {"puzzles": [...]}}
_SUBURBS_DATA: dict[str, dict] = {}  # suburbs.json (mascot/vibe payoff)


def init(corpus: dict, suburbs_data: dict) -> None:
    """Stash the corpus (called once from app.py)."""
    global _CORPUS, _SUBURBS_DATA
    _CORPUS = corpus
    _SUBURBS_DATA = suburbs_data


def theme_icon(label: str) -> str:
    icon = THEME_ICONS.get(label)
    if icon is None:
        icon = THEME_ICONS.get({k.lower(): k for k in THEME_ICONS}.get(label.lower()))
    return icon or "🧩"


def available_themes() -> list[tuple[str, str, int]]:
    """[(theme_label, icon, puzzle_count)] sorted by count desc, for the
    theme-select chiclets. Computed live from the corpus."""
    counts: dict[str, int] = {}
    for entry in _CORPUS.values():
        for p in entry["puzzles"]:
            counts[p["theme"]] = counts.get(p["theme"], 0) + 1
    return [(t, theme_icon(t), n)
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# --------------------------------------------------------------------------- #
# pure game state
# --------------------------------------------------------------------------- #
def new_game(theme: str | None = None) -> dict:
    """Pick a random suburb + one of its puzzles (optionally filtered to a
    theme); shuffle option order."""
    options: list[tuple[str, dict]] = []
    for suburb, entry in sorted(_CORPUS.items()):
        for p in entry["puzzles"]:
            if theme is None or p["theme"] == theme:
                options.append((suburb, p))
    if not options:
        raise ValueError(f"no puzzles for theme: {theme}")
    suburb, puzzle = random.choice(options)
    rounds = []
    for r in puzzle["rounds"]:
        r = dict(r)
        r["options"] = list(r["options"])
        random.shuffle(r["options"])
        rounds.append(r)
    return {
        "suburb": suburb,
        "theme": puzzle["theme"],
        "background": puzzle.get("background", ""),
        "reveal": puzzle.get("reveal", f"It was {suburb}."),
        "rounds": rounds,
        "idx": 0,
        "attempts": 0,
        "hintUsed": False,
        "points": 0,
        "results": [],  # per-round: {"state": str, "points": int}
        "done": False,
    }


def round_value(state: dict, hint_used: bool, attempt: int) -> int:
    """Points for a correct answer this round (hint halves the value)."""
    v = POINTS_FIRST if attempt == 1 else POINTS_SECOND
    return v // 2 if hint_used else v


def emoji_grid(state: dict) -> str:
    solved_total = state["points"]
    squares = "".join(_SQUARES[res["state"]] for res in state["results"])
    return "\n".join([
        f"Streetwise {solved_total}/{ROUNDS_PER_GAME * POINTS_FIRST}",
        squares,
        state["reveal"],
        "melb-map · Streetwise",
    ])


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
_CARD = {"background": "white", "border": "1px solid #E0E0E0", "borderRadius": "10px"}


def _payoff(suburb: str) -> list:
    """Mascot + vibe from suburbs.json, shown at the reveal."""
    d = _SUBURBS_DATA.get(suburb) or {}
    mascot = d.get("mascot") or {}
    bits: list = []
    if d.get("vibe"):
        bits.append(html.P(d["vibe"], style={"fontSize": "14px", "lineHeight": 1.5}))
    if mascot.get("name"):
        block = [html.Div(mascot["name"], style={"fontWeight": 600, "fontSize": "15px"})]
        if mascot.get("tagline"):
            block.append(html.Div(
                f"“{mascot['tagline']}”",
                style={"fontStyle": "italic", "color": "#616161", "fontSize": "13px"},
            ))
        if mascot.get("description"):
            block.append(html.P(mascot["description"],
                                style={"fontSize": "13px", "color": "#424242",
                                       "lineHeight": 1.5, "marginTop": "6px"}))
        bits.append(html.Div(block, style={"padding": "10px", "background": "#FAFAFA",
                                           "borderRadius": "8px", **{k: v for k, v in _CARD.items() if k == "border"}}))
    return bits


def _bg_div(state: dict) -> html.Div:
    """The theme opener shown at the top of the play area."""
    return html.Div(state["background"],
                    style={"padding": "12px 16px", "background": "#E0F2F1",
                           "borderRadius": "8px", "color": "#00695C",
                           "fontSize": "14px", "lineHeight": 1.5, "margin": "10px 0 16px",
                           "border": "1px solid #B2DFDB"})


def _celebration_copy(correct: int) -> tuple[str, str]:
    """(headline, sub) for the finale — a fuss when the player did well."""
    if correct == 5:
        return "Perfect! 🎉", "5/5 streets — absolutely flawless."
    if correct >= 4:
        return "Congrats! 🎉", f"You got {correct}/5 streets."
    if correct >= 3:
        return "Nice work!", f"You got {correct}/5 streets."
    if correct == 2:
        return "Not bad!", f"You got {correct}/5 streets."
    if correct == 1:
        return "Tough streets!", f"You got {correct}/5 streets."
    return "Brutal!", "The streets won this round."


_CONFETTI_COLORS = ["#26A69A", "#7E57C2", "#EC407A", "#FFA726", "#66BB6A",
                    "#D4AF37", "#42A5F5"]


def _confetti_pieces(n: int = 42) -> list:
    rng = random.Random(20260809)
    return [
        html.Div(className="sw-confetti", style={
            "left": f"{rng.uniform(0, 100):.1f}%",
            "width": f"{rng.uniform(6, 12):.1f}px",
            "height": f"{rng.uniform(10, 18):.1f}px",
            "background": rng.choice(_CONFETTI_COLORS),
            "animationDuration": f"{rng.uniform(2.4, 4.4):.2f}s",
            "animationDelay": f"{rng.uniform(0, 2.0):.2f}s",
        })
        for _ in range(n)
    ]


def _balloons(n: int = 8) -> list:
    rng = random.Random(20260810)
    return [
        html.Div(className="sw-balloon", style={
            "left": f"{rng.uniform(2, 90):.1f}%",
            "animationDuration": f"{rng.uniform(7, 11):.1f}s",
            "animationDelay": f"{rng.uniform(0, 3.0):.1f}s",
        }, children=[
            html.Div(className="sw-balloon-body", style={
                "width": "44px", "height": "54px",
                "background": f"radial-gradient(circle at 35% 30%, {rng.choice(_CONFETTI_COLORS)}, {rng.choice(_CONFETTI_COLORS)})",
            }),
            html.Div(style={"width": "2px", "height": "34px",
                            "background": "#B0BEC5", "margin": "0 auto"}),
        ])
        for _ in range(n)
    ]


def _round_ui(state: dict, street_cards: list, feedback=None, solved=False):
    """The play area for the current round (or the finale once done)."""
    if state["done"]:
        grid = emoji_grid(state)
        correct = sum(1 for res in state["results"] if res["state"] != "fail")
        headline, sub = _celebration_copy(correct)
        actions = html.Div([
            html.Button("Play again", id="streets-play-again", n_clicks=0,
                        style={"padding": "8px 16px", "border": "none",
                               "borderRadius": "8px", "background": "#26A69A",
                               "color": "white", "fontWeight": 600,
                               "cursor": "pointer", "marginRight": "8px"}),
            html.Button("More themes", id="streets-themes", n_clicks=0,
                        style={"padding": "8px 16px", "border": "1px solid #B2DFDB",
                               "borderRadius": "8px", "background": "#E0F2F1",
                               "color": "#00695C", "fontWeight": 600,
                               "cursor": "pointer", "marginRight": "8px"}),
            html.Button("Share 📋", id="streets-share", n_clicks=0,
                        style={"padding": "8px 16px", "border": "none",
                               "borderRadius": "8px", "background": "#37474F",
                               "color": "white", "fontWeight": 600,
                               "cursor": "pointer"}),
            html.Span(id="streets-share-status",
                      style={"marginLeft": "10px", "color": "#2E7D32",
                             "fontSize": "13px"}),
        ])
        # Celebration first (trophy + headline + confetti + balloons); the
        # suburb name sits in a card that flips open ~3s later (pure CSS).
        # Keyframes live in assets/streetwise.css (Dash auto-serves assets/).
        return [
            *_confetti_pieces(),
            *_balloons(),
            html.Div(
                style={"position": "relative", "zIndex": 1, "textAlign": "center"},
                children=[
                    html.Div("🏆", className="sw-trophy"),
                    html.Div(headline,
                             style={"fontWeight": 700, "fontSize": "26px",
                                    "color": "#00695C", "margin": "10px 0 2px"}),
                    html.Div(sub, style={"color": "#616161", "fontSize": "15px",
                                         "marginBottom": "6px"}),
                    html.Div(
                        className="sw-reveal-card",
                        style={"textAlign": "left"},
                        children=[
                            html.H2(f"It was {state['suburb']}!",
                                    style={"color": "#2E7D32", "textAlign": "center"}),
                            html.Div(state["reveal"],
                                     style={"fontSize": "16px", "fontStyle": "italic",
                                            "color": "#37474F", "margin": "6px 0 14px",
                                            "textAlign": "center"}),
                            *_payoff(state["suburb"]),
                            html.Div("streets in the mystery suburb:",
                                     style={"fontSize": "12px", "fontWeight": 600,
                                            "textTransform": "uppercase",
                                            "letterSpacing": ".4px",
                                            "color": "#90A4AE", "margin": "14px 0 6px"}),
                            *street_cards,
                            html.Div(f"Score: {state['points']}/{ROUNDS_PER_GAME * POINTS_FIRST}",
                                     style={"fontWeight": 700, "margin": "14px 0 8px"}),
                            html.Pre(grid, style={"background": "#FAFAFA",
                                                  "padding": "10px", "borderRadius": "8px",
                                                  "fontSize": "13px", "lineHeight": 1.3,
                                                  **_CARD}),
                            actions,
                        ],
                    ),
                ],
            ),
        ]

    r = state["rounds"][state["idx"]]
    round_no = state["idx"] + 1
    attempts_left = ATTEMPTS - state["attempts"]
    options_locked = solved or attempts_left <= 0

    hint_btn = html.Div(
        [html.Button(
            "💡 Hint — halves this round's points",
            id="streets-hint", n_clicks=0,
            style={"padding": "8px 14px", "border": "1px solid #B39DDB",
                   "borderRadius": "8px", "background": "#F3E5F5",
                   "color": "#5E35B1", "fontWeight": 600, "cursor": "pointer",
                   "fontSize": "13px"}),
         html.Span(" · " + ("used — round worth "
                            f"{round_value(state, True, min(state['attempts'] + 1, ATTEMPTS))}"
                            if state["hintUsed"] else
                            f"round worth {round_value(state, False, min(state['attempts'] + 1, ATTEMPTS))}"),
                   style={"fontSize": "12px", "color": "#9E9E9E"})],
        style={"marginTop": "10px"},
    )

    return [
        _bg_div(state),
        html.Div(
            f"Round {round_no}/{ROUNDS_PER_GAME}",
            style={"fontSize": "12px", "fontWeight": 600, "color": "#90A4AE",
                   "textTransform": "uppercase", "letterSpacing": ".4px",
                   "marginBottom": "4px"},
        ),
        html.Div(
            "One of the mystery suburb's streets is named after…",
            style={"color": "#616161", "fontSize": "13px", "marginBottom": "10px"},
        ),
        html.Div(r["clue"],
                 style={"fontSize": "17px", "lineHeight": 1.5, "color": "#212121",
                        "padding": "14px 18px", "background": "white",
                        "borderRadius": "8px", "border": "1px solid #E0E0E0",
                        "marginBottom": "14px"}),
        html.Div([
            html.Button(opt, id=f"streets-opt-{i}", n_clicks=0,
                        disabled=options_locked,
                        style={"display": "block", "width": "100%", "marginBottom": "8px",
                               "padding": "12px 16px", "border": "1px solid #E0E0E0",
                               "borderRadius": "8px", "background": "white",
                               "fontSize": "15px", "cursor": "pointer",
                               "textAlign": "left",
                               **({"opacity": "0.55", "cursor": "default"}
                                  if options_locked else {})})
            for i, opt in enumerate(r["options"])
        ]),
        hint_btn if not state["hintUsed"] and not options_locked else
        (html.Div(
            "💡 " + r["tidbit"],
            style={"padding": "10px 14px", "background": "#FFF8E1",
                   "border": "1px solid #FFE082", "borderRadius": "8px",
                   "fontSize": "14px", "fontStyle": "italic", "color": "#795548",
                   "marginTop": "10px"})
         if state["hintUsed"] else html.Div()),
        feedback if feedback is not None else html.Div(),
        *([html.Div(
            [
                html.Div(f"📍 {r['street']}", style={"fontWeight": 700,
                                                     "fontSize": "16px",
                                                     "color": "#00695C"}),
                html.Div(f"named after {r['namesake']}",
                         style={"fontSize": "13px", "color": "#616161",
                                "margin": "2px 0 8px"}),
                html.P(r["explainer"], style={"fontSize": "13.5px",
                                              "lineHeight": 1.5,
                                              "color": "#37474F", "margin": 0}),
            ],
            style={"padding": "12px 14px", "background": "#F1F8E9",
                   "border": "1px solid #DCEDC8", "borderRadius": "8px",
                   "marginBottom": "10px"})
           ] if solved else []),
        *([html.Button("🎉 Finish" if state["idx"] == ROUNDS_PER_GAME - 1
                       else "Next street →",
                       id="streets-next", n_clicks=0,
                       style={"padding": "10px 20px", "border": "none",
                              "borderRadius": "8px", "background": "#26A69A",
                              "color": "white", "fontWeight": 600,
                              "cursor": "pointer", "marginTop": "12px",
                              "display": "block"})] if solved else []),
    ]


def _theme_slug(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")


def _select_ui() -> list:
    """The theme-select screen: a big Random chiclet + one chiclet per
    theme (icon + label + puzzle count)."""
    themes = available_themes()
    total = sum(n for _, _, n in themes)
    chiclet = {
        "display": "flex", "alignItems": "center", "gap": "10px",
        "border": "1px solid #E0E0E0", "borderRadius": "12px",
        "background": "white", "fontFamily": "inherit", "cursor": "pointer",
        "width": "100%", "textAlign": "left", "padding": "12px 16px",
        "marginBottom": "8px", "fontSize": "15px",
    }
    return [
        html.P("Pick a theme, or roll the dice:",
               style={"color": "#757575", "fontSize": "14px", "margin": "14px 0 10px"}),
        html.Button(
            [
                html.Span("🎲", style={"fontSize": "26px"}),
                html.Span("Random", style={"fontWeight": 700, "fontSize": "17px"}),
                html.Span(f"any of {total} puzzles", style={"color": "#9E9E9E",
                                                            "fontSize": "13px"}),
            ],
            id={"type": "streets-chiclet", "index": "random"}, n_clicks=0,
            style={**chiclet, "padding": "18px 20px", "background": "#E0F2F1",
                   "border": "2px solid #26A69A"},
        ),
        html.Div("or pick a theme:",
                 style={"fontSize": "12px", "fontWeight": 600, "color": "#90A4AE",
                        "textTransform": "uppercase", "letterSpacing": ".4px",
                        "margin": "18px 0 8px"}),
        *[
            html.Button(
                [
                    html.Span(icon, style={"fontSize": "22px", "flex": "0 0 auto"}),
                    html.Span(label, style={"flex": "1 1 auto", "fontWeight": 600}),
                    html.Span(f"{n}", style={"color": "#9E9E9E", "fontSize": "13px",
                                             "flex": "0 0 auto"}),
                ],
                id={"type": "streets-chiclet", "index": _theme_slug(label)},
                n_clicks=0, style=chiclet,
            )
            for label, icon, n in themes
        ],
    ]


def layout() -> html.Div:
    return html.Div(
        style={"maxWidth": "560px", "margin": "0 auto", "padding": "28px 20px",
               "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"},
        children=[
            dcc.Link("← Map", href="/", style={"color": "#26A69A", "textDecoration": "none",
                                               "fontSize": "14px", "marginRight": "14px"}),
            dcc.Link("Detective", href="/play", style={"color": "#9E9E9E",
                                                       "textDecoration": "none", "fontSize": "14px"}),
            html.H1("Streetwise", style={"margin": "8px 0 2px", "fontSize": "30px"}),
            html.P("guess the suburb from its themed streets",
                   style={"color": "#757575", "marginTop": 0, "fontSize": "14px"}),
            html.Div(id="streets-play", children=_select_ui()),
            dcc.Store(id="streets-state", data={"screen": "select"}),
        ],
    )


def apply_action(state: dict, trigger: str | None):
    """Pure state transition for one button click.

    trigger: 'random', 'theme:<label>', 'themes', 'opt-0'..'opt-2', 'hint',
    'next', 'play-again'. Returns (new_state, feedback, solved) — feedback is
    a Dash component to show under the options; solved marks the current
    round as answered. Select triggers return a {'screen': 'select'} state.
    """
    if trigger == "random":
        return new_game(), None, False
    if trigger == "themes":
        return {"screen": "select"}, None, False
    if trigger and trigger.startswith("theme:"):
        return new_game(trigger.split(":", 1)[1]), None, False
    if trigger == "play-again":
        return new_game(state["theme"]), None, False
    if state["done"]:
        return state, None, False

    state = dict(state)
    state["rounds"] = [dict(r) for r in state["rounds"]]
    r = state["rounds"][state["idx"]]
    feedback = None
    solved = False

    if trigger == "next":
        state["idx"] += 1
        state["hintUsed"] = False
        state["attempts"] = 0
        if state["idx"] >= ROUNDS_PER_GAME:
            state["done"] = True
        return state, None, False

    if trigger == "hint":
        if not state["hintUsed"] and state["attempts"] < ATTEMPTS:
            state["hintUsed"] = True
        return state, None, False

    if trigger and trigger.startswith("opt-"):
        if state["attempts"] >= ATTEMPTS:
            return state, None, False
        choice = int(trigger.rsplit("-", 1)[1])
        picked = r["options"][choice]
        state["attempts"] += 1
        if picked == r["namesake"]:
            pts = round_value(state, state["hintUsed"], state["attempts"])
            state["points"] += pts
            tag = f"{'hint_' if state['hintUsed'] else ''}" \
                  f"{'first' if state['attempts'] == 1 else 'second'}"
            state["results"].append({"state": tag, "points": pts})
            feedback = html.Div(
                f"✓ {r['namesake']} — +{pts}",
                style={"fontWeight": 700, "color": "#2E7D32", "fontSize": "15px"},
            )
            solved = True
        elif state["attempts"] >= ATTEMPTS:
            state["results"].append({"state": "fail", "points": 0})
            feedback = html.Div(
                f"✗ Out of attempts — it was {r['namesake']}.",
                style={"fontWeight": 600, "color": "#C62828", "fontSize": "15px"},
            )
            solved = True
        else:
            feedback = html.Div(
                f"✗ Not that one — {ATTEMPTS - state['attempts']} attempt(s) left.",
                style={"fontWeight": 600, "color": "#E65100", "fontSize": "14px"},
            )
        if solved:
            state["attempts"] = 0
            state["hintUsed"] = False
    return state, feedback, solved


def _solved_cards(state: dict) -> list:
    """Compact street cards for all COMPLETED rounds (before state['idx'])."""
    cards = []
    for i in range(state["idx"]):
        prev = state["rounds"][i]
        cards.append(html.Div(
            [html.Div(f"📍 {prev['street']}",
                      style={"fontWeight": 700, "fontSize": "15px", "color": "#00695C"}),
             html.Div(f"named after {prev['namesake']}",
                      style={"fontSize": "12.5px", "color": "#616161"})],
            style={"padding": "9px 12px", "background": "#F1F8E9",
                   "border": "1px solid #DCEDC8", "borderRadius": "8px",
                   "marginBottom": "6px", "fontSize": "13px"},
        ))
    return cards


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #
def register_callbacks(app) -> None:
    @app.callback(
        Output("streets-state", "data"),
        Output("streets-play", "children"),
        Input({"type": "streets-chiclet", "index": ALL}, "n_clicks"),
        Input("streets-opt-0", "n_clicks"),
        Input("streets-opt-1", "n_clicks"),
        Input("streets-opt-2", "n_clicks"),
        Input("streets-hint", "n_clicks"),
        Input("streets-next", "n_clicks"),
        Input("streets-play-again", "n_clicks"),
        Input("streets-themes", "n_clicks"),
        State("streets-state", "data"),
        prevent_initial_call=True,
    )
    def _act(_themes, _0, _1, _2, _hint, _next, _again, _more, state):
        from dash import ctx
        trigger = ctx.triggered_id

        if isinstance(trigger, dict) and trigger.get("type") == "streets-chiclet":
            index = trigger["index"]
            mapped = "random" if index == "random" else (
                "theme:" + {_theme_slug(label): label
                            for label, _, _ in available_themes()}[index]
            )
        else:
            mapped = {
                "streets-opt-0": "opt-0", "streets-opt-1": "opt-1",
                "streets-opt-2": "opt-2", "streets-hint": "hint",
                "streets-next": "next", "streets-play-again": "play-again",
                "streets-themes": "themes",
            }.get(trigger)
        if mapped is None:
            return no_update, no_update

        new_state, feedback, solved = apply_action(state, mapped)
        if new_state is state:
            return no_update, no_update
        if new_state.get("screen") == "select":
            return new_state, _select_ui()
        if new_state["done"]:
            return new_state, _round_ui(new_state, _solved_cards(new_state))
        return new_state, _round_ui(new_state, _solved_cards(new_state),
                                    feedback, solved)

    # Copy the emoji grid to the clipboard (clientside — no server round-trip).
    app.clientside_callback(
        """
        function(n, state) {
            if (!n || !state) { return ""; }
            const squares = {first:"🟩", second:"🟨", hint_first:"🟦",
                             hint_second:"🟦", fail:"⬛"};
            const row = state.results.map(r => squares[r.state]).join("");
            const grid = ["Streetwise " + state.points + "/500", row,
                          state.reveal, "melb-map · Streetwise"].join("\\n");
            navigator.clipboard.writeText(grid);
            return "Copied!";
        }
        """,
        Output("streets-share-status", "children"),
        Input("streets-share", "n_clicks"),
        State("streets-state", "data"),
        prevent_initial_call=True,
    )
