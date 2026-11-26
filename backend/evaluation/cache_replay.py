"""
Size the terrain caches by replaying archive days through the live access pattern.

    python -m evaluation.cache_replay 2026-09-21 2026-10-04 2026-11-05

For each day it rebuilds what the node would have asked for: every 5 minutes
the live sampler's candidates (soaring, untowed, the first MAX_SAMPLE_GRIDS of
the live list in arrival order, sun above ~4°, skipping aircraft already inside
an earlier grid), and every 15 minutes the ledger's sites (the fixed ones plus
the busiest 0.1° cells).  Each grid then goes through the same caches the app
keeps — elevation grids, lattice points, full-resolution grids, Copernicus
blocks — and the replay counts block downloads and full-resolution rebuilds
for several cache sizes.

/predict clicks, the nightly seed and the hires fill-in are not modelled, so
the counts are a floor.  It reproduced the node's own figure: 16,554 downloads
for 4 Oct at the sizes of the time, against 15,716 counted live on 23 Nov by
19:37 UTC.  Block geometry is Copernicus GLO-30's: 3600 rows a tile, columns
narrowing above 50°, 1024 x 1024 blocks.  See DEM_RAW_CACHE_MB in config.py.
"""

import argparse
import math
from collections import OrderedDict

import numpy as np
import pandas as pd

from config import ARCHIVE_DIR, GRID_RADIUS, LEDGER_FIXED_SITES, LEDGER_DYNAMIC_SITES
from config import LEDGER_ISSUE_EVERY_MIN, LEDGER_MIN_AIRCRAFT, MAX_SAMPLE_GRIDS, TERRAIN_RES
from data.ogn_client import SOARING_AC_TYPES
from pipeline import hires_terrain as ht
from pipeline import solar

CYCLE_S = 300
GLIDER_TTL_S = 300               # ogn_client._GLIDER_TTL_S
MIN_SUN_ELEV = 4.0               # ~50 W/m2 GHI, the sampler's solar floor
REACH = (max(ht.RINGS_M) + 4 * ht.STEP_M) / 111_320.0


def _tile_width(tile_lat: int) -> int:
    a = abs(tile_lat + 0.5)
    for edge, w in ((50, 3600), (60, 2400), (70, 1800), (80, 1200), (85, 720)):
        if a < edge:
            return w
    return 360


