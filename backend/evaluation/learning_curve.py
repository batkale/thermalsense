"""
Retrospective learning curve: does the model rank lift better on a day it has
never seen once it has been trained on more days?

Run from the backend package root:

    python -m evaluation.learning_curve --buffer path/to/training_buffer.npz \
        --seed 0 --seed 1 --seed 2

The live gate cannot answer this.  Its benchmark is the frozen 1-in-5 slice of
whatever the buffer holds today, so the rows it scores on change every day: a
score that rises could be easier days arriving rather than a better model.
The forward answer is the daily score history (models/score_history.py), which
needs a week of nightly model copies before its first comparison.  This one
answers from the buffer already collected, by replaying it.

For every day D in the buffer, fit the production model twice, exactly as
ThermalModel.fit_and_gate fits it (same hyperparameters, the rows `_fit_rows`
admits, the train side of `holdout.split` only, the same early-stopping slice):

    newer   on every buffer day before D
    older   on every buffer day before D - gap  (gap = 7 by default)

Neither has seen any of day D.  Both score all of D's rows (both sides of the
benchmark split — D is withheld by date, so the hash split has nothing left to
protect) the way `_score_served` does, model output x terrain multiplier, and
are compared within (day, 0.5deg cell, hour) units: `decision_ids`, the unit
one heatmap ranks over.  The paired delta newer - older is taken over the same
units, so how hard day D happens to be is common to both and cancels — which
is exactly what the gate's own score, read day after day, cannot do.

The answer is the pooled delta over all days.  A single day's interval spans
only that day's units and is usually wide.

**Read a positive delta with one confound in mind.**  The newer model has seen
more days *and* the seven nearest D.  The replay alone cannot say whether a
gain is quantity or recency (same season, same weather regime).  For the
production question — is today's model better than last week's on tomorrow —
that distinction does not matter; for "will 60 more days help" it does.

**Seeds.**  Production's split has no seed: benchmark and early-stopping sides
are hashes under fixed salts, and XGBoost runs at random_state=42.  `--seed 0`
reproduces that exactly.  `--seed k` re-salts both hashes and runs at
random_state=42+k, so a different fifth of the groups is withheld from
training, a different slice picks the boosting round and the booster's row and
column subsampling changes — the same kind of variation the fold seed brings
to evaluate.py, which alone moves the base score by ~0.03.  Report several.
"""

import argparse
import datetime as dt
from collections import defaultdict

import numpy as np

from config import BUFFER_PATH
from evaluation.holdout import (_BENCH_SALT, _EARLYSTOP_SALT, _HOLDOUT_MODULUS,
                                _decode_cyclic, _mask_for, decision_ids,
                                group_ids)
from evaluation.metrics import bootstrap_ci, grouped_auc, paired_delta_ci

_COL_DOY_SIN, _COL_DOY_COS = 17, 18

# The year is not in the features; it is only used to print a day as a date.
_YEAR = 2026


def row_days(X: np.ndarray) -> np.ndarray:
    """Day-of-year per row, decoded exactly as holdout.group_ids decodes it."""
    return (np.round(_decode_cyclic(X[:, _COL_DOY_SIN], X[:, _COL_DOY_COS], 365))
            .astype(np.int64) % 365)


def day_label(doy: int, year: int = _YEAR) -> str:
    # tm_yday is 1-based, and `% 365` turns 31 Dec into 0.
    return (dt.date(year, 1, 1) + dt.timedelta(days=int(doy or 365) - 1)).isoformat()


def _salt(base: bytes, seed: int) -> bytes:
    return base if seed == 0 else base + f"/seed{seed}".encode()


def split_sides(X: np.ndarray, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """
    (fit, earlystop) row masks: the train side of `holdout.split`, as masks.

    At seed 0 these are the rows split() returns as its first two parts (a test
    pins it).  The early-stopping hash is a per-group decision, so computing it
    over all rows and then dropping the benchmark side is the same as computing
    it over the train side the way split() does.
    """
    gids = group_ids(X)
    bench = _mask_for(gids, _salt(_BENCH_SALT, seed), _HOLDOUT_MODULUS)
    es = _mask_for(gids, _salt(_EARLYSTOP_SALT, seed), _HOLDOUT_MODULUS)
    return ~bench & ~es, ~bench & es


def fit_like_production(Xf, yf, Xes, yes, random_state: int = 42, n_jobs: int = -1):
    """The XGBClassifier fit_and_gate builds, on the rows it would give it."""
    import xgboost as xgb

    use_es = len(yes) > 0 and 0 < int(yes.sum()) < len(yes)
    fit_pos = int(yf.sum())
    clf = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=max(1.0, (len(yf) - fit_pos) / max(1, fit_pos)),
        tree_method="hist",
        n_jobs=n_jobs,
        eval_metric="auc",
        random_state=random_state,
        early_stopping_rounds=20 if use_es else None,
    )
    if use_es:
        clf.fit(Xf, yf, eval_set=[(Xes, yes)], verbose=False)
    else:
        clf.fit(Xf, yf)
    return clf


