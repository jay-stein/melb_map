<!-- markdownlint-disable-file -->

# Task Research Notes: Street-Theme Suburb Guessing Game ("Guess the Suburb from its Streets")

## Research Executed

### File Analysis

- `assets/suburb_streetname_themes.txt` (user-provided seed)
  - 17 curated themes across Greater Melbourne: Crimean War battles (Balaclava, St Kilda, Caulfield), Royal Navy ships (Williamstown, Port Melbourne, Docklands), 1956 Olympics (Heidelberg West), Arthurian legend (Glen Waverley Camelot Estate), Classical composers (Wheelers Hill, Caroline Springs, Taylors Hill), Classic literature/poets (Pakenham, Berwick, Point Cook), Greco-Roman mythology (Frankston, Langwarrin, Sunbury), Golf courses (Dingley Village, Heatherton, Sunbury), Melbourne Cup racehorses (Flemington, Ascot Vale, Mentone, Pakenham), Aviation (Essendon Fields, Niddrie, Laverton, Point Cook), Astronomy (Greenvale, Roxburgh Park), Gemstones (Cranbourne, Narre Warren, Point Cook), Viticulture (Yarra Glen, Lilydale, Sunbury), Native flora (Blackburn, Mitcham, Ringwood, Ferntree Gully), British towns/rivers (Preston, Reservoir, Brighton, Hampton), Elite British universities (Kew, Hawthorn), PMs (Endeavour Hills, Gladstone Park).
  - **Coverage vs our 133**: 27 of our suburbs appear in the file (Ascot Vale, Balaclava, Blackburn, Boronia, Brighton, Caulfield, Cranbourne, Docklands, Essendon, Flemington, Frankston, Glen Waverley, Hampton, Hawthorn, Heidelberg, Kew, Lilydale, Melbourne, Pakenham, Point Cook, Port Melbourne, Preston, Reservoir, Ringwood, St Kilda, Sunbury, Williamstown).
  - **Known incompleteness**: the file's Poets theme lists Pakenham/Berwick/Point Cook but NOT Elwood — yet Elwood is Melbourne's most famous poet-street suburb (26 poet streets verified in this research). Any pipeline must therefore be data-driven, not file-trusting. The file is a seed/verification source, not ground truth.
  - Format has no machine-readable structure (markdown prose + summary table) — needs conversion to a theme→suburb mapping table during implementation.

- `suburble.py` (existing daily game, Dash side)
  - Architecture: self-contained module; `init(geojson, suburb_names, centroids)` populates globals from app.py; `daily_target()` deterministic via EPOCH + SHUFFLE_SEED; `layout()` + `register_callbacks(app)`; app.py routes `/play` to it; `dcc.Store` for state; `prevent_initial_call` callbacks; clientside callback for clipboard share.
  - Daily-puzzle anchors: `EPOCH = date(2026, 1, 1)`, `SHUFFLE_SEED = 20260101`, `MAX_GUESSES = 6`.
  - Answer input: `dcc.Dropdown` of all 133 suburbs (Dash) / chiclet tiles (static port).
  - Share grid: emoji squares (proximity) + compass arrow; header "Suburble #N score"; footer "melb-map · Suburble".

- `docs/suburble.js` (static vanilla-JS port)
  - Pattern: fetch `data/game-state.json` + `data/boundaries.geojson`, compute daily target identically to Python (same EPOCH/order), `pyRound` shim to match Python round-half-even, Plotly silhouette + guess-progress choropleth, chiclet tiles with CSS.escape.
  - Game state exported by `export_site.py` from the Python code path so Dash and static site agree.

- `docs/trivia.js` + `docs/trivia.html` (Suburb Detective game)
  - Closest pattern to the proposed game: **progressive clue reveals** — `MAX_CLUES = 5`, `revealed` counter, "Show next clue (N left)" button, score = `MAX_CLUES - revealed` (guess with fewer clues = higher score), 5 rounds, chiclet tile guessing, `renderFeedback`, results screen with trophy + share grid + play-again.
  - Clue dedup: Jaccard word-overlap >= 0.33 OR >= 3 shared content words ⇒ near-duplicate dropped (suburb name excluded from comparison).
  - `redact(text, suburb, nickname)` replaces the target's name with "this suburb" so clues never leak the answer.
  - Region-select screen (central/north/south/east/west pools from game-state.json `regions`).

