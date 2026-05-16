"""Download ABS SAL 2021 boundaries and filter to inner/middle Melbourne.

Run:
    uv run python -m scrape.boundaries

Outputs:
    data/boundaries.geojson  - filtered, in WGS84 (EPSG:4326)
    data/suburb_list.txt     - one suburb name per line
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

import geopandas as gpd

ABS_SAL_ZIP_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/"
    "jul2021-jun2026/access-and-downloads/digital-boundary-files/"
    "SAL_2021_AUST_GDA2020_SHP.zip"
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
ZIP_PATH = RAW / "SAL_2021_AUST_GDA2020_SHP.zip"
GEOJSON_OUT = DATA / "boundaries.geojson"
SUBURB_LIST_OUT = DATA / "suburb_list.txt"

# Curated inner/middle Melbourne suburbs (~100). Edit this list to widen/narrow scope.
TARGET_SUBURBS = [
    # Inner CBD + immediate
    "Melbourne", "Carlton", "Docklands", "East Melbourne", "North Melbourne",
    "Parkville", "Southbank", "South Wharf", "West Melbourne",
    # Inner north
    "Abbotsford", "Brunswick", "Brunswick East", "Brunswick West",
    "Carlton North", "Clifton Hill", "Coburg", "Collingwood", "Cremorne",
    "Fitzroy", "Fitzroy North", "Northcote", "Pascoe Vale", "Preston",
    "Princes Hill", "Richmond", "Thornbury",
    # Middle north (expansion ring)
    "Reservoir", "Coburg North", "Fawkner", "Glenroy", "Oak Park",
    # Inner east
    "Armadale", "Burnley", "Camberwell", "Canterbury", "Glen Iris",
    "Hawthorn", "Hawthorn East", "Kew", "Kew East", "Malvern",
    "Malvern East", "Prahran", "South Yarra", "Surrey Hills", "Toorak",
    "Windsor",
    # Middle east (expansion ring)
    "Balwyn", "Balwyn North", "Mont Albert", "Box Hill", "Ashburton",
    "Chadstone",
    # Inner south
    "Albert Park", "Balaclava", "Brighton", "Brighton East", "Caulfield",
    "Caulfield North", "Caulfield South", "Elsternwick", "Elwood",
    "Hampton", "Middle Park", "Port Melbourne", "Ripponlea", "Sandringham",
    "South Melbourne", "St Kilda", "St Kilda East", "St Kilda West",
    # Middle south-east (expansion ring)
    "Carnegie", "Murrumbeena", "Hughesdale", "Bentleigh", "Bentleigh East",
    # Middle south (expansion ring)
    "McKinnon", "Ormond", "Highett", "Hampton East",
    # Inner west
    "Ascot Vale", "Essendon", "Flemington", "Footscray", "Kensington",
    "Kingsville", "Maribyrnong", "Moonee Ponds", "Newport", "Seddon",
    "Spotswood", "Travancore", "West Footscray", "Williamstown",
    "Yarraville",
    # Middle west (expansion ring)
    "Sunshine", "Sunshine North", "Sunshine West", "Braybrook", "Maidstone",
]


def download_abs_zip() -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists() and ZIP_PATH.stat().st_size > 50 * 1024 * 1024:
        print(f"[boundaries] zip already cached: {ZIP_PATH}")
        return ZIP_PATH
    print(f"[boundaries] downloading {ABS_SAL_ZIP_URL} -> {ZIP_PATH}")
    urllib.request.urlretrieve(ABS_SAL_ZIP_URL, ZIP_PATH)
    print(f"[boundaries] downloaded {ZIP_PATH.stat().st_size / 1024 / 1024:.1f} MB")
    return ZIP_PATH


def main() -> None:
    zip_path = download_abs_zip()

    # geopandas/pyogrio can read directly from a zipped shapefile
    print(f"[boundaries] reading shapefile from zip")
    gdf = gpd.read_file(f"zip://{zip_path}")
    print(f"[boundaries] loaded {len(gdf)} SAL polygons; columns: {list(gdf.columns)}")

    # Filter to Victoria (STE_NAME21 == 'Victoria')
    state_col = "STE_NAME21" if "STE_NAME21" in gdf.columns else "STE_NAME_2021"
    name_col = "SAL_NAME21" if "SAL_NAME21" in gdf.columns else "SAL_NAME_2021"
    vic = gdf[gdf[state_col] == "Victoria"].copy()
    print(f"[boundaries] Victoria: {len(vic)} suburbs")

    # Filter to target suburb names. ABS suffixes some names with " (Vic.)".
    target_set = set(TARGET_SUBURBS)
    def normalize(name: str) -> str:
        return name.replace(" (Vic.)", "").strip()
    vic["_clean"] = vic[name_col].map(normalize)
    filtered = vic[vic["_clean"].isin(target_set)].copy()
    found = set(filtered["_clean"])
    missing = target_set - found
    if missing:
        print(f"[boundaries] WARNING: {len(missing)} target suburbs not matched: {sorted(missing)}")
    print(f"[boundaries] matched {len(filtered)} / {len(target_set)} target suburbs")

    # Reproject to WGS84 for Plotly
    if filtered.crs and filtered.crs.to_epsg() != 4326:
        filtered = filtered.to_crs(epsg=4326)

    # Keep only useful columns
    keep_cols = [name_col, "_clean", "geometry"]
    filtered = filtered[keep_cols].rename(columns={name_col: "sal_name", "_clean": "suburb"})

    DATA.mkdir(parents=True, exist_ok=True)
    filtered.to_file(GEOJSON_OUT, driver="GeoJSON")
    print(f"[boundaries] wrote {GEOJSON_OUT}")

    SUBURB_LIST_OUT.write_text("\n".join(sorted(found)) + "\n", encoding="utf-8")
    print(f"[boundaries] wrote {SUBURB_LIST_OUT}")


if __name__ == "__main__":
    sys.exit(main())
