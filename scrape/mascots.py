"""Generate the mascot cartoon image for one (or all) suburbs.

Reads `mascot.image_prompt` from data/suburbs.json, sends it to the configured
ImageGen backend (Pollinations free / Replicate FLUX dev), saves the PNG to
assets/mascots/{suburb}.png.

A small sanitiser strips drug/contraband references that hit safety filters
on most image gen backends. The generated image still depicts the rest of
the mascot's lore — just without the explicitly-illegal props.

Usage:
    uv run python -m scrape.mascots Elwood                   # one
    uv run python -m scrape.mascots Elwood Fitzroy Toorak    # several
    uv run python -m scrape.mascots --all                    # everything
    uv run python -m scrape.mascots Elwood --force           # overwrite
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from scrape.imagegen import get_image_gen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBURBS_PATH = DATA / "suburbs.json"
MASCOTS_DIR = ROOT / "assets" / "mascots"

# Tokens that reliably trigger image-gen safety filters. Stripped from the prompt
# before generation. The mascot text in suburbs.json is unaffected — only the
# rendered image loses these specific props.
UNSAFE_TOKENS = [
    r"\bcocaine\b", r"\bcoke\b", r"\bdrug\b", r"\bdrugs\b",
    r"\bwhite powder\b", r"\bsyringe\b", r"\bneedle\b",
    r"\bweapon\b", r"\bgun\b",
]


def sanitise(prompt: str) -> str:
    out = prompt
    for pat in UNSAFE_TOKENS:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    # collapse double spaces and stray commas left behind
    out = re.sub(r",\s*,", ",", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def generate_mascot(suburb: str, mascot_spec: dict, image_gen, force: bool = False) -> Path | None:
    MASCOTS_DIR.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    out = MASCOTS_DIR / f"{safe}.png"
    # Only PNG via this pipeline; clean up old JPG fallback if present
    legacy_jpg = MASCOTS_DIR / f"{safe}.jpg"
    if out.exists() and not force:
        print(f"[mascots] {suburb}: cached at {out}")
        return out

    raw_prompt = (mascot_spec.get("image_prompt") or "").strip()
    if not raw_prompt:
        print(f"[mascots] {suburb}: no image_prompt — re-run summarize")
        return None

    prompt = sanitise(raw_prompt)
    print(f"[mascots] {suburb}: generating via {image_gen.name}")
    print(f"[mascots]   prompt: {prompt[:140]}{'...' if len(prompt) > 140 else ''}")

    seed = abs(hash(suburb)) % 1000
    try:
        png_bytes = image_gen.generate(prompt, aspect="1:1", seed=seed)
    except Exception as e:
        print(f"[mascots]   FAILED: {e}")
        return None

    if not png_bytes or len(png_bytes) < 500:
        print(f"[mascots]   FAILED: only {len(png_bytes) if png_bytes else 0} bytes returned")
        return None

    out.write_bytes(png_bytes)
    if legacy_jpg.exists():
        legacy_jpg.unlink()
    print(f"[mascots]   wrote {out} ({len(png_bytes)/1024:.1f} KB)")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suburbs", nargs="*", help="suburb name(s); skip if --all")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    data = json.loads(SUBURBS_PATH.read_text(encoding="utf-8"))
    image_gen = get_image_gen()

    if args.all:
        suburbs = list(data.keys())
    elif args.suburbs:
        suburbs = args.suburbs
    else:
        parser.error("provide a suburb or --all")
        return 2

    for s in suburbs:
        entry = data.get(s)
        if not entry:
            print(f"[mascots] {s}: no entry in suburbs.json, skipping")
            continue
        mascot_spec = entry.get("mascot")
        if not mascot_spec:
            print(f"[mascots] {s}: no mascot spec — re-run summarize")
            continue
        generate_mascot(s, mascot_spec, image_gen, force=args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
