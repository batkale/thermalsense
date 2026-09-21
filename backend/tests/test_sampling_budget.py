"""
What one retrain cycle is allowed to spend.

The fixed cap of 20 grids existed to spare OpenTopoData's 1,000/day. Copernicus
removed that limit, and the cap then held collection to ~200 samples a day —
weeks away from the 30 mixed-label groups the gate needs. What binds now is
Open-Meteo's 10,000 forecast calls, the cycle deadline, and the ~7 MB feature
matrix each grid builds, so those are what the loop stops on.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import models.thermal_model as tm
from data import meteo_client as mc, terrain_client as tc


# ---------------------------------------------------------------------------
# Live forecast cache and counter
# ---------------------------------------------------------------------------

class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        t = ["2026-09-17T00:00"] + [f"2026-09-17T{h:02d}:00" for h in range(1, 24)]
        n = len(t)
        return {"hourly": {"time": t, "temperature_2m": [20.0] * n,
                           "relativehumidity_2m": [50.0] * n, "windspeed_10m": [10.0] * n,
                           "winddirection_10m": [270.0] * n, "cape": [500.0] * n,
                           "convective_inhibition": [5.0] * n,
                           "shortwave_radiation": [600.0] * n,
                           "temperature_850hPa": [10.0] * n, "temperature_500hPa": [-15.0] * n,
                           "boundary_layer_height": [1500.0] * n,
                           "soil_temperature_0_to_7cm": [22.0] * n}}


@pytest.fixture
def meteo(monkeypatch):
    calls = []

    class Client:
        async def get(self, url, params=None):
            calls.append(params)
            return _Resp()

    monkeypatch.setattr(mc, "_get_client", lambda: Client())
    monkeypatch.setattr(mc, "_live_cache", {})
    monkeypatch.setattr(mc, "_live_hour", "")
    monkeypatch.setattr(mc, "_live_usage", {"day": "", "requests": 0, "cache_hits": 0})
    return calls


async def test_grids_in_the_same_cell_and_hour_share_one_request(meteo):
    """build_feature_matrix spreads one point sample across the whole 11 km grid,
    so two requests 0.1° apart were always going to produce the same columns."""
    await mc.fetch_meteo_features(46.80, 11.20, 0)
    await mc.fetch_meteo_features(46.83, 11.23, 0)     # same 0.1° cell
    assert len(meteo) == 1
    u = mc.live_usage()
    assert u["requests"] == 1 and u["cache_hits"] == 1 and u["quota"] == 10000


async def test_a_different_cell_is_fetched(meteo):
    await mc.fetch_meteo_features(46.80, 11.20, 0)
    await mc.fetch_meteo_features(47.40, 11.20, 0)
    assert len(meteo) == 2


async def test_the_synthetic_fallback_is_never_cached(monkeypatch):
    class Boom:
        async def get(self, *a, **k):
            raise RuntimeError("offline")

    monkeypatch.setattr(mc, "_get_client", lambda: Boom())
    monkeypatch.setattr(mc, "_live_cache", {})
    monkeypatch.setattr(mc, "_live_hour", "")
    out = await mc.fetch_meteo_features(46.8, 11.2, 0)
    assert out == mc._FALLBACK
    assert mc._live_cache == {}, "pinning invented weather for an hour would feed it to the buffer"


# ---------------------------------------------------------------------------
# Cycle budgets
# ---------------------------------------------------------------------------

def _glider(i):
    return {"id": f"G{i}", "lat": 46.0 + i * 0.5, "lon": 11.0 + i * 0.5, "alt": 2000.0,
            "vario": 2.5, "circling": True, "ac_type": 1, "is_tow": False, "under_tow": False}


@pytest.fixture
def cycle(monkeypatch):
    """A retrain cycle with 50 candidates, counting what each grid costs."""
    import data.ogn_client as oc
    seen = {"meteo": 0, "terrain": 0}

    async def gliders():
        return [_glider(i) for i in range(50)]

    async def meteo_f(lat, lon, h):
        seen["meteo"] += 1
        return {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
                "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
                "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
                "pbl_height": 1500.0, "soil_temp": 22.0}

    async def terrain(lat, lon, radius=None, strict=False):
        seen["terrain"] += 1
        return np.full(tc.grid_shape(), 800.0)

    async def no_sleep(*_):
        return None

    monkeypatch.setattr(oc, "fetch_ogn_gliders", gliders)
    monkeypatch.setattr(oc, "is_thermal_evidence", lambda g: True)
    monkeypatch.setattr(mc, "fetch_meteo_features", meteo_f)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    monkeypatch.setattr(tm.asyncio, "sleep", no_sleep)
    return seen


async def test_cycle_stops_at_the_grid_cap(cycle, monkeypatch):
    monkeypatch.setattr(tm, "MAX_SAMPLE_GRIDS", 3)
    m = tm.ThermalModel()
    monkeypatch.setattr(m, "fit_and_gate", lambda: None)
    await m.retrain()
    assert cycle["meteo"] == 3


async def test_cycle_stops_when_the_time_budget_is_gone(cycle, monkeypatch):
    monkeypatch.setattr(tm, "MAX_SAMPLE_GRIDS", 50)
    # Negative, not 0: the clock has ~15 ms granularity on Windows, so a zero
    # budget lets a few iterations through before it ticks.
    monkeypatch.setattr(tm, "SAMPLE_TIME_BUDGET_S", -1.0)
    m = tm.ThermalModel()
    monkeypatch.setattr(m, "fit_and_gate", lambda: None)
    await m.retrain()
    assert cycle["meteo"] == 0, "the deadline must be checked before spending a call"


async def test_cycle_stops_at_the_meteo_quota(cycle, monkeypatch):
    monkeypatch.setattr(tm, "MAX_SAMPLE_GRIDS", 50)
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 10000, "cache_hits": 0, "quota": 10000})
    m = tm.ThermalModel()
    monkeypatch.setattr(m, "fit_and_gate", lambda: None)
    await m.retrain()
    assert cycle["meteo"] == 0


async def test_a_dark_grid_costs_no_terrain(cycle, monkeypatch):
    """At night every candidate is dropped on solar_ghi. Building the feature
    matrix first spent 7 MB and a terrain read to throw the result away."""
    async def dark(lat, lon, h):
        cycle["meteo"] += 1
        return {"temp_2m": 8.0, "humidity": 0.8, "wind_speed": 3.0, "wind_dir": 0.0,
                "cape": 0.0, "cin": 5.0, "solar_ghi": 0.0, "temp_850": 4.0,
                "temp_500": -20.0, "lapse_rate": 6.0, "cape_base": 0,
                "pbl_height": 200.0, "soil_temp": 10.0}

    monkeypatch.setattr(mc, "fetch_meteo_features", dark)
    monkeypatch.setattr(tm, "MAX_SAMPLE_GRIDS", 5)
    m = tm.ThermalModel()
    monkeypatch.setattr(m, "fit_and_gate", lambda: None)
    await m.retrain()
    assert cycle["meteo"] == 5 and cycle["terrain"] == 0
    assert m._buffer_X == []


# ---------------------------------------------------------------------------
# Nightly seed: the job that puts the archive into training
# ---------------------------------------------------------------------------

def _beacon_db(path, n=200):
    """Beacons a few hours old, so a days_back=1 window contains them."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    for i in range(n):
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((base + timedelta(seconds=i * 20)).isoformat(), f"AC{i % 7}",
                     46.0 + (i % 23) * 0.05, 11.0 + (i % 19) * 0.05, 2000.0,
                     2.5 if i % 4 == 0 else -1.2, 1 if i % 4 == 0 else 0, 0, 1, 0))
    con.commit()
    con.close()


