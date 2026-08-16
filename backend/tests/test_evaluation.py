import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from evaluation.holdout import (
    benchmark_mask, earlystop_mask, group_ids, grouped_folds, split,
)
from evaluation.metrics import (
    bootstrap_ci, grouped_auc, paired_delta_ci, per_group_pairs,
)
from pipeline.feature_engineering import FEATURE_COUNT

_DOY_SIN, _DOY_COS = 17, 18


def _rows(n, doy=200.0, lat=39.8, lon=30.1, seed=0):
    """A minimal buffer-shaped matrix with a valid cyclic day encoding."""
    rng = np.random.default_rng(seed)
    X = rng.random((n, FEATURE_COUNT))
    X[:, 0] = lat
    X[:, 1] = lon
    angle = 2 * np.pi * doy / 365
    X[:, _DOY_SIN] = np.sin(angle)
    X[:, _DOY_COS] = np.cos(angle)
    return X


# --- metric correctness -------------------------------------------------------

def test_perfect_ranker_scores_one():
    y = np.array([0, 1, 0, 1])
    g = np.array([1, 1, 2, 2])
    assert grouped_auc(y.astype(float), y, g)["micro"] == 1.0


def test_inverted_ranker_scores_zero():
    y = np.array([0, 1, 0, 1])
    g = np.array([1, 1, 2, 2])
    assert grouped_auc(-y.astype(float), y, g)["micro"] == 0.0


def test_constant_ranker_scores_exactly_chance():
    # Ties must count half, or a degenerate model that outputs one value
    # everywhere would score above chance and pass the gate.
    y = np.array([0, 1, 0, 1])
    g = np.array([1, 1, 2, 2])
    assert grouped_auc(np.full(4, 0.7), y, g)["micro"] == 0.5


def test_single_label_groups_are_excluded():
    # A group of all-negatives supports no positive/negative comparison and must
    # not silently count as a group.
    y = np.array([0, 0, 0, 1])
    g = np.array([1, 1, 2, 2])
    stats = grouped_auc(np.array([0.1, 0.2, 0.3, 0.9]), y, g)
    assert stats["groups"] == 1
    assert stats["pairs"] == 1


def test_micro_weights_groups_by_pair_count():
    # Group 1 has 1 pair and is ranked wrongly; group 2 has 4 and is ranked right.
    y = np.array([1, 0, 1, 1, 0, 0])
    g = np.array([1, 1, 2, 2, 2, 2])
    s = np.array([0.0, 1.0, 1.0, 1.0, 0.0, 0.0])
    stats = grouped_auc(s, y, g)
    assert stats["micro"] == pytest.approx(4 / 5)
    assert stats["macro"] == pytest.approx((0.0 + 1.0) / 2)


def test_no_mixed_groups_returns_nan_not_zero():
    y = np.array([0, 0])
    g = np.array([1, 1])
    stats = grouped_auc(np.array([0.1, 0.2]), y, g)
    assert stats["groups"] == 0
    assert np.isnan(stats["micro"])


def test_per_group_pairs_counts_products():
    y = np.array([1, 1, 0, 0, 0])
    g = np.array([1, 1, 1, 1, 1])
    _, tot = per_group_pairs(np.arange(5.0), y, g)
    assert tot.tolist() == [6.0]      # 2 positives x 3 negatives


# --- bootstrap ----------------------------------------------------------------

def test_bootstrap_interval_brackets_the_point_estimate():
    rng = np.random.default_rng(0)
    n = 400
    g = rng.integers(0, 40, n)
    y = rng.integers(0, 2, n)
    s = rng.random(n) + 0.3 * y
    ci = bootstrap_ci(s, y, g, n_boot=500, seed=1)
    assert ci["lo"] <= ci["point"] <= ci["hi"]


