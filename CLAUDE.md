# melb_map

Interactive map of 133 inner/middle/outer Melbourne suburbs showing quirky
character tags (e.g. "trust fund punks", "$9 oat lattes") sourced from
r/melbourne, melbz.com.au, eMelbourne, Wikipedia and the ABS census, and
summarised by DeepSeek. Inspired by hoodmaps.com but sourced from real community
data, not generic demographics. Ships with **Suburble**, a daily Worldle-style
suburb-guessing game at `/play`. See also `README.md` (user-facing).

## Stack

- **Plotly Dash** (Dash, not pure Plotly — needed so click → side panel works cleanly)
- **uv** for Python deps
- **OpenAI SDK pointed at DeepSeek** (`api.deepseek.com/v1`, model `deepseek-chat`)
  for summarisation. ~10x cheaper than Claude, plenty smart for this task,
  auto prompt-caching when system prompt is byte-identical.
- **No Reddit auth / API key**: parses old.reddit.com HTML with BeautifulSoup.
  Unauthenticated `.json` endpoints started 403ing in 2026-05, so the scraper
  switched to HTML (same output shape, nothing downstream changed). Original plan
  was PRAW but Reddit's app-creation captcha is broken — sidestepped entirely.
  NOTE (2026-08): old.reddit.com now serves a login wall to unauthenticated
  clients — `scrape/mine_threads.py` falls back to Wayback Machine snapshots
  of old.reddit HTML, and the DDG-hop unlock (see
  `.agents/skills/reddit-fetch/SKILL.md`) gets live `.json` through a browser
  session cookie.
- **Image generation**: pluggable via `IMAGE_GEN_PROVIDER` env var. Defaults to
  Pollinations (free), Replicate FLUX dev (`black-forest-labs/flux-dev`,
  ~$0.025/image) when paid. See `scrape/imagegen.py`.

## Data sources

The summariser ingests five corpora per suburb:

1. **Reddit r/melbourne + r/MelbourneActivities** (`scrape/reddit.py`) — top
   posts and their comments where the suburb is the post topic. Polite mode:
   random jitter 3-6s, honors Retry-After, hard-bails on 401/403.
2. **Reddit cross-suburb meta-threads** (same scraper, `--meta`) — full comment
   trees from "best/worst suburb" / "your suburb in 3 words" / etc threads
   (`data/raw/_meta.json`; 128 threads, ~21.5k comments). Per-suburb mentions
   extracted at summarise time (case-insensitive, word boundary, with
   longer-name disambiguation).
3. **MELBZ.com.au** (`scrape/melbz.py`) — curated suburb guides scraped from
   `melbz.com.au/{slug}/`. Sections: "Where Is X", "What X Is Actually Like",
   "Who Lives Here", "Eating and Drinking", "Verdict", etc. **Highest signal**;
   factual + opinionated character content, written by humans.
4. **eMelbourne** (`scrape/emelbourne.py`) — University of Melbourne suburb
   history encyclopaedia. **Primary history source** (history field + source
   attribution).
5. **Wikipedia** (`scrape/wikipedia.py`) — suburb articles as history fallback
   when eMelbourne has no entry.

Plus a non-LLM layer:

6. **ABS 2021 Census** (`scrape/census.py`) — Location-Quotient "quirk"
   detector: birthplaces/languages/ancestries over-represented vs Greater
   Melbourne (core: count ≥ 40 & LQ ≥ 2.0; emerging: count ≥ 25 & LQ ≥ 4.0),
   merged into each suburb's `census` field and rendered with flags in the
   panel. Cells with count < 20 are ABS-perturbation noise and never used.

And a quiz-only layer (NOT shown on the main page — the quiz must not recycle
panel content):

7. **Fun-fact corpus** (`scrape/mine_fun_facts.py` → `data/fun_facts.json`) —
   a dedicated Suburb Detective corpus: 262 city-wide facts mined from the
   hand-picked "best fun fact about Melbourne" threads plus ~510 suburb-
   specific facts. `scrape/mine_threads.py` fetches those 12 gold threads
   into `data/raw/_meta.json` (Wayback fallback + DDG-hop unlock; see
   `.agents/skills/reddit-fetch/SKILL.md`). trivia.js draws a "Fun fact"
   clue from it.

### Suburb name aliases

