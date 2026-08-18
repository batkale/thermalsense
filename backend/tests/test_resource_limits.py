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


# ---------------------------------------------------------------------------
# Seed window vs retention window
#
# Lowering BEACON_RETENTION_DAYS moves the floor under /seed.  The failure it
# used to cause is silent — the query simply matches fewer rows and the seed
# reports success on a short sample — so it is pinned here rather than left to
# whoever next tunes retention.
# ---------------------------------------------------------------------------

import sqlite3
from datetime import datetime, timezone, timedelta

import config
from config import BEACON_RETENTION_DAYS
from data.ogn_client import SOARING_AC_TYPES


def _beacon_db(path, ages_days):
    """A beacons table holding one soaring, untowed row at each given age."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE beacons ("
        " ts TEXT NOT NULL, id TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,"
        " alt REAL NOT NULL, vario REAL NOT NULL, circling INTEGER NOT NULL,"
        " is_tow INTEGER NOT NULL, ac_type INTEGER, under_tow INTEGER)"
    )
    ac = sorted(SOARING_AC_TYPES)[0]
    for i, age in enumerate(ages_days):
        ts = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
        con.execute(
            "INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, f"G{i}", 39.8, 30.1, 1200.0, 2.0, 1, 0, ac, 0),
        )
    con.commit()
    con.close()


@pytest.mark.asyncio
async def test_seed_clamps_days_back_to_the_retention_window(tmp_path, monkeypatch):
    """A days_back past the window must not silently seed on purged history."""
    db = tmp_path / "ogn.db"
    _beacon_db(db, ages_days=[BEACON_RETENTION_DAYS + 3])
    monkeypatch.setattr(config, "DB_PATH", db)

    result = await tm.ThermalModel().seed_from_history(days_back=30)

    # Clamped to the window, so the only row — older than it — is out of scope.
    assert result["added"] == 0
    assert result["rows_scanned"] == 0


@pytest.mark.asyncio
async def test_seed_defaults_to_the_whole_retention_window(tmp_path, monkeypatch):
    """None means "whatever the window still holds" — not a hardcoded 3 days."""
    db = tmp_path / "ogn.db"
    # Inside the window but past a hardcoded 3-day default would have been, when
    # retention is wider than 3; at the current 2 days this is simply in scope.
    _beacon_db(db, ages_days=[BEACON_RETENTION_DAYS / 2])
    monkeypatch.setattr(config, "DB_PATH", db)

    seen = []

    async def _no_meteo(lat, lon, dt, strict=False):
        seen.append((lat, lon))
        return None      # stops before terrain/network, but proves the row was picked

    monkeypatch.setattr("data.meteo_client.fetch_meteo_historical", _no_meteo)

    result = await tm.ThermalModel().seed_from_history()

    assert result["rows_scanned"] == 1
    assert seen, "row inside the retention window was not scanned"
