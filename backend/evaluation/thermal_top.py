"""
The thermal-top training filter: old `cape_base` against min(boundary layer, LCL).

Training drops every sample flying higher than the estimated thermal top + 300 m
(`_above_thermal_base`), so the estimate decides which rows reach the buffer.
Two questions, two subcommands:

    python -m evaluation.thermal_top replay
        Beacons from the Parquet archive, joined to the historical meteo cache
        on /seed's (0.5 deg, UTC hour) bucket, AGL from the cached Copernicus
        lattice.  A 2x2 table per label (which filter keeps, which drops), and
        how well each estimate predicts how high thermalling aircraft actually
        climbed in each bucket.  Old rows the buffer cannot show — the ones the
        old filter dropped — are only visible here.

    python -m evaluation.thermal_top buffer --buffer X.npz --seed 0 --seed 1 --seed 2
        A buffer collected under the old filter, with the new one applied on
        top.  Counts by label, then a paired comparison on identical folds: a
        model trained on every row against one trained only on the rows the new
        filter keeps, both scored within (day, cell, hour) on the same rows.
        --write-filtered saves the filtered buffer for evaluation.evaluate.

The old formula is recomputed from temp_2m and temp_850 rather than read from
`cape_base`: cache entries written since the switch carry the new value under
that key, for a rollback (meteo_client._with_legacy_base).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np

from data.meteo_client import is_fallback_row, thermal_top_agl, thermal_top_columns
from evaluation.holdout import decision_ids, earlystop_mask, group_ids, grouped_folds
from evaluation.metrics import grouped_auc, paired_delta_ci
from evaluation.regions import region_of

_COL_ALT_AGL = 21


def legacy_cape_base(meteo: dict) -> float:
    """The formula thermal_top_agl replaced, exactly as it was computed."""
    t2m, t850 = meteo["temp_2m"], meteo["temp_850"]
    return max(0, int(((t2m - 8) / max(1, (t2m - t850) / 1.5)) * 1000))


def _margin_floor():
    from models.thermal_model import _MIN_SAMPLE_AGL, _THERMAL_BASE_MARGIN_M
    return _THERMAL_BASE_MARGIN_M, _MIN_SAMPLE_AGL


# --- replay ------------------------------------------------------------------

def _load_meteo(path: str, min_ghi: float) -> dict:
    out = {}
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue                       # a partial last line
            v = r.get("v")
            if not v or v.get("solar_ghi", 0) < min_ghi:
                continue
            lat, lon, stamp = r["k"].split("|")
            out[(float(lat), float(lon), stamp)] = v
    return out


def _load_lattice(terrain_dir: str) -> dict:
    """Every cached 11x11 coarse grid, as one (lat*100, lon*100) -> metres lookup."""
    lattice = {}
    ii, jj = np.meshgrid(np.arange(11), np.arange(11), indexing="ij")
    for p in glob.glob(os.path.join(terrain_dir, "g_*_0.050.npy")):
        parts = os.path.basename(p)[2:-4].split("_")
        g = np.load(p)
        if g.shape != (11, 11):
            continue
        la = round(float(parts[0]) * 100) - 5 + ii.ravel()
        lo = round(float(parts[1]) * 100) - 5 + jj.ravel()
        lattice.update(zip(zip(la.tolist(), lo.tolist()), g.ravel().tolist()))
    return lattice


def _ground(lattice: dict, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Bilinear between lattice points — what the production upsample computes."""
    out = np.full(len(lat), np.nan)
    y, x = lat * 100, lon * 100
    y0, x0 = np.floor(y).astype(int), np.floor(x).astype(int)
    fy, fx = y - y0, x - x0
    for i in range(len(lat)):
        c = [lattice.get((y0[i] + a, x0[i] + b)) for a, b in ((0, 0), (0, 1), (1, 0), (1, 1))]
        if None in c:
            continue
        out[i] = (c[0] * (1 - fx[i]) * (1 - fy[i]) + c[1] * fx[i] * (1 - fy[i])
                  + c[2] * (1 - fx[i]) * fy[i] + c[3] * fx[i] * fy[i])
    return out