async def test_seed_stops_at_the_meteo_budget_and_says_so(tmp_path, monkeypatch):
    """
    The seed and the live sampler share one 10,000/day allowance. A nightly run
    that drained it would leave the next morning's collection with nothing.
    """
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "b.db"
    _beacon_db(db)
    monkeypatch.setattr("config.DB_PATH", db, raising=False)
    monkeypatch.setattr("config.ARCHIVE_DIR", tmp_path / "arch", raising=False)
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "buf.npz"))
    monkeypatch.setattr(tm, "SEED_METEO_BUDGET", 10)
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 50, "cache_hits": 0,
                                 "historical_requests": 0, "historical_cache_hits": 0,
                                 "total": 50, "quota": 10000})

    spent = []

    async def meteo(lat, lon, dt, strict=False):
        spent.append(1)
        return None

    monkeypatch.setattr(mc, "fetch_meteo_historical", meteo)

    m = tm.ThermalModel()
    result = await m.seed_from_history(days_back=1, limit=500, reset=True)
    assert result["stopped_at_meteo_budget"] is True
    assert spent == [], "the budget must be checked before the first call, not after"


async def test_seed_stops_at_the_time_budget(tmp_path, monkeypatch):
    """
    The budget that was missing on 18 Sep 2026.  The meteo allowance bounds what
    a run costs Open-Meteo; only the clock bounds what it costs the machine, and
    1,430 buckets never came near 6,000 calls, so nothing ever stopped the run.
    """
    db = tmp_path / "b.db"
    _beacon_db(db)
    monkeypatch.setattr("config.DB_PATH", db, raising=False)
    monkeypatch.setattr("config.ARCHIVE_DIR", tmp_path / "arch", raising=False)
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "buf.npz"))
    # Negative, not 0: the clock has ~15 ms granularity on Windows, so a zero
    # budget lets a few buckets through before it ticks.
    monkeypatch.setattr(tm, "SEED_TIME_BUDGET_S", -1.0)
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 0, "cache_hits": 0,
                                 "historical_requests": 0, "historical_cache_hits": 0,
                                 "total": 0, "quota": 10000})

    spent = []

    async def meteo(lat, lon, dt, strict=False):
        spent.append(1)
        return None

    monkeypatch.setattr(mc, "fetch_meteo_historical", meteo)

    m = tm.ThermalModel()
    result = await m.seed_from_history(days_back=1, limit=500, reset=True)
    assert result["stopped_at_time_budget"] is True
    assert result["stopped_at_meteo_budget"] is False
    assert spent == [], "the deadline must be checked before spending a call"


