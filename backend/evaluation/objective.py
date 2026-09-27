"""
Does the training objective, rather than the feature set, cap the score?

`evaluate.py` answers "which columns help".  This answers a different question,
and the measurement on 27 Sep 2026 is what prompted it: the served model ranks
cells at 0.542 [0.518, 0.568] within a (day, cell, hour) group, while
`cos_incidence` **alone** manages 0.532 — and adding cos_incidence to the 22
columns made the model *worse* (-0.019).  A single column carrying nearly the
whole score, and hurting when added, is not a feature problem.

The structural reason: of the 22 columns, 16 are constant inside a group.  Every
row in one (day, cell, hour) unit shares its weather, so only lat, lon,
elevation, slope, aspect and alt_agl vary — and those six are the only ones that
can possibly separate one cell from another in the same heatmap.  The constant
sixteen can still be *read* during training, where they identify the group
almost exactly, so a global classifier is rewarded for memorising which groups
are thermic instead of learning where inside a group the lift sits.  We then
score it on exactly the thing it was not trained to do.

So this is a 2x2: {all columns, varying columns only} x {classifier, pairwise
ranker grouped by (day, cell, hour)}.  Same split, same scoring, same folds as
evaluate.py, so the numbers are comparable to its table.

    python -m evaluation.objective --buffer <path>
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from pipeline.feature_engineering import FEATURE_COUNT
from evaluation.evaluate import _COL_ALT_AGL, solar_column, _bare_model
from evaluation.holdout import group_ids, decision_ids
from evaluation.metrics import grouped_auc, paired_delta_ci

# The six that are not constant inside a (day, cell, hour) group: position, the
# terrain derived from it, and the aircraft's own height.  Everything else in the
# row describes the hour, not the cell.
VARYING = [0, 1, 2, 3, 4, _COL_ALT_AGL]


def _fit_clf(Xtr, ytr, Xes, yes):
    """evaluate.py's fit, verbatim, so one arm of the 2x2 reproduces its row."""
    import xgboost as xgb
    pos, n = int(ytr.sum()), len(ytr)
    use_es = len(yes) > 0 and 0 < int(yes.sum()) < len(yes)
    clf = xgb.XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        scale_pos_weight=max(1.0, (n - pos) / max(1, pos)),
        tree_method="hist", n_jobs=-1, eval_metric="auc", random_state=42,
        early_stopping_rounds=20 if use_es else None,
    )
    clf.fit(Xtr, ytr, eval_set=[(Xes, yes)], verbose=False) if use_es else clf.fit(Xtr, ytr)
    return lambda X: clf.predict_proba(X)[:, 1]


def _fit_ranker(Xtr, ytr, gtr, Xes, yes, ges):
    """
    Pairwise ranker, grouped by the unit the heatmap is read in.

    XGBRanker needs its rows contiguous by group and a group-size vector, so the
    arrays are sorted here rather than trusted to arrive that way.  The objective
    then only ever compares rows *within* a group, which is the same comparison
    grouped_auc scores — a constant column cannot win a within-group pair, so the
    sixteen that identify the hour stop paying during training.
    """
    import xgboost as xgb

    def _prep(X, y, g):
        order = np.argsort(g, kind="stable")
        X, y, g = X[order], y[order], g[order]
        _, sizes = np.unique(g, return_counts=True)
        return X, y, sizes

    Xtr, ytr, gs_tr = _prep(Xtr, ytr, gtr)
    use_es = len(yes) > 0 and 0 < int(yes.sum()) < len(yes)
    rk = xgb.XGBRanker(
        objective="rank:pairwise",
        n_estimators=200, max_depth=5, learning_rate=0.1,
        subsample=0.8, colsample_bytree=0.8,
        tree_method="hist", n_jobs=-1, random_state=42,
        early_stopping_rounds=20 if use_es else None,
    )
    if use_es:
        Xes2, yes2, gs_es = _prep(Xes, yes, ges)
        rk.fit(Xtr, ytr, group=gs_tr, eval_set=[(Xes2, yes2)], eval_group=[gs_es],
               verbose=False)
    else:
        rk.fit(Xtr, ytr, group=gs_tr)
    # A ranker emits scores, not probabilities. grouped_auc only ever compares
    # them inside a group, so the scale does not matter.
    return rk.predict


