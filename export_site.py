"""Export the Dash app as a fully static site (GitHub Pages) into docs/.

The app renders everything client-side from cached JSON, so we ship the exact
same figure and data as static files — no server required. The map figure is
serialised from the real build_figure() so the static map is pixel-identical
to the Dash app's. Suburble's puzzle state (order, centroids, max distance) is
computed with the same Python code path, so the static game has the identical
daily targets to the app.

Hand-maintained static files (index.html, play.html, app.js, suburble.js,
style.css) live in docs/ and are NOT regenerated here — this script only
refreshes the data + assets payload beneath them.

Run:
    uv run python export_site.py
"""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DATA_OUT = DOCS / "data"
ASSETS_OUT = DOCS / "assets"

import app as dash_app  # noqa: E402  (imports load data + build figure)
import suburble  # noqa: E402  (state populated by app's suburble.init())


# Melbourne CBD (Flinders St) — reference point for direction/distance.
_MCBD = (-37.8136, 144.9631)  # (lat, lon)
_CENTRAL_RADIUS_DEG = 0.055   # ~6 km


def _classify_region(lat: float, lon: float) -> str:
    """Classify a suburb as central, north, south, east, or west."""
    dlat = (lat - _MCBD[0]) * 111_320
    dlon = (lon - _MCBD[1]) * 111_320 * math.cos(math.radians(_MCBD[0]))
    dist = math.hypot(dlat, dlon)
    if dist < 6_000:
        return "central"
    bearing = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360
    if 45 <= bearing < 135:
        return "east"
    if 135 <= bearing < 225:
        return "south"
    if 225 <= bearing < 315:
        return "west"
    return "north"


def _build_trivia_state(centroids: dict[str, list[float]]) -> dict:
    """Classify every suburb into a region and return per-region lists."""
    by_region: dict[str, list[str]] = {
        "central": [], "north": [], "south": [], "east": [], "west": [],
    }
    for name, xy in centroids.items():
        region = _classify_region(xy[0], xy[1])
        by_region[region].append(name)
    for r in by_region:
        by_region[r].sort()
    return {r: names for r, names in by_region.items() if names}


def main() -> int:
    DATA_OUT.mkdir(parents=True, exist_ok=True)

    # --- map figure (pixel-identical to the Dash app) ---------------------- #
    figure_json = dash_app.fig.to_json()
    (DATA_OUT / "figure.json").write_text(figure_json, encoding="utf-8")
    print(f"[export] data/figure.json  {len(figure_json) / 1e6:.2f} MB")

    # --- Suburble game state (same code path as the app) ------------------- #
    state = {
        "epoch": suburble.EPOCH.isoformat(),
        "maxGuesses": suburble.MAX_GUESSES,
        "order": suburble._ORDER,
        "centroids": {name: list(xy) for name, xy in suburble._CENTROIDS.items()},
        "regions": _build_trivia_state(
            {name: list(xy) for name, xy in suburble._CENTROIDS.items()}
        ),
        "maxDist": suburble._MAX_DIST,
    }
    game_json = json.dumps(state, ensure_ascii=False)
    (DATA_OUT / "game-state.json").write_text(game_json, encoding="utf-8")
    print(f"[export] data/game-state.json  {len(game_json) / 1e6:.2f} MB "
          f"({len(state['order'])} suburbs in puzzle order)")

    # --- data payloads ------------------------------------------------------ #
    boundaries_json = json.dumps(dash_app.geojson, ensure_ascii=False)
    (DATA_OUT / "boundaries.geojson").write_text(boundaries_json, encoding="utf-8")
    print(f"[export] data/boundaries.geojson  {len(boundaries_json) / 1e6:.2f} MB")

    suburbs_src = ROOT / "data" / "suburbs.json"
    suburbs_json = suburbs_src.read_text(encoding="utf-8")
    (DATA_OUT / "suburbs.json").write_text(suburbs_json, encoding="utf-8")
    print(f"[export] data/suburbs.json  {len(suburbs_json) / 1e6:.2f} MB")

    # --- mascot manifest: {suburb: filename} so the panel can show images --- #
    mascots: dict[str, str] = {}
    for f in sorted((ROOT / "assets" / "mascots").glob("*")):
        mascots[f.stem.replace("_", " ")] = f.name
    (DATA_OUT / "mascots.json").write_text(
        json.dumps(mascots, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[export] data/mascots.json  ({len(mascots)} images)")

    # --- assets (flags + mascots + reddit logo) ----------------------------- #
    for subdir in ("flags", "mascots"):
        src_dir = ROOT / "assets" / subdir
        dst_dir = ASSETS_OUT / subdir
        dst_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src_dir.glob("*"):
            shutil.copy2(f, dst_dir / f.name)
            n += 1
        print(f"[export] assets/{subdir}/  {n} files")
    shutil.copy2(ROOT / "assets" / "reddit_logo.svg", ASSETS_OUT / "reddit_logo.svg")

    print("[export] done — serve docs/ (e.g. `uv run python -m http.server 8051 -d docs`)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
