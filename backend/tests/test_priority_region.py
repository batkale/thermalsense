"""
Türkiye first (config.PRIORITY_BBOX).

The live buffer held 1 Turkish row of 78,428 on 20 Nov 2026: Türkiye is ~0.01%
of the worldwide feed, and both ways into the buffer sampled that feed blind.
These pin that the live sampler puts Turkish aircraft in front of the cap,
that the seed takes every labelable Turkish row before -- and apart from -- its
worldwide sample, that no row is read twice, and that the region fed is the
region the scores report.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

import config
import models.thermal_model as tm
from data import archive
from data import meteo_client as mc
from data import terrain_client as tc
from evaluation.regions import REGIONS

TR = [(39.80, 30.10), (36.55, 29.12)]          # İnönü, Babadağ
EU = (46.0, 11.0)

_METEO = {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
          "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
          "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
          "pbl_height": 1500.0, "soil_temp": 22.0}


def test_the_box_fed_is_the_turkiye_slice_the_scores_report():
    name, *box = REGIONS[0]
    assert name == "Türkiye"
    assert tuple(config.PRIORITY_BBOX) == tuple(box)


def test_in_priority_region():
    assert tm.in_priority_region(39.8, 30.1)
    assert not tm.in_priority_region(46.0, 11.0)
    assert not tm.in_priority_region(None, 30.1)
    assert not tm.in_priority_region("x", 30.1)


# ---------------------------------------------------------------------------
# The live sampler
# ---------------------------------------------------------------------------

def _glider(i, lat, lon):
    return {"id": f"G{i}", "lat": lat, "lon": lon, "alt": 2000.0, "vario": 2.5,
            "circling": True, "ac_type": 1, "is_tow": False, "under_tow": False}


async def test_turkish_aircraft_are_sampled_before_the_cap(monkeypatch):
    import data.ogn_client as oc
    asked = []
    europe = [_glider(i, 46.0 + i * 0.5, 11.0 + i * 0.5) for i in range(40)]
    turkish = [_glider(100 + k, la, lo) for k, (la, lo) in enumerate(TR)]

    async def gliders():
        return europe + turkish                    # feed order puts Türkiye last

    async def meteo_f(lat, lon, h, strict=False):
        asked.append((round(lat, 2), round(lon, 2)))
        return dict(_METEO)

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

    assert len(asked) == 3, "the cap still bounds the cycle"
    assert all(tm.in_priority_region(la, lo) for la, lo in asked[:2])
    assert not tm.in_priority_region(*asked[2]), "the rest keep feed order"


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------

def _beacon_db(path, n_europe=200):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    t0 = (datetime.now(timezone.utc) - timedelta(hours=6)).replace(minute=10, second=0,
                                                                   microsecond=0)

    def put(i, lat, lon, vario, circling, minutes=0):
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ((t0 + timedelta(minutes=minutes)).isoformat(), f"AC{i}",
                     lat, lon, 2000.0, vario, circling, 0, 7, 0))

    i = 0
    # Europe over three hours, Türkiye in one: the weather plan is biggest
    # first, so without the reordering Europe's request would be bought first.
    for k in range(n_europe):
        put(i, EU[0] + k * 1e-4, EU[1] + k * 1e-4,
            2.5 if k % 2 == 0 else -1.2, 1 if k % 2 == 0 else 0, minutes=k % 180)
        i += 1
    for la, lo in TR:                     # two labelable rows per site, one each way
        put(i, la, lo, 2.5, 1); i += 1
        put(i, la + 1e-3, lo, -1.2, 0); i += 1
        put(i, la, lo + 1e-3, 0.0, 0); i += 1        # ambiguous: never selected
    con.commit()
    con.close()


@pytest.fixture
def seed_env(tmp_path, monkeypatch):
    db = tmp_path / "b.db"
    _beacon_db(db)
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "arch")
    monkeypatch.setattr(tm, "_SEED_PACE_S", 0.0)
    ranges = []

    async def range_f(lat, lon, dts):
        ranges.append((lat, lon))
        return 0                                     # caches nothing; hours fetched one by one

    async def hist_f(lat, lon, dt, strict=False):
        return dict(_METEO)

    async def terrain(lat, lon, radius=None, strict=False):
        return np.full(tc.grid_shape(), 800.0)

    monkeypatch.setattr(mc, "fetch_meteo_historical_range", range_f)
    monkeypatch.setattr(mc, "fetch_meteo_historical", hist_f)
    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    return ranges


def _turkish_rows(m):
    X = np.array(m._buffer_X)
    return int(sum(tm.in_priority_region(la, lo) for la, lo in X[:, :2])) if len(X) else 0


async def test_the_seed_takes_every_turkish_row_before_its_worldwide_sample(seed_env):
    m = tm.ThermalModel()
    out = await m.seed_from_history(days_back=1, limit=5, reset=True)
    assert out["priority_rows"] == 4, "every labelable Turkish row, the ambiguous one excluded"
    assert out["rows_scanned"] == 4 + 5, "the worldwide sample is its own limit, outside the box"
    assert out["priority_added"] == 4 == _turkish_rows(m)
    # Bought first: the plan is otherwise biggest-first, which put Türkiye last.
    assert all(tm.in_priority_region(la, lo) for la, lo in seed_env[:2])


async def test_a_turkish_row_is_never_taken_twice(seed_env, monkeypatch):
    m = tm.ThermalModel()
    out = await m.seed_from_history(days_back=1, limit=10_000, reset=True)
    assert out["priority_rows"] == 4
    assert out["rows_scanned"] == 4 + 200
    assert _turkish_rows(m) == 4


async def test_priority_only_seeds_the_box_alone(seed_env):
    m = tm.ThermalModel()
    out = await m.seed_from_history(days_back=1, limit=5, reset=True, priority_only=True)
    assert out["rows_scanned"] == 4 and out["priority_only"] is True
    assert out["added"] == 4 == _turkish_rows(m)


async def test_the_priority_cap_holds(seed_env, monkeypatch):
    monkeypatch.setattr(tm, "PRIORITY_SEED_LIMIT", 2)
    m = tm.ThermalModel()
    out = await m.seed_from_history(days_back=1, limit=5, reset=True)
    assert out["priority_rows"] == 2


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------

def test_archive_rows_split_cleanly_on_the_box(tmp_path):
    db = tmp_path / "a.db"
    day = date(2026, 8, 1)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    places = [EU, (52.0, 9.0)] + TR
    for k in range(40):
        la, lo = places[k % len(places)]
        con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (datetime(2026, 8, 1, 12, k, tzinfo=timezone.utc).isoformat(), f"A{k}",
                     la, lo, 1500.0, 2.5, 1, 0, 1, 0))
    con.commit()
    con.close()
    root = tmp_path / "arch"
    archive.export_day(db, root, day)

    args = (root, day, day, [1], tm._POSITIVE_MIN_VARIO, tm._NEGATIVE_MAX_VARIO, 1000)
    inside = archive.training_rows(*args, bbox=config.PRIORITY_BBOX)
    outside = archive.training_rows(*args, exclude_bbox=config.PRIORITY_BBOX)
    everything = archive.training_rows(*args)
    assert len(inside) == 20 and all(tm.in_priority_region(r[1], r[2]) for r in inside)
    assert len(outside) == 20 and not any(tm.in_priority_region(r[1], r[2]) for r in outside)
    assert len(inside) + len(outside) == len(everything)
