"""Generate a flag image for one (or all) suburbs.

Approach:
- Flag bands are drawn procedurally with PIL (guaranteed correct layout)
- The emblem (a small silhouette in the centre) is fetched from Pollinations.ai
  with a focused prompt that just asks for one black silhouette on white. We
  then key out the white pixels to get a transparent emblem and composite onto
  the bands.

This separation matters because asking Pollinations for "a flag" returns
artistic interpretations rather than vexillographic flat layouts. Generating
just the emblem gets us reliable cartoon silhouettes.

Usage:
    uv run python -m scrape.flags Elwood             # one suburb
    uv run python -m scrape.flags --all              # everything in suburbs.json
    uv run python -m scrape.flags Elwood --force     # overwrite existing
"""
from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from scrape.imagegen import get_image_gen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBURBS_PATH = DATA / "suburbs.json"
FLAGS_DIR = ROOT / "assets" / "flags"

FLAG_W = 600
FLAG_H = 400  # 3:2 ratio
EMBLEM_SIZE = 220  # px in the final flag

DEFAULT_COLOURS = ["#37474F", "#FFFFFF", "#90A4AE"]  # neutral fallback


def hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i+2], 16) for i in (0, 2, 4))


def draw_bands(colors: list[str], style: str) -> Image.Image:
    """Draw the flag bands. Returns an RGB image."""
    img = Image.new("RGB", (FLAG_W, FLAG_H), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    if not colors:
        colors = DEFAULT_COLOURS

    style = (style or "horizontal tricolor").lower().strip()

    if "vertical" in style:
        n = len(colors)
        band_w = FLAG_W // n
        for i, c in enumerate(colors):
            x0 = i * band_w
            x1 = FLAG_W if i == n - 1 else (i + 1) * band_w
            draw.rectangle([(x0, 0), (x1, FLAG_H)], fill=hex_to_rgb(c))
    elif "diagonal" in style:
        # split into two triangles
        c0 = hex_to_rgb(colors[0])
        c1 = hex_to_rgb(colors[1] if len(colors) > 1 else colors[0])
        draw.rectangle([(0, 0), (FLAG_W, FLAG_H)], fill=c0)
        draw.polygon([(0, FLAG_H), (FLAG_W, FLAG_H), (FLAG_W, 0)], fill=c1)
    elif "quartered" in style:
        # 2x2 grid
        if len(colors) < 2:
            colors = colors + ["#FFFFFF"]
        c00 = hex_to_rgb(colors[0])
        c11 = hex_to_rgb(colors[0])
        c01 = hex_to_rgb(colors[1] if len(colors) > 1 else "#FFFFFF")
        c10 = hex_to_rgb(colors[2] if len(colors) > 2 else colors[1] if len(colors) > 1 else "#FFFFFF")
        mx, my = FLAG_W // 2, FLAG_H // 2
        draw.rectangle([(0, 0), (mx, my)], fill=c00)
        draw.rectangle([(mx, 0), (FLAG_W, my)], fill=c01)
        draw.rectangle([(0, my), (mx, FLAG_H)], fill=c10)
        draw.rectangle([(mx, my), (FLAG_W, FLAG_H)], fill=c11)
    elif "canton" in style:
        bg = hex_to_rgb(colors[0])
        canton = hex_to_rgb(colors[1] if len(colors) > 1 else "#FFFFFF")
        draw.rectangle([(0, 0), (FLAG_W, FLAG_H)], fill=bg)
        draw.rectangle([(0, 0), (FLAG_W // 2, FLAG_H // 2)], fill=canton)
    else:
        # horizontal tricolor / horizontal bicolor (default)
        n = len(colors)
        band_h = FLAG_H // n
        for i, c in enumerate(colors):
            y0 = i * band_h
            y1 = FLAG_H if i == n - 1 else (i + 1) * band_h
            draw.rectangle([(0, y0), (FLAG_W, y1)], fill=hex_to_rgb(c))

    return img


def fetch_emblem(emblem: str, image_gen, seed: int = 1) -> Image.Image | None:
    """Fetch a black silhouette of the emblem via the configured image gen
    backend, key out the white pixels, return RGBA PIL image."""
    if not emblem:
        return None
    prompt = (
        f"Single solid black silhouette of a {emblem}, centered, side view, "
        f"plain white background, minimalist vector clip art, simple flat shape, "
        f"no shadows, no gradients, no text"
    )
    print(f"[flags]   fetching emblem ({emblem!r}) via {image_gen.name}...")
    try:
        png_bytes = image_gen.generate(prompt, aspect="1:1", seed=seed)
    except Exception as e:
        print(f"[flags]   emblem fetch failed: {e}")
        return None
    if not png_bytes or len(png_bytes) < 500:
        print(f"[flags]   emblem fetch returned only {len(png_bytes) if png_bytes else 0} bytes")
        return None

    img = Image.open(BytesIO(png_bytes)).convert("RGBA")

    # Key out near-white pixels to transparent
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, _ = pixels[x, y]
            # Treat near-white as background
            if r > 230 and g > 230 and b > 230:
                pixels[x, y] = (255, 255, 255, 0)
            else:
                # Force any remaining colour to pure black for clean silhouette
                # (prompt asks for black silhouette but the model often adds tints)
                v = max(r, g, b)
                # if close to black, keep it; if mid-tone, decide by darkness
                a = 255 if v < 200 else int((255 - v) * 1.5)
                pixels[x, y] = (0, 0, 0, max(0, min(255, a)))
    return img


def compose_flag(bands: Image.Image, emblem: Image.Image | None) -> Image.Image:
    """Place the emblem at the centre of the flag, sized to EMBLEM_SIZE."""
    flag = bands.convert("RGBA")
    if emblem is None:
        return flag.convert("RGB")
    # Trim emblem to its bounding box of opaque pixels
    bbox = emblem.getbbox()
    if bbox:
        emblem = emblem.crop(bbox)
    # Resize keeping aspect, max dimension = EMBLEM_SIZE
    ew, eh = emblem.size
    scale = EMBLEM_SIZE / max(ew, eh)
    new_size = (int(ew * scale), int(eh * scale))
    emblem = emblem.resize(new_size, Image.LANCZOS)
    # Paste centred
    cx = (FLAG_W - emblem.width) // 2
    cy = (FLAG_H - emblem.height) // 2
    flag.alpha_composite(emblem, (cx, cy))
    return flag.convert("RGB")


def generate_flag(suburb: str, flag_spec: dict, image_gen, force: bool = False) -> Path | None:
    FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    safe = suburb.replace(" ", "_").replace("/", "_")
    out = FLAGS_DIR / f"{safe}.png"
    if out.exists() and not force:
        print(f"[flags] {suburb}: cached at {out}")
        return out

    colors = flag_spec.get("colors") or DEFAULT_COLOURS
    style = flag_spec.get("style") or "horizontal tricolor"
    emblem = (flag_spec.get("emblem") or "").strip()
    print(f"[flags] {suburb}: {style}, colors={colors}, emblem={emblem!r}")

    bands = draw_bands(colors, style)
    seed = abs(hash(suburb)) % 1000
    emblem_img = fetch_emblem(emblem, image_gen, seed=seed) if emblem else None
    flag = compose_flag(bands, emblem_img)
    flag.save(out, "PNG")
    print(f"[flags]   wrote {out}")
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
            print(f"[flags] {s}: no entry in suburbs.json, skipping")
            continue
        flag_spec = entry.get("flag")
        if not flag_spec:
            print(f"[flags] {s}: no flag spec — re-run summarize with new schema")
            continue
        generate_flag(s, flag_spec, image_gen, force=args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
