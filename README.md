# Melbourne Suburb Quirks

**Live: [jay-stein.github.io/melb_map](https://jay-stein.github.io/melb_map/)**

An interactive map of 133 Melbourne suburbs — the things locals joke about, argue over, and recognise instantly. Inspired by [hoodmaps.com](https://hoodmaps.com) but sourced from real community data, not generic demographics.

**[🎮 Play Suburble](https://jay-stein.github.io/melb_map/play.html)** — guess the mystery suburb from its shape, daily.

**[🕵️ Play Streetwise](https://jay-stein.github.io/melb_map/streets.html)** — name the person or thing a suburb's streets are named after; five clues, one suburb.

---

## What it is

Click any suburb on the map to get:
- **Vibe** — 2–3 sentences of character (what kind of place it actually is)
- **Tags** — specific, funny, observational phrases ("$9 oat lattes", "tradies in hi-vis at 6am")
- **Lore** — the things locals tell newcomers; mix of present-day and historical oddities
- **Origins & language** — over-represented birthplaces, languages, and ancestry vs the Greater Melbourne average, with flags. Powered by ABS 2021 Census.
- **History** — founding, etymology, defining migration wave
- **Reddit quotes** — verbatim lines from r/melbourne

## Sources

| Source | What it contributes |
|--------|-------------------|
| [r/melbourne](https://reddit.com/r/melbourne) | Per-suburb posts + comments; 128 cross-suburb "best/worst suburb" meta-threads (21k comments) |
| [melbz.com.au](https://melbz.com.au) | Curated suburb guides — highest signal for character and facts |
| [eMelbourne](https://www.emelbourne.net.au) | University of Melbourne suburb history encyclopaedia |
| [ABS SAL 2021](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3) | Official suburb boundaries + 2021 census demographic data |
| [DeepSeek](https://api.deepseek.com) | Summarisation (OpenAI-compatible API, ~10× cheaper than GPT-4) |

## Stack

- **[Plotly Dash](https://dash.plotly.com)** — map + click → side panel
- **[uv](https://github.com/astral-sh/uv)** — dependency management
- **DeepSeek** (`deepseek-chat` via `api.deepseek.com/v1`) — LLM summarisation
- No Reddit API keys — uses public HTML endpoints, polite scraping (~10 req/min)

## Setup

```sh
git clone https://github.com/jay-stein/melb_map.git
cd melb_map
cp .env.example .env          # fill in DEEPSEEK_API_KEY + REDDIT_USER_AGENT
uv sync
uv run python app.py          # http://localhost:8050
```

The map loads from pre-built `data/suburbs.json` and `data/boundaries.geojson` — no scraping needed to run the app.

To export a static site for hosting (no server needed):
```sh
uv run python export_site.py  # writes docs/ — runs on GitHub Pages or any static host
```

## Rebuild the data

```sh
# Scrape Reddit (per-suburb + cross-suburb meta-threads)
uv run python -u -m scrape.reddit --all          # ~1-2h, polite mode
uv run python -u -m scrape.reddit --meta         # cross-suburb threads

# Scrape melbz.com.au
uv run python -u -m scrape.melbz --all

# Summarise via DeepSeek (~20¢ for all 133 suburbs)
uv run python -u -m scrape.summarize --all

# Census demographics (ABS 2021, no API key needed)
uv run --with openpyxl --with pyshp python -m scrape.census --all

# Or run everything at once
uv run python -u -m scrape.refresh
```

## Suburble

A daily Wordle-style game at `/play`. Guess the mystery Melbourne suburb from its decontextualised map silhouette. Each wrong guess tells you how far away you were and in which direction. Six guesses, shareable emoji grid.

## Streetwise

A street-theme guessing game at `/streets` (and `streets.html` on the static site). One suburb per game, five rounds. Each round shows a clue about the person or thing one of the suburb's streets is named after — a quote, a riddle, or a description — and you pick the namesake from three options (two attempts). Hints cost points but reveal a tidbit. After five streets, the theme and the suburb are revealed.

The corpus (`data/street_themes.json`, 46 suburbs / 65 puzzles) is built by `scrape/street_themes.py`:

1. **Fetch** — downloads the [BBBike Melbourne OSM extract](https://download.bbbike.org/osm/bbbike/Melbourne/Melbourne.osm.pbf) (~88 MB, cached in `data/raw/`), attributes every named street to our 133 ABS suburb polygons via pyosmium + shapely.
2. **Match** — zero-cost keyword + suffix-pattern theme matching (poets, composers, gems, the Glenroy "-ana" ANA estate, the Coburg North camera estate…).
3. **Discover** — DeepSeek finds *novel* themes in suburbs the dictionary missed (e.g. Ashburton's WWII battles, Port Melbourne's aircraft, Mernda's Renaissance artists).
4. **Clues** — DeepSeek writes the five rounds per theme: clue, namesake, two same-category distractors, tidbit, and a 3–4 sentence explainer. Street names are always verified against the OSM attribution — the LLM never invents streets.

```
uv run python -u -m scrape.street_themes --all   # full rebuild (~10 min)
```

## Project layout

```
melb_map/
├── app.py                  # Dash app + map + panel rendering
├── suburble.py             # Daily suburb guessing game
├── streets.py              # Streetwise street-theme guessing game
├── scrape/
│   ├── reddit.py           # Reddit HTML scraper (polite, no API key)
│   ├── melbz.py            # melbz.com.au scraper
│   ├── summarize.py        # DeepSeek summariser
│   ├── census.py           # ABS census LQ quirks + flag ISO codes
│   ├── boundaries.py       # ABS SAL shapefile → boundaries.geojson
│   ├── street_themes.py    # Streetwise corpus (OSM pbf → themes → clues)
│   ├── imagegen.py         # Pluggable image gen (Pollinations / Replicate)
│   ├── mascots.py          # Suburb mascot image generation
│   └── refresh.py          # Full pipeline orchestrator
├── data/
│   ├── boundaries.geojson  # 133 suburb polygons (WGS84)
│   ├── suburbs.json        # Vibes, tags, lore, census, mascots per suburb
│   ├── street_themes.json  # Streetwise corpus (46 suburbs, 65 puzzles)
│   └── suburb_list.txt     # Canonical suburb names
└── assets/
    ├── flags/              # 51 local flag SVGs (CORS-safe)
    └── mascots/            # Generated suburb mascot images
```

## Environment variables

Copy `.env.example` to `.env`:

```
DEEPSEEK_API_KEY=...        # required for summarisation
REDDIT_USER_AGENT=...       # e.g. "melb-map (hobby project) by u/yourname"
IMAGE_GEN_PROVIDER=...      # pollinations (free) or replicate (~$0.025/image)
REPLICATE_API_TOKEN=...     # required if IMAGE_GEN_PROVIDER=replicate
```

## Coverage

133 inner, middle, and outer Melbourne suburbs. Outer suburbs were added for completeness but have thinner Reddit coverage — the cross-suburb meta-thread sweep (128 threads) ensures most suburbs appear even without dedicated posts.
