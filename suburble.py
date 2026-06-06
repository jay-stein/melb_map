"""Suburble — a daily "Worldle for Melbourne suburbs".

Guess the mystery suburb from its decontextualised map silhouette. Each guess
returns pure-geography feedback: distance (km), an 8-point direction arrow, and
a proximity %. Six guesses, a fresh puzzle each day, and a shareable emoji grid.

Self-contained game logic + Dash layout/callbacks. `app.py` calls `init(...)`
once (handing over the loaded geojson + centroids) then `register_callbacks(app)`,
and routes `/play` to `layout()`. This module never imports app (no cycle).
"""
from __future__ import annotations

import math
import random
from datetime import date

import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update

# Daily-puzzle anchors. EPOCH sets puzzle #1; SHUFFLE_SEED fixes the order so the
# target is deterministic per day and doesn't repeat until the list is exhausted.
EPOCH = date(2026, 1, 1)
SHUFFLE_SEED = 20260101
MAX_GUESSES = 6

# 8-point compass arrows (index 0 = N, clockwise). Emoji render in-browser + in
# the shared grid alike.
_ARROWS = ["⬆️", "↗️", "➡️", "↘️", "⬇️", "↙️", "⬅️", "↖️"]
_NEUTRAL = "🎯"

# Populated by init().
_SUBURBS: list[str] = []          # display order from app
_CENTROIDS: dict[str, tuple[float, float]] = {}   # {suburb: (lat, lon)}
_RINGS: dict[str, list[list[float]]] = {}         # {suburb: [[lon, lat], ...]}
_ORDER: list[str] = []            # shuffled puzzle order
_MAX_DIST: float = 1.0            # max pairwise centroid distance (km)


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #
def init(geojson: dict, suburb_names: list[str],
         centroids: dict[str, tuple[float, float]]) -> None:
    """Stash the data the game needs (called once from app.py)."""
    global _SUBURBS, _CENTROIDS, _RINGS, _ORDER, _MAX_DIST
    _CENTROIDS = centroids
    _RINGS = {
        f["properties"]["suburb"]: f["geometry"]["coordinates"][0]
        for f in geojson["features"]
    }
    # Only play suburbs we have both a centroid and geometry for.
    _SUBURBS = sorted(s for s in suburb_names if s in _CENTROIDS and s in _RINGS)
    _ORDER = list(_SUBURBS)
    random.Random(SHUFFLE_SEED).shuffle(_ORDER)
    _MAX_DIST = _max_pairwise_distance() or 1.0


def _max_pairwise_distance() -> float:
    pts = list(_CENTROIDS.values())
    mx = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            mx = max(mx, haversine(pts[i], pts[j]))
    return mx


