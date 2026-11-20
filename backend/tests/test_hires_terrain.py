"""
Full-resolution terrain in production (pipeline/hires_terrain.py) and the
switch from 22 to 30 columns.

Three things matter and each is pinned here:
  * what is served is what was measured — the vectorised columns equal the
    scalar reference in evaluation/hires_terrain.py;
  * the switch has no dark period — a 22-column model keeps serving on the
    first 22 columns, and a 22-column buffer is widened and filled in rather
    than set aside;
  * nothing is fabricated — a failed read pauses or skips, it never becomes a
    number, and terrain nobody could read stays NaN.
"""

from collections import OrderedDict

import numpy as np
import pytest
import xgboost as xgb

import models.thermal_model as tm
from data import terrain_client as tc
from evaluation import hires_terrain as ref
from pipeline import hires_terrain as ht
from pipeline.feature_engineering import COL_HIRES, FEATURE_COUNT, HIRES_COUNT


# --- what is served is what was measured -------------------------------------------

def test_sample_points_match_the_measured_reference():
    lat = np.array([39.81, 46.5, -33.9, 64.2])
    lon = np.array([30.11, 10.3, 18.4, -21.9])
    plat, plon = ht.sample_points(lat, lon)
    for k in range(len(lat)):
        want = np.array(ref.sample_points(lat[k], lon[k]))
        assert np.allclose(plat[k], want[:, 0], atol=1.5e-6)
        assert np.allclose(plon[k], want[:, 1], atol=1.5e-6)


def test_columns_match_the_measured_reference():
    rng = np.random.default_rng(3)
    elev = rng.uniform(200, 2400, (40, ht.N_POINTS))
    elev[5, 10] = np.nan                       # a hole in a patch: the whole row unknown
    elev[6, ht.N_PATCH + 2] = np.nan           # a hole in a ring: averaged around
    elev[7, ht.N_PATCH:ht.N_PATCH + ht.N_RING] = np.nan   # a whole ring missing
    got = ht.features(elev)
    want = np.array([ref.features_from(list(e)) for e in elev])
    assert ht.COLUMNS == ref.COLUMNS
    assert np.allclose(got, want, equal_nan=True)
    assert np.isnan(got[5]).all() and np.isnan(got[7, 4]) and np.isfinite(got[6, 4])


# --- the grid read -------------------------------------------------------------------

@pytest.fixture
def copernicus(monkeypatch):
    """Copernicus switched on, with a plane rising 0.1 m per metre to the north."""
    monkeypatch.setattr(tc, "TERRAIN_SOURCE", "copernicus")
    monkeypatch.setattr(tc, "_hires_cache", OrderedDict())
    calls = {"n": 0}

    async def window(*box):
        calls["n"] += 1
        return {}

    def plane(window, lats, lons):
        return ((np.asarray(lats) - 46.0) * 111_320.0 * 0.1 + 900.0).astype(np.float32)

    monkeypatch.setattr(tc.dem_client, "load_window", window)
    monkeypatch.setattr(tc.dem_client, "sample_window", plane)
    return calls


async def test_a_south_facing_plane_reads_as_one(copernicus):
    shape = (11, 11)
    g = await tc.fetch_hires_grid(46.3, 10.2, shape=shape)
    assert g.shape == (121, HIRES_COUNT)
    slope, north, east = g[:, 0], g[:, 1], g[:, 2]
    assert np.allclose(slope, np.degrees(np.arctan(0.1)), atol=0.05)
    assert np.allclose(north, -np.sin(np.arctan(0.1)), atol=1e-3)   # rises north: faces south
    assert np.allclose(east, 0.0, atol=1e-3)
    assert np.allclose(g[:, 4:], 0.0, atol=0.1)                     # a plane has no hills


async def test_a_grid_is_read_once_then_served_from_the_bounded_cache(copernicus, monkeypatch):
    monkeypatch.setattr(tc, "TERRAIN_HIRES_CACHE", 1)
    a = await tc.fetch_hires_grid(46.3, 10.2, shape=(5, 5))
    reads = copernicus["n"]
    b = await tc.fetch_hires_grid(46.3, 10.2, shape=(5, 5))
    assert b is a and copernicus["n"] == reads
    await tc.fetch_hires_grid(47.3, 11.2, shape=(5, 5))           # evicts the first
    assert len(tc._hires_cache) == 1
    assert tc.source_usage()["cached"]["hires_grids_max"] == 1


async def test_a_failed_read_is_none_to_training_and_unknown_to_display(monkeypatch):
    monkeypatch.setattr(tc, "TERRAIN_SOURCE", "copernicus")
    monkeypatch.setattr(tc, "_hires_cache", OrderedDict())

    async def broken(*box):
        raise RuntimeError("S3 unreachable")

    monkeypatch.setattr(tc.dem_client, "load_window", broken)
    assert await tc.fetch_hires_grid(46.3, 10.2, strict=True, shape=(5, 5)) is None
    shown = await tc.fetch_hires_grid(46.3, 10.2, shape=(5, 5))
    assert shown.shape == (25, HIRES_COUNT) and np.isnan(shown).all()
    assert len(tc._hires_cache) == 0                               # a failure is never cached