def _window_blocks(s: float, n: float, w: float, e: float) -> set:
    out = set()
    for tl in range(math.ceil(s) - 1, math.ceil(n)):
        for tn in range(math.floor(w), math.floor(e) + 1):
            width = _tile_width(tl)
            across = -(-width // 1024)
            r0 = int(np.clip((tl + 1 - min(n, tl + 1)) * 3600, 0, 3599))
            r1 = int(np.clip((tl + 1 - max(s, tl)) * 3600, 0, 3599))
            c0 = int(np.clip((max(w, tn) - tn) * width, 0, width - 1))
            c1 = int(np.clip((min(e, tn + 1) - tn) * width, 0, width - 1))
            for br in range(r0 // 1024, r1 // 1024 + 1):
                for bc in range(c0 // 1024, c1 // 1024 + 1):
                    out.add((tl, tn, br * across + bc))
    return out


def _point_block(lat: float, lon: float) -> tuple:
    tl, tn = math.ceil(lat) - 1, math.floor(lon)
    width = _tile_width(tl)
    r = int(np.clip(round((tl + 1 - lat) * 3600), 0, 3599))
    c = int(np.clip(round((lon - tn) * width), 0, width - 1))
    return tl, tn, (r // 1024) * -(-width // 1024) + c // 1024


class _LRU:
    def __init__(self, n: int):
        self.n, self.d, self.miss = n, OrderedDict(), 0

    def seen(self, key) -> bool:
        if key in self.d:
            self.d.move_to_end(key)
            return True
        self.miss += 1
        self.d[key] = None
        while len(self.d) > self.n:
            self.d.popitem(last=False)
        return False


def _snap(x: float) -> float:
    return round(round(x / TERRAIN_RES) * TERRAIN_RES, 6)


def _sunlit(lat: float, lon: float, doy: int, hour: float) -> bool:
    el, _ = solar.sun_position(np.array([lat]), np.array([lon]), doy, np.array([hour]))
    return bool(el[0] > MIN_SUN_ELEV)


def requests_for(day: str) -> list[tuple[str, float, float]]:
    """(kind, snapped lat, snapped lon) for every grid the node would have built."""
    df = pd.read_parquet(ARCHIVE_DIR / f"date={day}" / "part.parquet",
                         columns=["ts", "id", "lat", "lon", "ac_type", "under_tow"])
    df = df[df.ac_type.isin(SOARING_AC_TYPES) & (df.under_tow == 0)]
    t = (df.ts.astype("int64") // 1_000_000).to_numpy()     # microseconds -> s
    order = np.argsort(t, kind="stable")
    t, ids = t[order], df.id.to_numpy()[order]
    la, lo = df.lat.to_numpy()[order], df.lon.to_numpy()[order]
    day0 = int(pd.Timestamp(day, tz="UTC").timestamp())
    doy = pd.Timestamp(day).dayofyear
    live: "OrderedDict[str, tuple]" = OrderedDict()
    reqs, i = [], 0
    for cyc in range(day0, day0 + 86400, CYCLE_S):
        while i < len(t) and t[i] < cyc:
            if ids[i] in live and t[i] - live[ids[i]][2] >= GLIDER_TTL_S:
                del live[ids[i]]                              # re-enters at the back
            live[ids[i]] = (la[i], lo[i], t[i])
            i += 1
        for k in [k for k, v in live.items() if cyc - v[2] >= GLIDER_TTL_S]:
            del live[k]
        now = list(live.values())
        hour = (cyc - day0) / 3600.0
        if (cyc - day0) % (LEDGER_ISSUE_EVERY_MIN * 60) == 0:
            cells: dict = {}
            for a, b, _ in now:
                cells[(round(a, 1), round(b, 1))] = cells.get((round(a, 1), round(b, 1)), 0) + 1
            busy = [c for c, n in sorted(cells.items(), key=lambda x: -x[1])
                    if n >= LEDGER_MIN_AIRCRAFT][:LEDGER_DYNAMIC_SITES]
            for a, b in [(a, b) for _, a, b in LEDGER_FIXED_SITES] + busy:
                if _sunlit(a, b, doy, hour):
                    reqs.append(("ledger", _snap(a), _snap(b)))
        taken: list[tuple] = []
        for a, b, _ in now[:MAX_SAMPLE_GRIDS]:
            if any(s <= a <= n and w <= b <= e for s, n, w, e in taken):
                continue
            if not _sunlit(a, b, doy, hour):
                continue
            ca, cb = _snap(a), _snap(b)
            taken.append((ca - GRID_RADIUS, ca + GRID_RADIUS, cb - GRID_RADIUS, cb + GRID_RADIUS))
            reqs.append(("sample", ca, cb))
    return reqs


def replay(reqs, blocks: int, hires: int, grids: int = 64, points: int = 200_000) -> tuple[int, int]:
    """(block downloads, full-resolution rebuilds) at these cache sizes."""
    b, h, g, p = _LRU(blocks), _LRU(hires), _LRU(grids), _LRU(points)
    half = round(GRID_RADIUS / TERRAIN_RES)
    for _, ca, cb in reqs:
        if not g.seen((ca, cb)):
            for da in range(-half, half + 1):
                for db in range(-half, half + 1):
                    pt = (round(ca + da * TERRAIN_RES, 6), round(cb + db * TERRAIN_RES, 6))
                    if not p.seen(pt):
                        b.seen(_point_block(*pt))
        if not h.seen((ca, cb)):
            rl = REACH / max(math.cos(math.radians(abs(ca) + GRID_RADIUS + REACH)), 1e-3)
            for k in _window_blocks(ca - GRID_RADIUS - REACH, ca + GRID_RADIUS + REACH,
                                    cb - GRID_RADIUS - rl, cb + GRID_RADIUS + rl):
                b.seen(k)
    return b.miss, h.miss


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("days", nargs="+", help="archive days, YYYY-MM-DD")
    ap.add_argument("--sizes", default="8:16,8:64,171:16,171:64,171:128,256:64",
                    help="blocks:hires pairs; ~2.2 MB a compressed block, 1.3 MB a hires grid")
    args = ap.parse_args()
    sizes = [tuple(int(x) for x in s.split(":")) for s in args.sizes.split(",")]
    for day in args.days:
        reqs = requests_for(day)
        print(f"\n{day}: {sum(r[0] == 'sample' for r in reqs):,} sampled grids, "
              f"{sum(r[0] == 'ledger' for r in reqs):,} ledger grids, "
              f"{len({r[1:] for r in reqs}):,} distinct centres")
        base = None
        for blocks, hires in sizes:
            dl, rebuilt = replay(reqs, blocks, hires)
            base = base or dl
            print(f"  blocks {blocks:>4}  hires {hires:>4}:  {dl:>7,} downloads "
                  f"({1 - dl / base:4.0%} fewer)  {rebuilt:>6,} hires rebuilds")


if __name__ == "__main__":
    main()