# --------------------------------------------------------------------------- #
# pure geography
# --------------------------------------------------------------------------- #
def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def bearing(frm: tuple[float, float], to: tuple[float, float]) -> str:
    """8-point compass arrow pointing from `frm` toward `to`."""
    lat1, lat2 = math.radians(frm[0]), math.radians(to[0])
    dlon = math.radians(to[1] - frm[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    deg = (math.degrees(math.atan2(x, y)) + 360) % 360
    return _ARROWS[round(deg / 45) % 8]


def proximity_pct(km: float) -> int:
    """0–100; 100 = exact. Scaled to the city's own diameter, not the globe."""
    return max(0, round(100 * (_MAX_DIST - km) / _MAX_DIST))


# --------------------------------------------------------------------------- #
# daily target
# --------------------------------------------------------------------------- #
def daily_target(d: date | None = None) -> tuple[int, str]:
    """(puzzle_no, suburb) — stable for a given day, rotating without early repeats."""
    d = d or date.today()
    puzzle_no = (d - EPOCH).days
    return puzzle_no, _ORDER[puzzle_no % len(_ORDER)]


# --------------------------------------------------------------------------- #
# silhouette (name never reaches the browser — raw coords, no labels/hover)
# --------------------------------------------------------------------------- #
def silhouette_figure(suburb: str) -> go.Figure:
    ring = _RINGS[suburb]
    lat0 = _CENTROIDS[suburb][0]
    k = math.cos(math.radians(lat0))  # equirectangular x-scale so the shape isn't squashed
    xs = [pt[0] * k for pt in ring]
    ys = [pt[1] for pt in ring]
    fig = go.Figure(
        go.Scatter(
            x=xs, y=ys, fill="toself", mode="lines",
            line=dict(color="#37474F", width=1.5),
            fillcolor="#7E57C2", hoverinfo="skip",
        )
    )
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False, height=300,
        xaxis=dict(visible=False, scaleanchor="y", scaleratio=1),
        yaxis=dict(visible=False),
        dragmode=False,
    )
    return fig


# --------------------------------------------------------------------------- #
# share grid
# --------------------------------------------------------------------------- #
def _squares(prox: int) -> str:
    filled = max(0, min(5, round(prox / 20)))
    return "🟩" * filled + "⬛" * (5 - filled)


def emoji_grid(guesses: list[str], target: str, puzzle_no: int) -> str:
    solved = guesses and guesses[-1] == target
    score = f"{len(guesses)}/{MAX_GUESSES}" if solved else f"X/{MAX_GUESSES}"
    lines = [f"Suburble #{puzzle_no} {score}"]
    tc = _CENTROIDS[target]
    for g in guesses:
        if g == target:
            lines.append("🟩🟩🟩🟩🟩🎉")
            continue
        km = haversine(_CENTROIDS[g], tc)
        lines.append(_squares(proximity_pct(km)) + bearing(_CENTROIDS[g], tc))
    lines.append("melb-map · Suburble")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #
_CARD = {"background": "white", "border": "1px solid #E0E0E0", "borderRadius": "10px"}


def _guess_row(suburb: str, target: str) -> html.Div:
    tc, gc = _CENTROIDS[target], _CENTROIDS[suburb]
    if suburb == target:
        km, arrow, prox = 0.0, _NEUTRAL, 100
    else:
        km = haversine(gc, tc)
        arrow = bearing(gc, tc)
        prox = proximity_pct(km)
    win = suburb == target
    return html.Div(
        [
            html.Span(suburb, style={"flex": "1 1 auto", "fontWeight": 600,
                                     "color": "#2E7D32" if win else "#37474F"}),
            html.Span(f"{km:.1f} km", style={"flex": "0 0 70px", "textAlign": "right",
                                             "color": "#616161"}),
            html.Span(arrow, style={"flex": "0 0 34px", "textAlign": "center",
                                    "fontSize": "16px"}),
            html.Span(f"{prox}%", style={"flex": "0 0 46px", "textAlign": "right",
                                         "fontWeight": 600,
                                         "color": "#2E7D32" if prox >= 80 else "#9E9E9E"}),
        ],
        style={"display": "flex", "alignItems": "center", "gap": "8px",
               "padding": "9px 12px", "marginBottom": "6px",
               "background": "#E8F5E9" if win else "white",
               "border": "1px solid #E0E0E0", "borderRadius": "8px",
               "fontSize": "14px"},
    )


def layout() -> html.Div:
    puzzle_no, _ = daily_target()
    # The silhouette is rendered here for today's target; the answer is compared
    # server-side in the callback, never shipped to the client.
    _, target = daily_target()
    return html.Div(
        style={"maxWidth": "560px", "margin": "0 auto", "padding": "28px 20px",
               "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"},
        children=[
            dcc.Link("← Map", href="/", style={"color": "#7E57C2", "textDecoration": "none",
                                               "fontSize": "14px"}),
            html.H1("Suburble", style={"margin": "8px 0 2px", "fontSize": "30px"}),
            html.P(f"#{puzzle_no} · guess the mystery Melbourne suburb from its shape",
                   style={"color": "#757575", "marginTop": 0, "fontSize": "14px"}),
            dcc.Graph(
                figure=silhouette_figure(target),
                config={"displayModeBar": False, "staticPlot": True},
                style={"height": "300px", "margin": "8px 0"},
            ),
            html.Div(
                [
                    dcc.Dropdown(
                        id="suburble-guess", options=[{"label": s, "value": s} for s in _SUBURBS],
                        placeholder="Type a suburb…", style={"flex": "1 1 auto"},
                    ),
                    html.Button("Guess", id="suburble-submit", n_clicks=0,
                                style={"flex": "0 0 auto", "padding": "0 18px", "border": "none",
                                       "borderRadius": "8px", "background": "#7E57C2",
                                       "color": "white", "fontWeight": 600, "cursor": "pointer"}),
                ],
                style={"display": "flex", "gap": "8px", "alignItems": "stretch",
                       "margin": "6px 0 16px"},
            ),
            html.Div(id="suburble-rows"),
            html.Div(id="suburble-result", style={"marginTop": "12px"}),
            dcc.Store(id="suburble-guesses", data=[]),
            dcc.Store(id="suburble-grid", data=""),
        ],
    )


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #
def register_callbacks(app) -> None:
    @app.callback(
        Output("suburble-guesses", "data"),
        Output("suburble-rows", "children"),
        Output("suburble-result", "children"),
        Output("suburble-guess", "value"),
        Output("suburble-guess", "disabled"),
        Output("suburble-submit", "disabled"),
        Output("suburble-grid", "data"),
        Input("suburble-submit", "n_clicks"),
        State("suburble-guess", "value"),
        State("suburble-guesses", "data"),
        prevent_initial_call=True,
    )
    def _guess(_n, value, guesses):
        guesses = list(guesses or [])
        done_already = bool(guesses) and (
            guesses[-1] == daily_target()[1] or len(guesses) >= MAX_GUESSES
        )
        # Ignore empty picks, repeats, or input after the game is over.
        if not value or value in guesses or done_already:
            return (no_update,) * 7

        guesses.append(value)
        puzzle_no, target = daily_target()
        rows = [_guess_row(g, target) for g in guesses]
        solved = value == target
        out_of_guesses = len(guesses) >= MAX_GUESSES and not solved
        done = solved or out_of_guesses

        result = no_update
        grid = no_update
        if done:
            grid = emoji_grid(guesses, target, puzzle_no)
            if solved:
                msg = f"🎉 Solved in {len(guesses)}/{MAX_GUESSES}!"
            else:
                msg = f"Out of guesses — it was {target}."
            result = html.Div(
                [
                    html.Div(msg, style={"fontWeight": 700, "fontSize": "16px",
                                         "marginBottom": "8px"}),
                    html.Button("Share 📋", id="suburble-share", n_clicks=0,
                                style={"padding": "8px 16px", "border": "none",
                                       "borderRadius": "8px", "background": "#37474F",
                                       "color": "white", "fontWeight": 600, "cursor": "pointer"}),
                    html.Span(id="suburble-share-status",
                              style={"marginLeft": "10px", "color": "#2E7D32", "fontSize": "13px"}),
                    html.Pre(grid, style={"background": "#FAFAFA", "padding": "10px",
                                          "borderRadius": "8px", "marginTop": "10px",
                                          "fontSize": "13px", "lineHeight": 1.3,
                                          **_CARD}),
                ]
            )
        return guesses, rows, result, None, done, done, grid

    # Copy the emoji grid to the clipboard (clientside — no server round-trip).
    app.clientside_callback(
        """
        function(n, grid) {
            if (!n || !grid) { return ""; }
            navigator.clipboard.writeText(grid);
            return "Copied!";
        }
        """,
        Output("suburble-share-status", "children"),
        Input("suburble-share", "n_clicks"),
        State("suburble-grid", "data"),
        prevent_initial_call=True,
    )