def load_population(path: str) -> tuple[np.ndarray, np.ndarray, dict]:
    """
    The rows a production fit would see, from a buffer file.

    load() drops rows that are not coordinates on Earth and rows built from the
    synthetic weather fallback; fit_and_gate then keeps only `_fit_rows` (the
    AGL floor and the thermal top).  Both are applied here, so the population
    is the one the gate scores — not the file as written.
    """
    from data.meteo_client import is_fallback_row
    from models.thermal_model import _fit_rows, _plausible_coords
    from pipeline.feature_engineering import FEATURE_COUNT, LEGACY_WIDTHS

    with np.load(path, allow_pickle=False) as d:
        X, y = d["X"], d["y"].astype(int)
    if X.shape[1] != FEATURE_COUNT and X.shape[1] not in LEGACY_WIDTHS:
        raise SystemExit(f"buffer has {X.shape[1]} features, expected {FEATURE_COUNT}")

    info = {"file_rows": len(y), "file_positives": int(y.sum()), "width": X.shape[1]}
    keep = _plausible_coords(X) & ~is_fallback_row(X)
    info["load_dropped"] = int((~keep).sum())
    X, y = X[keep], y[keep]
    fit = _fit_rows(X)
    info["fit_dropped"] = int((~fit).sum())
    return X[fit], y[fit], info


class Replay:
    """Production fits on every buffer day before a cutoff, cached by day set."""

    def __init__(self, X, y, seed: int = 0, n_jobs: int = -1):
        from models import thermal_model as tm

        self.X, self.y, self.seed, self.n_jobs = X, y, seed, n_jobs
        self.days = row_days(X)
        self.uniq = np.unique(self.days)
        self.fit_side, self.es_side = split_sides(X, seed)
        self._tm = tm
        self._bare = tm.ThermalModel()   # __init__ touches no disk or network
        self._models: dict[int, dict | None] = {}

    def model_before(self, cutoff: int) -> dict | None:
        """
        The production fit on every day < cutoff, or None where it would refuse.

        Keyed on how many buffer days fall before the cutoff, so two cutoffs
        spanning the same days (a gap in the buffer) share one fit.
        """
        k = int(np.searchsorted(self.uniq, cutoff, side="left"))
        if k not in self._models:
            self._models[k] = self._fit(k)
        return self._models[k]

    def _fit(self, k: int) -> dict | None:
        tm = self._tm
        seen = np.isin(self.days, self.uniq[:k])
        # fit_and_gate's own refusals, on the rows it checks them on.
        n, pos = int(seen.sum()), int(self.y[seen].sum())
        if n < tm._MIN_SAMPLES or pos < tm._MIN_POSITIVES:
            return None
        f, es = seen & self.fit_side, seen & self.es_side
        yf = self.y[f]
        if len(yf) == 0 or yf.min() == yf.max():
            return None
        clf = fit_like_production(self.X[f], yf, self.X[es], self.y[es],
                                  random_state=42 + self.seed, n_jobs=self.n_jobs)
        return {"clf": clf, "days": k,
                "rows": int((f | es).sum()), "positives": int(self.y[f | es].sum())}

    def served(self, model: dict, X: np.ndarray) -> np.ndarray:
        return self._bare._score_served(model["clf"], X)

    def terrain(self, X: np.ndarray) -> np.ndarray:
        return self._bare._terrain_multiplier(X)


