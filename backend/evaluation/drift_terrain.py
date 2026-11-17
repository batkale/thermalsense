"""
Does reading terrain upwind of a glider rank lift better than reading it below?

A thermal leaves the ground at its trigger and drifts with the wind while it
rises, so a glider circling at height h is roughly wind * h / w downwind of the
ground that made it (w: how fast the bubble rises).  hires_terrain reads the
ground directly under each row, and CLAUDE.md records its skill fading above
~800 m AGL — which is what drift would do.

This reads the same hires columns at a point shifted from *every* row, positive
and negative alike.  The shift uses only wind and AGL, both of which a served
map knows (forecast wind, observer altitude), so nothing about the label enters
it.  Labels stay where they were observed: the score keeps its meaning and only
the features move.  Shifting positives alone would not be a test at all — it
moves the goalposts, and with lat/lon among the columns it would teach the
model "upwind edge of the traffic = lift".

Placebos shift the same distance downwind and crosswind.  If upwind wins only
because any smear of the terrain helps, they win too.

Wind columns are Open-Meteo 10 m wind in km/h (no unit is requested, so the
default applies).  Wind aloft is stronger than at 10 m, so W_EFF is an
effective rise rate against the 10 m wind, not the thermal's true climb rate;
the sweep brackets it rather than guessing one.

    python -m evaluation.drift_terrain compute --buffer X.npz --out drift.npz \\
        [--block-cache DIR]
    python -m evaluation.drift_terrain score --buffer X.npz --cache drift.npz \\
        --seed 0 --seed 1 --seed 2
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import time
from pathlib import Path

import numpy as np

from evaluation import hires_terrain as ht

_COL_LAT, _COL_LON, _COL_U, _COL_V, _COL_AGL = 0, 1, 7, 8, 21
MAX_DRIFT_M = 6000.0

# name -> (direction, effective rise rate in m/s against the 10 m wind)
# direction: -1 upwind, +1 downwind, "x" crosswind (wind vector turned 90 deg).
SHIFTS = {
    "under":    (0, None),
    "up3":      (-1, 3.0),
    "up1.5":    (-1, 1.5),
    "up0.75":   (-1, 0.75),
    "down1.5":  (+1, 1.5),
    "cross1.5": ("x", 1.5),
}


def drift_m(X: np.ndarray, w_eff: float) -> np.ndarray:
    speed_ms = np.hypot(X[:, _COL_U], X[:, _COL_V]) / 3.6
    return np.minimum(speed_ms * X[:, _COL_AGL] / w_eff, MAX_DRIFT_M)


def shifted(X: np.ndarray, name: str) -> np.ndarray:
    """(lat, lon) per row for one entry of SHIFTS."""
    lat = X[:, _COL_LAT].astype(float)
    lon = X[:, _COL_LON].astype(float)
    direction, w = SHIFTS[name]
    if w is None:
        return np.column_stack([lat, lon])
    u, v = X[:, _COL_U].astype(float), X[:, _COL_V].astype(float)
    s = np.hypot(u, v)
    ue = np.where(s > 1e-9, u / np.maximum(s, 1e-9), 0.0)   # unit vector the air moves toward
    ve = np.where(s > 1e-9, v / np.maximum(s, 1e-9), 0.0)
    if direction == "x":
        ue, ve = ve, -ue                                      # 90 deg clockwise
        sign = 1.0
    else:
        sign = float(direction)
    d = drift_m(X, w) * sign
    mlat = 111_320.0
    mlon = 111_320.0 * np.maximum(np.cos(np.radians(lat)), 1e-3)
    return np.column_stack([lat + ve * d / mlat, lon + ue * d / mlon])


class _DiskCachedClient:
    """Stands in for dem_client's httpx client, keeping every range read on disk.

    Lets the sweep be re-run with other shifts without downloading the same
    COG blocks again (~2 MB each, a few thousand for a worldwide buffer).
    """

    class _Resp:
        def __init__(self, status_code: int, content: bytes):
            self.status_code, self.content = status_code, content

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    def __init__(self, inner, root: Path):
        self.inner, self.root = inner, root
        self.is_closed = False
        root.mkdir(parents=True, exist_ok=True)

    async def get(self, url, headers=None):
        key = hashlib.sha1((url + "|" + (headers or {}).get("Range", "")).encode()).hexdigest()
        f = self.root / key
        miss = self.root / (key + ".missing")
        if f.exists():
            return self._Resp(206, f.read_bytes())
        if miss.exists():
            return self._Resp(int(miss.read_text() or 404), b"")
        # A dropped connection must not become a missing tile: the first run of
        # this lost DNS halfway through and left half the rows NaN.  Wait it
        # out for about a minute, then let dem_client count it as an error.
        import httpx
        for wait in (2, 4, 8, 16, 32, None):
            try:
                r = await self.inner.get(url, headers=headers)
                break
            except httpx.TransportError:
                if wait is None:
                    raise
                await asyncio.sleep(wait)
        if r.status_code in (403, 404):
            miss.write_text(str(r.status_code))
        elif r.status_code < 400:
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(f)
        return self._Resp(r.status_code, r.content)

    async def aclose(self):
        self.is_closed = True
        await self.inner.aclose()


async def compute(X: np.ndarray, names: list[str], block_cache: str | None,
                  batch: int = 150, log_every: int = 20) -> dict[str, np.ndarray]:
    from data import dem_client
    dem_client._BLOCK_CACHE_SIZE = max(dem_client._BLOCK_CACHE_SIZE, 96)
    if block_cache:
        wrapped = _DiskCachedClient(dem_client._get_client(), Path(block_cache))
        dem_client._get_client = lambda: wrapped

    pos = {n: shifted(X, n) for n in names}
    out = {n: np.full((len(X), len(ht.COLUMNS)), np.nan) for n in names}
    order = np.lexsort((np.floor(X[:, _COL_LON] * 4), np.floor(X[:, _COL_LAT] * 4)))
    t0 = time.time()
    try:
        for s in range(0, len(order), batch):
            idx = order[s:s + batch]
            allpts, spans = [], []
            for i in idx:
                for n in names:
                    spans.append((i, n, len(allpts)))
                    allpts += ht.sample_points(*pos[n][i])
            got = await dem_client.fetch_elevations(list(dict.fromkeys(allpts)))
            for i, n, a in spans:
                el = [got.get(p) for p in allpts[a:a + ht.N_POINTS]]
                out[n][i] = ht.features_from([np.nan if v is None else v for v in el])
            if (s // batch) % log_every == 0:
                u = dem_client.usage()
                print(f"{s + len(idx)}/{len(X)}  {time.time() - t0:.0f}s  "
                      f"blocks read {u['blocks']}  errors {u['errors']}", flush=True)
        print(f"fetch errors {dem_client.usage()['errors']} points — re-run to fill them "
              f"(the block cache keeps what arrived)", flush=True)
    finally:
        await dem_client.aclose()
    return out


def _fingerprint(X: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(X[:, [0, 1, 7, 8, 21]]).tobytes()).hexdigest()


def _load_buffer(path: str):
    with np.load(path, allow_pickle=False) as d:
        return d["X"], d["y"].astype(int)


def cmd_compute(args) -> int:
    X, _ = _load_buffer(args.buffer)
    names = args.shift or list(SHIFTS)
    print(f"{len(X)} rows x {len(names)} shifts: {', '.join(names)}", flush=True)
    feats = asyncio.run(compute(X, names, args.block_cache))
    np.savez(args.out, names=np.array(names), fingerprint=np.array(_fingerprint(X)),
             columns=np.array(ht.COLUMNS), **{f"f_{n}": feats[n] for n in names})
    for n in names:
        print(f"  {n:<9} missing share {np.isnan(feats[n][:, 0]).mean():.4f}")
    print(f"wrote {args.out}")
    return 0


def cmd_score(args) -> int:
    from data.meteo_client import is_fallback_row
    from evaluation.evaluate import _terrain_multiplier, oof_scores
    from evaluation.holdout import decision_ids, group_ids
    from evaluation.metrics import grouped_auc, paired_delta_ci
    from models.thermal_model import _MIN_SAMPLE_AGL

    X, y = _load_buffer(args.buffer)
    with np.load(args.cache, allow_pickle=False) as c:
        if str(c["fingerprint"]) != _fingerprint(X):
            print("cache was computed for a different buffer")
            return 1
        F = {str(n): c[f"f_{n}"] for n in c["names"]}

    # What load() and fit_and_gate train on: no invented weather, nothing on the field.
    keep = ~is_fallback_row(X) & (X[:, _COL_AGL] >= _MIN_SAMPLE_AGL)
    X, y = X[keep], y[keep]
    F = {n: f[keep] for n, f in F.items()}
    gfold, ge = group_ids(X), decision_ids(X)
    tm = _terrain_multiplier(X)
    agl = X[:, _COL_AGL]
    print(f"rows {len(y)}, positives {int(y.sum())}, "
          f"mixed-label (day, cell, hour) units "
          f"{grouped_auc(np.zeros(len(y)), y, ge)['groups']}")
    for n in F:
        miss = np.isnan(F[n][:, 0]).mean()
        shift = "" if n == "under" else (
            f"shift median {np.median(drift_m(X, SHIFTS[n][1])):.0f} m, "
            f"p90 {np.percentile(drift_m(X, SHIFTS[n][1]), 90):.0f} m, ")
        print(f"  {n:<9} {shift}missing {miss:.2%}")
    if np.isnan(F["under"][:, 0]).mean() > 0.02:
        print("more than 2% of rows have no terrain - a failed fetch, not ocean; "
              "re-run compute before reading anything below")
        return 1

    # Single columns need no model and no seed: the cleanest read of the mechanism.
    print("\nsingle columns, served by nothing (micro within-hour AUC)")
    for col in ("tpi_400", "tpi_1000", "tpi_2500"):
        j = ht.COLUMNS.index(col)
        cells = []
        for n in F:
            v = F[n][:, j]
            m = np.isfinite(v)
            cells.append(f"{n} {grouped_auc(v[m], y[m], ge[m])['micro']:.3f}")
        print(f"  {col:<9} " + "  ".join(cells))

    variants = {"base": lambda Z, idx: Z}
    for n in F:
        variants[f"hires_{n}"] = (lambda n: lambda Z, idx: np.column_stack([Z, F[n][idx]]))(n)
    if "up1.5" in F:
        variants["hires_under+up1.5"] = lambda Z, idx: np.column_stack([Z, F["under"][idx], F["up1.5"][idx]])

    # oof_scores hands the transform rows, not indices, so carry the row index
    # in a trailing column and strip it before the model sees it.
    Xi = np.column_stack([X, np.arange(len(X), dtype=float)])

    def tf_for(fn):
        return lambda Z: fn(Z[:, :-1], Z[:, -1].astype(int))

    ref = "hires_under"
    strata = {"all": np.ones(len(y), bool), "AGL<800": agl < 800, "AGL>=800": agl >= 800}
    for seed in args.seed:
        t0 = time.time()
        served = {name: oof_scores(Xi, y, gfold, tf_for(fn), k=5, seed=seed) * tm
                  for name, fn in variants.items()}
        print(f"\nseed {seed}  ({time.time() - t0:.0f}s)")
        print(f"  {'variant':<20} {'served':>7}   delta vs {ref} (paired 95% CI) by stratum")
        for name, s in served.items():
            line = f"  {name:<20} {grouped_auc(s, y, ge)['micro']:.3f}  "
            if name != ref:
                for label, m in strata.items():
                    d = paired_delta_ci(served[ref][m], s[m], y[m], ge[m], n_boot=args.boot, seed=seed)
                    line += f"  {label} {d['point']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            print(line, flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute")
    c.add_argument("--buffer", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--shift", action="append", choices=list(SHIFTS))
    c.add_argument("--block-cache", help="directory to keep COG range reads in")
    s = sub.add_parser("score")
    s.add_argument("--buffer", required=True)
    s.add_argument("--cache", required=True)
    s.add_argument("--seed", type=int, action="append")
    s.add_argument("--boot", type=int, default=1000)
    args = ap.parse_args()
    if args.cmd == "score" and not args.seed:
        args.seed = [0]
    return cmd_compute(args) if args.cmd == "compute" else cmd_score(args)


if __name__ == "__main__":
    raise SystemExit(main())