def replay(args) -> int:
    import pyarrow.dataset as ds
    from config import ARCHIVE_DIR
    from data import meteo_client, terrain_client
    from data.ogn_client import SOARING_AC_TYPES
    from models.thermal_model import (_MIN_SOLAR_GHI, _NEGATIVE_MAX_VARIO,
                                      _POSITIVE_MIN_VARIO)
    margin, floor = _margin_floor()

    meteo = _load_meteo(args.meteo_cache or str(meteo_client._hist_path()), _MIN_SOLAR_GHI)
    lattice = _load_lattice(args.terrain_cache or str(terrain_client._CACHE_DIR))
    print(f"daylight meteo buckets {len(meteo):,}, lattice points {len(lattice):,}")

    dataset = ds.dataset(str(args.archive or ARCHIVE_DIR), format="parquet",
                         partitioning="hive")
    labelable = (((ds.field("circling") == 1) & (ds.field("vario") > _POSITIVE_MIN_VARIO))
                 | ((ds.field("circling") == 0) & (ds.field("vario") < _NEGATIVE_MAX_VARIO)))
    snap = lambda v: np.round(np.round(v * 2) / 2, 1)      # /seed's bucket snap
    cols = defaultdict(list)
    for day in sorted({k[2][:10] for k in meteo}):
        filt = ((ds.field("date") == day) & (ds.field("under_tow") == 0)
                & ds.field("ac_type").isin(sorted(SOARING_AC_TYPES)) & labelable)
        t = dataset.to_table(columns=["ts", "id", "lat", "lon", "alt", "circling"],
                             filter=filt)
        if t.num_rows == 0:
            continue
        lat = t.column("lat").to_numpy().astype(float)
        lon = t.column("lon").to_numpy().astype(float)
        stamps = t.column("ts").to_numpy().astype("datetime64[h]").astype(str)
        keys = list(zip(snap(lat).tolist(), snap(lon).tolist(), stamps.tolist()))
        idx = np.flatnonzero([k in meteo for k in keys])
        if not len(idx):
            continue
        for name in ("lat", "lon", "alt", "circling"):
            cols[name].append(t.column(name).to_numpy()[idx].astype(float))
        ids = t.column("id").to_pylist()
        cols["unit"].extend(f"{ids[i]}|{keys[i][0]}|{keys[i][1]}|{keys[i][2]}" for i in idx)
        cols["key"].extend(keys[i] for i in idx)

    if not cols["key"]:
        print("no archived beacon falls in a cached daylight bucket")
        return 2
    lat, lon = np.concatenate(cols["lat"]), np.concatenate(cols["lon"])
    alt, y = np.concatenate(cols["alt"]), np.concatenate(cols["circling"]).astype(int)
    m = [meteo[k] for k in cols["key"]]
    old = np.array([legacy_cape_base(v) for v in m], dtype=float)
    new = np.array([thermal_top_agl(v) for v in m], dtype=float)
    agl = alt - _ground(lattice, lat, lon)
    ok = ~np.isnan(agl) & (agl >= floor)
    print(f"labelled beacons {len(y):,}; terrain known for "
          f"{np.mean(~np.isnan(agl)) * 100:.1f}%; {ok.sum():,} at or above {floor:.0f} m AGL")

    lat, lon, agl, y, old, new = lat[ok], lon[ok], agl[ok], y[ok], old[ok], new[ok]
    unit = np.array(cols["unit"], dtype=object)[ok]
    bucket = np.array([u.split("|", 1)[1] for u in unit], dtype=object)
    # The old function treated 0 as "no estimate"; the new one only None (NaN).
    old_drop = (old > 0) & (agl > old + margin)
    new_drop = ~np.isnan(new) & (agl > new + margin)
    region = region_of(np.column_stack([lat, lon]))

    _, inv, counts = np.unique(unit, return_inverse=True, return_counts=True)
    w = 1.0 / counts[inv]                     # each aircraft-hour counts once

    def table(mask, title):
        print(f"\n{title}")
        print(f"  {'':9s} {'beacons':>9s} {'both keep':>10s} {'old drops':>10s} "
              f"{'new drops':>10s} {'both drop':>10s} | keeps: old    new   "
              f"(per aircraft-hour: old    new)")
        for lab, name in ((1, "positive"), (0, "negative")):
            s = mask & (y == lab)
            n = int(s.sum())
            if not n:
                continue
            q = [int((s & a & b).sum()) for a, b in ((~old_drop, ~new_drop), (old_drop, ~new_drop),
                                                      (~old_drop, new_drop), (old_drop, new_drop))]
            W = w[s].sum()
            print(f"  {name:9s} {n:9,d} {q[0]:10,d} {q[1]:10,d} {q[2]:10,d} {q[3]:10,d} | "
                  f"{(n - q[1] - q[3]) / n * 100:5.1f}% {(n - q[2] - q[3]) / n * 100:5.1f}%   "
                  f"{w[s & ~old_drop].sum() / W * 100:5.1f}% {w[s & ~new_drop].sum() / W * 100:5.1f}%")

    table(np.ones(len(y), dtype=bool), "all regions (old drops = dropped by the old filter only)")
    for r in sorted(set(region)):
        table(region == r, r)

    # How high do thermalling aircraft get, against each estimate?  A thermal top
    # is a claim about exactly that, so the p95 climb height per bucket is the
    # nearest thing to an observation of it the feed carries.
    print(f"\nPer bucket with >= {args.min_aircraft} thermalling aircraft: p95 height of "
          f"climbing-circling beacons against each estimate")
    pos = y == 1
    rows = []
    for b in np.unique(bucket[pos]):
        s = pos & (bucket == b)
        if len(set(unit[s])) < args.min_aircraft:
            continue
        i = np.flatnonzero(s)[0]
        rows.append((np.percentile(agl[s], 95), old[i], new[i]))
    rows = np.array(rows)
    print(f"  {len(rows)} buckets; observed p95 median {np.median(rows[:, 0]):.0f} m")
    for j, name in ((1, "old cape_base"), (2, "min(PBL, LCL)")):
        est, obs = rows[:, j], rows[:, 0]
        use = est > 0
        lr = np.log(obs[use] / est[use])
        from scipy.stats import spearmanr
        print(f"  {name:14s} usable {use.mean() * 100:5.1f}%  median obs/est "
              f"{np.exp(np.median(lr)):.2f}  IQR(log) {np.subtract(*np.percentile(lr, [75, 25])):.2f}  "
              f"Spearman {spearmanr(obs[use], est[use])[0]:.3f}  median |miss| "
              f"{np.median(np.abs(obs[use] - est[use])):.0f} m  p90 estimate "
              f"{np.percentile(est, 90):.0f} m")
    return 0