def run(X, y, seed: int = 0, gap: int = 7, min_units: int = 10,
        n_boot: int = 2000, n_jobs: int = -1, verbose: bool = True,
        cv_reference: bool = False) -> dict:
    """Replay every day; return per-day rows and the pooled arrays."""
    rp = Replay(X, y, seed=seed, n_jobs=n_jobs)
    days, skipped = [], defaultdict(list)
    pooled = defaultdict(list)

    for D in rp.uniq:
        ev = rp.days == D
        Xd, yd = X[ev], y[ev]
        gd = decision_ids(Xd)
        units = grouped_auc(np.zeros(len(yd)), yd, gd)["groups"]
        if units < min_units:
            skipped["fewer than --min-units mixed units"].append(int(D))
            continue
        new, old = rp.model_before(D), rp.model_before(D - gap)
        if new is None or old is None:
            skipped["too little history to fit"].append(int(D))
            continue
        if new["days"] == old["days"]:
            # No buffer day falls in [D - gap, D): the two fits are one model.
            skipped[f"no buffer day in the {gap} before"].append(int(D))
            continue

        s_new, s_old, terr = rp.served(new, Xd), rp.served(old, Xd), rp.terrain(Xd)
        d = paired_delta_ci(s_old, s_new, yd, gd, n_boot=n_boot, seed=seed)
        days.append({
            "day": int(D), "rows": len(yd), "positives": int(yd.sum()), "units": units,
            "new_days": new["days"], "old_days": old["days"],
            "new_rows": new["rows"], "old_rows": old["rows"],
            "new": grouped_auc(s_new, yd, gd)["micro"],
            "old": grouped_auc(s_old, yd, gd)["micro"],
            "terrain": grouped_auc(terr, yd, gd)["micro"],
            "delta": d,
        })
        for key, val in (("y", yd), ("g", gd), ("new", s_new), ("old", s_old),
                         ("terrain", terr), ("day", np.full(len(yd), D)),
                         ("idx", np.flatnonzero(ev))):
            pooled[key].append(val)

    pooled = {k: np.concatenate(v) for k, v in pooled.items()}
    out = {"seed": seed, "gap": gap, "days": days, "skipped": dict(skipped),
           "pooled": pooled, "fits": sum(m is not None for m in rp._models.values())}
    out["summary"] = summarise(pooled, n_boot=n_boot, seed=seed) if days else None
    if cv_reference and days:
        out["cv"] = cv_reference_scores(X, y, rp, pooled, seed=seed, n_boot=n_boot)
    if verbose:
        report(out)
    return out


def cv_reference_scores(X, y, rp: "Replay", pooled: dict, seed: int = 0,
                        n_boot: int = 2000) -> dict:
    """
    The same units, scored the way evaluate.py scores them.

    Grouped 5-fold CV over (day, 0.5deg cell): a row is scored by a model that
    never saw its own (day, cell) but did see other cells on the same day and
    every other day, later ones included.  That is the offline harness's
    number, and the gate's benchmark is the same kind of split.  Set beside
    the replay, the difference is how much of that number a model only has
    when it has already seen the day it is asked about.
    """
    from evaluation.evaluate import oof_scores

    oof = oof_scores(X, y, group_ids(X), lambda a: a, k=5, seed=seed)
    idx = pooled["idx"]
    cv = oof[idx] * rp.terrain(X[idx])
    yy, gg = pooled["y"], pooled["g"]
    return {"score": bootstrap_ci(cv, yy, gg, n_boot=n_boot, seed=seed),
            "new_minus_cv": paired_delta_ci(cv, pooled["new"], yy, gg,
                                            n_boot=n_boot, seed=seed)}


def summarise(p: dict, n_boot: int = 2000, seed: int = 0) -> dict:
    y, g = p["y"], p["g"]
    return {
        "units": grouped_auc(np.zeros(len(y)), y, g)["groups"],
        "new": bootstrap_ci(p["new"], y, g, n_boot=n_boot, seed=seed),
        "old": bootstrap_ci(p["old"], y, g, n_boot=n_boot, seed=seed),
        "terrain": bootstrap_ci(p["terrain"], y, g, n_boot=n_boot, seed=seed),
        "delta": paired_delta_ci(p["old"], p["new"], y, g, n_boot=n_boot, seed=seed),
        "over_terrain": paired_delta_ci(p["terrain"], p["new"], y, g,
                                        n_boot=n_boot, seed=seed),
    }


def _ci(c: dict, signed: bool = False) -> str:
    f = "{:+.3f}" if signed else "{:.3f}"
    return (f"{f.format(c['point'])} [{f.format(c['lo'])}, {f.format(c['hi'])}]")