Most suburbs are searched/matched by their SAL name. **Exception: Melbourne**
(the CBD) — locals call it "the CBD", "Melbourne CBD", "city centre", never
plain "Melbourne". Configured in:
- `scrape/reddit.py::SUBURB_SEARCH_ALIASES` for Reddit search queries
- `scrape/summarize.py::SUBURB_MENTION_TERMS` for meta-mention scans
This dict is extensible if other suburbs need similar treatment.

## Data flow

```
ABS SAL 2021          Reddit r/melbourne        melbz.com.au
shapefile             (old.reddit HTML:          (curated guides)
                      per-suburb + meta)
    │                       │                          │
    ▼                       ▼                          ▼
data/boundaries.geojson  data/raw/{suburb}.json   data/raw/melbz/{suburb}.json
data/context_boundaries.geojson  data/raw/_meta.json
                                        │
                                        │       eMelbourne     Wikipedia
                                        │       (history)      (fallback)
                                        │           │              │
                                        │           ▼              ▼
                                        │     data/raw/emelbourne  data/raw/wikipedia
                                        └──────► DeepSeek ◄────────┘
                                                summarise
                                                    │
                                                    ▼
                                        data/suburbs.json (+census)
                                                    │
                                                    ▼
                                                Dash app
                                             + assets/mascots/{suburb}.{jpg|png}
```

Reddit + LLM is **batch / on-demand**, not live. App reads cached
`data/suburbs.json` and `data/boundaries.geojson` at startup.

## File layout

```
melb_map/
├── pyproject.toml          # uv-managed
├── .env.example            # copy to .env, fill in DEEPSEEK_API_KEY + REDDIT_USER_AGENT
├── .gitignore              # excludes .env and data/raw/
├── CLAUDE.md               # this file
├── README.md               # user-facing docs
├── app.py                  # Dash app entry point (map + panel + /play routing)
├── suburble.py             # Suburble daily guessing game (routes under /play)
├── data/
│   ├── boundaries.geojson  # 133 suburb polygons, WGS84
│   ├── context_boundaries.geojson  # grey basemap: non-target suburbs behind map
│   ├── suburb_list.txt     # canonical suburb names (one per line)
│   ├── suburbs.json        # vibes/tags/quotes/census/mascot per suburb
│   └── raw/                # gitignored: ABS zip + per-suburb Reddit caches + _meta.json
│       └── {melbz,emelbourne,wikipedia}/   # per-suburb scraped caches
├── assets/
│   ├── flags/              # local flag SVGs (CORS-safe) for census panel
│   └── mascots/            # rendered mascot images ({suburb}.jpg|png)
└── scrape/
    ├── __init__.py
    ├── boundaries.py       # ABS SAL 2021 download/filter → boundaries.geojson + suburb_list.txt
    ├── context_boundaries.py  # grey non-target basemap geojson
    ├── reddit.py           # old.reddit HTML scraper (per-suburb + --meta)
    ├── mine_threads.py     # fetch specific gold threads into _meta.json (Wayback fallback)
    ├── mine_fun_facts.py   # DeepSeek mines quiz-only fun_facts.json from gold threads
    ├── melbz.py            # melbz.com.au profile scraper
    ├── emelbourne.py       # eMelbourne history encyclopaedia scraper
    ├── wikipedia.py        # Wikipedia history fallback scraper
    ├── census.py           # ABS 2021 GCP LQ quirk detector → merges census into suburbs.json
    ├── summarize.py        # DeepSeek summariser, multi-source corpus
    ├── expand_quotes.py    # surgical quote re-extraction (~15 verbatim quotes/suburb)
    ├── imagegen.py         # pluggable image gen (Pollinations / Replicate)
    ├── mascots.py          # generate mascot images for suburbs
    └── refresh.py          # orchestrator: boundaries → scrape all → summarise
```

## Running things

```sh
uv sync                                              # install deps

# One-off boundary build (only if scope changes)
uv run python -m scrape.boundaries
uv run python -m scrape.context_boundaries            # grey basemap layer

# Reddit scrape (old.reddit HTML, polite)
uv run python -u -m scrape.reddit Fitzroy            # single suburb (test)
uv run python -u -m scrape.reddit --all              # all 133 (~1-2h, polite mode)
uv run python -u -m scrape.reddit --all --force      # re-scrape ignoring cache
uv run python -u -m scrape.reddit --meta             # cross-suburb meta-thread sweep

# History sources
uv run python -u -m scrape.emelbourne --all
uv run python -u -m scrape.wikipedia --all

# Summarise via DeepSeek
uv run python -u -m scrape.summarize Fitzroy        # single, prints JSON
uv run python -u -m scrape.summarize --all          # everything
uv run python -u -m scrape.summarize --all --force  # ignore existing entries

# Census quirks (ABS 2021; needs openpyxl + pyshp, not in project deps)
uv run --with openpyxl --with pyshp python -u -m scrape.census --all

# Quote surgery (after summarise; only rewrites quotes + top_quote)
uv run python -u -m scrape.expand_quotes --all

# Everything in one go (boundaries → all scrapes → summarise)
uv run python -u -m scrape.refresh

# Run the app (map + Suburble at http://localhost:8050/play)
uv run python -u app.py                              # http://localhost:8050
```

