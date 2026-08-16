"""
Frozen, group-disjoint benchmark split.

The retrain loop used to score each fit on the newest 20% of the buffer and
compare that number against the score the *saved* model earned on its own,
different, newest-20%.  Two numbers measured on different samples are not
comparable, so the gate could not distinguish a better model from a luckier
holdout — which is why observed val AUC wandered across a ~0.07 band with no
real change behind it.

Every sample is assigned permanently to train or holdout by hashing a group
key, which fixes two things at once:

  * a given (day, region) always lands on the same side however the buffer
    grows, so every fit is scored against the same yardstick and successive
    scores are actually comparable;
  * whole groups move together, so near-duplicate rows cannot straddle the
    split.  That matters more here than it looks: occupants of one grid share
    16 of their 22 feature values, differing only in terrain, position and
    alt_agl, so a positional split routinely put near-copies of a row on both
    sides and scored the model on samples it had effectively already seen.

The group key is (day-of-year, 0.5deg lat cell, 0.5deg lon cell) — the
"decision context" a pilot faces: one day, one local area, roughly one weather
regime.  Grouping at that scale is also what makes the metric in metrics.py
mean anything, since ranking cells within a shared weather regime is the
question the heatmap actually answers.
"""

import hashlib
import numpy as np

# Cells per degree for the spatial half of the group key.  2 => 0.5deg cells.
# Coarser than the 0.1deg alternative on purpose: 0.1deg splits the same day's
# traffic into many single-label groups, and a ranking metric can only use
# groups holding both a positive and a negative.
_CELLS_PER_DEG = 2.0

# 1-in-N groups are held out.  5 => a ~20% benchmark set.
_HOLDOUT_MODULUS = 5

# Salt keeps the benchmark split independent of the early-stopping split, which
# is drawn the same way from the training side.  Without distinct salts the two
# would select correlated groups and early stopping would peek at the benchmark.
_BENCH_SALT = b"thermalsense/benchmark/v1"
_EARLYSTOP_SALT = b"thermalsense/earlystop/v1"

# Feature column indices this module reads.  Kept local rather than imported so
# a feature-layout change surfaces here as a test failure instead of silently
# regrouping the benchmark set.
_COL_LAT, _COL_LON = 0, 1
_COL_DOY_SIN, _COL_DOY_COS = 17, 18


def _decode_cyclic(sin_v: np.ndarray, cos_v: np.ndarray, period: float) -> np.ndarray:
    """Invert feature_engineering._cyclic back to the original value."""
    angle = np.arctan2(sin_v, cos_v) % (2 * np.pi)
    return angle / (2 * np.pi) * period


def group_ids(X: np.ndarray) -> np.ndarray:
    """
    Integer group id per row: (day-of-year, 0.5deg lat cell, 0.5deg lon cell).

    Derived from existing feature columns rather than stored alongside them, so
    buffers written before this module existed can still be grouped.  Note the
    year is not recoverable from the cyclic encoding: two samples one year apart
    at the same place collapse into one group.  Harmless while the buffer holds
    days rather than years, and it fails safe — it merges groups, never splits
    a group across the train/holdout boundary.
    """
    doy = np.round(_decode_cyclic(X[:, _COL_DOY_SIN], X[:, _COL_DOY_COS], 365)).astype(np.int64) % 365
    lat_idx = np.round((X[:, _COL_LAT] + 90.0) * _CELLS_PER_DEG).astype(np.int64)
    lon_idx = np.round((X[:, _COL_LON] + 180.0) * _CELLS_PER_DEG).astype(np.int64)

    n_lat = int(180 * _CELLS_PER_DEG) + 1
    n_lon = int(360 * _CELLS_PER_DEG) + 1
    lat_idx = np.clip(lat_idx, 0, n_lat - 1)
    lon_idx = np.clip(lon_idx, 0, n_lon - 1)
    return (doy * n_lat + lat_idx) * n_lon + lon_idx


def _side(gid: int, salt: bytes, modulus: int) -> bool:
    """True when this group falls on the selected side of a salted hash split."""
    digest = hashlib.blake2b(str(gid).encode(), key=salt, digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulus == 0


def _mask_for(gids: np.ndarray, salt: bytes, modulus: int) -> np.ndarray:
    """Boolean mask over rows, computed once per distinct group."""
    uniq, inverse = np.unique(gids, return_inverse=True)
    picked = np.fromiter(
        (_side(int(g), salt, modulus) for g in uniq), dtype=bool, count=len(uniq)
    )
    return picked[inverse]


def benchmark_mask(X: np.ndarray) -> np.ndarray:
    """Rows belonging to the frozen benchmark set. Stable across buffer growth."""
    return _mask_for(group_ids(X), _BENCH_SALT, _HOLDOUT_MODULUS)


def earlystop_mask(gids: np.ndarray) -> np.ndarray:
    """
    Rows of the *training* side reserved for early stopping.

    Drawn with a different salt so it is independent of the benchmark split.
    Early stopping picks the boosting round, so the set it picks on cannot also
    be the set the score is reported from — doing both on one set reports the
    maximum of a noisy series over ~200 rounds and calls it an estimate.
    """
    return _mask_for(gids, _EARLYSTOP_SALT, _HOLDOUT_MODULUS)


def grouped_folds(gids: np.ndarray, k: int = 5, seed: int = 0):
    """
    Yield (train_mask, test_mask) pairs keeping whole groups on one side.

    The frozen benchmark exists to make scores comparable over time, which
    costs it most of the data: a 1-in-5 split of a buffer holding only ~75
    mixed-label groups leaves ~20 to measure on.  For a one-off offline
    comparison there is no need to pay that — rotating every group through a
    test fold scores each row with a model that never saw its group, and pools
    predictions over all of them.  Same leakage guarantee, roughly five times
    the measurable pairs.
    """
    uniq = np.unique(gids)
    rng = np.random.default_rng(seed)
    fold_of = np.empty(len(uniq), dtype=int)
    fold_of[rng.permutation(len(uniq))] = np.arange(len(uniq)) % k
    lookup = dict(zip(uniq.tolist(), fold_of.tolist()))
    fold = np.fromiter((lookup[g] for g in gids.tolist()), dtype=int, count=len(gids))
    for f in range(k):
        yield fold != f, fold == f


def split(X: np.ndarray, y: np.ndarray):
    """
    Partition into (train, earlystop, benchmark), each as (X, y, group_ids).

    Group-disjoint by construction: every row of a given (day, cell) lands in
    exactly one of the three.
    """
    gids = group_ids(X)
    bench = benchmark_mask(X)

    train_gids = gids[~bench]
    es = earlystop_mask(train_gids)

    Xt, yt = X[~bench], y[~bench]
    return (
        (Xt[~es], yt[~es], train_gids[~es]),
        (Xt[es], yt[es], train_gids[es]),
        (X[bench], y[bench], gids[bench]),
    )
