import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from data.circling_prior import (
    ClimbPrior, NO_DATA, PRIOR_RES, _cell, _hour_key, build_index,
)

SOARING = frozenset({"glider"})
T0 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path, beacons):
    """beacons: (offset_hours, lat, lon, vario, circling) relative to T0."""
    path = tmp_path / "ogn.db"
    with sqlite3.connect(path) as con:
        con.execute("""
            CREATE TABLE beacons (
                ts TEXT, id TEXT, lat REAL, lon REAL, alt REAL,
                vario REAL, circling INTEGER, is_tow INTEGER,
                ac_type TEXT, under_tow INTEGER
            )
        """)
        con.executemany(
            "INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
            [((T0 + timedelta(hours=dt)).isoformat(), "x", la, lo, 1000.0,
              v, c, 0, "glider", 0) for dt, la, lo, v, c in beacons],
        )
    return path


def test_cell_index_floors_through_zero():
    # round() would fold -0.004 and +0.004 into the same cell as 0.0 from both
    # sides, making one cell twice as wide as the rest.
    assert _cell(0.005) == 0
    assert _cell(-0.005) == -1
    assert _cell(0.015) == 1
    assert _cell(-0.015) == -2


def test_a_samples_own_beacons_cannot_reach_its_prior(tmp_path):
    """
    The leakage guard, stated directly: the beacon that produced a label is in
    the same database as the prior.  A prior that can see it is reading the
    answer, and would look excellent offline while adding nothing in the air.
    """
    lat, lon = 39.815, 30.115
    # Every beacon is simultaneous with the sample and all are climbing.
    path = _db(tmp_path, [(0, lat, lon, 3.0, 1) for _ in range(200)])
    build_index(path, SOARING)

    prior = ClimbPrior(path, lag_hours=24)
    assert np.isnan(prior.value(lat, lon, T0)), "same-hour beacons must be invisible"


def test_history_older_than_the_lag_is_visible(tmp_path):
    lat, lon = 39.815, 30.115
    path = _db(tmp_path, [(-30, lat, lon, 3.0, 1) for _ in range(200)])
    build_index(path, SOARING)

    prior = ClimbPrior(path, lag_hours=24)
    value = prior.value(lat, lon, T0)
    assert not np.isnan(value)
    assert value > 0.5, "a cell of pure climbs should read high"


def test_history_inside_the_lag_window_is_excluded(tmp_path):
    """23 hours before is still inside a 24 hour lag, so it must not count."""
    lat, lon = 39.815, 30.115
    path = _db(tmp_path, [(-23, lat, lon, 3.0, 1) for _ in range(200)])
    build_index(path, SOARING)

    assert np.isnan(ClimbPrior(path, lag_hours=24).value(lat, lon, T0))
    # The same data is visible once the lag is short enough to admit it.
    assert not np.isnan(ClimbPrior(path, lag_hours=12).value(lat, lon, T0))


def test_sparse_cells_shrink_toward_the_global_rate(tmp_path):
    """
    One circling beacon must not read as a certainty.  Without shrinkage a cell
    seen once, climbing, scores 1.0 and outranks a cell with a hundred
    observations and a genuinely good rate.
    """
    good = ([(-30, 39.815, 30.115, 3.0, 1)] * 90
            + [(-30, 39.815, 30.115, 0.0, 0)] * 10)      # rate 0.90 over 100
    bulk = ([(-30, 43.815, 30.115, 3.0, 1)] * 10
            + [(-30, 43.815, 30.115, 0.0, 0)] * 890)     # holds the global rate low
    lonely = [(-30, 41.615, 30.115, 3.0, 1)]             # seen exactly once
    path = _db(tmp_path, good + bulk + lonely)
    build_index(path, SOARING)

    prior = ClimbPrior(path, lag_hours=24, smoothing=50.0)
    global_rate = 101 / 1001
    sparse = prior.value(41.615, 30.115, T0)
    dense = prior.value(39.815, 30.115, T0)

    assert sparse < 0.3, "a single observation must not read as a certainty"
    assert abs(sparse - global_rate) < 0.05, "it should sit near the global rate"
    assert dense > sparse, "a well-observed good cell must outrank a lucky single hit"