- `export_site.py`
  - Exports figure.json, game-state.json, boundaries.geojson, suburbs.json, fun_facts.json, quiz_questions.json, mascots.json + assets into `docs/`. Hand-maintained HTML/JS files are NOT regenerated. A new street-themes corpus would be added here as `data/street_themes.json` (+ new `streets.html`/`streets.js` hand-maintained).

- `scrape/mine_city_quiz.py` (LLM corpus pipeline pattern)
  - Pattern: load corpus → DeepSeek (OpenAI SDK, `api.deepseek.com/v1`, `deepseek-chat`) → strict JSON array output, temperature 0.7, retry loop, `random.Random(seed).shuffle`, write `data/quiz_questions.json`. Same DeepSeek wiring as `scrape/summarize.py` (byte-identical system prompt for prompt caching).

- `data/` payload formats
  - `fun_facts.json`: `{"city": [...], "<Suburb>": [...]}` — flat suburb-keyed arrays of strings.
  - `quiz_questions.json`: `[{"text": str, "truth": bool}]`.
  - `docs/data/game-state.json`: `{epoch, maxGuesses, order, centroids, regions, maxDist}`.

### Code Search Results

- `assets/*` → `suburb_streetname_themes.txt` found (the seed file; 17 themes, 27 of our suburbs mentioned)
- `*.py` → `suburble.py` (game module), `app.py` (routing + init), `export_site.py` (static export), `scrape/mine_city_quiz.py` (LLM corpus pipeline)
- `docs/*.js` → `suburble.js` (static port pattern), `trivia.js` (progressive-clue pattern)
- `style.css` → reusable classes: `.game`, `.game-wide`, `.back-link`, `.tiles-grid`, `.tile`, `.clue-card`, `.clue-new`, `.reveal-btn`, `.clue-cost`, `.guess-row-card`, `.result-msg`, `.grid-pre`, `.play-again` — all reusable by a new streets game page
- No existing research files under `.copilot-tracking/research/` (this is the first)

### External Research

