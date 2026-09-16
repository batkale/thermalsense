"""
Persistent cache for archived-hour meteo.

The cold beacon archive makes the training buffer rebuildable in principle, but
Open-Meteo allows 10,000 calls a day and a year of feed is on the order of a
million (0.5deg, hour) buckets — so an uncached rebuild is months of fetching,
which is the same as not being rebuildable.  The past does not change and a
response is a dozen floats, so it is cached forever.

It also pins reproducibility: Open-Meteo revises its archive as reanalysis
lands, so without this the same beacons would rebuild into a different buffer.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, timezone

import pytest

from data import meteo_client as mc


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "_hist_path", lambda: tmp_path / "meteo.jsonl")
    monkeypatch.setattr(mc, "_hist_cache", {})
    monkeypatch.setattr(mc, "_hist_pending", [])
    monkeypatch.setattr(mc, "_hist_loaded", False)
    yield


def _dt(h=12):
    return datetime(2026, 8, 1, h, tzinfo=timezone.utc)


def test_key_buckets_by_half_degree_and_hour():
    """The same bucket /seed groups on, so every beacon in it shares one entry."""
    a = mc._hist_key(47.02, 13.03, _dt())
    b = mc._hist_key(47.19, 13.21, _dt())
    assert a == b
    assert mc._hist_key(47.02, 13.03, _dt(13)) != a
    assert mc._hist_key(47.6, 13.03, _dt()) != a


def test_put_then_get_round_trips():
    key = mc._hist_key(47.0, 13.0, _dt())
    mc._hist_cache_put(key, {"cape": 42.0})
    assert mc._hist_cache_get(key) == {"cape": 42.0}


def test_cache_survives_a_restart():
    key = mc._hist_key(47.0, 13.0, _dt())
    mc._hist_cache_put(key, {"cape": 42.0})
    mc.flush_historical_cache(force=True)

    # Simulate a fresh process.
    mc._hist_cache.clear()
    mc._hist_loaded = False
    assert mc._hist_cache_get(key) == {"cape": 42.0}


def test_a_torn_last_line_does_not_lose_the_rest(tmp_path):
    """Appended JSON Lines: a crash mid-write can only damage the final record,
    and that one is skipped rather than taking the file with it."""
    key = mc._hist_key(47.0, 13.0, _dt())
    mc._hist_cache_put(key, {"cape": 42.0})
    mc.flush_historical_cache(force=True)
    with mc._hist_path().open("a", encoding="utf-8") as fh:
        fh.write('{"k": "broken", "v": {"cap')

    mc._hist_cache.clear()
    mc._hist_loaded = False
    assert mc._hist_cache_get(key) == {"cape": 42.0}


async def test_fetch_uses_the_cache_instead_of_the_network(monkeypatch):
    """The saving that matters: a second seed over the same hours costs nothing."""
    key = mc._hist_key(47.0, 13.0, _dt())
    mc._hist_cache_put(key, {"cape": 7.0, "solar_ghi": 500.0})

    def boom(*a, **k):
        raise AssertionError("network hit despite a cache entry")

    monkeypatch.setattr(mc, "_get_client", boom)
    got = await mc.fetch_meteo_historical(47.0, 13.0, _dt(), strict=True)
    assert got == {"cape": 7.0, "solar_ghi": 500.0}


def test_flush_is_batched_until_forced():
    """Writing every entry inline would turn a seed into one fsync per bucket."""
    for i in range(3):
        mc._hist_cache_put(mc._hist_key(47.0 + i, 13.0, _dt()), {"cape": float(i)})
    assert not mc._hist_path().exists()
    mc.flush_historical_cache(force=True)
    lines = mc._hist_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert all(json.loads(l)["k"] for l in lines)
