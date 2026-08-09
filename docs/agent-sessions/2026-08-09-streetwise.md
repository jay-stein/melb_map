# Agent Session: Streetwise game build (2026-08-09)

## Goal

Build "Streetwise" — a street-theme suburb guessing game — from the research
conducted earlier today (`docs/agent-sessions/2026-08-09-street-theme-game-research.md`
is the research doc under `.copilot-tracking/research/`). Full pipeline
(Option B): BBBike OSM pbf → theme discovery (keyword + DeepSeek) → clue
generation (DeepSeek) → Dash game + static port.

## Files changed

| File | Change |
| --- | --- |
| `scrape/street_themes.py` | NEW — 5-stage corpus pipeline (fetch/match/discover/clues/build) |
| `data/street_themes.json` | NEW — committed corpus: 46 suburbs, 65 puzzles |
| `streets.py` | NEW — Dash game module (route `/streets`) |
| `test_streets.py` | NEW — simulated full-game state-machine tests (PASS) |
| `docs/streets.html` + `docs/streets.js` | NEW — static vanilla-JS port |
| `docs/style.css` | Streetwise styles (Monad design system) |
| `docs/index.html`, `play.html`, `trivia.html` | Nav links to streets.html |
| `export_site.py` | Exports `data/street_themes.json` → docs/data/ |
| `app.py` | streets import/init, `/streets` route, header link |
| `pyproject.toml` | `+ osmium` dependency |
| `README.md`, `CLAUDE.md` | Documented Streetwise + pipeline |
| `.copilot-tracking/research/20260809-street-theme-game-research.md` | Updated with implementation results + multi-puzzle design |

## Commands executed

```sh
uv add osmium
uv run python -u -m scrape.street_themes --fetch --match   # pbf → 24,031 streets / 127 suburbs; 36 Layer-1 themed suburbs
uv run python -u -m scrape.street_themes --discover        # Layer 2a: +10 novel themes (46 total)
uv run python -u -m scrape.street_themes --clues           # Layer 2b: 65 puzzles generated (2 transient failures retried OK)
uv run python -u -m scrape.street_themes --build           # → data/street_themes.json
uv run python test_streets.py                              # PASS — 0 failures
uv run python export_site.py                               # static export incl. street_themes.json
```

## Key decisions

- **Data source: BBBike Melbourne .osm.pbf (88 MB, download.bbbike.org)** —
  Overpass public instance hit `rate_limited` 406s + 504s repeatedly during
  research; the pbf is one download, fully local, deterministic (24k streets
  across 127 suburbs in ~2 min with pyosmium + shapely point-in-polygon).
- **Theme matching in three layers**: Layer 1 curated keyword dictionary +
  base-name suffix patterns (Glenroy's "-ana" ANA estate matches on "Menana"
  → base after stripping "Road"); Layer 2a DeepSeek discovers NOVEL themes
  (Ashburton WWII, Port Melbourne aircraft, Mernda Renaissance artists,
  Balwyn North constellations, etc. — the user's "bonus points" requirement);
  Layer 2b DeepSeek writes 5 clue rounds per theme (clue/namesake/2 same-
  category distractors/tidbit/3-4 sentence explainer). Street names are
  hard-verified against the OSM attribution — the LLM never invents streets.
- **Multi-theme suburbs get one puzzle per theme** (Sunbury 8, Point Cook 4,
  Glen Waverley 3, Glenroy 2) — the game picks a random puzzle per play.
  Initially only the dominant theme was generated; refactored after Glenroy
  (Native Flora, 11 streets) buried the ANA estate (8 streets).
- **Scoring** (user-confirmed): 2 attempts per round; first-try 100,
  second-try 50, hint halves the round's value, exhausted 0. Share grid
  squares: 🟩🟨🟦⬛.
- **One suburb per game** (user-confirmed), endless random play + play-again.
- **State machine extracted as pure `apply_action()`** in streets.py so the
  game logic is testable without a browser (test_streets.py simulates full
  games incl. clean sweeps, all-fail games, hint economy).
- Windows stdout is cp1252 — all non-ASCII chars stripped from print()
  statements (repeated UnicodeEncodeError during the build).

## Blockers / notes

- Layer 2b had 2 transient DeepSeek JSON failures (Burwood/Native Flora,
  Ivanhoe) — both succeeded on retry; per-suburb+theme caching makes reruns
  free.
- Elwood's corpus: Tennyson first (real quote), Byron, Shelley, Dickens,
  Burns — suburb name never leaks into clues (prompt-enforced).
- Static site verified: all pages 200, corpus parses, `node --check` clean.
