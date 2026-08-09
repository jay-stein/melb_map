"""Build the Streetwise game corpus: data/street_themes.json.

Each themed suburb in the corpus is a 5-round puzzle. A round presents a clue
about the person/thing one of the suburb's streets is named after; the player
picks the namesake from 3 options; solving reveals the street card and a short
explainer; after 5 rounds the theme and suburb are revealed.

Stages (run with `uv run python -u -m scrape.street_themes [flags]`):

  --fetch     download the BBBike Melbourne .osm.pbf once (cached in
              data/raw/, gitignored), parse named highways with pyosmium and
              attribute each to our 133 ABS suburb polygons via
              point-in-polygon; cache per-suburb street lists.
  --match     Layer 1: zero-cost keyword + suffix-pattern theme matching
              against the curated theme dictionary. No API key needed.
  --discover  Layer 2a: DeepSeek theme discovery for suburbs Layer 1 missed
              (surfaces NOVEL themes — e.g. the Coburg North camera estate).
  --clues     Layer 2b: DeepSeek clue generation (rounds) for themed suburbs.
  --build     assemble data/street_themes.json from the stage outputs.
  --all       fetch → match → discover → clues → build

Data flow:
  data/raw/Melbourne.osm.pbf  (gitignored, downloaded once)
  data/raw/streets.json       (gitignored: {suburb: [street names]})
  data/raw/street_match.json  (gitignored: Layer 1 + 2a theme matches)
  data/raw/street_clues/{suburb}.json  (gitignored: Layer 2b rounds)
  data/street_themes.json     (committed game corpus)

Street names are ALWAYS taken verbatim from the OSM attribution — the LLM
never invents streets (verified hard against the attribution lists).
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import shapely.geometry
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PBF_PATH = RAW_DIR / "Melbourne.osm.pbf"
PBF_URL = "https://download.bbbike.org/osm/bbbike/Melbourne/Melbourne.osm.pbf"
STREETS_JSON = RAW_DIR / "streets.json"
MATCH_JSON = RAW_DIR / "street_match.json"
CLUES_DIR = RAW_DIR / "street_clues"
OUT_JSON = ROOT / "data" / "street_themes.json"
BOUNDARIES_JSON = ROOT / "data" / "boundaries.geojson"

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# A theme only counts if at least this many streets in one suburb follow it
# (the game needs 5 rounds per suburb, so clusters under this are unplayable).
MIN_CLUSTER = 5
ROUNDS_PER_SUBURB = 5

# Road-type tokens stripped to get the "base name" (Menana Road → "menana").
ROAD_SUFFIXES = {
    "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "court",
    "ct", "crescent", "cres", "walk", "lane", "ln", "grove", "gr", "way",
    "place", "pl", "boulevard", "bvd", "circuit", "cct", "close", "terrace",
    "parade", "park", "loop", "mews", "square", "sq", "view", "esplanade",
    "trail", "path", "track", "bend", "circle", "circ", "crest", "gate",
    "heights", "hill", "pde", "rise", "row", "vista", "courtyard", "promenade",
    "arcade", "staircase", "steps", "north", "south", "east", "west", "upper",
    "lower",
}


def base_name(full: str) -> str:
    """Tennyson Street → tennyson; Menana Road → menana."""
    toks = full.replace("-", " ").split()
    while toks and toks[-1].lower() in ROAD_SUFFIXES:
        toks.pop()
    return " ".join(toks).lower()


# --------------------------------------------------------------------------- #
# Layer 1: curated theme dictionary (keywords match the base name)
# --------------------------------------------------------------------------- #
# Each theme: (label, description for the LLM, [keywords]). Keywords are
# matched case-insensitively as substrings of the street base name. Cluster
# size >= MIN_CLUSTER is required before a theme counts, so a stray "Scott
# Street" can't manufacture a poets theme on its own.
THEME_RULES: list[tuple[str, str, list[str]]] = [
    ("Literary Poets", "streets named after famous poets and writers",
     ["byron", "tennyson", "keats", "milton", "shelley", "burns", "browning",
      "coleridge", "dante", "dickens", "dryden", "goldsmith", "hood", "joyce",
      "lawson", "lytton", "moore", "morres", "ruskin", "scott", "southey",
      "spenser", "thackeray", "wordsworth", "kingsley", "kendall", "chaucer",
      "bronte", "rosetti", "herrick", "poe", "shakespeare", "austen",
      "kipling", "carlyle", "boccaccio", "petrarch", "virgil", "horace",
      "homer", "frost", "whitman", "emerson", "thoreau", "hawthorne",
      "whittier", "longfellow", "dickinson", "o'hara"]),
    ("Classical Composers", "streets named after famous composers",
     ["mozart", "beethoven", "chopin", "bach", "schubert", "vivaldi", "wagner",
      "handel", "liszt", "brahms", "haydn", "puccini", "verdi", "mendelssohn",
      "strauss", "debussy", "ravel", "tchaikovsky", "grieg", "sibelius",
      "dvorak", "rossini", "bellini", "bizet", "paganini"]),
    ("Crimean War", "streets named after Crimean War battles, fortresses and figures",
     ["alma", "inkerman", "balaclava", "sebastopol", "redan", "malakoff",
      "cardigan", "raglan", "kertch", "silistria", "kars", "otchakoff"]),
    ("Champion Racehorses", "streets named after famous Melbourne Cup winners and racehorses",
     ["phar lap", "carbine", "makybe", "saintly", "subzero", "bel esprit",
      "kingston town", "jezabeel", "archaemenid", "risorgimento", "winnings",
      "amounis", "poseidon", "cavalcade", "tulloch", "rancho"]),
    ("Precious Gemstones", "streets named after gemstones and minerals",
     ["diamond", "ruby", "emerald", "sapphire", "topaz", "opal", "amethyst",
      "jade", "pearl", "garnet", "onyx", "turquoise", "agate", "beryl",
      "zircon", "lapis"]),
    ("Native Flora", "streets named after native Australian plants and trees",
     ["acacia", "banksia", "boronia", "grevillea", "hakea", "melaleuca",
      "waratah", "wattle", "casuarina", "correa", "dianella", "lomandra",
      "sheoak", "boobialla", "kurrajong", "murnong", "bottlebrush",
      "tea tree", "eucalyptus", "callistemon", "ti tree"]),
    ("Astronomy & Space", "streets named after stars, constellations and space concepts",
     ["galaxy", "orion", "sirius", "pegasus", "polaris", "cassiopeia",
      "comet", "centaurus", "nebula", "aurora", "eclipse", "zenith",
      "cosmos", "meteor", "nova", "astral", "stellar", "andromeda",
      "cygnus", "lyra", "vega", "mars", "saturn", "jupiter", "mercury",
      "venus", "pluto"]),
    ("Greco-Roman Mythology", "streets named after ancient gods and mythical figures",
     ["apollo", "neptune", "vulcan", "minerva", "olympus", "athena", "zeus",
      "hermes", "diana", "juno", "triton", "achilles", "hercules", "atlas",
      "titan", "odyssey", "nike", "morpheus", "medusa", "psyche", "eros",
      "thor", "odin", "loki", "valkyrie", "asgard", "midgard", "norse"]),
    ("Arthurian Legend", "streets named after Camelot and the Knights of the Round Table",
     ["camelot", "guinevere", "lancelot", "excalibur", "merlin", "pendragon",
      "avalon", "galahad", "mordred", "gawain", "percival", "tristan"]),
    ("Aviation Pioneers & Aircraft", "streets named after aviation pioneers, airlines and aircraft",
     ["avro", "fokker", "catalina", "ansett", "kingsford", "hargrave",
      "hawker", "spitfire", "mustang", "boeing", "douglas", "lockheed",
      "vickers", "ulm", "airspeed", "aviator", "aerodrome", "lancaster",
      "hurricane", "pioneer"]),
    ("Elite English Schools", "streets named after famous English schools and universities",
     ["eton", "harrow", "rugby", "cambridge", "oxford", "trinity", "balliol",
      "winchester", "charterhouse"]),
    ("British Towns & Rivers", "streets named after English towns, counties and rivers",
     ["thames", "severne", "trent", "mersey", "arundel", "chichester",
      "worthing", "sussex", "norfolk", "suffolk", "essex", "hampstead",
      "kensington", "wimbledon", "ealing", "fulham", "dorset", "somerset",
      "gloucester", "warwick", "wessex", "anglia", "chelsea", "balham"]),
    ("Golf Courses", "streets named after famous golf courses and golfing legends",
     ["augusta", "st andrews", "pebble", "troon", "carnoustie", "wentworth",
      "muirfield", "lytham", "gleneagles", "birkdale", "sunningdale"]),
    ("Olympic Games", "streets named after Olympic champions and games motifs",
     ["olympic", "marathon", "flack", "landy", "cuthbert"]),
    ("Prime Ministers", "streets named after Australian prime ministers and federal leaders",
     ["barton", "deakin", "hughes", "menzies", "curtin", "chifley", "holt",
      "whitlam", "gorton", "scullin", "fadden", "lyons", "bruce", "fisher",
      "watson"]),
    ("Camera & Photography", "streets named after cameras, photography and optics",
     ["aperture", "spectrum", "snapshot", "focus", "cyan", "lens", "shutter",
      "exposure", "zoom", "flash", "camera", "image", "photography", "pixel",
      "portrait", "kodak", "viewfinder"]),
    ("Viticulture & Wine", "streets named after grape varieties and winemaking",
     ["shiraz", "merlot", "chardonnay", "cabernet", "pinot", "sauvignon",
      "vintage", "viognier", "riesling", "semillon", "malbec", "grenache",
      "sangiovese", "verdelho", "muscat"]),
]

# Suffix-pattern themes: (label, description, suffix, min cluster). Matched
# against the BASE name (Glenroy's ANA estate: Menana, Tarana, Warana...).
SUFFIX_RULES: list[tuple[str, str, str, int]] = [
    ("ANA Aviation Estate",
     "an estate where every street name ends in 'ana' — built for workers of "
     "Australian National Airways (ANA) based at Essendon Airport", "ana", 5),
]


# --------------------------------------------------------------------------- #
# stage 1: fetch + attribution
# --------------------------------------------------------------------------- #
def fetch_pbf() -> Path:
    if PBF_PATH.exists():
        print(f"[fetch] pbf cached: {PBF_PATH} ({PBF_PATH.stat().st_size / 1e6:.1f} MB)")
        return PBF_PATH
    import requests
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] downloading {PBF_URL}")
    with requests.get(PBF_URL, stream=True, timeout=600) as r:
        r.raise_for_status()
        with PBF_PATH.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    print(f"[fetch] saved {PBF_PATH} ({PBF_PATH.stat().st_size / 1e6:.1f} MB)")
    return PBF_PATH


def attribute_streets() -> dict[str, list[str]]:
    """Parse the pbf and attribute every named highway to our 133 ABS
    polygons via its way centroid. Cached in data/raw/streets.json."""
    if STREETS_JSON.exists():
        print(f"[fetch] using cached attribution {STREETS_JSON}")
        return json.loads(STREETS_JSON.read_text(encoding="utf-8"))

    import osmium

    class StreetHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.streets: list[tuple[str, float, float]] = []

        def way(self, w):
            if "highway" in w.tags and "name" in w.tags:
                coords = [(n.lon, n.lat) for n in w.nodes]
                if coords:
                    self.streets.append((
                        w.tags["name"],
                        sum(c[0] for c in coords) / len(coords),
                        sum(c[1] for c in coords) / len(coords),
                    ))

    print("[fetch] parsing pbf with pyosmium (locations=True)...")
    handler = StreetHandler()
    handler.apply_file(str(PBF_PATH), locations=True)
    print(f"[fetch] {len(handler.streets)} named highway ways")

    geojson = json.loads(BOUNDARIES_JSON.read_text(encoding="utf-8"))
    from shapely.prepared import prep
    polys = [(f["properties"]["suburb"], prep(shapely.geometry.shape(f["geometry"])))
             for f in geojson["features"]]
    print(f"[fetch] {len(polys)} suburb polygons")

    per_suburb: dict[str, set[str]] = {}
    for name, lon, lat in handler.streets:
        pt = shapely.geometry.Point(lon, lat)
        for suburb, pp in polys:
            if pp.contains(pt):
                per_suburb.setdefault(suburb, set()).add(name)
                break

    out = {s: sorted(names) for s, names in per_suburb.items()}
    STREETS_JSON.write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    total = sum(len(v) for v in out.values())
    print(f"[fetch] attributed {total} streets across {len(out)} suburbs -> {STREETS_JSON}")
    return out


# --------------------------------------------------------------------------- #
# stage 2: Layer 1 matching
# --------------------------------------------------------------------------- #
def _match_keywords(base: str, keywords: list[str]) -> bool:
    return any(k in base for k in keywords)


def match_themes(suburb: str, streets: list[str]) -> list[dict]:
    """Return [{theme, desc, streets}] for every rule that clears the cluster
    threshold, most streets first."""
    bases = {s: base_name(s) for s in streets}
    found: list[dict] = []
    for label, desc, keywords in THEME_RULES:
        hits = [s for s, b in bases.items()
                if b and _match_keywords(b, keywords)]
        if len(hits) >= MIN_CLUSTER:
            found.append({"theme": label, "desc": desc, "streets": sorted(hits)})
    for label, desc, suffix, min_n in SUFFIX_RULES:
        hits = [s for s, b in bases.items() if b and b.endswith(suffix)]
        if len(hits) >= min_n:
            found.append({"theme": label, "desc": desc, "streets": sorted(hits)})
    found.sort(key=lambda d: len(d["streets"]), reverse=True)
    return found


def run_match(streets_by_suburb: dict[str, list[str]]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for suburb, streets in sorted(streets_by_suburb.items()):
        matches = match_themes(suburb, streets)
        if matches:
            results[suburb] = matches
    MATCH_JSON.write_text(json.dumps(results, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[match] {len(results)} suburbs with a Layer-1 theme")
    for suburb, matches in sorted(results.items()):
        for m in matches:
            print(f"  {suburb:24s} {m['theme']:28s} {len(m['streets'])} streets")
    return results


# --------------------------------------------------------------------------- #
# stage 3: Layer 2a DeepSeek discovery
# --------------------------------------------------------------------------- #
DISCOVER_SYSTEM = """You analyse Melbourne suburb street names for a trivia game.