Always pass `python -u` (unbuffered) for backgrounded runs — otherwise stdout is
buffered and you can't see progress until the process exits.

## Configuration / secrets

`.env` (copy from `.env.example`):
- `DEEPSEEK_API_KEY` — required for summarisation
- `REDDIT_USER_AGENT` — descriptive UA with username, e.g.
  `melb-map (hobby project) by u/yourname`. No Reddit API keys needed.
- `IMAGE_GEN_PROVIDER` — `pollinations` (free, default) or `replicate` (~$0.025/img)
- `REPLICATE_API_TOKEN` — required if `IMAGE_GEN_PROVIDER=replicate`

`.env` is gitignored. `.env.example` should never contain real secrets.

## Key decisions

- **133 inner, middle AND outer suburbs** (100 original + 33 outer added for
  completeness; outer have thinner Reddit coverage but meta-threads fill gaps).
  Suburb list is hardcoded in `scrape/boundaries.py::TARGET_SUBURBS` and frozen
  to `data/suburb_list.txt` for downstream stability.
- **ABS SAL 2021 boundaries** (official, free). The 99 MB shapefile zip is
  downloaded once and cached under `data/raw/`. Filter is by suburb name, not
  LGA — cleaner since SAL boundaries straddle LGA borders.
- **`choropleth_map`** (Plotly's maplibre-based version, no Mapbox token
  needed). Clean SVG geo, light-grey canvas, grey `context_boundaries.geojson`
  basemap behind the coloured suburbs, explicit axis ranges for zoom.
- **old.reddit HTML, not PRAW / JSON endpoints**. Reddit's app-registration
  captcha has been broken for years AND unauthenticated `.json` endpoints now
  403 — parsing old.reddit.com HTML sidesteps both with no functional loss at
  our scale.
- **DeepSeek over Anthropic** for summarisation. ~10x cheaper, OpenAI-compatible
  API, quality is plenty for this task.
- **Census quirks, not demographic wallpaper** — LQ-based over-representation
  (core/emerging tiers tuned against ABS perturbation noise floor) surfaces
  character ("top 1% for Khmer") instead of generic charts.
- **Polite scraping**: random jitter (3-6s per request, 10-25s between
  suburbs), honours `Retry-After` on 429, hard-bails on 401/403, exponential
  backoff on errors, warmup hit on session start. Full batch ~1-2h instead of
  ~30 min, but firmly polite-citizen territory.
- **Click → side panel** via Dash callback on `clickData`. Hover via
  `customdata` in the choropleth trace. Suburble is self-contained
  (`suburble.py`) — `app.py` calls `init()` once, routes `/play`, no import
  cycle.

## Mascots

