"""
Offline evaluator: score any candidate against the frozen benchmark set.

Run from the backend package root:

    python -m evaluation.evaluate
    python -m evaluation.evaluate --variant base --variant solar

Trains each feature-set variant on the identical train split, scores every
candidate on the identical frozen benchmark, and reports a bootstrap interval
for each plus a *paired* interval for the difference between variants.

Two properties make this able to answer questions the live gate could not:

  * every candidate meets the same benchmark rows, so scores are comparable
    across runs, across variants, and across days;
  * baselines are scored alongside the model.  If XGBoost cannot beat the
    terrain heuristic it is already multiplied by, the model is not earning its
    place — and nothing in the codebase previously reported that.
"""

import argparse
import numpy as np

from config import BUFFER_PATH, DATA_DIR
from evaluation.holdout import (decision_ids, earlystop_mask, group_ids,
                                grouped_folds, split)
from evaluation.metrics import CHANCE, bootstrap_ci, grouped_auc, paired_delta_ci
from evaluation.regions import NAMES as REGION_NAMES, region_of
from pipeline.feature_engineering import FEATURE_COUNT
from pipeline.solar import cos_incidence, sun_position

_COL_LAT, _COL_LON, _COL_SLOPE, _COL_ASPECT = 0, 1, 3, 4
# Scored as a baseline in its own right.  It is the one column that describes the
# *aircraft* rather than the ground, so a model that cannot beat it has learned
# who is flying high, not where the lift is — which is exactly what happened, and
# went unnoticed for want of this row in the table.
_COL_ALT_AGL = 21
_COL_HOUR_SIN, _COL_HOUR_COS = 15, 16
_COL_DOY_SIN, _COL_DOY_COS = 17, 18


def _decode_cyclic(sin_v, cos_v, period):
    return (np.arctan2(sin_v, cos_v) % (2 * np.pi)) / (2 * np.pi) * period


def solar_column(X: np.ndarray) -> np.ndarray:
    """
    cos(incidence) retrofitted onto existing buffer rows.

    Every input is already present in the matrix, so the whole variant can be
    A/B'd on samples collected before the feature existed — no recollection and
    no waiting for a fresh buffer.  The year is not recoverable from the cyclic
    encoding, but solar declination repeats annually to well within the
    accuracy this needs, so day-of-year alone is sufficient.
    """
    hour = _decode_cyclic(X[:, _COL_HOUR_SIN], X[:, _COL_HOUR_COS], 24)
    doy = _decode_cyclic(X[:, _COL_DOY_SIN], X[:, _COL_DOY_COS], 365)
    elev, azim = sun_position(X[:, _COL_LAT], X[:, _COL_LON], doy, hour)
    return cos_incidence(X[:, _COL_SLOPE], X[:, _COL_ASPECT], elev, azim)


_COL_HEAT, _COL_ALBEDO = 13, 14

# Land cover is fetched per 0.5deg area rather than per row: 5,149 buffer rows
# occupy only ~411 such areas, against ~1,414 at 0.1deg, and one 51x51 grid over
# 0.5deg still resolves 0.01deg cells — same resolution, a quarter of the
# requests.
_LC_SNAP = 0.5
_LC_RADIUS = 0.25
_LC_SHAPE = (51, 51)
_LC_CACHE = DATA_DIR / "cache" / "buffer_landcover.npz"


def row_datetimes(X: np.ndarray, year: int | None = None):
    """
    Reconstruct each row's UTC timestamp from its cyclic time columns.

    The year is not recoverable from a sin/cos encoding of day-of-year, so it is
    assumed to be the current one.  That is only used to look up lagged history,
    where being a year out would simply find nothing rather than find something
    wrong.
    """
    from datetime import datetime, timedelta, timezone
    if year is None:
        year = datetime.now(timezone.utc).year
    hour = _decode_cyclic(X[:, _COL_HOUR_SIN], X[:, _COL_HOUR_COS], 24)
    doy = _decode_cyclic(X[:, _COL_DOY_SIN], X[:, _COL_DOY_COS], 365)
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    return [base + timedelta(days=float(d) - 1, hours=float(h))
            for d, h in zip(doy, hour)]


def _lc_keys(X: np.ndarray):
    lat = np.round(X[:, _COL_LAT] / _LC_SNAP) * _LC_SNAP
    lon = np.round(X[:, _COL_LON] / _LC_SNAP) * _LC_SNAP
    return lat, lon


# Per-point results, not per-area.  Caching an area's representative value
# would make a rerun disagree with the run that populated it — every row in the
# area would collapse onto whichever row happened to be written last, quietly
# coarsening the feature between runs.  Points are also what the fold
# transforms ask for repeatedly: oof_scores calls each transform twice per
# fold, so without this the whole fetch would run ten times over.
_lc_points: dict[tuple[float, float], tuple[float, float]] = {}


