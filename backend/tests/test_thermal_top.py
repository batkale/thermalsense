"""
The thermal top: min(boundary layer height, condensation level), metres AGL.

It replaced `cape_base` on 19 Nov 2026 as the "thermal base" on the map and as
the ceiling of the training filter (`_above_thermal_base`).  These pin the
estimate, that both training paths filter on it rather than on whatever
`cape_base` a meteo dict carries, that a buffer row can recompute it from its
own columns, and that the historical cache stays readable by an older image.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import main
import models.thermal_model as tm
from data import meteo_client as mc
from data import terrain_client as tc
from pipeline.feature_engineering import build_feature_matrix
from tests.test_sampling_floor import _meteo, _patch_predict_deps


def _wx(t2m, rh, pbl, **kw):
    return {"temp_2m": t2m, "humidity": rh, "pbl_height": pbl, **kw}


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------

def test_inonu_noon_19_nov_gives_the_cloud_base_not_2273_m():
    """The case that exposed cape_base: 17.7 degC, 63%, a 1,000 m layer.
    The layer clears the ~890 m condensation level, so cumulus caps the
    thermals there."""
    top = mc.thermal_top_agl(_wx(17.7, 0.63, 1000.0))
    assert top == pytest.approx(891, abs=5)
    assert top == pytest.approx(mc.lcl_agl(17.7, 0.63))


def test_blue_thermals_stop_at_the_boundary_layer():
    # 25 degC at 40%: condensation level ~1,800 m, far above a 1,200 m layer.
    assert mc.lcl_agl(25.0, 0.40) == pytest.approx(1800, abs=30)
    assert mc.thermal_top_agl(_wx(25.0, 0.40, 1200.0)) == 1200.0


def test_saturated_air_puts_the_top_on_the_ground():
    assert mc.thermal_top_agl(_wx(12.0, 1.0, 300.0)) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("meteo", [
    _wx(20.0, 0.0, 1500.0),               # no dew point
    _wx(20.0, None, 1500.0),
    _wx(None, 0.5, 1500.0),
    {"temp_2m": 20.0, "humidity": 0.5},   # no layer height
])
def test_missing_inputs_are_no_estimate(meteo):
    assert mc.thermal_top_agl(meteo) is None


def test_it_reads_no_cape_base():
    """A dict still carrying the old value — every historical-cache entry
    written before the switch — gets the new estimate."""
    a = mc.thermal_top_agl(_wx(20.0, 0.5, 1500.0))
    b = mc.thermal_top_agl(_wx(20.0, 0.5, 1500.0, cape_base=27_200))
    assert a == b


def test_a_buffer_row_recomputes_its_own_top():
    """Columns 5, 6 and 19 are temp_2m, humidity and pbl_height, so the
    offline harness can apply the filter to rows collected before it."""
    elev = np.full((9, 9), 500.0)
    rows, want = [], []
    for t2m, rh, pbl in ((24.0, 0.5, 1400.0), (17.7, 0.63, 1000.0),
                         (8.0, 0.95, 300.0), (30.0, 0.2, 3500.0)):
        meteo = dict(_meteo(600.0), temp_2m=t2m, humidity=rh, pbl_height=pbl)
        X = build_feature_matrix(meteo, elev, dt=datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
                                 lat_bounds=(46.0, 46.1), lon_bounds=(11.0, 11.1),
                                 alt_amsl=1500.0)
        rows.append(X[:3])
        want += [mc.thermal_top_agl(meteo)] * 3
    X = np.vstack(rows)
    assert np.allclose(mc.thermal_top_columns(X), want)
    dry = X[:1].copy()
    dry[:, 6] = 0.0                          # no humidity: no estimate, not a number
    assert np.isnan(mc.thermal_top_columns(dry)).all()


# ---------------------------------------------------------------------------
# The historical cache, for a rollback
# ---------------------------------------------------------------------------

class _HistResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        hourly = {k: [10.0] * 24 for k in mc.HOURLY_VARS}
        hourly.update(temperature_2m=[20.0] * 24, relativehumidity_2m=[50.0] * 24,
                      temperature_850hPa=[10.0] * 24, boundary_layer_height=[1500.0] * 24,
                      shortwave_radiation=[600.0] * 24)
        hourly["time"] = [f"2026-09-17T{h:02d}:00" for h in range(24)]
        return {"hourly": hourly}


async def test_cached_hours_carry_the_top_under_the_old_key(monkeypatch, tmp_path):
    """An older image reads meteo["cape_base"] from every historical-cache
    entry.  Without the key, a rollback's /seed would fail every beacon of
    every bucket cached after the switch."""
    class Client:
        async def get(self, url, params=None):
            return _HistResp()

    monkeypatch.setattr(mc, "_get_client", lambda: Client())
    monkeypatch.setattr(mc, "_hist_cache", {})
    monkeypatch.setattr(mc, "_hist_loaded", True)
    monkeypatch.setattr(mc, "_hist_pending", [])
    monkeypatch.setattr(mc, "_hist_path", lambda: tmp_path / "meteo_historical.jsonl")

    out = await mc.fetch_meteo_historical(46.0, 11.0, datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
                                          strict=True)
    top = mc.thermal_top_agl(out)
    assert out["cape_base"] == int(round(top))
    # The old formula would have said (20 - 8) / ((20 - 10) / 1.5) * 1000 = 1,800.
    assert out["cape_base"] != 1800
    assert "cape_base" not in mc._FALLBACK


# ---------------------------------------------------------------------------
# Both training paths filter on it
# ---------------------------------------------------------------------------
#
# Gliders at 2,000 m AMSL over 800 m ground are 1,200 m AGL.  With 20 degC at
# 50% the condensation level is ~1,340 m.  The old formula said 1,800 m (and
# the dicts below carry cape_base 3,000), so it kept them all; a 500 m layer
# puts the new top at 500 m and the gliders 400 m above top + margin.

def _training_meteo(pbl):
    return {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
            "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
            "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
            "pbl_height": pbl, "soil_temp": 22.0}


def _glider(i):
    return {"id": f"G{i}", "lat": 46.0 + i * 0.5, "lon": 11.0 + i * 0.5, "alt": 2000.0,
            "vario": 2.5, "circling": True, "ac_type": 1, "is_tow": False, "under_tow": False}


async def _live_cycle(monkeypatch, pbl):
    import data.ogn_client as oc

    async def gliders():
        return [_glider(i) for i in range(3)]

    async def meteo_f(lat, lon, h, strict=False):
        return _training_meteo(pbl)

    async def terrain(lat, lon, radius=None, strict=False):
        return np.full(tc.grid_shape(), 800.0)

    async def no_sleep(*_):
        return None

    monkeypatch.setattr(oc, "fetch_ogn_gliders", gliders)
    monkeypatch.setattr(oc, "is_thermal_evidence", lambda g: True)
    monkeypatch.setattr(mc, "fetch_meteo_features", meteo_f)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    monkeypatch.setattr(tm.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tm, "MAX_SAMPLE_GRIDS", 3)
    m = tm.ThermalModel()
    monkeypatch.setattr(m, "fit_and_gate", lambda: None)
    await m.retrain()
    return m


async def test_live_sampler_keeps_climbs_inside_the_layer(monkeypatch):
    m = await _live_cycle(monkeypatch, pbl=1500.0)
    assert len(m._buffer_X) == 3


async def test_live_sampler_drops_climbs_above_the_new_top(monkeypatch):
    m = await _live_cycle(monkeypatch, pbl=500.0)
    assert m._buffer_X == [], "cape_base 3,000 must not be what the filter reads"


async def _seed(monkeypatch, tmp_path, pbl):
    import sqlite3

    db = tmp_path / "b.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    # Ten minutes into an hour, so the 30 s of beacons share one meteo bucket.
    t0 = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(minute=10, second=0)
    for i in range(10):
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((t0 + timedelta(seconds=i * 3)).isoformat(), f"AC{i}",
                     46.0 + i * 1e-5, 11.0 + i * 1e-5, 2000.0,
                     2.5 if i % 2 == 0 else -1.2, 1 if i % 2 == 0 else 0, 0, 1, 0))
    con.commit()
    con.close()

    monkeypatch.setattr("config.DB_PATH", db, raising=False)
    monkeypatch.setattr("config.ARCHIVE_DIR", tmp_path / "arch", raising=False)
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "buf.npz"))
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 0, "cache_hits": 0,
                                 "historical_requests": 0, "historical_cache_hits": 0,
                                 "total": 0, "quota": 10000})

    async def meteo_f(lat, lon, dt, strict=False):
        return _training_meteo(pbl)

    async def terrain(lat, lon, radius=None, strict=False):
        return np.full(tc.grid_shape(), 800.0)

    monkeypatch.setattr(mc, "fetch_meteo_historical", meteo_f)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    return await tm.ThermalModel().seed_from_history(days_back=1, limit=500, reset=True)


async def test_seed_keeps_climbs_inside_the_layer(monkeypatch, tmp_path):
    out = await _seed(monkeypatch, tmp_path, pbl=1500.0)
    assert out["added"] == 10 and out["skipped_above_base"] == 0


async def test_seed_drops_climbs_above_the_new_top(monkeypatch, tmp_path):
    out = await _seed(monkeypatch, tmp_path, pbl=500.0)
    assert out["added"] == 0 and out["skipped_above_base"] == 10


# ---------------------------------------------------------------------------
# What the map shows
# ---------------------------------------------------------------------------

async def test_predict_shows_the_thermal_top_in_50_m_steps(monkeypatch):
    # _meteo(): 12 degC at 80% condenses at ~420 m, just above its 400 m layer.
    _patch_predict_deps(monkeypatch, solar_ghi=600.0)
    out = await main.predict(lat=47.1, lon=13.1)
    assert out["thermal_base"] == out["weather"]["thermal_base"] == 400
    assert out["thermal_base"] != _meteo(600.0)["cape_base"]


async def test_predict_sends_no_thermal_base_without_an_estimate(monkeypatch):
    async def meteo(lat, lon, forecast_h):
        return dict(_meteo(600.0), humidity=0.0)

    _patch_predict_deps(monkeypatch, solar_ghi=600.0)
    monkeypatch.setattr(main, "fetch_meteo_features", meteo)
    out = await main.predict(lat=47.1, lon=13.1)
    assert out["thermal_base"] is None and out["weather"]["thermal_base"] is None
