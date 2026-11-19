"""
3D thermal blocks: where aircraft have climbed, by place AND height.

The method thermal.kk7.ch uses for its 3D layer, run on our own OGN archive:
split the air into blocks of about 100 x 100 m and 100 m of height, and for
each block count the aircraft that passed through it and the ones circling in
a climb.  A circling aircraft climbing at 1 m/s or more counts fully, a weaker
climb partly, and an aircraft counts once per block per day however many
beacons it sent there.  The block's thermal share is climbers / passes.

The 2D prior (circling_prior / archive_prior) counts per ~1 km cell and hour
with no height.  A 3D layer also knows where lift sits at the aircraft's own
altitude, which is where a thermal that leans with the wind is found: the
drift-correction tests in CLAUDE.md could not model that lean from physics,
and this measures it from where aircraft actually climbed.

It is a climatology, never today's weather.  As a model column it is read
strictly from OTHER days, two ways:

    past   days before the row's day only (what a live system would know)
    loo    every other archived day (more history, still no same-day traffic)

Blocks are ~100 m on the ground everywhere: longitude is scaled by the cosine
of each 1-degree latitude band's centre, so blocks line up within a band and
shift slightly at integer latitudes.  Neighbourhoods do not cross that seam
exactly; at 100 m it changes nothing that matters.

    python -m evaluation.thermal_blocks build --archive DIR --out blocks.parquet \\
        [--bbox S N W E] [--min-n 5]
    python -m evaluation.thermal_blocks features --archive DIR --buffer X.npz --out f.npz
    python -m evaluation.thermal_blocks score --buffer X.npz --features f.npz \\
        --hires drift.npz --seed 0 --seed 1 --seed 2
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import time
from datetime import date

import numpy as np

BLOCK_M = 100.0
M_PER_DEG = 111_320.0
DLAT = BLOCK_M / M_PER_DEG
TOP_M = 6300.0                       # above this nothing soars on thermals
ALPHA = 5.0                          # pseudo-passes pulling a thin block to the base rate
_OFF_Y, _SPAN_X, _OFF_X, _SPAN_Z = 110_000, 420_000, 210_000, 64
_COL_LAT, _COL_LON, _COL_ELEV, _COL_AGL = 0, 1, 2, 21
COLUMNS = ("b3_share", "b3_log_n", "b2_share", "b2_log_n")
_READ = ["id", "lat", "lon", "alt", "vario", "circling", "ac_type", "under_tow"]


# --- geometry -----------------------------------------------------------------

def block_index(lat, lon, alt_m):
    """(iy, ix, iz) of the ~100 m block holding each point."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    iy = np.floor(lat / DLAT).astype(np.int64)
    cos_band = np.cos(np.radians(np.floor(lat) + 0.5))
    ix = np.floor(lon * M_PER_DEG * cos_band / BLOCK_M).astype(np.int64)
    iz = np.clip(np.floor(np.asarray(alt_m, dtype=float) / BLOCK_M), 0, _SPAN_Z - 1).astype(np.int64)
    return iy, ix, iz


def column_key(iy, ix):
    return (np.asarray(iy) + _OFF_Y) * _SPAN_X + (np.asarray(ix) + _OFF_X)


def block_key(iy, ix, iz):
    return column_key(iy, ix) * _SPAN_Z + np.asarray(iz)


def block_centre(key):
    """(lat, lon, alt floor in m) of block keys — for exporting the layer."""
    key = np.asarray(key, dtype=np.int64)
    iz = key % _SPAN_Z
    col = key // _SPAN_Z
    iy = col // _SPAN_X - _OFF_Y
    ix = col % _SPAN_X - _OFF_X
    lat = (iy + 0.5) * DLAT
    cos_band = np.cos(np.radians(np.floor(lat) + 0.5))
    lon = (ix + 0.5) * BLOCK_M / (M_PER_DEG * cos_band)
    return lat, lon, iz * BLOCK_M