# --- buffer ------------------------------------------------------------------

def buffer(args) -> int:
    from evaluation.evaluate import _fit, _terrain_multiplier
    margin, floor = _margin_floor()

    with np.load(args.buffer, allow_pickle=False) as d:
        X, y = d["X"], d["y"].astype(int)
    # What load() and fit_and_gate do before anything trains.
    fb = is_fallback_row(X)
    X, y = X[~fb], y[~fb]
    keep = X[:, _COL_ALT_AGL] >= floor
    X, y = X[keep], y[keep]
    top = thermal_top_columns(X)
    kept = ~(X[:, _COL_ALT_AGL] > top + margin)          # NaN top: kept
    print(f"buffer {len(y):,} rows ({int(fb.sum())} fallback rows and the AGL floor "
          f"removed), {int(y.sum()):,} positives")
    for lab, name in ((1, "positive"), (0, "negative")):
        s = y == lab
        print(f"  {name}: new filter drops {int((s & ~kept).sum()):,} of {int(s.sum()):,} "
              f"({(s & ~kept).sum() / s.sum() * 100:.1f}%)")
    region = region_of(X)
    for r in sorted(set(region)):
        s = region == r
        print(f"  {r:8s} drops {(s & ~kept & (y == 1)).sum():5,d} positives, "
              f"{(s & ~kept & (y == 0)).sum():6,d} negatives of {s.sum():,} rows")

    if args.write_filtered:
        np.savez(args.write_filtered, X=X[kept], y=y[kept])
        print(f"wrote {kept.sum():,} rows to {args.write_filtered}")

    gfold, ge = group_ids(X), decision_ids(X)
    tmul = _terrain_multiplier(X)
    for seed in args.seed or [0]:
        a, b = np.full(len(y), np.nan), np.full(len(y), np.nan)
        for train, test in grouped_folds(gfold, k=args.folds, seed=seed):
            tr = np.flatnonzero(train)
            es = earlystop_mask(gfold[tr])
            if es.all() or not es.any():
                es = np.zeros(len(tr), dtype=bool)
            fit_a = _fit(X[tr[~es]], y[tr[~es]], X[tr[es]], y[tr[es]])
            kt = kept[tr]
            fit_b = _fit(X[tr[~es & kt]], y[tr[~es & kt]], X[tr[es & kt]], y[tr[es & kt]])
            a[test] = fit_a.predict_proba(X[test])[:, 1]
            b[test] = fit_b.predict_proba(X[test])[:, 1]
        sa, sb = a * tmul, b * tmul
        print(f"\nseed {seed}: served score within (day, cell, hour), "
              f"trained on all rows vs on the rows the new filter keeps")
        for name, m in (("rows the new filter keeps", kept),
                        ("every row", np.ones(len(y), dtype=bool))):
            ra = grouped_auc(sa[m], y[m], ge[m])
            rb = grouped_auc(sb[m], y[m], ge[m])
            dlt = paired_delta_ci(sa[m], sb[m], y[m], ge[m], n_boot=args.boot, seed=seed)
            print(f"  scored on {name:26s} all {ra['micro']:.3f}  filtered {rb['micro']:.3f}  "
                  f"delta {dlt['point']:+.3f} [{dlt['lo']:+.3f}, {dlt['hi']:+.3f}] "
                  f"over {dlt['groups']} units")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--archive", help="Parquet archive root (default config.ARCHIVE_DIR)")
    r.add_argument("--meteo-cache", help="meteo_historical.jsonl (default: the app's)")
    r.add_argument("--terrain-cache", help="Copernicus grid cache dir (default: the app's)")
    r.add_argument("--min-aircraft", type=int, default=3)
    b = sub.add_parser("buffer")
    b.add_argument("--buffer", required=True)
    b.add_argument("--seed", type=int, action="append")
    b.add_argument("--folds", type=int, default=5)
    b.add_argument("--boot", type=int, default=1500)
    b.add_argument("--write-filtered", help="save the buffer with the new filter applied")
    args = ap.parse_args()
    return replay(args) if args.cmd == "replay" else buffer(args)


if __name__ == "__main__":
    raise SystemExit(main())