async def test_without_copernicus_the_columns_are_unknown_not_zero():
    out = await tc.hires_at_points(np.array([46.3, 39.8]), np.array([10.2, 30.1]))
    assert out.shape == (2, HIRES_COUNT) and np.isnan(out).all()


# --- the switch from 22 to 30 columns ----------------------------------------------

def _old_buffer(groups=150, per_group=16, seed=0):
    """A 22-column buffer of controlled (day, cell) groups; labels track slope."""
    rng = np.random.default_rng(seed)
    n = groups * per_group
    X = rng.random((n, 22))
    gi = np.repeat(np.arange(groups), per_group)
    angle = 2 * np.pi * (150 + gi % 10) / 365
    X[:, 17], X[:, 18] = np.sin(angle), np.cos(angle)
    X[:, 0] = 39.0 + (gi // 10 % 4) * 0.5
    X[:, 1] = 29.0 + (gi // 40) * 0.5
    X[:, 3] = rng.uniform(0, 30, n)
    X[:, 4] = rng.uniform(0, 360, n)
    X[:, 21] = rng.uniform(300, 2500, n)
    X[:, 5], X[:, 6], X[:, 19] = 20.0, 0.3, 3000.0   # thermal top ~2,260 m, above every row
    y = (rng.random(n) < 1 / (1 + np.exp(-(X[:, 3] - 15) / 4))).astype(int)
    return X, y


async def _load_old(tmp_path, monkeypatch, serving_score="0.6"):
    """Write a 22-column model and buffer as the live node has them, then load."""
    model_path, buffer_path = tmp_path / "thermal_xgb.json", tmp_path / "b.npz"
    monkeypatch.setattr(tm, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(buffer_path))
    X, y = _old_buffer()
    # Fitted the way production fits: never on the frozen benchmark rows it is
    # later scored on.  Fitted on them, it would look better than it is and the
    # gate would reject any challenger for the wrong reason.
    from evaluation.holdout import benchmark_mask
    train = ~benchmark_mask(X)
    old = xgb.XGBClassifier(n_estimators=20, max_depth=3)
    old.fit(X[train], y[train])
    old.get_booster().set_attr(**{tm._BENCH_ATTR: serving_score})
    old.save_model(str(model_path))
    np.savez(buffer_path, X=X, y=y, label_version=np.array(tm._LABEL_VERSION))
    m = tm.ThermalModel()
    await m.load()
    return m, old, X


async def test_a_22_column_model_keeps_serving_on_the_first_22_columns(tmp_path, monkeypatch):
    m, old, X = await _load_old(tmp_path, monkeypatch)
    assert m.is_loaded and m.model.n_features_in_ == 22
    wide = np.hstack([X[:200], np.random.default_rng(1).random((200, 8))])
    served = m._score_served(m.model, wide)
    expected = old.predict_proba(X[:200])[:, 1] * m._terrain_multiplier(wide)
    assert np.allclose(served, expected)
    mean, std = m.predict(wide)                       # the /predict path does not raise
    assert len(mean) == 200


async def test_an_old_buffer_is_widened_not_set_aside_and_fits_wait(tmp_path, monkeypatch):
    m, old, X = await _load_old(tmp_path, monkeypatch)
    assert not list(tmp_path.glob("b.npz.v*"))        # nothing set aside
    rows = np.array(m._buffer_X)
    assert rows.shape == (len(X), FEATURE_COUNT)
    assert np.array_equal(rows[:, :COL_HIRES], X) and np.isnan(rows[:, COL_HIRES:]).all()
    assert m.hires_status()["backfill_pending"] is True
    m.fit_and_gate()
    assert m.last_fit["outcome"] == "filling_in_terrain"
    assert m.model.n_features_in_ == 22               # the old model still serves


async def test_a_failed_read_pauses_the_fill_in_without_giving_up_on_rows(tmp_path, monkeypatch):
    m, _, _ = await _load_old(tmp_path, monkeypatch)

    async def broken(lats, lons):
        raise RuntimeError("S3 unreachable")

    monkeypatch.setattr(tc, "hires_at_points", broken)
    await m._backfill_hires(60)
    assert m._hires_backfill and m.hires_status()["rows_remaining"] == len(m._buffer_X)


async def test_without_copernicus_the_fill_in_finishes_with_unknown_terrain(tmp_path, monkeypatch):
    m, _, _ = await _load_old(tmp_path, monkeypatch)
    await m._backfill_hires(60)                      # conftest: no Copernicus in tests
    assert not m._hires_backfill
    assert np.isnan(np.array(m._buffer_X)[:, COL_HIRES:]).all()


async def test_after_the_fill_in_the_gate_judges_30_columns_against_22(tmp_path, monkeypatch):
    m, _, X = await _load_old(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "_MIN_BENCH_GROUPS", 10)

    async def terrain(lats, lons):
        rng = np.random.default_rng(int(abs(lats[0]) * 1000))
        return rng.random((len(lats), HIRES_COUNT)).astype(np.float32)

    monkeypatch.setattr(tc, "hires_at_points", terrain)
    await m._backfill_hires(600)
    assert not m._hires_backfill and m.hires_status()["rows_remaining"] == 0
    assert np.isfinite(np.array(m._buffer_X)[:, COL_HIRES:]).all()
    # Saved wide, so a restart does not fill the same rows in again.
    with np.load(tmp_path / "b.npz") as d:
        assert d["X"].shape[1] == FEATURE_COUNT

    m.fit_and_gate()
    fit = m.last_fit
    # The decision itself depends on whether the new columns carry signal —
    # random ones here do not, real terrain did offline.  What must hold either
    # way: a 30-column fit was judged against the 22-column model on the same
    # rows, the comparison is on record, and whichever won is what serves.
    assert fit["outcome"] in ("accepted", "rejected"), fit
    assert fit["width"] == FEATURE_COUNT and fit["serving_width"] == 22
    assert fit["delta"] is not None
    assert m.model.n_features_in_ == (FEATURE_COUNT if fit["outcome"] == "accepted" else 22)


async def test_a_wider_model_wins_when_the_new_columns_carry_the_signal(tmp_path, monkeypatch):
    """The accepting side of the same decision: labels a 22-column model cannot
    see, but a 30-column one can, must replace the serving model."""
    m, _, _ = await _load_old(tmp_path, monkeypatch)
    monkeypatch.setattr(tm, "_MIN_BENCH_GROUPS", 10)
    rng = np.random.default_rng(7)
    for r in m._buffer_X:                       # terrain that decides the label
        r[COL_HIRES:] = rng.random(HIRES_COUNT)
    m._buffer_y = [int(r[COL_HIRES + 4] > 0.5) for r in m._buffer_X]
    m._hires_backfill = False
    m.fit_and_gate()
    assert m.last_fit["outcome"] == "accepted", m.last_fit
    assert m.model.n_features_in_ == FEATURE_COUNT and m.last_fit["delta"][0] > 0


async def test_a_cycle_that_adds_nothing_still_fills_in_and_then_fits(tmp_path, monkeypatch):
    """retrain() skips the fit when the buffer is unchanged since the last one.
    The fill-in changes rows in place and holds every fit until it is done, so
    a dark cycle must still run it, and finishing it must count as a change —
    or the first wider model waits for the next daylight sample."""
    import data.ogn_client as oc
    from data import meteo_client as mc

    m, _, _ = await _load_old(tmp_path, monkeypatch)

    async def gliders():
        return [{"id": "G1", "lat": 39.5, "lon": 29.5, "alt": 2000.0, "vario": 2.5,
                 "circling": True, "ac_type": 1, "is_tow": False, "under_tow": False}]

    async def dark(lat, lon, h, strict=False):
        return {"temp_2m": 8.0, "humidity": 0.8, "wind_speed": 3.0, "wind_dir": 0.0,
                "cape": 0.0, "cin": 5.0, "solar_ghi": 0.0, "temp_850": 4.0,
                "temp_500": -20.0, "lapse_rate": 6.0, "cape_base": 0,
                "pbl_height": 200.0, "soil_temp": 10.0}

    reads = {"ok": False}

    async def terrain(lats, lons):
        if not reads["ok"]:
            raise RuntimeError("S3 unreachable")
        return np.random.default_rng(0).random((len(lats), HIRES_COUNT)).astype(np.float32)

    monkeypatch.setattr(oc, "fetch_ogn_gliders", gliders)
    monkeypatch.setattr(oc, "is_thermal_evidence", lambda g: True)
    monkeypatch.setattr(mc, "fetch_meteo_features", dark)
    monkeypatch.setattr(tc, "hires_at_points", terrain)

    fits = []
    real_fit = m.fit_and_gate
    monkeypatch.setattr(m, "fit_and_gate", lambda: (fits.append(1), real_fit()))

    await m.retrain()                     # first cycle after start: fill-in paused, fit held
    assert m._hires_backfill and m.last_fit["outcome"] == "filling_in_terrain"
    assert len(fits) == 1

    reads["ok"] = True
    await m.retrain()                     # adds nothing, but the fill-in finishes
    assert not m._hires_backfill
    assert len(fits) == 2 and m.last_fit["outcome"] != "filling_in_terrain"

    await m.retrain()                     # now nothing has changed
    assert len(fits) == 2