- #fetch:https://overpass-api.de/api/interpreter (OSM Overpass API — live testing)
  - **Verified working pattern**: `POST https://overpass-api.de/api/interpreter` with form field `data=<query>`; REQUIRES a descriptive `User-Agent` header (406 "Not Acceptable" without one — Apache-level block). Public instance rate-limits aggressively: second query in a ~5s window returned `rate_limited` 406 (OSM3S dispatcher quota). Observed ~1 successful query per 20–30 s sustained.
  - Query form used: `[out:json][timeout:40];way["highway"]["name"](poly:"lat lon lat lon ...");out tags N;`
  - **Polygon gotchas (all hit live)**: (1) `poly:` attribute must be **space-separated** `lat lon` pairs (commas ⇒ "even number of float values" static error); (2) ABS SAL rings contain consecutive duplicate vertices at 4-decimal rounding — Overpass rejects degenerate edges, must dedupe consecutive points (Elwood 218 → 211); (3) use POST for long queries (Elwood poly ≈ 3.8 KB; GET URLs risk server limits); (4) `out tags N` with no geometry keeps responses small (99 ways for Elwood).
  - Live results — **Elwood (inside ABS polygon)**: 99 named streets; 26 poet-theme names: Bronte Ln, Browning, Burns, Byron, Coleridge, Dante Ln, Dickens, Dryden, Goldsmith, Hood, Joyce, Keats, Kendall, Kingsley, Lawson, Lytton, Milton, Moore, Poets Grove, Ruskin, Scott, Southey Ct/Grove/St, Tennyson, Thackeray. Confirms theme file's biggest omission and proves the pipeline.
  - Live results — **Balaclava (700 m radius)**: 93 streets incl. Inkerman, Malakoff, Sebastopol, Cardigan, Raglan + Balaclava Rd (Crimean War cluster confirmed). Also Mozart, Dickens, Kipling (poets!) and Wimbledon Ct/Westbury (English towns) — suburbs can carry several mini-themes.
  - Live results — **Flemington racecourse precinct (900 m radius)**: 165 streets incl. Jezabeel Ct (1998 Cup winner), Epsom Rd; racehorse cluster concentrated near the course — radius/center choice matters, polygon intersection is more reliable for attribution.
  - Data licence: OSM data is ODbL — the site footer already credits sources; a "street names © OpenStreetMap contributors" line must be added.
  - Rate-limit implication: 133 suburbs × 1 query ≈ 45–75 min with polite pacing + retry on 406 (mirrors the project's existing polite-scraping conventions in `scrape/reddit.py`: jitter, Retry-After honouring, hard-bail, exponential backoff). Zero cost.
  - Mirror endpoints exist (e.g. overpass.kumi.systems) if the primary instance throttles hard.
  - **Public-instance instability observed 2026-08-09**: 3 episodes of `rate_limited` 406 and 504 Gateway Timeouts in one session; sustained load is unreliable. The full 133-suburb batch MUST NOT depend on the public instance alone.

- #fetch:https://download.bbbike.org/osm/bbbike/Melbourne/Melbourne.osm.pbf (BBBike Melbourne city extract)
  - **VERIFIED live**: 200 OK, 83.7 MB `.osm.pbf` city extract covering all of Greater Melbourne. Downloaded to `%TEMP%\opencode\Melbourne.osm.pbf` for the POC.
  - **POC result (proven end-to-end)**: `uv run --with osmium` + pyosmium `SimpleHandler` with `locations=True` parsed 157,496 named highway ways in ~2 min; attributed 24,031 street names to 127 of our 133 ABS suburb polygons via shapely point-in-polygon (way centroid). No rate limits, deterministic, reproducible. Per-suburb lists reproduced the Overpass results exactly (Coburg North 11 camera streets; Elwood 94 streets incl. Shelley St + Rosetti Ln which Overpass missed).
  - This makes the pbf the **primary data source**; Overpass becomes a fallback/refresh path.
- #fetch:https://www.openstreetmap.org/api/0.6/map (OSM API map bbox endpoint — what the website's Export button uses)
  - **VERIFIED NEGATIVE**: Glenroy bbox returned "You requested too many nodes (limit is 50000)" — dense inner suburbs with buildings mapped routinely exceed the 50k-element cap. NOT viable for per-suburb fetches; also against OSM's bulk-use policy.
- #fetch:https://download.geofabrik.de/australia-oceania/australia-latest.osm.pbf
  - VERIFIED: 956 MB whole-Australia pbf exists as a wider-scope fallback (unnecessary when the BBBike Melbourne extract covers our 133 suburbs).

### User-confirmed theme claims (live-verified via pbf + Overpass)

| Theme | Suburb (in our 133) | Verified streets | Status |
| --- | --- | --- | --- |
| **Camera / photography estate** | Coburg North | Aperture St, Camera Walk, Cyan Walk, Focus Dr, Image Walk, Lens St, Photography Dr, Pixel Circuit, Portrait Way, Snapshot Dr, Spectrum Way (11) | ✅ CONFIRMED — bigger than claimed (user listed 5) |
| **ANA estate (-ana suffix)** | Glenroy | Menana Rd, Tarana Ave, Warana Ct, Kadana St, Palana St, Pengana Ave, Loongana Ave (7) | ✅ CONFIRMED — names match on the BASE NAME (suffix "ana"); full strings like "Menana Road" do not end in -ana |
| **Aviation manufacturers** | Strathmore Heights (NOT in our 133) | Boeing Rd, Douglas Ct/St, Hawker St, Lockheed St, Vickers Ave/St (7) | ⚠️ REAL but not playable — outside our suburb list; Oak Park's polygon contains none |
| Elwood poets (user example) | Elwood | 27 poet streets incl. Shelley St, Rosetti Ln | ✅ CONFIRMED (see earlier) |
| Bonus novel themes discovered by the clustering approach | Coburg North | Ulm St + Kingsford Ave (aviation pioneers); Krithia St + Suvla Grove + Anzac Ave + Jacka St + Allenby St (Gallipoli/WWI) | 🎁 EXACTLY the "bonus themes" the user wants the scraper to surface |
| Bonus candidate | Fawkner | North Weald Way (RAF airfield, Battle of Britain) | 🎁 candidate for LLM validation |

### Project Conventions

- Standards referenced: `CLAUDE.md` (game architecture, corpus pipelines, polite scraping, DeepSeek wiring, static-site dual implementation), `docs/agent-sessions/*` (session summaries), `.github/instructions/` conventions, Global Agent Rules (git workflow, conventional commits, session export).
- Instructions followed: existing game patterns (suburble.py ↔ suburble.js, trivia.js progressive clues, export_site.py state export, DeepSeek strict-JSON corpus builders).

## Key Discoveries

### Project Structure

- Games live as self-contained modules: `suburble.py` (Dash) ↔ `docs/suburble.js` (static), `docs/trivia.js` (static-only so far). Dash app routes `/play` via `app.py`'s `@app.callback(Output("page-content", "children"), Input("url", "pathname"))`; a streets game would add `/streets` (and mirror page `streets.html` + `streets.js` in docs/).
- Corpus data: `data/*.json` (suburbs.json, fun_facts.json, quiz_questions.json), exported to `docs/data/` by `export_site.py`; game state computed in Python, serialised once (game-state.json) so both implementations agree.
- Scrape pipeline: `scrape/*.py` modules, run via `uv run python -u -m scrape.<module>`, outputs to `data/`.

### Implementation Patterns

- **Progressive-reveal game flow** (trivia.js): clue list built client-side, `revealed` counter, "Show next clue (N left)" button, score = MAX_CLUES − revealed, chiclet tiles for guessing, feedback + results screen with share grid. Directly transferable.
- **Daily determinism** (suburble): EPOCH + seeded shuffle ⇒ same puzzle for everyone each day; no server state.
- **LLM corpus building** (mine_city_quiz.py / summarize.py): strict-JSON DeepSeek prompts, batches, retry-once, seeded shuffle, byte-identical system prompt for prompt caching.
- **Anti-spoiler redaction** (trivia.js): `redact()` replaces the target suburb name (and nickname) with "this suburb" in clue text. For a streets game the street names themselves are the clues — no redaction needed, but the THEME label must not name the suburb (e.g. say "named after Melbourne Cup winners" rather than "like in Flemington").
- **Emoji share grid**: Suburble/trivia both produce a monospace emoji grid + copy button via clientside callback / navigator.clipboard.
- **Safety**: LLM-derived text HTML-escaped (`esc()`/`html_escape`) everywhere; street names are NOT LLM-derived (they come from OSM) but still escaped on render.

### Complete Examples

```json
// Verified Overpass query for a suburb's street names (Elwood ABS polygon)
// POST data=<urlencoded> to https://overpass-api.de/api/interpreter
// Headers: User-Agent: melb-map-research/0.1 (suburb street theme investigation)
[out:json][timeout:40];
way["highway"]["name"](poly:"-37.8724 144.9843 -37.8722 144.9849 ... -37.8724 144.9843");
out tags 250;
```

```json
// Proposed data/street_themes.json shape (game corpus) — round-based schema
{
  "Elwood": {
    "theme": "Literary Poets",
    "background": "Victorian-era estates often borrowed names from English literature. This suburb's streets are named after famous poets.",
    "reveal": "Elwood — where every street is a poet",
    "rounds": [
      {
        "street": "Tennyson Street",
        "namesake": "Alfred, Lord Tennyson",
        "clue": "This poet wrote \"'Tis better to have loved and lost / Than never to have loved at all\"",
        "options": ["Alfred, Lord Tennyson", "Robert Browning", "William Wordsworth"],
        "tidbit": "As Poet Laureate for 42 years, Tennyson held the office longer than anyone before or since.",
        "explainer": "Alfred, Lord Tennyson (1809–1892) was Queen Victoria's Poet Laureate for 42 years — the longest tenure ever. His 'In Memoriam' gave English the phrase 'tis better to have loved and lost'. He also wrote 'The Charge of the Light Brigade', about the Crimean War that gave Balaclava its street names.",
        "points": 100
      },
      {
        "street": "Byron Street",
        "namesake": "Lord Byron",
        "clue": "A scandalous Romantic who swam the Hellespont and died in Greece at 36",
        "options": ["Percy Shelley", "Lord Byron", "John Keats"],
        "tidbit": "Byron's daughter Ada Lovelace is considered the world's first computer programmer.",
        "explainer": "Lord Byron (1788–1824) was the rock star of the Romantics — famously 'mad, bad and dangerous to know'. He died fighting for Greek independence, and his daughter Ada Lovelace became the world's first computer programmer.",
        "points": 100
      },
      ...
    ]
  },
  ...
}
```

```json
// Glenroy ANA estate — example of a SUFFIX-PATTERN theme (matches on base name)
{
  "Glenroy": {
    "theme": "ANA Aviation Estate",
    "background": "This estate was built to house Australian National Airways workers based at nearby Essendon Airport, then Melbourne's main airport. Every street name ends in 'ana'.",
    "rounds": [
      {
        "street": "Menana Road",
        "namesake": "The 'ana' estate",
        "clue": "Every street in this estate ends with the same four letters — the initials of the airline that employed the neighbourhood",
        "options": ["ANA", "TAA", "Qantas"],
        "tidbit": "ANA merged with Ansett in 1957 to become Ansett-ANA.",
        "explainer": "The Australian National Airways estate in Glenroy was built in the 1940s to house airline workers stationed at Essendon Airport...",
        "points": 100
      }
    ]
  }
}
```

### API and Schema Documentation

- **OSM Overpass API** (overpass-api.de/api/interpreter):
  - POST form field `data` (query string). GET `?data=` also works for short queries.
  - Requires descriptive User-Agent (else 406). Public instance: ~1 query / 20–30 s sustained; `rate_limited` errors returned as 200-with-error-body or 406 — must parse response body for `remark`/`Error` too.
  - `poly:"lat1 lon1 lat2 lon2 ..."` — space-separated, ≥3 pairs, no consecutive duplicate vertices, no self-intersection. `out tags N` returns elements with id + tags only (small).
  - `around:radius,lat,lon` (circle) works but misattributes boundary-adjacent streets — polygon form preferred for per-suburb attribution.
  - Licence: ODbL — attribution required ("street names © OpenStreetMap contributors").

### Configuration Examples

```text
# data flow for the streets game
ABS boundaries.geojson ──► scrape/street_themes.py (new)
        │                     │  Overpass query per suburb (poly, polite pacing)
        │                     ▼
        │              data/raw/streets/{suburb}.json   (cached street lists, gitignored)
        │                     │
        │                     ▼
        │              theme attribution: curated theme file (27 suburbs)
        │              + DeepSeek discovery pass (remaining ~106, ~2-3c total)
        │                     ▼
        │              data/street_themes.json  (game corpus: theme + 5-8 verified streets)
        ▼                     │
export_site.py ◄──────────────┘  (writes docs/data/street_themes.json)
        │
        ▼
docs/streets.html + streets.js  (static game, vanilla JS + plotly.js)
app.py route /streets ──► streets.py module (Dash twin, mirrors suburble.py pattern)
```

### Technical Requirements

- Street names in the game corpus MUST be verified against the actual OSM fetch for that suburb (no LLM-generated street names — hallucination risk; verify membership).
- Data pipeline must not trust the curated theme file blindly (Elwood's poets prove it incomplete) — OSM street lists are ground truth; the file is seed context for theme naming + verification.
- Rate limiting: 133 Overpass queries with 20–30 s pacing + backoff/retry (406/rate_limited) + optional mirror fallback; cache results under `data/raw/streets/` so re-runs are free (matches `data/raw/` gitignore convention).
- Both Dash (server) and static (client) implementations must share the corpus via export_site.py; static game needs the corpus + suburbs.json only (no boundaries needed unless a reveal map is wanted).
- Attribution: add "street names © OpenStreetMap contributors" to game page footer (ODbL).
- Security: escape all rendered text (street names, theme labels) — site convention after the XSS hardening pass.

## Recommended Approach

**Data pipeline: BBBike Melbourne pbf (primary) + Overpass (fallback) → theme discovery → clue generation, shipped as `data/street_themes.json`. (Full pipeline — Option B confirmed by user.)**

1. `scrape/street_themes.py` — **fetch layer**: download `Melbourne.osm.pbf` (83.7 MB, verified URL) once, cache under `data/raw/` (gitignored, same pattern as the ABS zip). Parse with pyosmium (`uv add osmium`): collect all `highway`+`name` ways with centroids; attribute to our 133 ABS polygons via shapely point-in-polygon (POC-verified: 24,031 streets across 127 suburbs in ~2 min). Cache per-suburb lists to `data/raw/streets/{suburb}.json`. Overpass polygon queries remain the fallback/refresh path (proven queries + pacing + retry on 406/504; never the sole source).
2. Theme attribution in two layers:
   - **Layer 1 (curated, free)**: convert the theme file's 17 themes into matching rules — keyword dictionaries (poets, composers, gods, gems, Cup winners, courses) **PLUS suffix/base-name patterns** (the Glenroy ANA estate only matches when the road-type suffix is stripped: "Menana Road" → base "Menana" ends in "ana"; verified live).
   - **Layer 2a (DeepSeek, ~$0.03 total)**: for suburbs with no keyword match, detect ANY coherent naming pattern (strict-JSON, batched, byte-identical prompt for caching). **Novelty is an explicit requirement (user: "bonus points for the scraper if it figures out new themes")** — the Coburg North camera estate (11 streets: Aperture, Camera Walk, Cyan Walk, Focus, Image Walk, Lens, Photography, Pixel, Portrait, Snapshot, Spectrum) is the flagship example of a theme NO keyword dictionary would catch. Discovered novel themes feed back into the Layer-1 dictionary for future runs (feedback loop). Only accept streets that literally appear in the suburb's street list.
3. **Layer 2b (DeepSeek clue generation)**: for every themed street selected into the corpus, generate a round: `{clue (1–2 sentence quote/riddle identifying the namesake), namesake (answer), options (2 plausible same-category distractors), tidbit (one-liner), explainer (3–4 sentence interesting background on the person/thing — e.g. Spitfire: "The Supermarine Spitfire was Britain's iconic WWII fighter..."), points}`. Distractors MUST be same-category (poets vs poets, gems vs gems). Batched like mine_city_quiz.py; deterministic seeded option shuffle afterwards.
4. Emit `data/street_themes.json`: per-suburb `{theme, background, reveal, rounds[5], source}`; suburbs with no detectable theme are excluded from the pool. Every round's `street` must literally exist in the suburb's OSM street list.
5. Extend `export_site.py` to copy the corpus to `docs/data/street_themes.json`.

**Game: "name the namesake" — 5 clue rounds then suburb + theme reveal (user-confirmed flow), implemented twice (Dash + static).**

- New `streets.py` module (Dash) + `docs/streets.html` + `docs/streets.js` (static), app.py routes `/streets`; page links from map header alongside Suburble and Suburb Detective.
- Round structure (Elwood worked example, first puzzle: Tennyson Street):
  1. Opening: 1–2 sentence background about the theme ("Victorian estates borrowed names from English literature…").
  2. Round 1: clue about the street's namesake — a famous quote ("'Tis better to have loved and lost…") or a riddle ("A Lord born in 1809, the definitive poet of the Victorian era…") → **3-option chiclet** (Tennyson / Browning / Wordsworth).
  3. Player may take a **hint** first: fewer points for the round, but reveals a tidbit about the namesake (e.g. "Poet Laureate for 42 years — the longest tenure ever").
  4. **2 attempts per round (user-confirmed scoring)**: first try = full points, second try = half; after 2 wrong attempts the round ends and the answer is revealed.
  5. On reveal (correct or exhausted): show the namesake + **explainer — 3–4 sentences of interesting background about the person or thing** (e.g. a Spitfire round explains the plane and WWII), plus the street card ("Tennyson → Tennyson Street in the mystery suburb").
  6. Rounds 2–5: same flow for Byron St, Keats St, Milton St, Poets Grove (5 streets, each a different namesake).
  7. Finale: **reveal the theme + suburb** ("It was **Elwood** — every street is a poet"), with the suburb's mascot/vibe from suburbs.json as payoff.
- Theme-first reveal: for non-person themes (flora, gems, stars, rivers) the "namesake" is the thing itself (Diamond → "a gemstone, the April birthstone"); for pattern themes (Glenroy ANA) the question targets the pattern/airline — the 3-option mechanic generalises.
- **One suburb per game (user-confirmed)** — 5 rounds then "Play again" for a fresh random suburb; daily deterministic mode (Suburble EPOCH pattern) possible later once the pool is large enough.
- Shareable emoji grid per game (squares per round: 2nd-try/hint/1st-try variants); attribution footer line for OSM.

**MVP path** (first shippable slice): pbf fetch (done, POC-proven) + Layer 1 + Layer 2b for the verified themes first (Elwood poets, Coburg North camera, Glenroy ANA, Balaclava/St Kilda Crimean, Flemington/Ascot Vale racehorses) — then widen to all 127 street-bearing suburbs via Layer 2a.

## Implementation Guidance

- **Objectives**: validate street data availability (DONE — Overpass + pbf both proven live; 4 theme clusters verified), build corpus pipeline (pbf fetch → theme discovery → clue generation), ship a playable 5-round namesake game on both Dash and static sites.
- **Key Tasks**:
  1. `scrape/street_themes.py` — pbf download (cached in data/raw/) + pyosmium parse + polygon attribution (POC script exists at %TEMP%\opencode\parse_pbf_poc.py).
  2. Theme matching rules from `assets/suburb_streetname_themes.txt` (keyword dictionaries + suffix/base-name patterns like Glenroy's -ana).
  3. Layer 2a theme discovery (DeepSeek, strict JSON, batched) with explicit **novel-theme bonus** requirement + dictionary feedback loop.
  4. Layer 2b clue generation (DeepSeek): per street → clue/namesake/options/tidbit/**explainer (3–4 sentences)**; verify streets against pbf street lists; seeded option shuffle.
  5. Corpus builder → `data/street_themes.json` (round-based schema; Elwood as first puzzle).
  6. `streets.py` Dash module + route in app.py + link in map header.
  7. `docs/streets.html` + `docs/streets.js` static port + export_site.py addition.
  8. Attribution footer (OSM ODbL) + XSS-escape conventions; update CLAUDE.md/README; session summary per Global Agent Rules.
- **Dependencies**: `uv add osmium` (+ shapely/geopandas already present); BBBike pbf URL (verified); DeepSeek key for Layers 2a/2b (total ~$0.10 for all 133); Overpass only as fallback.
- **Success Criteria**: ≥25 suburbs with verified theme + 5 clue rounds in corpus; every street name exists in its suburb's pbf-attributed list; Elwood puzzle playable end-to-end (Tennyson → Byron → Keats → Milton → Poets Grove → reveal with explainers); Coburg North camera + Glenroy ANA themes playable; game playable on both `uv run python app.py` (/streets) and `docs/` static export; share grid + attribution footer working.