def _load_lc_cache() -> None:
    if _lc_points or not _LC_CACHE.exists():
        return
    try:
        with np.load(_LC_CACHE, allow_pickle=False) as d:
            for la, lo, h, a in zip(d["lat"], d["lon"], d["heat"], d["albedo"]):
                _lc_points[(float(la), float(lo))] = (float(h), float(a))
    except Exception:
        _lc_points.clear()


def _save_lc_cache() -> None:
    try:
        _LC_CACHE.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted(_lc_points)
        np.savez(
            _LC_CACHE,
            lat=np.array([k[0] for k in keys]),
            lon=np.array([k[1] for k in keys]),
            heat=np.array([_lc_points[k][0] for k in keys]),
            albedo=np.array([_lc_points[k][1] for k in keys]),
        )
    except Exception:
        pass


def landcover_columns(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(heat, albedo) per row from ESA WorldCover, memoised by exact point."""
    import asyncio
    from data.landcover_client import fetch_landcover_props

    _load_lc_cache()
    pts = [(round(float(la), 6), round(float(lo), 6))
           for la, lo in zip(X[:, _COL_LAT], X[:, _COL_LON])]
    todo = sorted({p for p in pts if p not in _lc_points})

    if todo:
        # Group by 0.5deg area so one grid fetch serves every point inside it.
        by_area: dict[tuple[float, float], list] = {}
        for lat, lon in todo:
            key = (round(round(lat / _LC_SNAP) * _LC_SNAP, 3),
                   round(round(lon / _LC_SNAP) * _LC_SNAP, 3))
            by_area.setdefault(key, []).append((lat, lon))

        async def _fill():
            for n, (area, points) in enumerate(sorted(by_area.items()), 1):
                try:
                    heat, albedo = await fetch_landcover_props(
                        area[0], area[1], radius=_LC_RADIUS, shape=_LC_SHAPE
                    )
                    rows, cols = heat.shape
                    for lat, lon in points:
                        gi = int(round((lat - (area[0] - _LC_RADIUS)) / (2 * _LC_RADIUS) * (rows - 1)))
                        gj = int(round((lon - (area[1] - _LC_RADIUS)) / (2 * _LC_RADIUS) * (cols - 1)))
                        gi, gj = min(max(gi, 0), rows - 1), min(max(gj, 0), cols - 1)
                        _lc_points[(lat, lon)] = (float(heat[gi, gj]), float(albedo[gi, gj]))
                except Exception:
                    for p in points:
                        _lc_points[p] = (0.4, 0.2)      # neutral default
                if n % 50 == 0:
                    print(f"  landcover {n}/{len(by_area)} areas", flush=True)

        print(f"fetching land cover: {len(todo)} new points in "
              f"{len(by_area)} areas", flush=True)
        asyncio.run(_fill())
        _save_lc_cache()

    vals = np.array([_lc_points[p] for p in pts])
    return vals[:, 0], vals[:, 1]


_prior_ready = False


def prior_column(X: np.ndarray) -> np.ndarray:
    """
    Lagged per-cell climb rate from this project's own beacon history.

    The hourly aggregate is rebuilt on first use rather than assumed present, so
    the comparison always reflects the beacons currently on disk instead of
    whatever a stale index happened to hold.
    """
    global _prior_ready
    from config import DB_PATH
    from data.circling_prior import ClimbPrior, build_index

    if not _prior_ready:
        stats = build_index(DB_PATH)
        print(f"climb prior: {stats.get('buckets', 0)} buckets over "
              f"{stats.get('hours', 0)} h, global rate "
              f"{stats.get('global_rate', 0):.4f}", flush=True)
        _prior_ready = True
    return ClimbPrior(DB_PATH).values(X[:, _COL_LAT], X[:, _COL_LON], row_datetimes(X))


def _with_landcover(X: np.ndarray) -> np.ndarray:
    """Replace the two constant land-use columns in place — no width change."""
    heat, albedo = landcover_columns(X)
    out = X.copy()
    out[:, _COL_HEAT] = heat
    out[:, _COL_ALBEDO] = albedo
    return out


VARIANTS = {
    "base": lambda X: X,
    "solar": lambda X: np.column_stack([X, solar_column(X)]),
    "landcover": _with_landcover,
    "prior": lambda X: np.column_stack([X, prior_column(X)]),
    "both": lambda X: np.column_stack([_with_landcover(X), prior_column(X)]),
}


def _bare_model():
    """A ThermalModel with nothing loaded — __init__ touches no disk or network."""
    from models.thermal_model import ThermalModel
    return ThermalModel()


def _terrain_multiplier(X: np.ndarray) -> np.ndarray:
    """The heuristic production multiplies every model output by."""
    return _bare_model()._terrain_multiplier(X)


def _physics(X: np.ndarray) -> np.ndarray:
    return np.asarray(_bare_model()._physics_fallback(X))


def _fit(Xtr, ytr, Xes, yes):
    """Fit with production hyperparameters, early-stopping on a disjoint set."""
    import xgboost as xgb

    pos = int(ytr.sum())
    n = len(ytr)
    use_es = len(yes) > 0 and 0 < int(yes.sum()) < len(yes)
    clf = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=max(1.0, (n - pos) / max(1, pos)),
        tree_method="hist", n_jobs=-1, eval_metric="auc", random_state=42,
        early_stopping_rounds=20 if use_es else None,
    )
    if use_es:
        clf.fit(Xtr, ytr, eval_set=[(Xes, yes)], verbose=False)
    else:
        clf.fit(Xtr, ytr)
    return clf


def oof_scores(X, y, gids, transform, k=5, seed=0):
    """
    Out-of-fold predictions: every row scored by a model blind to its group.

    Early stopping gets its own slice of each training fold, so the round count
    is never chosen on the rows the score is read from.
    """
    out = np.full(len(y), np.nan)
    for train, test in grouped_folds(gids, k=k, seed=seed):
        Xt, yt = transform(X[train]), y[train]
        es = earlystop_mask(gids[train])
        if es.all() or not es.any():
            es = np.zeros(len(yt), dtype=bool)
        clf = _fit(Xt[~es], yt[~es], Xt[es], yt[es])
        out[test] = clf.predict_proba(transform(X[test]))[:, 1]
    return out


def _row(name, scores, y, gids, seed=0):
    stats = grouped_auc(scores, y, gids)
    ci = bootstrap_ci(scores, y, gids, seed=seed)
    return (name, stats["micro"], stats["macro"], ci["lo"], ci["hi"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buffer", default=BUFFER_PATH)
    ap.add_argument("--variant", action="append", choices=sorted(VARIANTS),
                    help="repeatable; defaults to base+solar")
    ap.add_argument("--mode", choices=("cv", "holdout"), default="cv",
                    help="cv: grouped K-fold over the whole buffer (more power). "
                         "holdout: the frozen benchmark the live gate uses.")
    ap.add_argument("--by-hour", action="store_true",
                    help="score within (day, cell, HOUR) instead of (day, cell) — "
                         "the unit one heatmap actually ranks over. Narrower, so "
                         "it cannot borrow credit from ranking one hour above "
                         "another. cv mode only.")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--no-agl-floor", action="store_true",
                    help="keep rows below _MIN_SAMPLE_AGL, which production "
                         "drops. Useful only to show the leak they cause.")
    args = ap.parse_args()
    variants = args.variant or ["base", "solar"]

    with np.load(args.buffer, allow_pickle=False) as d:
        X, y = d["X"], d["y"].astype(int)
    if X.shape[1] != FEATURE_COUNT:
        print(f"buffer has {X.shape[1]} features, expected {FEATURE_COUNT}")
        return 1

    print(f"buffer      {len(y)} rows, {int(y.sum())} positives ({y.mean()*100:.2f}%)")

    # Match what fit_and_gate trains on.  Without this the harness scores a
    # population production no longer sees, and its numbers stop being about the
    # served model — which is the entire point of the harness.
    if not args.no_agl_floor:
        from models.thermal_model import _MIN_SAMPLE_AGL
        keep = X[:, _COL_ALT_AGL] >= _MIN_SAMPLE_AGL
        if not keep.all():
            print(f"agl floor   {int((~keep).sum())} rows below {_MIN_SAMPLE_AGL:.0f} m "
                  f"AGL dropped, {int(y[~keep].sum())} of them positive "
                  f"(--no-agl-floor to keep them)")
            X, y = X[keep], y[keep]

    if args.mode == "holdout":
        (Xtr, ytr, _), (Xes, yes, _), (Xe, ye, ge) = split(X, y)
        print(f"split       train {len(ytr)} / earlystop {len(yes)} / "
              f"benchmark {len(ye)}  (group-disjoint, frozen)")
        scored = {}
        for name in variants:
            tf = VARIANTS[name]
            clf = _fit(tf(Xtr), ytr, tf(Xes), yes)
            scored[name] = clf.predict_proba(tf(Xe))[:, 1]
    else:
        # Folds are always cut on the coarse key even when scoring by hour: two
        # hours of the same day over the same ground share weather and terrain,
        # so splitting between them would put near-duplicates on both sides —
        # the leak the group-disjoint split exists to prevent.  The hour only
        # narrows what a *comparison* is allowed to pool.
        gfold = group_ids(X)
        ge = decision_ids(X) if args.by_hour else gfold
        Xe, ye = X, y
        print(f"split       grouped {args.folds}-fold CV over "
              f"{len(np.unique(gfold))} groups (out-of-fold predictions)"
              + (f", scored within {len(np.unique(ge))} (day, cell, hour) units"
                 if args.by_hour else ""))
        scored = {
            name: oof_scores(X, y, gfold, VARIANTS[name], k=args.folds)
            for name in variants
        }

    stats = grouped_auc(np.zeros(len(ye)), ye, ge)
    print(f"evaluated   {len(np.unique(ge))} groups, {stats['groups']} mixed-label, "
          f"{stats['pairs']} pairs, {int(ye.sum())} positives")
    if stats["groups"] == 0:
        print("\nNo mixed-label groups — nothing is measurable yet.")
        return 2
    print()

    rows = [
        _row("constant (chance)", np.full(len(ye), 0.5), ye, ge),
        _row("terrain_multiplier", _terrain_multiplier(Xe), ye, ge),
        _row("physics_fallback", _physics(Xe), ye, ge),
        _row("cos_incidence alone", solar_column(Xe), ye, ge),
        _row("alt_agl alone", Xe[:, _COL_ALT_AGL], ye, ge),
    ]

    served = {}
    for name in variants:
        # Production multiplies the model output by the terrain heuristic, so
        # that product is what has to be scored.  MC noise is deliberately not
        # applied: it perturbs meteo columns, which are constant within a group,
        # so it cannot change within-group ranking and only adds variance.
        served[name] = scored[name] * _terrain_multiplier(Xe)
        rows.append(_row(f"xgb {name} (raw)", scored[name], ye, ge))
        rows.append(_row(f"xgb {name} (served)", served[name], ye, ge))

    width = max(len(r[0]) for r in rows)
    print(f"{'ranker'.ljust(width)}   micro   macro   95% CI")
    print("-" * (width + 32))
    for name, micro, macro, lo, hi in rows:
        flag = "" if lo > CHANCE else "   (not above chance)"
        print(f"{name.ljust(width)}   {micro:.3f}   {macro:.3f}   "
              f"[{lo:.3f}, {hi:.3f}]{flag}")

    if len(variants) == 2:
        a, b = variants
        d = paired_delta_ci(served[a], served[b], ye, ge, n_boot=args.boot)
        verdict = (f"{b} better" if d["lo"] > 0 else
                   f"{a} better" if d["hi"] < 0 else
                   "inconclusive - interval spans 0")
        print(f"\npaired delta (served): {b} - {a} = {d['point']:+.3f}  "
              f"[{d['lo']:+.3f}, {d['hi']:+.3f}]  over {d['groups']} groups")
        print(f"verdict: {verdict}")

    _report_regions(Xe, ye, ge, served, n_boot=args.boot)
    return 0


# Below this many mixed-label groups a regional score is reported as not
# measurable.  Same threshold the live gate uses before it trusts a benchmark.
_MIN_REGION_GROUPS = 30


def _report_regions(Xe, ye, ge, served: dict, n_boot: int) -> None:
    """
    Model against the terrain heuristic, separately per region.

    Groups never straddle a region (a group is one 0.5deg cell), so slicing rows
    by region slices groups cleanly and each regional score is a real
    within-group AUC, not an average over borders.
    """
    reg = region_of(Xe)
    terrain = _terrain_multiplier(Xe)
    names = REGION_NAMES
    print("\nby region (served model vs terrain_multiplier)")
    print(f"{'region':10s} {'rows':>6s} {'pos':>5s} {'groups':>6s}   "
          + "   ".join(f"{v:>22s}" for v in served) + "   terrain   delta vs terrain")
    for name in names:
        m = reg == name
        if not m.any():
            print(f"{name:10s} {0:>6d} {0:>5d} {0:>6d}   no data")
            continue
        stats = grouped_auc(np.zeros(int(m.sum())), ye[m], ge[m])
        head = f"{name:10s} {int(m.sum()):>6d} {int(ye[m].sum()):>5d} {stats['groups']:>6d}   "
        if stats["groups"] < _MIN_REGION_GROUPS:
            print(head + f"not measurable ({stats['groups']} mixed-label groups, "
                         f"need {_MIN_REGION_GROUPS})")
            continue
        cells = []
        for v, s in served.items():
            ci = bootstrap_ci(s[m], ye[m], ge[m], n_boot=n_boot)
            cells.append(f"{ci['point']:.3f} [{ci['lo']:.3f},{ci['hi']:.3f}]")
        t = grouped_auc(terrain[m], ye[m], ge[m])["micro"]
        first = next(iter(served.values()))
        d = paired_delta_ci(terrain[m], first[m], ye[m], ge[m], n_boot=n_boot)
        print(head + "   ".join(f"{c:>22s}" for c in cells)
              + f"   {t:.3f}     {d['point']:+.3f} [{d['lo']:+.3f},{d['hi']:+.3f}]")


if __name__ == "__main__":
    raise SystemExit(main())