async def test_beacons_in_one_bucket_share_an_elevation_grid(tmp_path, monkeypatch):
    """
    The seed builds a 201x201 grid per beacon to keep one row of it.  Beacons in
    a 0.5 deg square within one hour mostly snap to the same centre, so the grid
    is the same array fetched over and over.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "b.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    # 12 beacons within metres of each other: one bucket, one snapped centre.
    for i in range(12):
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((base + timedelta(seconds=i)).isoformat(), f"AC{i}",
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

    async def meteo(lat, lon, dt, strict=False):
        return {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
                "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
                "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
                "pbl_height": 1500.0, "soil_temp": 22.0}

    fetched = []

    async def terrain(lat, lon, radius=None, strict=False):
        fetched.append((lat, lon))
        return np.full(tc.grid_shape(), 800.0)

    monkeypatch.setattr(mc, "fetch_meteo_historical", meteo)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)

    m = tm.ThermalModel()
    await m.seed_from_history(days_back=1, limit=500, reset=True)
    assert len(fetched) == 1, f"one grid for 12 co-located beacons, got {len(fetched)}"


# ---------------------------------------------------------------------------
# One feature matrix per grid, not per beacon
# ---------------------------------------------------------------------------

def test_only_three_columns_belong_to_the_observation():
    """
    The seed builds a grid's matrix once and corrects the columns that belong to
    the beacon rather than to the ground.  That is only sound while those really
    are the only columns an observation moves, so pin it against the matrix
    builder itself rather than against a written list.
    """
    from datetime import datetime, timezone
    from pipeline import feature_engineering as fe

    meteo = {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
             "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
             "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
             "pbl_height": 1500.0, "soil_temp": 22.0}
    elev = np.linspace(500.0, 900.0, 9 * 9).reshape(9, 9)
    bounds = ((46.0, 46.2), (11.0, 11.2))

    base = fe.build_feature_matrix(meteo, elev, dt=datetime(2026, 9, 17, 13, 0,
                                   tzinfo=timezone.utc),
                                   lat_bounds=bounds[0], lon_bounds=bounds[1],
                                   alt_amsl=None)
    moved = fe.build_feature_matrix(meteo, elev, dt=datetime(2026, 9, 17, 13, 37,
                                    tzinfo=timezone.utc),
                                    lat_bounds=bounds[0], lon_bounds=bounds[1],
                                    alt_amsl=2400.0)

    differing = {c for c in range(fe.FEATURE_COUNT)
                 if not np.allclose(base[:, c], moved[:, c])}
    assert differing == {fe.COL_HOUR_SIN, fe.COL_HOUR_COS, fe.COL_ALT_AGL}, (
        f"columns {sorted(differing)} move with the observation; the seed only "
        f"corrects {sorted({fe.COL_HOUR_SIN, fe.COL_HOUR_COS, fe.COL_ALT_AGL})}"
    )


def test_seed_row_matches_a_full_build():
    """
    The reused-grid row must equal the row a per-beacon build would have
    produced, exactly.  If it does not, the seed is quietly training on
    different features from the ones the live sampler collects.
    """
    from datetime import datetime, timezone
    from pipeline import feature_engineering as fe

    meteo = {"temp_2m": 18.0, "humidity": 0.45, "wind_speed": 5.0, "wind_dir": 210.0,
             "cape": 640.0, "cin": 12.0, "solar_ghi": 810.0, "temp_850": 9.0,
             "temp_500": -14.0, "lapse_rate": 6.1, "cape_base": 2600,
             "pbl_height": 1720.0, "soil_temp": 24.0}
    elev = np.linspace(400.0, 1200.0, 11 * 11).reshape(11, 11)
    lat_bounds, lon_bounds = (46.0, 46.2), (11.0, 11.2)
    dt = datetime(2026, 9, 17, 14, 23, tzinfo=timezone.utc)
    alt = 2150.0
    idx = 57

    # What the seed now does: grid matrix once, then correct the row.
    base = fe.build_feature_matrix(meteo, elev, dt=dt, lat_bounds=lat_bounds,
                                   lon_bounds=lon_bounds, alt_amsl=None)
    row = base[idx].copy()
    row[fe.COL_ALT_AGL] = max(alt - float(elev.ravel()[idx]), 0.0)
    row[fe.COL_HOUR_SIN], row[fe.COL_HOUR_COS] = fe.cyclic(dt.hour + dt.minute / 60, 24)

    # What it used to do: a whole matrix for this one beacon.
    full = fe.build_feature_matrix(meteo, elev, dt=dt, lat_bounds=lat_bounds,
                                   lon_bounds=lon_bounds, alt_amsl=alt)

    assert np.array_equal(row, full[idx]), (
        f"reused row differs at columns "
        f"{[c for c in range(fe.FEATURE_COUNT) if row[c] != full[idx][c]]}"
    )


def test_a_row_does_not_retain_its_grid(tmp_path, monkeypatch):
    """A view into the cached matrix would keep all ~7 MB of it alive for every
    sample in the buffer — the leak the per-grid cache exists to avoid."""
    from datetime import datetime, timezone
    from pipeline import feature_engineering as fe

    meteo = {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
             "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
             "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
             "pbl_height": 1500.0, "soil_temp": 22.0}
    elev = np.full((9, 9), 700.0)
    base = fe.build_feature_matrix(meteo, elev, dt=datetime(2026, 9, 17, 13, 0,
                                   tzinfo=timezone.utc),
                                   lat_bounds=(46.0, 46.2), lon_bounds=(11.0, 11.2),
                                   alt_amsl=None)
    row = base[5].copy()
    assert row.base is None, "the buffered row must own its memory"


async def test_one_matrix_per_grid_not_per_beacon(tmp_path, monkeypatch):
    """
    The 18 and 21 Sep 2026 outages in one assertion: many beacons at one place
    must cost one feature matrix, not one apiece.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from pipeline import feature_engineering as fe

    db = tmp_path / "b.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    base_t = datetime.now(timezone.utc) - timedelta(hours=3)
    for i in range(30):
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((base_t + timedelta(seconds=i * 3)).isoformat(), f"AC{i}",
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
        return {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
                "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
                "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
                "pbl_height": 1500.0, "soil_temp": 22.0}

    async def terrain(lat, lon, radius=None, strict=False):
        return np.full(tc.grid_shape(), 800.0)

    builds = []
    real_build = fe.build_feature_matrix

    def counting_build(*a, **kw):
        builds.append(kw.get("alt_amsl"))
        return real_build(*a, **kw)

    monkeypatch.setattr(mc, "fetch_meteo_historical", meteo_f)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    monkeypatch.setattr(tm, "build_feature_matrix", counting_build, raising=False)
    monkeypatch.setattr(fe, "build_feature_matrix", counting_build)

    m = tm.ThermalModel()
    result = await m.seed_from_history(days_back=1, limit=500, reset=True)
    assert result["added"] > 0, "the fixture must produce labelable rows"
    assert len(builds) == 1, f"30 co-located beacons built {len(builds)} matrices"
    assert builds == [None], "the grid matrix must be built without a beacon altitude"


async def test_nightly_seed_reads_yesterday(tmp_path, monkeypatch):
    """One day back, appending rather than resetting: the live sampler's rows
    from the same day are still worth keeping."""
    import main

    calls = {}

    async def fake_seed_job(days_back, limit, reset=False):
        calls.update(days_back=days_back, limit=limit, reset=reset)

    monkeypatch.setattr(main, "_seed_job", fake_seed_job)
    await main._nightly_seed_job()
    assert calls == {"days_back": 1, "limit": main.NIGHTLY_SEED_LIMIT, "reset": False}
