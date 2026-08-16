"""
Within-group ranking metrics.

Global AUC over the whole buffer is the wrong instrument for this model.  Only
6 of the 22 feature columns vary across a grid — the other 16 are one
Open-Meteo point sample replicated by np.full — so a global score is dominated
by variation *between* weather regimes.  It mostly measures "is this a soaring
hour", which solar_ghi and hour_sin answer almost alone, and which the pilot
already knows.  The heatmap answers a different question: given this day and
this area, which cell?

That is a within-group ranking question, so it is measured within groups.  For
every group holding at least one positive and one negative, every
positive/negative pair is checked for correct ordering; the pooled fraction of
correctly ordered pairs is the score.  It is an AUC, computed only over
comparisons the product actually has to get right — and it is chance-level for
a model that only knows what kind of day it is, which is the point.
"""

import numpy as np

# Fraction of pairs a coin-flip ranker gets right.  Scores are meaningful
# relative to this, not to zero.
CHANCE = 0.5


def _group_pairs(scores: np.ndarray, y: np.ndarray):
    """
    (concordant, total) pairs for one group.

    Ties score half, matching the usual AUC convention: a model that outputs
    one constant everywhere should land exactly at chance, not above it.
    """
    pos = scores[y == 1]
    neg = scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.0, 0
    diff = pos[:, None] - neg[None, :]
    concordant = float((diff > 0).sum() + 0.5 * (diff == 0).sum())
    return concordant, pos.size * neg.size


def per_group_pairs(scores: np.ndarray, y: np.ndarray, gids: np.ndarray):
    """
    Concordant/total pair counts for each mixed-label group.

    Returned per group rather than pooled so the bootstrap can resample groups,
    which is the unit that is actually independent here.  Resampling rows would
    understate the interval badly: rows in one group share 16 identical feature
    values and are nowhere near independent draws.
    """
    conc, tot = [], []
    for g in np.unique(gids):
        m = gids == g
        c, t = _group_pairs(scores[m], y[m])
        if t > 0:
            conc.append(c)
            tot.append(t)
    return np.asarray(conc, dtype=float), np.asarray(tot, dtype=float)


def grouped_auc(scores: np.ndarray, y: np.ndarray, gids: np.ndarray) -> dict:
    """
    Pooled ("micro") and per-group-mean ("macro") within-group AUC.

    micro weights each group by how many comparisons it supports, so a group of
    94 aircraft counts for more than a group of 2.  It is the headline number.
    macro is reported alongside because a large gap between them means the
    score rests on a handful of big groups.
    """
    conc, tot = per_group_pairs(scores, y, gids)
    if tot.size == 0:
        return {"micro": float("nan"), "macro": float("nan"),
                "groups": 0, "pairs": 0}
    return {
        "micro": float(conc.sum() / tot.sum()),
        "macro": float(np.mean(conc / tot)),
        "groups": int(tot.size),
        "pairs": int(tot.sum()),
    }


def bootstrap_ci(scores: np.ndarray, y: np.ndarray, gids: np.ndarray,
                 n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> dict:
    """
    Percentile bootstrap CI for the micro score, resampling whole groups.

    The interval is the deliverable, not the point estimate.  With few mixed
    groups it comes back wide, and a wide interval is the harness correctly
    reporting that the data cannot yet separate two models — which is a result,
    not a failure.
    """
    conc, tot = per_group_pairs(scores, y, gids)
    if tot.size == 0:
        return {"lo": float("nan"), "hi": float("nan"), "point": float("nan"),
                "groups": 0}

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, tot.size, size=(n_boot, tot.size))
    draws = conc[idx].sum(axis=1) / tot[idx].sum(axis=1)
    return {
        "point": float(conc.sum() / tot.sum()),
        "lo": float(np.percentile(draws, 100 * alpha / 2)),
        "hi": float(np.percentile(draws, 100 * (1 - alpha / 2))),
        "groups": int(tot.size),
    }


def paired_delta_ci(scores_a: np.ndarray, scores_b: np.ndarray,
                    y: np.ndarray, gids: np.ndarray,
                    n_boot: int = 2000, alpha: float = 0.05,
                    seed: int = 0) -> dict:
    """
    CI for (B - A), bootstrapping the two models over the *same* resampled groups.

    Pairing matters.  Comparing two independently-drawn intervals is a much
    weaker test: both models see the same groups, so most of the sampling noise
    is common to them and cancels in the difference.  Overlapping individual
    intervals are entirely compatible with a difference that is reliably
    non-zero, so this is the number that decides whether a feature earned its
    place.
    """
    ca, ta = per_group_pairs(scores_a, y, gids)
    cb, tb = per_group_pairs(scores_b, y, gids)
    if ta.size == 0 or ta.size != tb.size:
        return {"lo": float("nan"), "hi": float("nan"), "point": float("nan"),
                "groups": 0}

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, ta.size, size=(n_boot, ta.size))
    draws = (cb[idx].sum(axis=1) / tb[idx].sum(axis=1)
             - ca[idx].sum(axis=1) / ta[idx].sum(axis=1))
    return {
        "point": float(cb.sum() / tb.sum() - ca.sum() / ta.sum()),
        "lo": float(np.percentile(draws, 100 * alpha / 2)),
        "hi": float(np.percentile(draws, 100 * (1 - alpha / 2))),
        "groups": int(ta.size),
    }
