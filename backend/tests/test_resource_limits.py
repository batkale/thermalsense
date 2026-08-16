"""
Resource limits for the 2-vCPU / 896 MB deployment target.

These are not about model quality — they are about the app staying responsive
and staying alive on a box with no headroom, where the failure modes are
contention, an unbounded buffer, and a full disk.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
import xgboost as xgb

import main
import models.thermal_model as tm
from config import XGB_FIT_THREADS, XGB_PREDICT_THREADS

# ---------------------------------------------------------------------------
# Thread budget
# ---------------------------------------------------------------------------

def test_fit_threads_leave_a_core_for_serving():
    """The retrain fit must not be able to claim every core."""
    assert XGB_FIT_THREADS < (os.cpu_count() or 2) or (os.cpu_count() or 2) == 1


def test_for_serving_retunes_thread_count():
    clf = xgb.XGBClassifier(n_jobs=XGB_FIT_THREADS)
    assert tm._for_serving(clf).get_params()["n_jobs"] == XGB_PREDICT_THREADS


def _booster_nthread(clf) -> int:
    import json
    return int(json.loads(clf.get_booster().save_config())
               ["learner"]["generic_param"]["nthread"])


def _fitted_at_fit_threads():
    X = np.random.default_rng(0).random((40, 4))
    y = (X[:, 0] > 0.5).astype(int)
    clf = xgb.XGBClassifier(n_estimators=5, n_jobs=XGB_FIT_THREADS)
    clf.fit(X, y)
    return clf


def test_a_fitted_booster_keeps_the_fit_thread_count():
    """The trap _for_serving exists to close.

    fit_and_gate promotes its challenger object straight to self.model, and the
    booster carries the threads it was fitted under. Untouched, that pins every
    prediction until the next restart to one core — silently, since only the
    latency changes.
    """
    assert _booster_nthread(_fitted_at_fit_threads()) == XGB_FIT_THREADS


def test_for_serving_retunes_the_booster_not_just_the_wrapper():
    clf = tm._for_serving(_fitted_at_fit_threads())
    assert clf.get_params()["n_jobs"] == XGB_PREDICT_THREADS
    assert _booster_nthread(clf) == XGB_PREDICT_THREADS


def test_reloaded_model_is_not_pinned_to_the_fit_thread_count(tmp_path):
    """save_model does not carry n_jobs, so load() is not exposed to the trap.

    Asserted rather than assumed: if a future XGBoost starts persisting it, the
    reload path acquires the same silent single-threading and this fails.
    """
    path = str(tmp_path / "m.json")
    _fitted_at_fit_threads().save_model(path)     # deliberately not retuned
    reloaded = xgb.XGBClassifier()
    reloaded.load_model(path)
    assert _booster_nthread(reloaded) != XGB_FIT_THREADS or XGB_FIT_THREADS == 0


def test_predict_is_admission_controlled():
    from config import PREDICT_CONCURRENCY
    assert main._predict_sem._value == PREDICT_CONCURRENCY

# ---------------------------------------------------------------------------
# Training buffer cap
#
# The old value was 21,024,000 — at the ~5,760 samples/day online collection
# actually produces, the "rolling window" would not have rolled for a decade.
# ---------------------------------------------------------------------------

def test_buffer_cap_is_reachable_within_the_deployment_lifetime():
    samples_per_day = 20 * (86_400 / 300)      # 20 per retrain cycle, 300 s apart
    assert tm._MAX_BUFFER / samples_per_day < 365


def test_buffer_cap_fits_in_the_vm_memory_budget():
    """Rows are float64 x FEATURE_COUNT; the cap has to stay well under 896 MB."""
    from pipeline.feature_engineering import FEATURE_COUNT
    bytes_at_cap = tm._MAX_BUFFER * FEATURE_COUNT * 8
    assert bytes_at_cap < 128 * 2**20


def test_retrain_trims_the_buffer_to_the_cap(monkeypatch):
    monkeypatch.setattr(tm, "_MAX_BUFFER", 50)
    m = tm.ThermalModel()
    m._buffer_X = [np.zeros(4) for _ in range(120)]
    m._buffer_y = list(range(120))

    # The trim block is what retrain() and seed_from_history() share; exercise it
    # directly rather than standing up OGN, meteo and terrain to reach it.
    if len(m._buffer_X) > tm._MAX_BUFFER:
        m._buffer_X = m._buffer_X[-tm._MAX_BUFFER:]
        m._buffer_y = m._buffer_y[-tm._MAX_BUFFER:]

    assert len(m._buffer_X) == 50
    assert m._buffer_y[0] == 70      # oldest dropped, newest kept

# ---------------------------------------------------------------------------
# Disk guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disk_guard_is_a_noop_with_headroom(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_free_disk_gb", lambda: 99.0)
    monkeypatch.setattr(main, "purge_old_beacons", lambda days=None: calls.append(days))
    await main._disk_guard()
    assert calls == []


@pytest.mark.asyncio
async def test_disk_guard_shortens_retention_when_tight(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_free_disk_gb", lambda: 0.5)
    monkeypatch.setattr(main, "purge_old_beacons",
                        lambda days=None: (calls.append(days), 1000)[1])
    await main._disk_guard()
    assert calls, "a disk under the floor must trigger an emergency purge"
    assert all(d < main.BEACON_RETENTION_DAYS for d in calls)


@pytest.mark.asyncio
async def test_disk_guard_never_purges_below_the_floor(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_free_disk_gb", lambda: 0.1)
    monkeypatch.setattr(main, "purge_old_beacons",
                        lambda days=None: (calls.append(days), 1000)[1])
    await main._disk_guard()
    assert all(d >= main.MIN_RETENTION_DAYS for d in calls)


@pytest.mark.asyncio
async def test_disk_guard_gives_up_rather_than_grinding(monkeypatch):
    """Space never recovers on a pre-auto_vacuum DB — the loop must still end."""
    monkeypatch.setattr(main, "_free_disk_gb", lambda: 0.5)
    monkeypatch.setattr(main, "purge_old_beacons", lambda days=None: 0)
    await main._disk_guard()   # must return, not spin


@pytest.mark.asyncio
async def test_purge_job_survives_a_failing_disk_guard(monkeypatch):
    """A broken guard must not stop the ordinary retention purge from counting."""
    monkeypatch.setattr(main, "purge_old_beacons", lambda days=None: 5)
    def boom():
        raise OSError("statvfs failed")
    monkeypatch.setattr(main, "_free_disk_gb", boom)
    await main._purge_job()    # logs and moves on