I give you a suburb and its full street-name list. Detect whether a SUBSTANTIAL
subset follows ONE clear naming theme (named after poets, composers, Greek
gods, aircraft, British towns, gemstones, US presidents, a pattern estate,
etc.).

RULES:
- Only report a theme if at least 5 streets clearly follow it.
- It must be a deliberate naming pattern, not a coincidence (5 streets named
  after different US presidents is a theme; 5 streets all starting with 'M'
  is not).
- Return matching street names VERBATIM, exactly as written in the input.
- If there is no theme, return theme: null.

Output STRICT JSON, one object:
{"theme": "short theme label" or null, "streets": ["verbatim names"] or []}"""


def discover(client: OpenAI, suburb: str, streets: list[str]) -> dict | None:
    payload = f"Suburb: {suburb}\nStreets:\n" + "\n".join(streets)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": DISCOVER_SYSTEM},
                  {"role": "user", "content": payload}],
        temperature=0.2,
        max_tokens=800,
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    data = json.loads(text)
    theme = (data.get("theme") or "").strip()
    streets_out = [s.strip() for s in data.get("streets") or []]
    # hard verify: every reported street must literally exist in the suburb
    known = set(streets)
    streets_out = [s for s in streets_out if s in known]
    if not theme or len(streets_out) < MIN_CLUSTER:
        return None
    return {"theme": theme, "desc": theme, "streets": sorted(streets_out)}


def run_discover(client: OpenAI, streets_by_suburb: dict[str, list[str]],
                 matched: dict[str, dict]) -> dict[str, dict]:
    """Layer 2a on suburbs Layer 1 missed. Persists after EVERY suburb (empty
    list = checked, no theme) so a crash or re-run resumes without rework."""
    pending = [s for s in sorted(streets_by_suburb) if s not in matched]
    print(f"[discover] {len(pending)} suburbs without a Layer-1 theme")
    for i, suburb in enumerate(pending, 1):
        try:
            hit = discover(client, suburb, streets_by_suburb[suburb])
        except Exception as e:
            print(f"[discover] {suburb} failed: {e}")
            continue
        matched[suburb] = [hit] if hit else []
        # persist incrementally — a crash must not lose completed work
        MATCH_JSON.write_text(json.dumps(matched, indent=1, ensure_ascii=False),
                              encoding="utf-8")
        if hit:
            print(f"[discover] {suburb}: {hit['theme']} ({len(hit['streets'])} streets)")
        else:
            print(f"[discover] {suburb}: none")
    themed = {s: v for s, v in matched.items() if v}
    print(f"[discover] {len(themed)} themed suburbs total -> {MATCH_JSON}")
    return themed


# --------------------------------------------------------------------------- #
# stage 4: Layer 2b DeepSeek clue generation
# --------------------------------------------------------------------------- #
CLUES_SYSTEM = """You write quiz content for "Streetwise", a game where players guess a
Melbourne suburb by identifying what its streets are named after. Each round
shows a CLUE about the person/thing one street honours; the player picks the
namesake from 3 options.

