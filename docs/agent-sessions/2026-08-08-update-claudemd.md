# 2026-08-08 — Refresh CLAUDE.md to current 133-suburb state

## Goal

Scan the repo, understand current state vs docs, and update `CLAUDE.md` (which
still described the ~75-suburb, 3-source project) to match the shipped
133-suburb, 5-source reality. Also fix stale comments in three other files.

## Files changed

- `CLAUDE.md` — full rewrite: 133 inner/middle/outer suburbs, five-source
  corpus (Reddit old.reddit HTML, melbz, eMelbourne, Wikipedia, ABS census
  LQ quirks), Suburble game, updated data-flow diagram, file layout, run
  commands (incl. `scrape.census --with openpyxl --with pyshp`,
  `scrape.emelbourne/wikipedia/expand_quotes`), env vars
  (`IMAGE_GEN_PROVIDER`, `REPLICATE_API_TOKEN`), refreshed status checklist
  (only remaining: 122 mascot images), updated risks.
- `scrape/summarize.py` — docstring "75 calls" → "133 calls"
- `.env.example` — "public JSON endpoints" → "parses old.reddit.com HTML"
- `pyproject.toml` — description "inner/middle Melbourne … Claude" →
  "133 Melbourne suburbs … DeepSeek"

## Commands executed

- `git checkout -b agent/update-claudemd`
- `uv run python -c "import scrape.summarize; import scrape.census"` (smoke check)
- `git add … && git commit -m "docs: refresh CLAUDE.md to current 133-suburb state"`

## Decisions / notes

- Verified facts on disk before writing: 133 suburbs in list, 133 in
  `suburbs.json`, 11/133 mascot images, all raw corpora present (melbz 133,
  emelbourne 134, wikipedia 133, reddit 137 files).
- No linters/tests configured in this repo (noted as possible follow-up).
- Branch `agent/update-claudemd` has 1 commit, not yet merged/pushed (left for
  user review per AGENTS.md — user decides on merge).

## Suggested next tasks

- Generate the remaining 122 mascot images (`uv run python -u -m scrape.mascots --all`) — needs user go-ahead (~$3 Replicate or free Pollinations).
- Optionally add ruff/pytest config so the global AGENTS.md test/lint rules have something to run.
