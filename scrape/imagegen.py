"""Pluggable image generation backends.

Default: Pollinations.ai (free, no API key, uses FLUX-style models).
Optional: Replicate FLUX dev (paid, ~$0.025/image, much more reliable).

Backend chosen via env var IMAGE_GEN_PROVIDER (defaults to "pollinations").

Usage:
    from scrape.imagegen import get_image_gen
    gen = get_image_gen()
    png_bytes = gen.generate("a cartoon toadfish in a doctor's coat", aspect="1:1", seed=42)
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Protocol

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class ImageGen(Protocol):
    """Generate one image. Returns raw bytes (PNG or JPEG)."""

    name: str

    def generate(self, prompt: str, aspect: str = "1:1", seed: int | None = None) -> bytes:
        ...


# ----------------------------- Pollinations ----------------------------

class PollinationsImageGen:
    """Free, no API key. Quality is OK but inconsistent."""

    name = "pollinations"

    SIZE_BY_ASPECT = {
        "1:1": (768, 768),
        "3:2": (768, 512),
        "2:3": (512, 768),
        "16:9": (896, 504),
    }

    def __init__(self, model: str = "flux") -> None:
        self.model = model
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "Mozilla/5.0 (compatible; melb-map/0.1)"

    def generate(self, prompt: str, aspect: str = "1:1", seed: int | None = None) -> bytes:
        w, h = self.SIZE_BY_ASPECT.get(aspect, (768, 768))
        encoded = urllib.parse.quote(prompt[:1000])
        params = f"width={w}&height={h}&model={self.model}&nologo=true"
        if seed is not None:
            params += f"&seed={seed}"
        url = f"https://image.pollinations.ai/prompt/{encoded}?{params}"
        resp = self.session.get(url, timeout=180)
        resp.raise_for_status()
        return resp.content


# ----------------------------- Replicate ------------------------------

class ReplicateImageGen:
    """Replicate FLUX dev. Requires REPLICATE_API_TOKEN env var.

    At <$5 credit Replicate throttles to 6 prediction creations per minute with
    burst=1, so we enforce a minimum interval between requests and retry 429s.
    """

    name = "replicate"

    # Conservative: ~5/min effective, well under the 6/min low-credit cap
    MIN_INTERVAL_SECONDS = 12.0
    MAX_RETRIES = 4

    def __init__(self, model: str = "black-forest-labs/flux-dev") -> None:
        try:
            import replicate  # noqa: F401
        except ImportError as e:
            raise ImportError("install with `uv add replicate`") from e
        if not os.getenv("REPLICATE_API_TOKEN"):
            raise RuntimeError("REPLICATE_API_TOKEN not set in .env")
        self.model = model
        self._last_call_at: float = 0.0

    def _throttle(self) -> None:
        gap = time.time() - self._last_call_at
        if gap < self.MIN_INTERVAL_SECONDS:
            wait = self.MIN_INTERVAL_SECONDS - gap
            print(f"[imagegen]   throttle: sleeping {wait:.1f}s before next call")
            time.sleep(wait)

    def generate(self, prompt: str, aspect: str = "1:1", seed: int | None = None) -> bytes:
        import replicate
        from replicate.exceptions import ReplicateError

        inp = {
            "prompt": prompt,
            "aspect_ratio": aspect,
            "output_format": "png",
            "output_quality": 90,
            "num_outputs": 1,
            "num_inference_steps": 28,  # default for flux-dev
        }
        if seed is not None:
            inp["seed"] = seed

        last_err: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            self._throttle()
            try:
                output = replicate.run(self.model, input=inp)
                self._last_call_at = time.time()
                if not output:
                    raise RuntimeError("replicate returned empty output")
                first = output[0] if isinstance(output, list) else output
                if hasattr(first, "read"):
                    return first.read()
                if isinstance(first, str):
                    resp = requests.get(first, timeout=120)
                    resp.raise_for_status()
                    return resp.content
                raise RuntimeError(f"unexpected replicate output type: {type(first)}")
            except ReplicateError as e:
                self._last_call_at = time.time()
                msg = str(e)
                last_err = e
                if "429" in msg or "throttle" in msg.lower():
                    wait = 15 * (attempt + 1)
                    print(f"[imagegen]   429 from replicate, sleeping {wait}s (attempt {attempt+1}/{self.MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError(f"replicate exhausted retries: {last_err}")


# ----------------------------- Factory --------------------------------

_BACKENDS = {
    "pollinations": PollinationsImageGen,
    "replicate": ReplicateImageGen,
}


def get_image_gen(provider: str | None = None) -> ImageGen:
    """Return an ImageGen instance. Reads IMAGE_GEN_PROVIDER from .env if not given."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    name = (provider or os.getenv("IMAGE_GEN_PROVIDER") or "pollinations").lower().strip()
    cls = _BACKENDS.get(name)
    if not cls:
        raise ValueError(f"unknown IMAGE_GEN_PROVIDER {name!r}; available: {list(_BACKENDS)}")
    print(f"[imagegen] backend: {name}")
    return cls()