I give you: the suburb, its theme, the theme description, the suburb's full
street list, and the streets that follow the theme. You create 5 rounds, each
about ONE theme street (pick the 5 with the most FAMOUS namesakes; order the
rounds most-famous first).

For each round:
- "street": the street name, verbatim from the theme street list.
- "clue": 1-2 sentences — a famous real quote, a riddle, or a description that
  identifies the namesake. Must NOT contain the street's own name, the suburb
  name, or the theme's key word spelled out (e.g. for a poets theme never
  write "this poet..." — the category is already known; identify WHICH one).
- "namesake": short label of the person/thing (e.g. "Alfred, Lord Tennyson",
  "The Spitfire", "Diamond").
- "options": exactly 3 strings: the correct namesake + 2 plausible
  same-category WRONG candidates. Prefer namesakes of OTHER theme streets
  from the same suburb as distractors when suitable.
- "tidbit": one fascinating, true one-liner about the namesake.
- "explainer": 3-4 sentences of interesting, true background about the
  person/thing — for people: life, era, achievements, a fun angle. For
  objects/concepts (planes, gems, gods, airlines): what it is, history,
  notable facts. Max 4 sentences. Never mention the suburb name.

Top level:
- "background": 1-2 sentence opener about the suburb's theme, WITHOUT naming
  the suburb (e.g. "Victorian estates often borrowed street names from
  English literature. Every street here is named after a poet.").
- "reveal": one punchy line that announces the answer and names the suburb
  (e.g. "It was Elwood — where every street is a poet.").

For PATTERN or ESTATE themes (e.g. every street ends in '-ana' because the
estate was built for an airline): the namesake is the concept the estate
commemorates (the airline/company). Make each round a DIFFERENT interesting
fact about that concept so players learn something new each round; the options
are candidate concepts.

RULES: facts and quotes must be real and verifiable. No made-up quotes. The
suburb name may ONLY appear in "reveal". Output STRICT JSON:
{"background": str, "reveal": str,
 "rounds": [{"street": str, "clue": str, "namesake": str,
             "options": [str, str, str], "tidbit": str, "explainer": str}]}"""


def _json_from(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text)


def gen_clues(client: OpenAI, suburb: str, match: dict) -> dict | None:
    streets = [s for s in match["streets"]]
    payload = (
        f"Suburb: {suburb}\n"
        f"Theme: {match['theme']}\n"
        f"Theme description: {match['desc']}\n"
        f"Streets that follow the theme (choose 5):\n"
        + "\n".join(streets)
    )
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[{"role": "system", "content": CLUES_SYSTEM},
                  {"role": "user", "content": payload}],
        temperature=0.7,
        max_tokens=4000,
    )
    data = _json_from(resp.choices[0].message.content)

    # verify + normalise: only theme streets, 5 rounds, 3 options each
    known = set(streets)
    rounds = []
    for r in data.get("rounds") or []:
        street = (r.get("street") or "").strip()
        if street not in known:
            continue
        namesake = (r.get("namesake") or "").strip()
        clue = (r.get("clue") or "").strip()
        explainer = (r.get("explainer") or "").strip()
        tidbit = (r.get("tidbit") or "").strip()
        options = [str(o).strip() for o in (r.get("options") or [])
                   if str(o).strip()]
        if not (namesake and clue and explainer and len(options) >= 2):
            continue
        if namesake not in options:
            options.insert(0, namesake)
        options = options[:3]
        rng = random.Random(f"{suburb}|{street}")
        rng.shuffle(options)
        rounds.append({
            "street": street, "namesake": namesake, "clue": clue,
            "options": options, "tidbit": tidbit, "explainer": explainer,
        })
        if len(rounds) >= ROUNDS_PER_SUBURB:
            break

    if len(rounds) < ROUNDS_PER_SUBURB:
        # deterministic fallback: fill from remaining theme streets
        used = {r["street"] for r in rounds}
        fallback = [s for s in streets if s not in used]
        for s in fallback[: ROUNDS_PER_SUBURB - len(rounds)]:
            rounds.append({
                "street": s, "namesake": s.split()[0], "clue": s,
                "options": [s.split()[0]], "tidbit": "", "explainer": s,
            })

    return {
        "theme": match["theme"],
        "background": (data.get("background") or "").strip(),
        "reveal": (data.get("reveal") or f"It was {suburb}.").strip(),
        "rounds": rounds,
    }


def _theme_slug(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label.lower()).strip("_")


def run_clues(client: OpenAI, matches: dict[str, dict]) -> dict[str, dict]:
    """Generate one puzzle per theme (multi-themed suburbs get several; the
    game picks one at random). Cached per suburb+theme."""
    CLUES_DIR.mkdir(parents=True, exist_ok=True)
    for suburb, match_list in sorted(matches.items()):
        for match in match_list:
            if len(match["streets"]) < ROUNDS_PER_SUBURB:
                continue
            out_path = CLUES_DIR / f"{suburb.replace(' ', '_')}__{_theme_slug(match['theme'])}.json"
            if out_path.exists():
                print(f"[clues] {suburb} / {match['theme']}: cached")
                continue
            try:
                data = gen_clues(client, suburb, match)
            except Exception as e:
                print(f"[clues] {suburb} / {match['theme']} failed: {e}")
                continue
            out_path.write_text(json.dumps(data, indent=1, ensure_ascii=False),
                                encoding="utf-8")
            print(f"[clues] {suburb} / {match['theme']}: {len(data['rounds'])} rounds")


def build(clues: dict[str, dict]) -> None:
    """Assemble the corpus from the per-suburb__theme clue caches: each suburb
    gets a list of puzzles (one per theme) and the game picks one at random."""
    by_suburb: dict[str, list[dict]] = {}
    for path in sorted(CLUES_DIR.glob("*__*.json")):
        suburb, _, _ = path.stem.partition("__")
        suburb = suburb.replace("_", " ")
        data = json.loads(path.read_text(encoding="utf-8"))
        if len(data.get("rounds") or []) < ROUNDS_PER_SUBURB:
            print(f"[build] skipping {path.name}: {len(data.get('rounds') or [])} rounds")
            continue
        by_suburb.setdefault(suburb, []).append(data)
    if not by_suburb:
        print("[build] no clue caches found - run --clues first")
        return
    corpus = {s: {"puzzles": sorted(pl, key=lambda d: d["theme"])}
              for s, pl in sorted(by_suburb.items())}
    OUT_JSON.write_text(json.dumps(corpus, indent=1, ensure_ascii=False),
                        encoding="utf-8")
    n_puzzles = sum(len(v["puzzles"]) for v in corpus.values())
    print(f"[build] wrote {OUT_JSON} - {len(corpus)} playable suburbs, {n_puzzles} puzzles")


def make_client() -> OpenAI:
    load_dotenv(ROOT / ".env")
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY not set in .env")
    return OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL)


def main() -> int:
    args = sys.argv[1:]
    do_fetch = "--fetch" in args or "--all" in args
    do_match = "--match" in args or "--all" in args
    do_discover = "--discover" in args or "--all" in args
    do_clues = "--clues" in args or "--all" in args
    do_build = "--build" in args or "--all" in args

    if not any((do_fetch, do_match, do_discover, do_clues, do_build)):
        print(__doc__)
        return 1

    streets_by_suburb: dict[str, list[str]] = {}
    if do_fetch:
        fetch_pbf()
        streets_by_suburb = attribute_streets()
    elif STREETS_JSON.exists():
        streets_by_suburb = json.loads(STREETS_JSON.read_text(encoding="utf-8"))
    if not streets_by_suburb:
        print("[main] no street attribution — run --fetch first")
        return 1

    matches: dict[str, dict] = {}
    if do_match:
        matches = run_match(streets_by_suburb)
    elif MATCH_JSON.exists():
        # loaded map may contain empty-list tombstones (checked, no theme)
        raw = json.loads(MATCH_JSON.read_text(encoding="utf-8"))
        matches = {s: v for s, v in raw.items() if v}

    if do_discover:
        client = make_client()
        matches = run_discover(client, streets_by_suburb, matches)

    if do_clues:
        if not matches:
            print("[main] no theme matches - run --match/--discover first")
            return 1
        client = make_client()
        run_clues(client, matches)

    if do_build:
        build({})  # build() reads the per-suburb__theme caches itself

    return 0


if __name__ == "__main__":
    sys.exit(main())
