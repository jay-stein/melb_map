"""Build a light-grey "basemap" layer: the Melbourne suburbs we DON'T cover.

Reads the cached ABS SAL 2021 shapefile, takes every Victorian suburb that
falls inside the bounding box of our target suburbs but isn't one of them,
clips it to that box (so huge rural shapes don't sprawl), simplifies it, and
writes data/context_boundaries.geojson. The Dash app draws these behind the
coloured target suburbs as grey context with grey labels.

Run:
    uv run python -m scrape.context_boundaries
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ZIP_PATH = RAW / "SAL_2021_AUST_GDA2020_SHP.zip"
TARGET_GEOJSON = DATA / "boundaries.geojson"
CONTEXT_OUT = DATA / "context_boundaries.geojson"

MARGIN_FRAC = 0.04          # expand target bbox by 4% each side for a little breathing room
SIMPLIFY_TOLERANCE = 0.0006  # ~65 m; context only needs to read as a shape


def main() -> int:
    if not ZIP_PATH.exists():
        print(f"[context] missing {ZIP_PATH} — run scrape.boundaries first")
        return 1

    targets = gpd.read_file(TARGET_GEOJSON)
    target_names = set(targets["suburb"])
    minx, miny, maxx, maxy = targets.total_bounds
    dx, dy = (maxx - minx) * MARGIN_FRAC, (maxy - miny) * MARGIN_FRAC
    bbox = box(minx - dx, miny - dy, maxx + dx, maxy + dy)
    print(f"[context] target bbox: {minx:.3f},{miny:.3f} -> {maxx:.3f},{maxy:.3f} "
          f"({len(target_names)} target suburbs)")

    print("[context] reading SAL shapefile from zip (slow, ~national file)...")
    gdf = gpd.read_file(f"zip://{ZIP_PATH}")
    state_col = "STE_NAME21" if "STE_NAME21" in gdf.columns else "STE_NAME_2021"
    name_col = "SAL_NAME21" if "SAL_NAME21" in gdf.columns else "SAL_NAME_2021"
    vic = gdf[gdf[state_col] == "Victoria"].copy()
    if vic.crs and vic.crs.to_epsg() != 4326:
        vic = vic.to_crs(epsg=4326)
    vic["suburb"] = vic[name_col].map(lambda s: s.replace(" (Vic.)", "").strip())

    # Inside the view box, and not one of our coloured target suburbs.
    inbox = vic[vic.intersects(bbox) & ~vic["suburb"].isin(target_names)].copy()
    print(f"[context] {len(inbox)} non-target Victorian suburbs intersect the view box")

    # Clip to the box so rural shapes at the edge don't sprawl / bloat the file.
    inbox["geometry"] = inbox.geometry.intersection(bbox)
    inbox = inbox[~inbox.geometry.is_empty].copy()
    inbox["geometry"] = inbox.geometry.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    inbox = inbox[~inbox.geometry.is_empty].copy()

    out = inbox[["suburb", "geometry"]]
    out.to_file(CONTEXT_OUT, driver="GeoJSON")

    size_mb = CONTEXT_OUT.stat().st_size / 1e6
    verts = 0
    gj = json.loads(CONTEXT_OUT.read_text(encoding="utf-8"))
    for f in gj["features"]:
        g = f["geometry"]; c = g["coordinates"]
        if g["type"] == "Polygon":
            verts += sum(len(r) for r in c)
        elif g["type"] == "MultiPolygon":
            verts += sum(len(r) for p in c for r in p)
    print(f"[context] wrote {CONTEXT_OUT}: {len(out)} suburbs, {verts} vertices, {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
