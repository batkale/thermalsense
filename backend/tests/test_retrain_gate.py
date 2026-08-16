"""
Gate behaviour: which fits are allowed to replace the model in service.

The failure these guard against is specific.  The old gate scored each fit on
the newest 20% of the buffer and compared it against a number the saved model
had earned on a *different* newest 20%, so it was differencing two
measurements taken on different samples.  That is not a quality signal, and it
let noise evict good models.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import models.thermal_model as tm
from evaluation.holdout import benchmark_mask
from pipeline.feature_engineering import FEATURE_COUNT


def _corrupt_training_labels(m, seed=1):
    """
    Shuffle labels on the training side only, leaving the benchmark intact.

    Shuffling the whole buffer would destroy the benchmark labels too, so both
    models would score at chance there and the comparison would correctly come
    back inconclusive — measuring nothing.  The challenger has to be the only
    thing degraded.
    """
    X = np.array(m._buffer_X)
    y = np.array(m._buffer_y)
    train = ~benchmark_mask(X)
    y[train] = np.random.default_rng(seed).permutation(y[train])
    m._buffer_y = list(y)


def _buffer(groups=150, per_group=16, seed=0):
    """
    A buffer laid out as `groups` distinct (day, 0.5deg cell) units.

    Group count and occupancy are controlled rather than random because the
    metric only sees groups holding both labels: scatter the same rows over too
    many groups and every one is single-label, leaving nothing measurable.
    """
    rng = np.random.default_rng(seed)
    n = groups * per_group
    X = rng.random((n, FEATURE_COUNT))
    gi = np.repeat(np.arange(groups), per_group)

    angle = 2 * np.pi * (150 + gi % 10) / 365
    X[:, 17], X[:, 18] = np.sin(angle), np.cos(angle)
    X[:, 0] = 39.0 + (gi // 10 % 4) * 0.5            # lat cell
    X[:, 1] = 29.0 + (gi // 40) * 0.5                # lon cell
    X[:, 3] = rng.uniform(0, 30, n)                  # slope
    X[:, 4] = rng.uniform(0, 360, n)                 # aspect

    # Label tracks slope, so a fit has real within-group signal to find.
    p = 1 / (1 + np.exp(-(X[:, 3] - 15) / 4))
    y = (rng.random(n) < p).astype(int)
    return X, y


def _prepare(tmp_path, monkeypatch, seed=0, min_groups=10):
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "thermal_xgb.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "b.npz"))
    # The production threshold is calibrated to real traffic; these tests are
    # about the decision logic, so they only need the gate to be active.
    monkeypatch.setattr(tm, "_MIN_BENCH_GROUPS", min_groups)
    X, y = _buffer(seed=seed)
    m = tm.ThermalModel()
    m._buffer_X = list(X)
    m._buffer_y = list(y)
    return m


def test_benchmark_split_is_disjoint_from_the_training_split():
    """The score has to come from rows the fit never saw, or it measures nothing."""
    X, y = _buffer()
    (_, _, gtr), (_, _, ges), (_, _, gb) = tm.bench_split(X, y)
    assert set(gtr).isdisjoint(set(gb))
    assert set(ges).isdisjoint(set(gb))


def test_unverifiable_fit_does_not_replace_a_serving_model(tmp_path, monkeypatch):
    """
    Too few mixed-label groups to score means the fit cannot be judged.
    Replacing a working model on an unverifiable basis is the exact failure
    the gate exists to prevent, so the incumbent stays.
    """
    m = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "_MIN_BENCH_GROUPS", 10_000)   # never satisfiable
    sentinel = object()
    m.model = sentinel

    m.fit_and_gate()

    assert m.model is sentinel
    assert not (tmp_path / "thermal_xgb.json").exists()


def test_unverifiable_fit_is_accepted_when_nothing_is_serving(tmp_path, monkeypatch):
    """With no model at all, an unverified fit still beats the physics fallback."""
    m = _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "_MIN_BENCH_GROUPS", 10_000)
    assert m.model is None

    m.fit_and_gate()

    assert m.model is not None
    assert (tmp_path / "thermal_xgb.json").exists()
    # No score was earned, so none may be recorded — otherwise the next cycle
    # would be judged against a number this model never achieved.
    assert m._saved_bench_score() is None


def test_accepted_fit_records_a_comparable_benchmark_score(tmp_path, monkeypatch):
    m = _prepare(tmp_path, monkeypatch)

    m.fit_and_gate()

    score = m._saved_bench_score()
    assert score is not None, "a gated fit must record the score it earned"
    assert 0.0 <= score <= 1.0


def test_a_reliably_worse_challenger_is_rejected(tmp_path, monkeypatch):
    """
    The incumbent is a genuinely good ranker; the challenger is fitted on
    shuffled labels so it cannot rank.  The paired interval should sit entirely
    below zero and the incumbent should survive.
    """
    m = _prepare(tmp_path, monkeypatch)

    # First cycle: fit and accept a model that learned the real signal.
    m.fit_and_gate()
    incumbent = m.model
    assert incumbent is not None

    # Second cycle with the training signal destroyed — nothing to learn.
    _corrupt_training_labels(m)
    m.fit_and_gate()

    assert m.model is incumbent, "a reliably worse fit must not take over"
    assert m._skipped_fits == 1


def test_repeated_rejection_eventually_rebaselines(tmp_path, monkeypatch):
    """A lucky high score must not freeze the model permanently."""
    m = _prepare(tmp_path, monkeypatch)
    m.fit_and_gate()
    incumbent = m.model

    monkeypatch.setattr(tm, "_MAX_CONSECUTIVE_SKIPS", 3)
    _corrupt_training_labels(m)

    for _ in range(3):
        m.fit_and_gate()

    assert m.model is not incumbent, "the gate must re-baseline rather than freeze"
    assert m._skipped_fits == 0


def test_legacy_model_without_a_recorded_score_is_replaced(tmp_path, monkeypatch):
    """
    Models saved before this harness were fitted on a positional split, so
    their training data overlaps today's benchmark groups.  Their score is not
    comparable and must not be defended.
    """
    m = _prepare(tmp_path, monkeypatch)
    m.fit_and_gate()
    legacy = m.model
    legacy.get_booster().set_attr(**{tm._BENCH_ATTR: None})
    assert m._saved_bench_score() is None

    m.fit_and_gate()

    assert m.model is not legacy


def test_served_score_includes_the_terrain_multiplier(tmp_path, monkeypatch):
    """
    Production serves predict_proba x terrain_multiplier.  Scoring the bare
    classifier would evaluate an artefact that is never served.
    """
    m = _prepare(tmp_path, monkeypatch)
    X = np.array(m._buffer_X)

    class _Stub:
        def predict_proba(self, f):
            return np.column_stack([np.zeros(len(f)), np.full(len(f), 0.5)])

    served = m._score_served(_Stub(), X)
    assert np.allclose(served, 0.5 * m._terrain_multiplier(X))
    assert not np.allclose(served, 0.5), "the multiplier must actually be applied"
