# melb_map

Interactive map of inner/middle Melbourne (~75 suburbs) showing quirky character
tags (e.g. "trust fund punks", "$9 oat lattes") sourced from r/melbourne and
summarised by an LLM. Inspired by hoodmaps.com but not generic demographic data.

## Stack

- **Plotly Dash** (Dash, not pure Plotly — needed so click → side panel works cleanly)
- **uv** for Python deps
- **OpenAI SDK pointed at DeepSeek** (`api.deepseek.com/v1`, model `deepseek-chat`)
  for summarisation. ~10x cheaper than Claude, plenty smart for this task,
  auto prompt-caching when system prompt is byte-identical.
- **No Reddit auth**: uses public JSON endpoints
  (`https://www.reddit.com/r/melbourne/search.json?...`). Original plan was PRAW
  but Reddit's app-creation captcha is broken — sidestepped entirely.
- **Image generation**: pluggable via `IMAGE_GEN_PROVIDER` env var. Defaults to
  Pollinations (free), Replicate FLUX dev (`black-forest-labs/flux-dev`,
  ~$0.025/image) when paid. See `scrape/imagegen.py`.

## Data sources

The summariser ingests three corpora per suburb:

1. **MELBZ.com.au** (`scrape/melbz.py`) — curated suburb guides scraped from
   `melbz.com.au/{slug}/`. Sections: "Where Is X", "What X Is Actually Like",
   "Who Lives Here", "Eating and Drinking", "Verdict", etc. **Highest signal**;
   factual + opinionated character content, written by humans.
2. **Reddit r/melbourne + r/MelbourneActivities** (`scrape/reddit.py`) — top
   posts and their comments where the suburb is the post topic. Polite mode:
   random jitter 3-6s, honors Retry-After, hard-bails on 401/403.
3. **Reddit cross-suburb meta-threads** (same scraper, `--meta`) — full comment
   trees from "best/worst suburb" / "your suburb in 3 words" / etc threads.
   Per-suburb mentions extracted at summarise time (case-insensitive, word
   boundary, with longer-name disambiguation). 128 threads, 21k comments.

### Suburb name aliases

Most suburbs are searched/matched by their SAL name. **Exception: Melbourne**
(the CBD) — locals call it "the CBD", "Melbourne CBD", "city centre", never
plain "Melbourne". Configured in:
- `scrape/reddit.py::SUBURB_SEARCH_ALIASES` for Reddit search queries
- `scrape/summarize.py::SUBURB_MENTION_TERMS` for meta-mention scans
This dict is extensible if other suburbs need similar treatment.

## Data flow

```
ABS SAL 2021         Reddit r/melbourne          melbz.com.au
shapefile            (per-suburb + meta)         (curated guides)
    │                       │                          │
    ▼                       ▼                          ▼
data/boundaries.geojson  data/raw/{suburb}.json   data/raw/melbz/{suburb}.json
                         data/raw/_meta.json
                                  │                          │
                                  └─────► DeepSeek ◄─────────┘
                                          summarise
                                              │
                                              ▼
                                         data/suburbs.json
                                              │
                                              ▼
                                          Dash app
                                       + assets/mascots/{suburb}.png
                                       + assets/flags/{suburb}.png
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
├── app.py                  # Dash app entry point
├── data/
│   ├── boundaries.geojson  # 75 suburb polygons, WGS84
│   ├── suburb_list.txt     # canonical suburb names (one per line)
│   ├── suburbs.json        # vibes/tags/quotes per suburb (output of summarize)
│   └── raw/                # gitignored: ABS zip + per-suburb Reddit caches + _meta.json
└── scrape/
    ├── __init__.py
    ├── boundaries.py       # downloads ABS SAL 2021, filters, writes geojson
    ├── reddit.py           # Reddit JSON-endpoint scraper (per-suburb + --meta)
    ├── melbz.py            # melbz.com.au profile scraper
    ├── summarize.py        # DeepSeek summariser, multi-source corpus
    ├── imagegen.py         # pluggable image gen (Pollinations / Replicate)
    ├── mascots.py          # generate mascot images for suburbs
    ├── flags.py            # generate flag images (PIL bands + AI emblem)
    └── refresh.py          # orchestrator: boundaries → scrape → summarise
```

