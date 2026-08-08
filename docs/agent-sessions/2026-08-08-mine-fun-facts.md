# 2026-08-08 — Mine gold fun-fact threads into a quiz-only corpus

## Goal

Fetch 12 hand-picked r/melbourne "best fun fact" / "fact about a suburb" /
"little-known history" threads, mine them for quiz-worthy gold, make suburb
facts longer on the quirks page, and build a **separate** fun-fact corpus so
the Suburb Detective quiz never recycles panel content.

## Blocker found & solved

old.reddit.com now serves Reddit's login wall to unauthenticated clients
(even the homepage and `.json` URLs). Solutions used, in order:

1. Wayback Machine snapshots of old.reddit HTML — parsed with the existing
   BeautifulSoup selectors. Got 9 of 12 threads (~620 comments), some thin.
2. DDG-hop unlock (`.agents/skills/reddit-fetch/SKILL.md`): replicate the
   method with puppeteer — DDG result redirect sets a Reddit session cookie,
   then `.json` fetches work for the session. Upgraded the thin threads
   (p6m0nu 1→86, n9ntdm 1→59, 1hlyn4i 1→72, 1hus1er 60→94) and fetched the
   one with no archive snapshot (1i3yrkd, 8).

## Files changed

- `scrape/mine_threads.py` (new) — fetch the 12 gold threads by post ID into
  `data/raw/_meta.json`, deduped; live fetch → Wayback fallback
- `scrape/mine_fun_facts.py` (new) — DeepSeek mines 262 clean city-wide facts
  from the gold threads; moves 510 suburb-specific `fun_facts` out of
  `suburbs.json` into `data/fun_facts.json` (idempotent, preserves existing)
- `scrape/summarize.py` — vibe now 3-4 sentences (longer page content);
  removed the temporary `fun_facts` schema field (quiz-only corpus now)
- `data/suburbs.json` — re-summarised all 133 with the richer meta corpus
- `data/fun_facts.json` (new) — 134 keys, 772 facts (city + per-suburb)
- `export_site.py` + `docs/` — fun_facts.json exported; trivia.js gets a
  "Fun fact:" clue source (redacted, from the quiz corpus only)
- `CLAUDE.md` — old.reddit wall note, mining scripts, quiz corpus

## Commands executed

- `uv run python -u -m scrape.mine_threads` (twice: first run added empty
  placeholders when old.reddit was walled; fixed to refetch empties)
- `uv run python -u -m scrape.mine_fun_facts` (twice: smaller batches +
  max_tokens bump + code-fence stripping fixed JSON parse failures)
- `uv run python -u -m scrape.summarize --all --force` (133 suburbs, richer
  corpus, longer vibes)
- puppeteer DDG-hop unlock (temp dir) for the 4 thin threads

## Verification

- Zero name/nickname leaks in clues (133 suburbs × 25 runs, word-boundary)
- `suburbs.json` has 0 `fun_facts` keys; panel code has no fun_facts refs —
  quiz corpus is fully separate from page content
- Quiz end-to-end works live (tiles, map, round advance, fun-fact clues)

## Notes

- `data/raw/_meta.json` is gitignored (as before); `data/fun_facts.json` is
  committed (like suburbs.json).
- Fun-fact corpus is one-off mined; re-run `scrape.mine_fun_facts` if the
  meta corpus grows.