Each suburb has one illustrated identity:
- **Mascot**: a recognisable Melbourne archetype drawn cartoon-style (e.g. "Dave
  the Brunswick Sparkie, 38") — appears in the side panel when a suburb is
  clicked.

The LLM generates the mascot text description and an `image_prompt`. Mascot
first names are enforced unique across all suburbs (live exclusion list during
summarise). `scrape/mascots.py` sanitises prompts (strips drug/contraband
references that hit safety filters) and renders via the configured backend
(Pollinations `flux` — free, no key, needs `User-Agent` header; or Replicate
FLUX dev with 12s self-throttling under the 6/min low-credit cap). Images land
in `assets/mascots/{suburb}.{jpg|png}`; Dash serves them from `/assets/`.
**11 of 133 rendered so far** (Elwood via Pollinations; Carlton North, Ascot
Vale, Kew, Hampton, Flemington, Caulfield South, Carlton, Camberwell,
Ripponlea, Balaclava via Replicate, ~$0.50) — 122 remaining, pending go-ahead.

## Status (working notes)

- [x] Scaffold (pyproject, `.env.example`, dirs)
- [x] `scrape/boundaries.py` — `data/boundaries.geojson` (133 suburbs) +
      `data/suburb_list.txt`
- [x] `scrape/context_boundaries.py` — grey non-target basemap layer
- [x] `scrape/reddit.py` — polite old.reddit HTML scraper (JSON endpoints 403
      since 2026-05; output shape unchanged). `--meta` mode → 128 cross-suburb
      threads, 21,534 comments in `data/raw/_meta.json`.
- [x] `scrape/melbz.py` — all 133 suburbs profiled (curated guides, highest
      signal for character)
- [x] `scrape/emelbourne.py` + `scrape/wikipedia.py` — history sources
      (eMelbourne primary, Wikipedia fallback), both stubbed gracefully when
      a suburb is missing
- [x] `scrape/census.py` — LQ quirk detector (core/emerging tiers), merges
      `census` into `data/suburbs.json`
- [x] `scrape/summarize.py` — DeepSeek summariser, byte-identical system prompt
      for auto cache. Schema: nickname, tags (7-12), vibe (2-3 sentences), lore
      (5-8), history (+ source attribution), top_quote, quotes (~15),
      primary_category, census, mascot {name, tagline, description,
      image_prompt}. Joins meta-mentions into the corpus; enforces unique mascot
      first names across the batch.
- [x] `scrape/expand_quotes.py` — surgical quote re-extraction (~15 verbatim,
      suburb-linked, self-contained quotes; verbatim-checked against corpus)
- [x] `scrape/refresh.py` — orchestrator (boundaries → reddit → melbz →
      emelbourne → wikipedia → summarise)
- [x] `app.py` — choropleth + grey context basemap + zoom + hover tooltip +
      click → side panel rendering all rich fields incl. census quirks with
      flags and mascot image (if present) + mascot bio
- [x] `suburble.py` — daily guessing game at `/play` (6 guesses, distance +
      8-point direction + proximity %, shareable emoji grid)
- [x] All 133 suburbs summarised with full schema (nickname, tags, vibe, lore,
      history, top_quote, quotes, census, mascot)
- [x] `scrape/imagegen.py` + `scrape/mascots.py` — pluggable image gen
      (Pollinations free / Replicate FLUX dev ~$0.025), sanitisation + throttle
- [x] **11 mascots rendered** (Elwood + 10 via Replicate; ~$0.50)
- [x] **Melbourne CBD aliasing** — search/scan terms swap "Melbourne" with
      ["Melbourne CBD", "the CBD", "city centre"]. Extensible via
      `SUBURB_SEARCH_ALIASES` / `SUBURB_MENTION_TERMS`.
- [x] Map polish — zoom, clean SVG geo, context basemap, panel styling
- [x] README + this doc refreshed to the 133-suburb state
- [x] `export_site.py` — static site exporter (figure JSON + data + assets → docs/)
- [x] **Static site deployed** — GitHub Pages at [jay-stein.github.io/melb_map](https://jay-stein.github.io/melb_map/). Map + Suburble ported to vanilla JS + plotly.js, fully client-side.
- [x] **Security hardened** — dependency CVEs patched (0 known vulns), LLM-derived text HTML-escaped in figure customdata/hovertemplate (prevents stored XSS), URL scheme allowlisted for history links.
- [ ] Auto-generate mascot images for the remaining 122 suburbs — pending user
      go-ahead (~$3 via Replicate, or free via Pollinations)

## Risks / things to watch

- **Reddit content thinness** for less-discussed suburbs (e.g. Aberfeldie,
  many outer suburbs). The meta-thread scrape directly addresses this — most
  suburbs get named in cross-suburb discussions even when they have few
  dedicated posts.
- **Reddit HTML fragility**: the scraper parses old.reddit.com markup; if
  Reddit changes their HTML structure, selectors may break (vs JSON which had a
  stable schema). Watch the parse-failure rate on big batch runs.
- **Tone calibration**: r/melbourne can skew negative/edgy. Summariser prompt
  explicitly requires "playful, observational, not mean-spirited". Eyeball
  output on a few suburbs before trusting the full batch.
- **DeepSeek cost**: 133 suburbs × richer prompt with prompt caching ≈ ~20-25¢
  for the full re-summarise. Negligible.
- **Reddit rate limit**: ~30 req/min unauthenticated. Polite scraper averages
  ~10 req/min, so well under threshold. Isolated 429s in full runs, all
  resolved by backoff.
- **Census noise**: ABS perturbs small counts; the LQ tiers (core/emerging)
  are tuned to stay above the noise floor, and cells with count < 20 are never
  used.