## Running things

```sh
uv sync                                              # install deps

# One-off boundary build (only if scope changes)
uv run python -m scrape.boundaries

# Reddit scrape
uv run python -u -m scrape.reddit Fitzroy            # single suburb (test)
uv run python -u -m scrape.reddit --all              # all 75 suburbs (~1-2h, polite mode)
uv run python -u -m scrape.reddit --all --force      # re-scrape ignoring cache
uv run python -u -m scrape.reddit --meta             # cross-suburb meta-thread sweep (planned)

# Summarise via DeepSeek
uv run python -u -m scrape.summarize Fitzroy        # single, prints JSON
uv run python -u -m scrape.summarize --all          # everything
uv run python -u -m scrape.summarize --all --force  # ignore existing entries

# Both stages in one go
uv run python -u -m scrape.refresh                  # full pipeline

uv run python -u app.py                              # http://localhost:8050
```

Always pass `python -u` (unbuffered) for backgrounded runs — otherwise stdout is
buffered and you can't see progress until the process exits.

## Configuration / secrets

`.env` (copy from `.env.example`):
- `DEEPSEEK_API_KEY` — required for summarisation
- `REDDIT_USER_AGENT` — descriptive UA with username, e.g.
  `melb-map (hobby project) by u/yourname`. No Reddit API keys needed.

`.env` is gitignored. `.env.example` should never contain real secrets.

## Key decisions

- **Inner/middle Melbourne only** (~75 suburbs). Outer/growth-corridor suburbs
  have thinner Reddit coverage. Suburb list is hardcoded in
  `scrape/boundaries.py::TARGET_SUBURBS` and frozen to `data/suburb_list.txt` for
  downstream stability.
- **ABS SAL 2021 boundaries** (official, free). The 99 MB shapefile zip is
  downloaded once and cached under `data/raw/`. Filter is by suburb name, not
  LGA — cleaner since SAL boundaries straddle LGA borders.
- **`choropleth_map`** (Plotly's newer maplibre-based version, no Mapbox token
  needed).
- **Reddit JSON endpoints, not PRAW**. Reddit's app-registration captcha has
  been broken in many users' browsers for years; using public JSON endpoints
  sidesteps this with no functional loss at our scale.
- **DeepSeek over Anthropic** for summarisation. ~10x cheaper, OpenAI-compatible
  API, quality is plenty for this task.
- **Polite scraping**: random jitter (3-6s per request, 10-25s between
  suburbs), honours `Retry-After` on 429, hard-bails on 401/403, exponential
  backoff on errors, warmup hit on session start. Full batch ~1-2h instead of
  ~30 min, but firmly polite-citizen territory.
- **Click → side panel** via Dash callback on `clickData`. Hover via
  `customdata` in the choropleth trace.

## Mascots & flags

Each suburb has TWO illustrated identities:
- **Mascot**: an absurd anthropomorphic character (Warren P. Toadfish, ESQ. for
  Elwood) — appears in the side panel when a suburb is clicked.
- **Flag**: a vexillographic civic flag (2-3 colours + single emblem) — appears
  as a centroid overlay on the map for at-a-glance suburb identity.

LLM generates text descriptions for both. Pollinations.ai (`flux` model, free,
no API key, requires `User-Agent` header) renders the actual cartoon images,
which are saved to `assets/mascots/{suburb}.{jpg|png}` and
`assets/flags/{suburb}.{jpg|png}` respectively. Dash serves them from `/assets/`.

## Map rendering: flag overlay (Option A)

We chose **centroid flag overlay** over literal pattern-fill polygons. Rationale:

- Plotly's `choropleth_map` doesn't support pattern/image fills natively. Literal
  fill would require migrating to dash-leaflet or custom MapLibre + rebuilding all
  the hover/click/side-panel callbacks. Day-plus refactor.
- Centroid overlay preserves all interactivity (hover, click, side panel) and
  gets a flag-per-suburb visual immediately, so we can judge if the *concept*
  works before committing to the bigger refactor.

How it's done:
- Compute polygon centroid + bounding box via `shapely`
- For each suburb with a flag PNG, add a `map.layers` entry with `sourcetype=image`,
  positioned at corner coords inside the polygon's bbox (a small rectangle near
  the centroid, sized to keep the flag legible without overlapping neighbours)