def oof(X, y, gfold, gscore, cols, ranker, k=5, seed=0):
    """Out-of-fold scores, folds cut on the coarse key, ranking groups on the fine one."""
    from sklearn.model_selection import GroupKFold
    Xc = X[:, cols]
    out = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=k).split(Xc, y, groups=gfold):
        # Early-stopping set carved out of train by group, never by row: rows
        # from one group on both sides would leak the answer.
        gtr = gfold[tr]
        uniq = np.unique(gtr)
        rng = np.random.default_rng(seed)
        held = set(rng.choice(uniq, max(1, len(uniq) // 5), replace=False).tolist())
        mask = np.array([g in held for g in gtr])
        fit_i, es_i = tr[~mask], tr[mask]
        if ranker:
            predict = _fit_ranker(Xc[fit_i], y[fit_i], gscore[fit_i],
                                  Xc[es_i], y[es_i], gscore[es_i])
        else:
            predict = _fit_clf(Xc[fit_i], y[fit_i], Xc[es_i], y[es_i])
        out[te] = predict(Xc[te])
    return out


def _ci(scores, y, gids, n_boot):
    """Bootstrap CI for one ranker, via the paired routine against chance."""
    r = paired_delta_ci(np.zeros(len(y)), scores, y, gids, n_boot=n_boot)
    # The comparison is against a constant, whose within-group AUC is exactly
    # 0.5, so the delta interval shifted by 0.5 is this ranker's own interval.
    return 0.5 + r["lo"], 0.5 + r["hi"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buffer", required=True)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()

    with np.load(args.buffer, allow_pickle=False) as d:
        X, y = d["X"], d["y"].astype(int)
    if X.shape[1] != FEATURE_COUNT:
        print(f"buffer has {X.shape[1]} features, expected {FEATURE_COUNT}")
        return 1

    # The same AGL floor fit_and_gate applies, for the same reason evaluate.py
    # applies it: without it the harness scores a population production has
    # dropped, and 18% of the buffer sits at 0 m AGL, all negative.
    from models.thermal_model import _MIN_SAMPLE_AGL
    keep = X[:, _COL_ALT_AGL] >= _MIN_SAMPLE_AGL
    if not keep.all():
        print(f"agl floor   {int((~keep).sum())} rows below "
              f"{_MIN_SAMPLE_AGL:.0f} m AGL dropped")
        X, y = X[keep], y[keep]

    # Folds cut on the coarse key, comparisons pooled within the fine one — see
    # evaluate.py for why splitting between two hours of the same day leaks.
    gfold, gscore = group_ids(X), decision_ids(X)
    print(f"buffer      {len(y)} rows, {int(y.sum())} positives "
          f"({y.mean() * 100:.2f}%)")
    print(f"split       grouped {args.folds}-fold CV over "
          f"{len(np.unique(gfold))} groups, scored within "
          f"{len(np.unique(gscore))} (day, cell, hour) units")
    print(f"columns     all 22  vs  the {len(VARYING)} that vary inside a group "
          f"{VARYING}\n")

    allc = list(range(X.shape[1]))
    # cos_incidence varies per cell, so it belongs with the varying set rather
    # than appended to a matrix that is mostly constant.
    Xs = np.column_stack([X, solar_column(X)])
    varying_solar = VARYING + [Xs.shape[1] - 1]

    arms = {
        "all cols, classifier":        (X,  allc,          False),
        "all cols, ranker":            (X,  allc,          True),
        "varying only, classifier":    (X,  VARYING,       False),
        "varying only, ranker":        (X,  VARYING,       True),
        "varying+solar, ranker":       (Xs, varying_solar, True),
    }

    terrain = _bare_model()._terrain_multiplier(X)
    scores, rows = {}, []
    for name, (mat, cols, rank) in arms.items():
        raw = oof(mat, y, gfold, gscore, cols, rank, k=args.folds)
        # Production multiplies the model's output by the terrain heuristic; a
        # ranker's scores are unbounded, so scale them to [0,1] first or the
        # multiply would be meaningless.
        norm = (raw - raw.min()) / max(1e-9, raw.max() - raw.min())
        served = norm * terrain
        scores[name] = served
        st = grouped_auc(served, y, gscore)
        ci = _ci(served, y, gscore, args.boot)
        rows.append((name, st["micro"], st["macro"], ci[0], ci[1]))
        print(f"  measured  {name}")

    print("\nranker                        micro   macro   95% CI")
    print("-" * 62)
    for name, mi, ma, lo, hi in rows:
        flag = "" if lo > 0.5 else "   (not above chance)"
        print(f"{name:<28}  {mi:.3f}   {ma:.3f}   [{lo:.3f}, {hi:.3f}]{flag}")

    base = "all cols, classifier"
    print(f"\npaired deltas against '{base}' (the shipped setup), over the same groups")
    print("-" * 62)
    for name in arms:
        if name == base:
            continue
        r = paired_delta_ci(scores[base], scores[name], y, gscore,
                            n_boot=args.boot)
        d, lo, hi = r["point"], r["lo"], r["hi"]
        verdict = ("better" if lo > 0 else "worse" if hi < 0 else "inconclusive")
        print(f"{name:<28} {d:+.3f}  [{lo:+.3f}, {hi:+.3f}]   {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