def report(out: dict) -> None:
    gap = out["gap"]
    print(f"\n=== seed {out['seed']}: newer = all days before D, older = days before "
          f"D-{gap}; {out['fits']} fits ===")
    print(f"{'day':10} {'rows':>5} {'pos':>5} {'units':>5} {'train days':>11} "
          f"{'newer':>6} {'older':>6} {'delta newer-older [95% CI]':>28} {'terrain':>7}")
    for r in out["days"]:
        print(f"{day_label(r['day']):10} {r['rows']:5d} {r['positives']:5d} {r['units']:5d} "
              f"{r['new_days']:5d} /{r['old_days']:4d} "
              f"{r['new']:6.3f} {r['old']:6.3f} {_ci(r['delta'], True):>28} "
              f"{r['terrain']:7.3f}")
    for why, ds in out["skipped"].items():
        print(f"skipped ({why}): " + ", ".join(day_label(d)[5:] for d in ds))

    s = out["summary"]
    if s is None:
        print("no day could be scored")
        return
    deltas = np.array([r["delta"]["point"] for r in out["days"]])
    print(f"\npooled over {len(out['days'])} days, {s['units']} mixed (day, cell, hour) "
          f"units, intervals from resampling units:")
    print(f"  newer model           {_ci(s['new'])}")
    print(f"  older model           {_ci(s['old'])}")
    print(f"  terrain alone         {_ci(s['terrain'])}")
    print(f"  delta newer - older   {_ci(s['delta'], True)}")
    print(f"  newer - terrain       {_ci(s['over_terrain'], True)}")
    print(f"  days with delta > 0   {int((deltas > 0).sum())} of {len(deltas)}")
    if "cv" in out:
        print(f"  grouped-CV reference  {_ci(out['cv']['score'])}  (same units; has seen "
              f"other cells that day and later days)")
        print(f"  newer - CV reference  {_ci(out['cv']['new_minus_cv'], True)}")

    # By month of D: whether the gain shrinks or grows as the history lengthens.
    p = out["pooled"]
    months = np.array([day_label(d)[:7] for d in p["day"]])
    print(f"\n{'month':8} {'days':>4} {'units':>5} {'newer':>6} "
          f"{'delta newer-older [95% CI]':>28} {'newer-terrain [95% CI]':>26}")
    for mo in sorted(set(months)):
        m = months == mo
        sub = {k: v[m] for k, v in p.items()}
        sm = summarise(sub, n_boot=1000, seed=out["seed"])
        n_days = len(np.unique(sub["day"]))
        print(f"{mo:8} {n_days:4d} {sm['units']:5d} {sm['new']['point']:6.3f} "
              f"{_ci(sm['delta'], True):>28} {_ci(sm['over_terrain'], True):>26}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buffer", default=BUFFER_PATH)
    ap.add_argument("--seed", type=int, action="append",
                    help="repeatable; 0 is production's own split and booster seed. "
                         "Report at least three.")
    ap.add_argument("--gap", type=int, default=7,
                    help="days withheld from the older model (default 7)")
    ap.add_argument("--min-units", type=int, default=10,
                    help="skip a day with fewer mixed-label (day, cell, hour) units")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--threads", type=int, default=-1,
                    help="XGBoost fit threads (production fits on 1; the trees "
                         "do not depend on it)")
    ap.add_argument("--cv-reference", action="store_true",
                    help="also score the same units with evaluate.py's grouped "
                         "5-fold CV, which has seen the scored day's other cells "
                         "and later days")
    args = ap.parse_args()
    seeds = args.seed or [0]

    X, y, info = load_population(args.buffer)
    days = row_days(X)
    uniq = np.unique(days)
    if len(uniq) and uniq[-1] - uniq[0] > 300:
        # Day-of-year has no year: a buffer across 1 Jan would order January
        # before December and the replay would train on the future.
        print("buffer spans the turn of a year; day-of-year cannot order it")
        return 1
    print(f"buffer      {info['file_rows']} rows, {info['file_positives']} positives, "
          f"width {info['width']}")
    print(f"population  {len(y)} rows, {int(y.sum())} positives after load() drops "
          f"{info['load_dropped']} and _fit_rows drops {info['fit_dropped']}")
    print(f"days        {len(uniq)}, {day_label(uniq[0])} to {day_label(uniq[-1])}")

    runs = [run(X, y, seed=s, gap=args.gap, min_units=args.min_units,
                n_boot=args.boot, n_jobs=args.threads,
                cv_reference=args.cv_reference) for s in seeds]

    print(f"\n=== across seeds (gap {args.gap} days) ===")
    print(f"{'seed':>4} {'days':>4} {'units':>5} {'newer [95% CI]':>22} "
          f"{'older [95% CI]':>22} {'delta [95% CI]':>24} {'terrain':>7} "
          f"{'newer-terrain [95% CI]':>24}")
    for r in runs:
        s = r["summary"]
        if s is None:
            continue
        print(f"{r['seed']:4d} {len(r['days']):4d} {s['units']:5d} {_ci(s['new']):>22} "
              f"{_ci(s['old']):>22} {_ci(s['delta'], True):>24} "
              f"{s['terrain']['point']:7.3f} {_ci(s['over_terrain'], True):>24}")
    if args.cv_reference:
        print(f"\n{'seed':>4} {'grouped CV, same units':>24} {'newer - CV [95% CI]':>24}")
        for r in runs:
            if "cv" in r:
                print(f"{r['seed']:4d} {_ci(r['cv']['score']):>24} "
                      f"{_ci(r['cv']['new_minus_cv'], True):>24}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