def test_unseen_cell_in_a_populated_window_gets_the_global_rate(tmp_path):
    path = _db(tmp_path, [(-30, 39.815, 30.115, 3.0, 1) for _ in range(60)]
                         + [(-30, 39.815, 30.115, 0.0, 0) for _ in range(40)])
    build_index(path, SOARING)
    prior = ClimbPrior(path, lag_hours=24)
    far = prior.value(-20.0, 150.0, T0)     # nothing has ever been seen there
    assert far == pytest.approx(0.6, abs=0.01)


def test_no_history_at_all_returns_nodata_not_zero(tmp_path):
    """
    NaN, not 0.0.  XGBoost routes missing values down a learned default branch,
    so "never observed" stays distinct from "observed and never worked" — which
    is the difference between an unknown cell and a known dead one.
    """
    path = _db(tmp_path, [(0, 39.815, 30.115, 3.0, 1)])
    build_index(path, SOARING)
    assert np.isnan(ClimbPrior(path, lag_hours=24).value(39.815, 30.115, T0))


def test_towed_and_non_soaring_traffic_is_excluded(tmp_path):
    path = tmp_path / "ogn.db"
    with sqlite3.connect(path) as con:
        con.execute("""
            CREATE TABLE beacons (
                ts TEXT, id TEXT, lat REAL, lon REAL, alt REAL,
                vario REAL, circling INTEGER, is_tow INTEGER,
                ac_type TEXT, under_tow INTEGER
            )
        """)
        ts = (T0 - timedelta(hours=30)).isoformat()
        con.executemany(
            "INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
            # A tug and a glider on the rope both climb at 3 m/s on power.
            [(ts, "a", 39.815, 30.115, 1000.0, 3.0, 1, 1, "tow_plane", 0)] * 50
            + [(ts, "b", 39.815, 30.115, 1000.0, 3.0, 1, 0, "glider", 1)] * 50,
        )
    stats = build_index(path, SOARING)
    assert stats["beacons"] == 0, "powered climbs are not evidence of lift"


def test_rate_not_count_so_busy_airspace_does_not_dominate(tmp_path):
    """
    Raw counts encode where gliders fly, which is equally true of the negatives.
    A busy cell that rarely produces climbs must score below a quiet cell that
    reliably does.
    """
    busy = ([(-30, 39.815, 30.115, 3.0, 1)] * 20
            + [(-30, 39.815, 30.115, 0.0, 0)] * 980)      # 1000 beacons, 2%
    quiet = ([(-30, 41.615, 30.115, 3.0, 1)] * 120
             + [(-30, 41.615, 30.115, 0.0, 0)] * 80)      # 200 beacons, 60%
    path = _db(tmp_path, busy + quiet)
    build_index(path, SOARING)

    prior = ClimbPrior(path, lag_hours=24)
    assert prior.value(41.615, 30.115, T0) > prior.value(39.815, 30.115, T0)


def test_grid_matches_feature_matrix_orientation(tmp_path):
    """
    Row 0 must be the SOUTH edge, matching build_feature_matrix. A flipped axis
    would attach every prior to the mirrored cell.
    """
    south, north = 39.0, 39.2
    path = _db(tmp_path, [(-30, south + 0.005, 30.005, 3.0, 1) for _ in range(200)]
                         + [(-30, north - 0.005, 30.005, 0.0, 0) for _ in range(200)])
    build_index(path, SOARING)

    prior = ClimbPrior(path, lag_hours=24)
    grid = prior.grid((south, north), (30.0, 30.01), (21, 2), T0)
    assert grid[0, 0] > grid[-1, 0], "row 0 should carry the southern climbs"


def test_grid_and_point_lookup_agree(tmp_path):
    path = _db(tmp_path, [(-30, 39.115, 30.115, 3.0, 1) for _ in range(80)]
                         + [(-30, 39.115, 30.115, 0.0, 0) for _ in range(20)])
    build_index(path, SOARING)
    prior = ClimbPrior(path, lag_hours=24)
    grid = prior.grid((39.11, 39.12), (30.11, 30.12), (3, 3), T0)
    assert grid[1, 1] == pytest.approx(prior.value(39.115, 30.115, T0))


def test_hour_key_sorts_chronologically():
    a = _hour_key(datetime(2026, 8, 9, 23, tzinfo=timezone.utc))
    b = _hour_key(datetime(2026, 8, 10, 0, tzinfo=timezone.utc))
    assert a < b, "lexicographic order must match time order for the cutoff scan"
