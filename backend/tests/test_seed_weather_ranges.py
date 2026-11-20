"""
The seed's archived weather, one request per 0.5 deg cell per fortnight.

Open-Meteo bills a request up to two weeks long the same as a single day.  The
seed used to ask for a whole day per (cell, hour) bucket and keep one hour, so
a cell flown for nine hours cost nine calls for one day's data.  Rebuilding
28 Sep-6 Nov 2026 from the archive with ranges took 1,741 calls where
hour-by-hour would have been ~17,000.

These pin that a range caches exactly what the single-hour fetch would have,
that the seed then makes one request per cell, adds the same rows either way,
and that the budget still holds -- without stopping cached buckets.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

import models.thermal_model as tm
from data import meteo_client as mc
from data import terrain_client as tc


# ---------------------------------------------------------------------------
# A fake archive: every hour of every day asked for, values that depend on the
# place and the hour, so an hour read from the wrong slot shows.
# ---------------------------------------------------------------------------

def _value(var, lat, lon, stamp):
    hour = int(stamp[11:13])
    day = int(stamp[8:10])
    if var == "shortwave_radiation":
        return 0.0 if hour == 3 else 650.0 + hour
    if var == "soil_temperature_0_to_7cm" and hour == 13:
        return None                       # the archive does return gaps
    base = {"temperature_2m": 15.0, "relativehumidity_2m": 30.0,
            "boundary_layer_height": 2500.0, "temperature_850hPa": 8.0,
            "temperature_500hPa": -15.0, "cape": 400.0}.get(var, 5.0)
    return base + hour / 100 + day / 1000 + lat / 1e4 + lon / 1e5


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _Archive:
    def __init__(self, days_returned=None):
        self.calls = []
        self.days_returned = days_returned

    async def get(self, url, params=None):
        self.calls.append(dict(params))
        d0 = date.fromisoformat(params["start_date"])
        d1 = date.fromisoformat(params["end_date"])
        n = (d1 - d0).days + 1
        if self.days_returned is not None:
            n = min(n, self.days_returned)
        stamps = [f"{d0 + timedelta(days=k)}T{h:02d}:00" for k in range(n) for h in range(24)]
        hourly = {v: [_value(v, params["latitude"], params["longitude"], s) for s in stamps]
                  for v in mc.HOURLY_VARS}
        hourly["time"] = stamps
        return _Resp({"hourly": hourly})


@pytest.fixture
def archive(monkeypatch):
    a = _Archive()
    monkeypatch.setattr(mc, "_get_client", lambda: a)
    monkeypatch.setattr(mc, "_live_usage", {"day": "", "requests": 0, "cache_hits": 0,
                                            "historical_requests": 0,
                                            "historical_cache_hits": 0})
    return a


def _h(day, hour):
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def _fresh_cache(monkeypatch, path):
    monkeypatch.setattr(mc, "_hist_cache", {})
    monkeypatch.setattr(mc, "_hist_pending", [])
    monkeypatch.setattr(mc, "_hist_loaded", False)
    monkeypatch.setattr(mc, "_hist_path", lambda: path)


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def test_a_cells_hours_become_one_request():
    plan = mc.plan_historical_ranges([
        (47.0, 13.0, _h(1, 10)), (47.0, 13.0, _h(1, 11)), (47.1, 13.2, _h(1, 10)),
        (47.0, 13.0, _h(3, 9)), (46.5, 11.0, _h(1, 12)),
    ])
    assert [(la, lo, len(hours)) for la, lo, hours in plan] == [
        (47.0, 13.0, 3),          # 47.1/13.2 is the same cell and the same hour
        (46.5, 11.0, 1),
    ]
    assert plan[0][2] == sorted(plan[0][2])


def test_a_span_never_bills_as_more_than_one_call():
    plan = mc.plan_historical_ranges(
        [(47.0, 13.0, _h(d, 12)) for d in (1, 14, 15, 28, 29)])
    spans = [(hours[0].date(), hours[-1].date()) for _, _, hours in plan]
    assert sorted(spans) == [(date(2026, 8, 1), date(2026, 8, 14)),
                             (date(2026, 8, 15), date(2026, 8, 28)),
                             (date(2026, 8, 29), date(2026, 8, 29))]
    assert all((b - a).days < mc._HIST_RANGE_DAYS for a, b in spans)


def test_cached_hours_are_not_planned():
    mc._hist_cache_put(mc._hist_key(47.0, 13.0, _h(1, 10)), {"solar_ghi": 600.0})
    plan = mc.plan_historical_ranges([(47.0, 13.0, _h(1, 10)), (47.0, 13.0, _h(1, 11))])
    assert [hours for _, _, hours in plan] == [[_h(1, 11)]]
    assert mc.plan_historical_ranges([(47.0, 13.0, _h(1, 10))]) == []


# ---------------------------------------------------------------------------
# The range fetch
# ---------------------------------------------------------------------------

async def test_a_range_caches_what_single_hours_would(archive, monkeypatch, tmp_path):
    """Including the hour with a gap in it and the dark one: the cache is
    permanent, so the two paths must not disagree about a single value."""
    hours = [_h(1, 3), _h(1, 12), _h(1, 13), _h(2, 9), _h(4, 16)]
    for dt in hours:
        assert await mc.fetch_meteo_historical(47.0, 13.0, dt, strict=True) is not None
    singly = dict(mc._hist_cache)
    assert len(archive.calls) == len(hours)

    _fresh_cache(monkeypatch, tmp_path / "second.jsonl")
    archive.calls.clear()
    assert await mc.fetch_meteo_historical_range(47.0, 13.0, hours) == len(hours)

    assert mc._hist_cache == singly
    assert len(archive.calls) == 1
    assert (archive.calls[0]["start_date"], archive.calls[0]["end_date"]) == (
        "2026-08-01", "2026-08-04")


async def test_a_range_keeps_only_the_hours_asked_for(archive):
    """The response carries 72 hours here; the cache is held in memory whole."""
    assert await mc.fetch_meteo_historical_range(47.0, 13.0, [_h(1, 12), _h(3, 12)]) == 2
    assert len(mc._hist_cache) == 2


async def test_a_range_asks_at_the_cells_centre(archive):
    await mc.fetch_meteo_historical_range(47.12, 13.21, [_h(1, 12)])
    assert (archive.calls[0]["latitude"], archive.calls[0]["longitude"]) == (47.0, 13.0)
    assert mc.historical_cached(47.0, 13.0, _h(1, 12))


async def test_hours_missing_from_the_answer_stay_uncached(archive, monkeypatch):
    short = _Archive(days_returned=1)
    monkeypatch.setattr(mc, "_get_client", lambda: short)
    assert await mc.fetch_meteo_historical_range(47.0, 13.0, [_h(1, 12), _h(2, 12)]) == 1
    assert not mc.historical_cached(47.0, 13.0, _h(2, 12))


async def test_a_failed_range_caches_nothing_and_raises_nothing(archive, monkeypatch):
    class Down:
        async def get(self, url, params=None):
            raise OSError("getaddrinfo failed")

    monkeypatch.setattr(mc, "_get_client", lambda: Down())
    before = mc.live_usage()["historical_requests"]
    assert await mc.fetch_meteo_historical_range(47.0, 13.0, [_h(1, 12)]) == 0
    assert mc._hist_cache == {}
    assert mc.live_usage()["historical_requests"] == before


async def test_a_span_over_two_weeks_is_refused(archive):
    with pytest.raises(ValueError):
        await mc.fetch_meteo_historical_range(47.0, 13.0, [_h(1, 12), _h(15, 12)])
    assert archive.calls == []


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------

def _beacon_db(path):
    """Two cells, each flown in two hours: four buckets."""
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    t0 = (datetime.now(timezone.utc) - timedelta(hours=6)).replace(minute=10, second=0,
                                                                   microsecond=0)
    i = 0
    for lat, lon in ((46.0, 11.0), (47.0, 13.0)):
        for start in (t0, t0 + timedelta(hours=2)):
            for k in range(6):
                con.execute("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                            ((start + timedelta(seconds=k * 5)).isoformat(), f"AC{i}",
                             lat + k * 1e-4, lon + k * 1e-4, 2000.0,
                             2.5 if k % 2 == 0 else -1.2, 1 if k % 2 == 0 else 0,
                             0, 1, 0))
                i += 1
    con.commit()
    con.close()


@pytest.fixture
def seed_env(tmp_path, monkeypatch, archive):
    db = tmp_path / "b.db"
    _beacon_db(db)
    monkeypatch.setattr("config.DB_PATH", db, raising=False)
    monkeypatch.setattr("config.ARCHIVE_DIR", tmp_path / "arch", raising=False)
    monkeypatch.setattr(tm, "_SEED_PACE_S", 0.0)

    async def terrain(lat, lon, radius=None, strict=False):
        return np.full(tc.grid_shape(), 800.0)

    monkeypatch.setattr(tc, "fetch_elevation_grid", terrain)
    return archive


async def _seed():
    m = tm.ThermalModel()
    out = await m.seed_from_history(days_back=1, limit=500, reset=True)
    return m, out


async def test_the_seed_asks_once_per_cell_and_keeps_the_same_rows(
        seed_env, monkeypatch, tmp_path):
    m_range, out = await _seed()
    assert len(seed_env.calls) == 2, "one request per cell, not per bucket"
    assert out["meteo_range_requests"] == 2 and out["meteo_hours_prefetched"] == 4
    assert out["added"] > 0

    # The same seed hour by hour, from an empty cache.
    _fresh_cache(monkeypatch, tmp_path / "hourly.jsonl")
    seed_env.calls.clear()
    monkeypatch.setattr(mc, "plan_historical_ranges", lambda points: [])
    m_hourly, out_hourly = await _seed()
    assert len(seed_env.calls) == 4

    assert out_hourly["added"] == out["added"]
    assert m_range._buffer_y == m_hourly._buffer_y
    assert np.array_equal(np.array(m_range._buffer_X), np.array(m_hourly._buffer_X),
                          equal_nan=True)


async def test_the_budget_is_checked_before_a_range_is_bought(seed_env, monkeypatch):
    monkeypatch.setattr(tm, "SEED_METEO_BUDGET", 10)
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 50, "cache_hits": 0,
                                 "historical_requests": 0, "historical_cache_hits": 0,
                                 "total": 50, "quota": 10000})
    _, out = await _seed()
    assert seed_env.calls == []
    assert out["stopped_at_meteo_budget"] is True
    assert out["meteo_deferred_buckets"] == 4


async def test_a_spent_budget_still_lets_cached_buckets_through(seed_env, monkeypatch):
    """The prefetch can spend the budget and leave most buckets cached; the loop
    used to stop at the first bucket once the budget was reached."""
    import config
    with sqlite3.connect(config.DB_PATH) as con:
        first = con.execute("SELECT MIN(ts) FROM beacons WHERE lat >= 47").fetchone()[0]
    hour = datetime.fromisoformat(first).replace(minute=0, second=0, microsecond=0)
    mc._hist_cache_put(mc._hist_key(47.0, 13.0, hour), {
        "temp_2m": 15.0, "humidity": 0.3, "wind_speed": 5.0, "wind_dir": 0.0,
        "cape": 400.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 8.0,
        "temp_500": -15.0, "lapse_rate": 6.6, "pbl_height": 2500.0,
        "soil_temp": 5.0, "cape_base": 2000})
    monkeypatch.setattr(tm, "SEED_METEO_BUDGET", 10)
    monkeypatch.setattr(mc, "live_usage",
                        lambda: {"day": "d", "requests": 50, "cache_hits": 0,
                                 "historical_requests": 0, "historical_cache_hits": 0,
                                 "total": 50, "quota": 10000})
    _, out = await _seed()
    assert seed_env.calls == []
    assert out["meteo_deferred_buckets"] == 3
    assert out["added"] > 0, "the cached bucket was not processed"