# --- counting -----------------------------------------------------------------

def climb_weight(vario, circling):
    """kk7's rule: circling at >= 1 m/s counts fully, a weaker climb partly."""
    return np.where(np.asarray(circling) == 1, np.clip(np.asarray(vario, dtype=float), 0.0, 1.0), 0.0)


def day_blocks(lat, lon, alt, vario, circling, ids):
    """
    One day's counts: (block keys, passes, climb) and (column keys, passes, climb).

    An aircraft counts once per block (and once per column), with the strongest
    climb it showed there.  Columns ignore height: the 2D twin of the 3D layer.
    """
    import pandas as pd
    iy, ix, iz = block_index(lat, lon, alt)
    w = climb_weight(vario, circling)
    out = []
    for key in (block_key(iy, ix, iz), column_key(iy, ix)):
        per = pd.DataFrame({"k": key, "id": ids, "w": w}).groupby(["k", "id"], sort=False).w.max()
        g = per.groupby(level=0).agg(["size", "sum"])
        out.append((g.index.to_numpy(np.int64), g["size"].to_numpy(float), g["sum"].to_numpy(float)))
    return out


def _archive_days(archive_dir):
    days = {}
    for f in glob.glob(os.path.join(archive_dir, "date=*", "part.parquet")):
        days[date.fromisoformat(os.path.basename(os.path.dirname(f))[5:15])] = f
    return dict(sorted(days.items()))


def read_day(path, bbox=None):
    """Soaring, untowed beacons of one archived day as numpy columns."""
    import pyarrow.parquet as pq
    from data.ogn_client import SOARING_AC_TYPES
    t = pq.read_table(path, columns=_READ).to_pandas()
    m = t.ac_type.isin(sorted(SOARING_AC_TYPES)) & (t.under_tow == 0) & (t.alt >= 0) & (t.alt < TOP_M)
    if bbox is not None:
        s, n, w, e = bbox
        m &= (t.lat >= s) & (t.lat < n) & (t.lon >= w) & (t.lon < e)
    t = t[m]
    return (t.lat.to_numpy(float), t.lon.to_numpy(float), t.alt.to_numpy(float),
            t.vario.to_numpy(float), t.circling.to_numpy(), t.id.to_numpy())


def shrunk_share(c, n, p0, alpha=ALPHA):
    """Climb share pulled toward the base rate p0 where traffic is thin; NaN with none."""
    c, n = np.asarray(c, dtype=float), np.asarray(n, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, (c + alpha * p0) / (n + alpha), np.nan)


# --- the layer ------------------------------------------------------------------