- Polygon fill stays category-coloured but at low opacity (~0.3) so flags pop

If we later decide we want literal fill (each polygon textured with its flag),
the migration target is **dash-leaflet** with SVG `<pattern>` defs applied via
`fillPattern`. Documented but not implemented.

## Status (working notes)

- [x] Scaffold (pyproject, `.env.example`, dirs)
- [x] `scrape/boundaries.py` — produces `data/boundaries.geojson` (75 suburbs)
      and `data/suburb_list.txt`
- [x] `scrape/reddit.py` — polite JSON-endpoint scraper, polite mode (random
      jitter, honors Retry-After, hard-bails on 401/403). `--meta` mode scrapes
      cross-suburb threads ("best/worst suburb", "your suburb in 3 words", etc.)
      to `data/raw/_meta.json` (128 threads, 21,534 comments collected).
- [x] `scrape/summarize.py` — DeepSeek summariser, byte-identical system prompt
      for auto cache. Schema: tags (7-12), vibe (2-3 sentences), lore (3-6),
      food_and_drink (2-4), quotes (3-5), primary_category, mascot {name,
      tagline, description, image_prompt}, flag {colors, emblem, style,
      description, image_prompt}. Joins meta-mention comments into the corpus
      before summarising.
- [x] `scrape/refresh.py` — orchestrator
- [x] `app.py` — choropleth + hover tooltip + click → side panel rendering all
      rich fields including mascot image (if `assets/mascots/{suburb}.{jpg|png}`
      exists) and mascot bio.
- [x] All 75 suburbs summarised with rich schema (tags, vibe, lore,
      food_and_drink, quotes, mascot, flag).
- [x] `scrape/imagegen.py` — pluggable image gen (Pollinations / Replicate).
      Replicate path uses `black-forest-labs/flux-dev`, with self-throttling
      to stay under the 6/min low-credit cap (12s minimum between calls).
- [x] `scrape/mascots.py` — fetches mascot image_prompt from suburbs.json,
      sanitises (strips drug/contraband references that hit safety filters),
      generates via configured backend.
- [x] `scrape/flags.py` — hybrid flag generator: PIL draws bands from
      `flag.{colors, style}`, image gen supplies black silhouette emblem on
      white, chroma key strips background, composites onto bands.
- [x] **11 mascots + flags rendered** so far: Elwood (Pollinations) + 10
      random suburbs (Replicate FLUX dev): Carlton North, Ascot Vale, Kew,
      Hampton, Flemington, Caulfield South, Carlton, Camberwell, Ripponlea,
      Balaclava. Cost: ~$0.50.
- [x] **MELBZ.com.au scraper** — all 75 suburbs profiled, 461 sections,
      710 KB of curated content. Multi-source summariser ingests it
      alongside Reddit + meta-mentions.
- [x] **Melbourne CBD aliasing** — search/scan terms swap "Melbourne" with
      ["Melbourne CBD", "the CBD", "city centre"]. Mechanism extensible via
      `SUBURB_SEARCH_ALIASES` and `SUBURB_MENTION_TERMS` dicts.
- [ ] Re-summarise all 75 with the multi-source corpus (currently running)
- [ ] Auto-generate mascot + flag images for remaining ~64 suburbs
      (~$3, ~25 min) — pending user go-ahead
- [ ] Polish: palette, panel styling, README

## Risks / things to watch

- **Reddit content thinness** for less-discussed suburbs (e.g. Aberfeldie). The
  meta-thread scrape directly addresses this — most suburbs get named in
  cross-suburb discussions even when they have few dedicated posts.
- **Tone calibration**: r/melbourne can skew negative/edgy. Summariser prompt
  explicitly requires "playful, observational, not mean-spirited". Eyeball
  output on a few suburbs before trusting the full batch.
- **DeepSeek cost**: 75 suburbs × richer prompt with prompt caching ≈ ~20¢
  for the full re-summarise. Negligible.
- **Reddit rate limit**: ~30 req/min unauthenticated. Polite scraper averages
  ~10 req/min, so well under threshold. 4 isolated 429s in the full 75-suburb
  run, all resolved by backoff.