def test_bootstrap_is_deterministic_for_a_fixed_seed():
    rng = np.random.default_rng(0)
    n, g = 200, np.repeat(np.arange(20), 10)
    y = rng.integers(0, 2, n)
    s = rng.random(n)
    a = bootstrap_ci(s, y, g, n_boot=200, seed=7)
    b = bootstrap_ci(s, y, g, n_boot=200, seed=7)
    assert a == b


def test_paired_delta_detects_a_real_improvement():
    rng = np.random.default_rng(0)
    n = 2000
    g = rng.integers(0, 200, n)
    y = rng.integers(0, 2, n)
    weak = rng.random(n) + 0.05 * y
    strong = rng.random(n) + 1.5 * y
    d = paired_delta_ci(weak, strong, y, g, n_boot=500, seed=3)
    assert d["lo"] > 0, "a clearly better ranker must produce an interval above 0"


def test_paired_delta_is_inconclusive_for_identical_rankers():
    rng = np.random.default_rng(0)
    n = 600
    g = rng.integers(0, 60, n)
    y = rng.integers(0, 2, n)
    s = rng.random(n)
    d = paired_delta_ci(s, s.copy(), y, g, n_boot=300, seed=3)
    assert d["point"] == pytest.approx(0.0, abs=1e-12)
    assert d["lo"] <= 0 <= d["hi"]


# --- split integrity ----------------------------------------------------------

def test_group_ids_separate_distinct_days():
    a = _rows(5, doy=200.0)
    b = _rows(5, doy=201.0)
    assert len(set(group_ids(np.vstack([a, b])).tolist())) == 2


def test_group_ids_merge_nearby_points_on_the_same_day():
    # 0.5 degree cells: 39.8 and 39.9 belong to the same cell.
    a = _rows(3, lat=39.8)
    b = _rows(3, lat=39.9)
    assert len(set(group_ids(np.vstack([a, b])).tolist())) == 1


def test_benchmark_assignment_is_stable_as_the_buffer_grows():
    # The whole point of the frozen split: a group's side must not change when
    # new samples arrive, or successive scores stop being comparable.
    old = np.vstack([_rows(4, doy=float(d), seed=d) for d in range(200, 240)])
    new = np.vstack([old, _rows(50, doy=300.0, seed=99)])
    assert benchmark_mask(new)[: len(old)].tolist() == benchmark_mask(old).tolist()


def test_split_is_group_disjoint():
    X = np.vstack([_rows(6, doy=float(d), seed=d) for d in range(200, 260)])
    y = np.arange(len(X)) % 2
    (_, _, gtr), (_, _, ges), (_, _, gb) = split(X, y)
    assert set(gtr).isdisjoint(set(gb))
    assert set(ges).isdisjoint(set(gb))
    assert set(gtr).isdisjoint(set(ges))


def test_split_covers_every_row_exactly_once():
    X = np.vstack([_rows(6, doy=float(d), seed=d) for d in range(200, 260)])
    y = np.arange(len(X)) % 2
    (_, ytr, _), (_, yes, _), (_, yb, _) = split(X, y)
    assert len(ytr) + len(yes) + len(yb) == len(y)


def test_earlystop_split_is_not_the_benchmark_split():
    # Distinct salts: if both used the same hash, early stopping would select
    # the same groups the benchmark reports from.
    X = np.vstack([_rows(4, doy=float(d), seed=d) for d in range(200, 300)])
    g = group_ids(X)
    assert benchmark_mask(X).tolist() != earlystop_mask(g).tolist()


def test_grouped_folds_never_split_a_group():
    g = np.repeat(np.arange(50), 4)
    for train, test in grouped_folds(g, k=5, seed=0):
        assert set(g[train]).isdisjoint(set(g[test]))


def test_grouped_folds_cover_every_row_exactly_once():
    g = np.repeat(np.arange(50), 4)
    seen = np.zeros(len(g), dtype=int)
    for _, test in grouped_folds(g, k=5, seed=0):
        seen[test] += 1
    assert seen.tolist() == [1] * len(g)
