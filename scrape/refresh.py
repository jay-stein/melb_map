"""End-to-end orchestrator: ensure boundaries → scrape Reddit → summarise.

Just calls the underlying modules; both stages are interrupt-safe and incremental,
so a partial run picks up where it left off on the next invocation.

Usage:
    uv run python -m scrape.refresh         # full pipeline
    uv run python -m scrape.refresh --force # ignore caches in scrape + summarize
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scrape import boundaries, emelbourne, melbz, reddit, summarize, wikipedia

ROOT = Path(__file__).resolve().parent.parent
BOUNDARIES_GEOJSON = ROOT / "data" / "boundaries.geojson"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="re-scrape and re-summarise even if cached")
    args = parser.parse_args()

    print("=" * 60)
    print("STAGE 1: boundaries")
    print("=" * 60)
    if BOUNDARIES_GEOJSON.exists() and not args.force:
        print(f"[refresh] {BOUNDARIES_GEOJSON} exists, skipping (use --force to redo)")
    else:
        boundaries.main()

    print()
    print("=" * 60)
    print("STAGE 2: reddit scrape (per-suburb)")
    print("=" * 60)
    sys.argv = ["reddit", "--all"] + (["--force"] if args.force else [])
    rc = reddit.main()
    if rc != 0:
        print(f"[refresh] scrape stage exited with {rc} — stopping")
        return rc

    print()
    print("=" * 60)
    print("STAGE 3: melbz scrape")
    print("=" * 60)
    sys.argv = ["melbz", "--all"] + (["--force"] if args.force else [])
    rc = melbz.main()
    if rc != 0:
        print(f"[refresh] melbz stage exited with {rc}")
        return rc

    print()
    print("=" * 60)
    print("STAGE 4: eMelbourne scrape (history — primary)")
    print("=" * 60)
    sys.argv = ["emelbourne", "--all"] + (["--force"] if args.force else [])
    rc = emelbourne.main()
    if rc != 0:
        print(f"[refresh] emelbourne stage exited with {rc}")
        return rc

    print()
    print("=" * 60)
    print("STAGE 5: Wikipedia scrape (history — fallback)")
    print("=" * 60)
    sys.argv = ["wikipedia", "--all"] + (["--force"] if args.force else [])
    rc = wikipedia.main()
    if rc != 0:
        print(f"[refresh] wikipedia stage exited with {rc}")
        return rc

    print()
    print("=" * 60)
    print("STAGE 6: summarise")
    print("=" * 60)
    sys.argv = ["summarize", "--all"] + (["--force"] if args.force else [])
    rc = summarize.main()
    if rc != 0:
        print(f"[refresh] summarise stage exited with {rc}")
        return rc

    print()
    print("[refresh] complete — restart `uv run python app.py` to load fresh data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
