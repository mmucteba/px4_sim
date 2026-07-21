"""Generate a procedural ground texture for optical-flow worlds.

Why: no downloaded asset is available at flow scale (aerial.png is ~0.9 m/px;
the downward camera at 2.5 m AGL needs ~1 cm/px detail for SIFT to see
motion). Multi-octave value noise + speckle gives natural-looking,
non-repeating-at-footprint-scale detail. Seeded -> reproducible.

Usage:
  venv/bin/python scripts/worlds/make_ground_texture.py \
      --out generated_worlds/textures/ground_speckle_2048.png \
      --size 2048 --seed 42
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def value_noise(rng: np.random.Generator, size: int, octaves: int = 6) -> np.ndarray:
    """Sum of upsampled random grids, halving amplitude per octave."""
    out = np.zeros((size, size), dtype=np.float64)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        grid = 2 ** (o + 2)  # 4, 8, 16, ... coarse->fine
        layer = rng.random((grid, grid))
        layer = cv2.resize(layer, (size, size), interpolation=cv2.INTER_CUBIC)
        out += amp * layer
        total += amp
        amp *= 0.55
    return out / total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    size = args.size

    # Base: green-brown field tones modulated by low/mid-frequency noise.
    base = value_noise(rng, size, octaves=6)
    field = (base - base.min()) / (np.ptp(base) + 1e-9)

    # Map to RGB soil/grass ramp.
    r = 60 + 120 * field
    g = 80 + 130 * field
    b = 40 + 80 * field
    img = np.stack([b, g, r], axis=-1).astype(np.float64)

    # High-frequency detail SIFT can latch onto:
    # 1) unblurred per-pixel speckle (grass-blade scale),
    img += (rng.random((size, size, 1)) - 0.5) * 55
    # 2) scattered bright/dark blobs (stones, tufts, bare patches).
    n_blobs = (size * size) // 700
    ys = rng.integers(0, size, n_blobs)
    xs = rng.integers(0, size, n_blobs)
    radii = rng.integers(2, 9, n_blobs)
    deltas = rng.uniform(-60, 60, n_blobs)
    blob_layer = np.zeros((size, size), dtype=np.float64)
    for y, x, rad, d in zip(ys, xs, radii, deltas):
        cv2.circle(blob_layer, (int(x), int(y)), int(rad), float(d), -1)
    blob_layer = cv2.GaussianBlur(blob_layer, (0, 0), 1.0)
    img += blob_layer[..., None]

    img = np.clip(img, 0, 255).astype(np.uint8)  # BGR for cv2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), img)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=400)
    kp = sift.detect(gray[: size // 4, : size // 4], None)
    print(f"wrote {out} size={size} std={gray.std():.1f} sift_kp_quarter={len(kp)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
