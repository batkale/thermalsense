"""
evaluation/learning_curve.py replays the buffer day by day.  What it reports
is only about production if its fits are production's fits, on production's
rows, and never see the day they are scored on.
"""

import numpy as np

import models.thermal_model as tm
from evaluation import learning_curve as lc
from evaluation.holdout import split
from pipeline.feature_engineering import FEATURE_COUNT


def _buffer(n_days=20, cells=6, per_cell=20, first_day=150, seed=0):
    """Rows over `n_days` consecutive days, a few 0.5deg cells each, one hour."""
    rng = np.random.default_rng(seed)
    day = np.repeat(np.arange(first_day, first_day + n_days), cells * per_cell)
    cell = np.tile(np.repeat(np.arange(cells), per_cell), n_days)
    n = len(day)
    X = rng.random((n, FEATURE_COUNT))
    angle = 2 * np.pi * day / 365
    X[:, 17], X[:, 18] = np.sin(angle), np.cos(angle)
    X[:, 15], X[:, 16] = np.sin(2 * np.pi * 12 / 24), np.cos(2 * np.pi * 12 / 24)
    X[:, 0] = 39.0 + (cell % 3) * 0.5
    X[:, 1] = 29.0 + (cell // 3) * 0.5
    X[:, 3] = rng.uniform(0, 30, n)                  # slope
    X[:, 4] = rng.uniform(0, 360, n)                 # aspect
    X[:, 21] = rng.uniform(300, 2500, n)             # alt_agl, above the floor
    X[:, 5], X[:, 6], X[:, 19] = 20.0, 0.3, 3000.0   # thermal top above every row
    p = 1 / (1 + np.exp(-(X[:, 3] - 15) / 4))
    y = (rng.random(n) < p).astype(int)
    return X, y


def test_days_decode_as_the_group_key_does():
    X, _ = _buffer(n_days=3, first_day=300)
    assert sorted(set(lc.row_days(X).tolist())) == [300, 301, 302]
    assert lc.day_label(324) == "2026-11-20"
    assert lc.day_label(0) == "2026-12-31"


def test_seed_zero_is_productions_split():
    X, y = _buffer()
    fit, es = lc.split_sides(X, seed=0)
    (Xf, _, _), (Xes, _, _), (Xb, _, _) = split(X, y)
    assert np.array_equal(X[fit], Xf)
    assert np.array_equal(X[es], Xes)
    assert (~(fit | es)).sum() == len(Xb)


def test_other_seeds_withhold_other_groups():
    X, _ = _buffer()
    f0, _ = lc.split_sides(X, seed=0)
    f1, _ = lc.split_sides(X, seed=1)
    assert not np.array_equal(f0, f1)


def test_fit_is_the_one_fit_and_gate_makes(tmp_path, monkeypatch):
    """Same rows, same hyperparameters: the two boosters predict identically."""
    X, y = _buffer()
    m = tm.ThermalModel()
    m._buffer_X, m._buffer_y = list(X), list(y)
    m.fit_and_gate()
    assert m.model is not None

    fit, es = lc.split_sides(X, seed=0)
    ours = lc.fit_like_production(X[fit], y[fit], X[es], y[es],
                                  random_state=42, n_jobs=tm.XGB_FIT_THREADS)
    np.testing.assert_allclose(ours.predict_proba(X)[:, 1],
                               m.model.predict_proba(X)[:, 1], rtol=0, atol=1e-6)


def test_replay_never_trains_on_the_day_it_scores(monkeypatch):
    X, y = _buffer()
    trained_through = []
    real_fit = lc.fit_like_production

    def spy(Xf, yf, Xes, yes, **kw):
        trained_through.append(int(max(lc.row_days(Xf).max(), lc.row_days(Xes).max())))
        return real_fit(Xf, yf, Xes, yes, **kw)

    monkeypatch.setattr(lc, "fit_like_production", spy)
    out = lc.run(X, y, seed=0, gap=7, min_units=1, n_boot=50, verbose=False)

    assert out["days"], "nothing was scored"
    for r in out["days"]:
        assert r["new_days"] > r["old_days"]
        assert r["new_days"] == r["day"] - 150       # every earlier day, no more
        assert r["old_days"] == r["day"] - 7 - 150
    # Models are cached by day set, so each distinct history is fitted once.
    assert len(trained_through) == len(set(trained_through)) == out["fits"]
    assert out["summary"]["delta"]["groups"] > 0


def test_day_with_no_new_history_is_skipped():
    """Two cutoffs spanning the same buffer days are one model, not a comparison."""
    X, y = _buffer(n_days=6, first_day=150)
    X2, y2 = _buffer(n_days=2, first_day=170, seed=1)   # 14-day hole before it
    X, y = np.vstack([X, X2]), np.concatenate([y, y2])
    out = lc.run(X, y, seed=0, gap=7, min_units=1, n_boot=50, verbose=False)
    scored = {r["day"] for r in out["days"]}
    assert 170 not in scored
    assert 170 in out["skipped"]["no buffer day in the 7 before"]
