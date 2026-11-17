"""
Full-resolution terrain features for buffer rows, read from Copernicus GLO-30.

Production samples elevation every TERRAIN_RES (0.01 deg, ~1.1 km) and
bilinearly fills the 50 m grid between, so every slope and aspect the model has
ever seen is a plane between points a kilometre apart: no ridge, knoll or
valley side narrower than that exists for it.  That spacing was set to spare
OpenTopoData's 1,000 calls a day; Copernicus has no quota, so it is no longer
forced.  This module reads the real ~30 m surface around each row and derives:

    slope_hr, northness_hr, eastness_hr   3x3 Horn gradient at STEP_M spacing
    rugged                                std of a 7x7 patch (~200 m across)
    tpi_150, tpi_400, tpi_1000, tpi_2500  centre minus the mean of a ring

TPI (topographic position index) is the one that matters: positive on hilltops
and ridges, negative in valleys.  Measured on the 30 Oct 2026 export it ranks
climbs above sink within an hour at 0.61 on its own, above the whole 22-column
model — see CLAUDE.md, "Full-resolution terrain".

Run once per buffer to build a cache, then pass it to the evaluator:

    python -m evaluation.hires_terrain --buffer X.npz --out cache/hires.npz
    python -m evaluation.evaluate --buffer X.npz --hires cache/hires.npz \\
        --variant base --variant hires --by-hour

~16 min for 26k positions on a home connection (1,500 COG blocks).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

import numpy as np

STEP_M = 33.0                                  # ~ one GLO-30 pixel
RINGS_M = (150.0, 400.0, 1000.0, 2500.0)
N_RING = 12
_PATCH = [(i, j) for i in range(-3, 4) for j in range(-3, 4)]
_ANGLES = np.linspace(0, 2 * np.pi, N_RING, endpoint=False)
N_POINTS = len(_PATCH) + len(RINGS_M) * N_RING
COLUMNS = ("slope_hr", "northness_hr", "eastness_hr", "rugged",
           "tpi_150", "tpi_400", "tpi_1000", "tpi_2500")


def sample_points(lat: float, lon: float) -> list[tuple[float, float]]:
    """The N_POINTS (lat, lon) this module reads around one position."""
    mlat = 111_320.0
    mlon = 111_320.0 * max(math.cos(math.radians(lat)), 1e-3)
    pts = [(round(lat + i * STEP_M / mlat, 6), round(lon + j * STEP_M / mlon, 6))
           for i, j in _PATCH]
    for r in RINGS_M:
        pts += [(round(lat + r * math.cos(a) / mlat, 6), round(lon + r * math.sin(a) / mlon, 6))
                for a in _ANGLES]
    return pts


def features_from(elev: list[float]) -> list[float]:
    """COLUMNS from the elevations at sample_points, in that order (NaN = missing)."""
    patch = np.array(elev[:len(_PATCH)], dtype=float).reshape(7, 7)   # rows north, cols east
    if np.isnan(patch).any():
        return [np.nan] * len(COLUMNS)
    c = patch[3, 3]
    w = patch[2:5, 2:5]
    dzdx = ((w[0, 2] + 2 * w[1, 2] + w[2, 2]) - (w[0, 0] + 2 * w[1, 0] + w[2, 0])) / (8 * STEP_M)
    dzdy = ((w[2, 0] + 2 * w[2, 1] + w[2, 2]) - (w[0, 0] + 2 * w[0, 1] + w[0, 2])) / (8 * STEP_M)
    slope = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
    # Same convention as feature_engineering._slope_aspect: the bearing the
    # slope faces, i.e. the downhill direction.
    aspect = math.radians(math.degrees(math.atan2(-dzdx, -dzdy)) % 360)
    s = math.sin(math.radians(slope))
    tpis = []
    for k in range(len(RINGS_M)):
        a = len(_PATCH) + k * N_RING
        ring = np.array(elev[a:a + N_RING], dtype=float)
        tpis.append(c - np.nanmean(ring) if np.isfinite(ring).any() else np.nan)
    return [slope, math.cos(aspect) * s, math.sin(aspect) * s, float(patch.std()), *tpis]


async def compute(positions: np.ndarray, batch: int = 200, log_every: int = 20) -> np.ndarray:
    """Features for each (lat, lon) row.  Sorted by area so COG blocks are reused."""
    from data import dem_client
    dem_client._BLOCK_CACHE_SIZE = max(dem_client._BLOCK_CACHE_SIZE, 96)
    order = np.lexsort((np.floor(positions[:, 1] * 4), np.floor(positions[:, 0] * 4)))
    out = np.full((len(positions), len(COLUMNS)), np.nan)
    t0 = time.time()
    try:
        for s in range(0, len(order), batch):
            idx = order[s:s + batch]
            allpts, spans = [], []
            for i in idx:
                p = sample_points(*positions[i])
                spans.append(len(allpts))
                allpts += p
            got = await dem_client.fetch_elevations(list(dict.fromkeys(allpts)))
            for i, a in zip(idx, spans):
                el = [got.get(p) for p in allpts[a:a + N_POINTS]]
                out[i] = features_from([np.nan if v is None else v for v in el])
            if (s // batch) % log_every == 0:
                print(f"{s + len(idx)}/{len(positions)}  {time.time() - t0:.0f}s  "
                      f"blocks {dem_client.usage()['blocks']}", flush=True)
    finally:
        await dem_client.aclose()
    return out


def load_lookup(path) -> dict[tuple[float, float], np.ndarray]:
    with np.load(path) as h:
        return {(float(a), float(b)): f for (a, b), f in zip(h["pos"], h["feats"])}


def columns_for(X: np.ndarray, lookup: dict) -> np.ndarray:
    """COLUMNS for each row of a feature matrix, by its (lat, lon)."""
    keys = np.round(X[:, :2].astype(float), 6)
    nan = np.full(len(COLUMNS), np.nan)
    return np.array([lookup.get((float(a), float(b)), nan) for a, b in keys])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buffer", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    with np.load(args.buffer) as d:
        X = d["X"]
    pos = np.unique(np.round(X[:, :2].astype(float), 6), axis=0)
    print(f"{len(pos)} unique positions", flush=True)
    feats = asyncio.run(compute(pos))
    np.savez(args.out, pos=pos, feats=feats, columns=np.array(COLUMNS))
    print(f"wrote {args.out}; missing share {np.isnan(feats[:, 0]).mean():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