def cmd_build(args) -> int:
    """Every block over the archive (optionally a box), kept where n >= min_n."""
    import pandas as pd
    bbox = tuple(args.bbox) if args.bbox else None
    total = None
    t0 = time.time()
    for d, f in _archive_days(args.archive).items():
        lat, lon, alt, vario, circ, ids = read_day(f, bbox)
        if len(lat) == 0:
            continue
        (k3, n3, c3), _ = day_blocks(lat, lon, alt, vario, circ, ids)
        day = pd.DataFrame({"n": n3, "c": c3}, index=k3)
        total = day if total is None else total.add(day, fill_value=0.0)
        print(f"{d}  {len(total):,} blocks so far  {time.time() - t0:.0f}s", flush=True)
    if total is None:
        print("no soaring beacons in the archive (or the box)")
        return 1
    p0 = float(total.c.sum() / total.n.sum())
    keep = total[total.n >= args.min_n]
    lat, lon, alt = block_centre(keep.index.to_numpy(np.int64))
    out = pd.DataFrame({"lat": lat, "lon": lon, "alt_floor_m": alt, "passes": keep.n.to_numpy(),
                        "climb": keep.c.to_numpy(),
                        "share": shrunk_share(keep.c.to_numpy(), keep.n.to_numpy(), p0)})
    out.to_parquet(args.out, index=False)
    cols = len(np.unique(keep.index.to_numpy(np.int64) // _SPAN_Z))
    print(f"wrote {args.out}: {len(out):,} blocks with >= {args.min_n:g} aircraft-days "
          f"over {cols * BLOCK_M ** 2 / 1e6:,.0f} km^2 of ground, base climb share {p0:.3f}")
    return 0


# --- per-row columns for the evaluation ---------------------------------------------

def _neighbours(iy, ix, iz=None, reach=1):
    """Keys of the (2r+1)^2 columns, or (2r+1)^3 blocks, around each row."""
    r = range(-reach, reach + 1)
    if iz is None:
        return np.stack([column_key(iy + dy, ix + dx) for dy in r for dx in r], axis=1)
    # A neighbour below the ground or above the top is no block at all: -1 never
    # matches a counted key.  Clipping it instead would count the edge block twice.
    return np.stack([np.where((iz + dz >= 0) & (iz + dz < _SPAN_Z), block_key(iy + dy, ix + dx, iz + dz), -1)
                     for dy in r for dx in r for dz in r], axis=1)


def row_features(X, archive_dir, reach=1):
    """
    COLUMNS for each buffer row, read from other days only: (past, loo, report).

    past = days strictly before the row's own day; loo = all archived days but
    its own.  Counts are summed over the 3x3x3 blocks (3D) or 3x3 columns (2D)
    around the row, so one aircraft's line through the next block still counts.
    """
    from evaluation.evaluate import row_datetimes
    days_of_row = np.array([t.date() for t in row_datetimes(X)])
    alt = X[:, _COL_ELEV].astype(float) + X[:, _COL_AGL].astype(float)
    iy, ix, iz = block_index(X[:, _COL_LAT], X[:, _COL_LON], alt)
    nb = {"3": _neighbours(iy, ix, iz, reach), "2": _neighbours(iy, ix, None, reach)}
    q = {k: np.unique(v) for k, v in nb.items()}                  # the only keys ever read
    pos = {k: np.searchsorted(q[k], nb[k]) for k in nb}
    cum = {k: np.zeros((len(q[k]), 2)) for k in nb}               # passes, climb
    own = {k: np.zeros((len(X), 2)) for k in nb}
    past = {k: np.full((len(X), 2), np.nan) for k in nb}
    seen_n = seen_c = 0.0

    archive = _archive_days(archive_dir)
    t0 = time.time()
    for d in sorted(set(archive) | set(days_of_row)):
        rows = np.flatnonzero(days_of_row == d)
        for k in nb:                                              # before d is added: strictly past
            past[k][rows] = cum[k][pos[k][rows]].sum(axis=1)
        if d not in archive:
            continue
        lat, lon, a, vario, circ, ids = read_day(archive[d])
        if len(lat) == 0:
            continue
        day = dict(zip("32", day_blocks(lat, lon, a, vario, circ, ids)))
        seen_n += day["3"][1].sum()
        seen_c += day["3"][2].sum()
        for k, (keys, n, c) in day.items():
            i = np.searchsorted(q[k], keys)
            hit = (i < len(q[k])) & (q[k][np.minimum(i, len(q[k]) - 1)] == keys)
            today = np.zeros((len(q[k]), 2))
            today[i[hit], 0], today[i[hit], 1] = n[hit], c[hit]
            cum[k] += today
            own[k][rows] = today[pos[k][rows]].sum(axis=1)
        print(f"{d}  rows {len(rows):>5}  {time.time() - t0:.0f}s", flush=True)

    p0 = seen_c / max(seen_n, 1.0)
    out = {}
    for mode in ("past", "loo"):
        cols = []
        for k in ("3", "2"):
            nc = past[k] if mode == "past" else cum[k][pos[k]].sum(axis=1) - own[k]
            cols += [shrunk_share(nc[:, 1], nc[:, 0], p0), np.log1p(nc[:, 0])]
        out[mode] = np.column_stack(cols)
    report = {"p0": p0, "archive_days": len(archive),
              **{f"{m}_rows_with_history": float((out[m][:, 1] > 0).mean()) for m in out},
              **{f"{m}_rows_ge20": float((np.expm1(out[m][:, 1]) >= 20).mean()) for m in out}}
    return out["past"], out["loo"], report


def _fingerprint(X):
    import hashlib
    return hashlib.sha1(np.ascontiguousarray(X[:, [0, 1, 2, 15, 16, 17, 18, 21]]).tobytes()).hexdigest()


def cmd_features(args) -> int:
    with np.load(args.buffer, allow_pickle=False) as d:
        X = d["X"]
    past, loo, report = row_features(X, args.archive, reach=args.reach)
    np.savez(args.out, fingerprint=np.array(_fingerprint(X)), columns=np.array(COLUMNS),
             past=past, loo=loo, **{k: np.array(v) for k, v in report.items()})
    for k, v in report.items():
        print(f"  {k:<22} {v:.3f}" if isinstance(v, float) else f"  {k:<22} {v}")
    print(f"wrote {args.out}")
    return 0


def cmd_score(args) -> int:
    from data.meteo_client import is_fallback_row
    from evaluation.evaluate import _terrain_multiplier, oof_scores
    from evaluation.holdout import decision_ids, group_ids
    from evaluation.metrics import grouped_auc, paired_delta_ci
    from models.thermal_model import _MIN_SAMPLE_AGL

    with np.load(args.buffer, allow_pickle=False) as d:
        X, y = d["X"], d["y"].astype(int)
    with np.load(args.features, allow_pickle=False) as f:
        if str(f["fingerprint"]) != _fingerprint(X):
            raise SystemExit(f"{args.features} was computed for a different buffer")
        F = {"past": f["past"], "loo": f["loo"]}
    with np.load(args.hires, allow_pickle=False) as h:
        hires = h["f_under"]
    if np.isnan(hires[:, 0]).mean() > 0.02:
        raise SystemExit(f"{args.hires}: more than 2% of rows have no terrain")

    keep = ~is_fallback_row(X) & (X[:, _COL_AGL] >= _MIN_SAMPLE_AGL)
    X, y, hires = X[keep], y[keep], hires[keep]
    F = {m: v[keep] for m, v in F.items()}
    gfold, ge = group_ids(X), decision_ids(X)
    tm = _terrain_multiplier(X)
    agl = X[:, _COL_AGL]
    print(f"rows {len(y)}, positives {int(y.sum())}, mixed-label units "
          f"{grouped_auc(np.zeros(len(y)), y, ge)['groups']}")
    for m, v in F.items():
        n3 = np.expm1(v[:, 1])
        print(f"  {m:<4} rows with any 3D history {(n3 > 0).mean():.1%}, with >= 20 aircraft-days "
              f"{(n3 >= 20).mean():.1%}")

    print("\nsingle columns, no model (micro within-hour AUC; rows without history left out)")
    print(f"  {'alt_agl alone':<22} {grouped_auc(agl, y, ge)['micro']:.3f}")
    for m, v in F.items():
        for j, name in ((0, "b3_share"), (2, "b2_share")):
            s = v[:, j]
            ok = np.isfinite(s)
            r = grouped_auc(s[ok], y[ok], ge[ok])
            line = f"  {name + ' ' + m:<22} {r['micro']:.3f}  over {r['groups']} units"
            for label, band in (("AGL<800", agl < 800), ("AGL>=800", agl >= 800)):
                b = ok & band
                line += f"   {label} {grouped_auc(s[b], y[b], ge[b])['micro']:.3f}"
            print(line)

    # The leak to rule out is the alt_agl one in another form: gliders fly the
    # same circuits and approaches into an airfield every day, so a climatology
    # can learn "this corridor sinks" rather than where lift is.  If the gain
    # holds on rows kilometres from any field, that is not what it learned.
    km = _airfield_km(X, args.airports) if args.airports else None
    if km is not None:
        print(f"\nairfields: median distance {np.median(km):.1f} km (positives "
              f"{np.median(km[y == 1]):.1f}, negatives {np.median(km[y == 0]):.1f})")
        s = F["past"][:, 0]
        for lo in (0, 3, 5, 8):
            m = (km >= lo) & np.isfinite(s)
            print(f"  >= {lo} km: b3_share past alone {grouped_auc(s[m], y[m], ge[m])['micro']:.3f}"
                  f" over {grouped_auc(s[m], y[m], ge[m])['groups']} units")

    blocks = {"hires": hires, "b3 past": F["past"][:, :2], "b2 past": F["past"][:, 2:],
              "b3 loo": F["loo"][:, :2], "b2 loo": F["loo"][:, 2:]}
    variants = {"base": (), "hires": ("hires",),
                "hires+b2 past": ("hires", "b2 past"), "hires+b3 past": ("hires", "b3 past"),
                "hires+b2 loo": ("hires", "b2 loo"), "hires+b3 loo": ("hires", "b3 loo")}
    Xi = np.column_stack([X, np.arange(len(X), dtype=float)])

    def tf(parts):
        return lambda Z: np.column_stack([Z[:, :-1]] + [blocks[p][Z[:, -1].astype(int)] for p in parts])

    for seed in args.seed:
        t0 = time.time()
        served = {name: oof_scores(Xi, y, gfold, tf(parts), k=5, seed=seed) * tm
                  for name, parts in variants.items()}
        print(f"\nseed {seed}  ({time.time() - t0:.0f}s)")
        for name, s in served.items():
            line = f"  {name:<15} served {grouped_auc(s, y, ge)['micro']:.3f}"
            if name != "hires":
                d = paired_delta_ci(served["hires"], s, y, ge, n_boot=args.boot, seed=seed)
                line += f"   vs hires {d['point']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            print(line, flush=True)
        if km is not None:
            for lo in (3, 5, 8):
                m = km >= lo
                line = f"  >= {lo} km from an airfield:"
                for name in ("hires+b3 past", "hires+b3 loo", "hires+b2 past"):
                    d = paired_delta_ci(served["hires"][m], served[name][m], y[m], ge[m],
                                        n_boot=args.boot, seed=seed)
                    line += f"   {name} {d['point']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
                print(line + f"   ({d['groups']} units)", flush=True)
    return 0


def _airfield_km(X, airports_csv):
    """Distance from each row to the nearest airfield in OurAirports' airports.csv."""
    import pandas as pd
    from sklearn.neighbors import BallTree
    ap = pd.read_csv(airports_csv, usecols=["type", "latitude_deg", "longitude_deg"])
    ap = ap[ap.type.isin(["small_airport", "medium_airport", "large_airport"])]
    tree = BallTree(np.radians(ap[["latitude_deg", "longitude_deg"]].values), metric="haversine")
    return tree.query(np.radians(X[:, :2].astype(float)), k=1)[0][:, 0] * 6371.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--archive", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--bbox", type=float, nargs=4, metavar=("S", "N", "W", "E"))
    b.add_argument("--min-n", type=float, default=5)
    f = sub.add_parser("features")
    f.add_argument("--archive", required=True)
    f.add_argument("--buffer", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--reach", type=int, default=1, help="neighbourhood half-width in blocks")
    s = sub.add_parser("score")
    s.add_argument("--buffer", required=True)
    s.add_argument("--features", required=True)
    s.add_argument("--hires", required=True, help="drift_terrain cache; its 'under' set")
    s.add_argument("--seed", type=int, action="append")
    s.add_argument("--boot", type=int, default=1000)
    s.add_argument("--airports", help="OurAirports airports.csv: also score rows >= 3/5/8 km "
                                      "from any airfield")
    args = ap.parse_args()
    if args.cmd == "score" and not args.seed:
        args.seed = [0]
    return {"build": cmd_build, "features": cmd_features, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
