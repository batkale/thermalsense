"""
Evaluation-only candidate features: full-resolution terrain, the archive-built
climb prior, and the three retrofitted columns.  None of these is served; the
tests pin that each computes what its docstring says, so a score read off the
harness is about the feature and not about a sign error.
"""

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from evaluation import hires_terrain as ht
from evaluation import drift_terrain as dt
from evaluation import evaluate as ev
from evaluation.archive_prior import FastPrior
from data.circling_prior import ClimbPrior, build_index


def _elevations(fn, lat=46.0, lon=10.0):
    """Elevation at every sample point from fn(north_m, east_m)."""
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(lat))
    return [fn((a - lat) * mlat, (b - lon) * mlon) for a, b in ht.sample_points(lat, lon)]


def test_a_hilltop_has_positive_tpi_and_a_valley_negative():
    hill = ht.features_from(_elevations(lambda n, e: -0.05 * math.hypot(n, e)))
    valley = ht.features_from(_elevations(lambda n, e: 0.05 * math.hypot(n, e)))
    tpi = slice(4, 8)
    assert all(v > 0 for v in hill[tpi])
    assert all(v < 0 for v in valley[tpi])
    # wider rings see further down the cone
    assert hill[7] > hill[4]


def test_a_south_facing_plane_reads_as_south_facing():
    # ground falls toward the south: higher to the north
    f = ht.features_from(_elevations(lambda n, e: 0.2 * n))
    slope, northness, eastness = f[0], f[1], f[2]
    assert slope == pytest.approx(math.degrees(math.atan(0.2)), abs=0.5)
    assert northness == pytest.approx(-math.sin(math.radians(slope)), abs=0.01)
    assert eastness == pytest.approx(0.0, abs=0.01)


def test_missing_patch_gives_nans_not_numbers():
    el = _elevations(lambda n, e: 100.0)
    el[24] = np.nan
    assert all(math.isnan(v) for v in ht.features_from(el))


def test_columns_for_finds_rows_by_position():
    lut = {(46.0, 10.0): np.arange(8.0)}
    X = np.zeros((2, 22))
    X[0, :2] = (46.0, 10.0)
    X[1, :2] = (47.0, 11.0)
    out = ht.columns_for(X, lut)
    assert out[0].tolist() == list(range(8))
    assert np.isnan(out[1]).all()


def test_windward_is_positive_facing_into_the_wind():
    from pipeline.feature_engineering import _wind_components
    u, v = _wind_components(10.0, 270.0)          # wind from the west
    X = np.zeros((2, 22))
    X[:, 3] = 20.0                                # slope
    X[:, 7], X[:, 8] = u, v
    X[0, 4], X[1, 4] = 270.0, 90.0                # faces west / east
    cols = ev.wind_aspect_columns(X)
    assert cols[0, 0] > 0 > cols[1, 0]


def test_insolation_favours_the_east_face_in_the_morning():
    from pipeline.feature_engineering import cyclic
    X = np.zeros((2, 22))
    X[:, 0], X[:, 1], X[:, 3] = 46.0, 10.0, 25.0
    X[0, 4], X[1, 4] = 90.0, 270.0                # east face, west face
    X[:, 15], X[:, 16] = cyclic(8.0, 24)          # 08:00 UTC
    X[:, 17], X[:, 18] = cyclic(180.0, 365)
    on_slope, ratio = ev.insolation_columns(X).T
    assert on_slope[0] > on_slope[1] and ratio[0] > 1 > ratio[1]


def test_fast_prior_agrees_with_climb_prior(tmp_path):
    db = tmp_path / "b.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL, alt REAL,
                   vario REAL, circling INTEGER, is_tow INTEGER, ac_type INTEGER,
                   under_tow INTEGER)""")
    from data.ogn_client import SOARING_AC_TYPES
    glider = min(SOARING_AC_TYPES)
    rng = np.random.default_rng(1)
    t0 = datetime(2026, 9, 20, 8, tzinfo=timezone.utc)
    rows = []
    for k in range(2000):
        t = t0 + timedelta(minutes=int(rng.integers(0, 60 * 72)))
        lat = 46.0 + rng.integers(0, 3) * 0.01 + 0.005
        lon = 10.0 + rng.integers(0, 3) * 0.01 + 0.005
        circ = int(rng.random() < 0.3)
        rows.append((t.isoformat(), f"g{k % 7}", lat, lon, 1500.0,
                     float(rng.normal(1.0, 1.5)), circ, 0, glider, 0))
    con.executemany("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    build_index(db)

    slow, fast = ClimbPrior(db), FastPrior(db)
    for h in (10, 30, 50, 80, 100):
        when = t0 + timedelta(hours=h)
        for lat, lon in ((46.005, 10.005), (46.025, 10.015), (47.0, 11.0)):
            a, b = slow.value(lat, lon, when), fast.value(lat, lon, when)
            if math.isnan(a):
                assert math.isnan(b)
            else:
                assert b == pytest.approx(a, rel=1e-9)


def _metres(X, name):
    """(north_m, east_m) of one SHIFTS entry from the row's own position."""
    la, lo = dt.shifted(X, name)[0]
    return ((la - X[0, 0]) * 111_320.0,
            (lo - X[0, 1]) * 111_320.0 * math.cos(math.radians(X[0, 0])))


def _row(wind_kmh, wind_from_deg, agl):
    from pipeline.feature_engineering import _wind_components
    X = np.zeros((1, 22))
    X[0, 0], X[0, 1] = 46.0, 10.0
    X[0, 7], X[0, 8] = _wind_components(wind_kmh, wind_from_deg)
    X[0, 21] = agl
    return X


def test_upwind_reads_the_ground_the_wind_comes_from():
    # 36 km/h = 10 m/s from the west, 600 m up, rising at 3 m/s: 2 km to the west
    X = _row(36.0, 270.0, 600.0)
    n, e = _metres(X, "up3")
    assert e == pytest.approx(-2000.0, abs=1.0) and abs(n) < 1.0
    n, e = _metres(X, "down1.5")
    assert e == pytest.approx(4000.0, abs=1.0) and abs(n) < 1.0
    n, e = _metres(X, "cross1.5")
    assert math.hypot(n, e) == pytest.approx(4000.0, abs=1.0) and abs(e) < 1.0
    assert _metres(X, "under") == (0.0, 0.0)


def test_drift_is_capped_and_vanishes_without_wind():
    assert math.hypot(*_metres(_row(80.0, 0.0, 3000.0), "up0.75")) == pytest.approx(dt.MAX_DRIFT_M, abs=1.0)
    assert math.hypot(*_metres(_row(0.0, 0.0, 3000.0), "up0.75")) < 1e-6


async def test_block_cache_answers_a_repeat_read_from_disk(tmp_path):
    calls = []

    class Inner:
        async def get(self, url, headers=None):
            calls.append(url)
            return dt._DiskCachedClient._Resp(206, b"block-bytes")

        async def aclose(self):
            pass

    c = dt._DiskCachedClient(Inner(), tmp_path)
    for _ in range(2):
        r = await c.get("https://x/tile.tif", headers={"Range": "bytes=0-9"})
        assert r.content == b"block-bytes"
    assert calls == ["https://x/tile.tif"]
