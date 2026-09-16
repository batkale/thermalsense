"""
/training/status — the panel a training node is watched through.

What matters here is that it tells the truth about why the model is or is not
changing, and that one broken section cannot blank the whole page.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import main
import models.thermal_model as tm
from data import terrain_client
from pipeline.feature_engineering import FEATURE_COUNT


def _rows(n, lat, lon, day=200, agl=800.0, positives=0, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.random((n, FEATURE_COUNT))
    X[:, 0], X[:, 1] = lat, lon
    a = 2 * np.pi * day / 365
    X[:, 17], X[:, 18] = np.sin(a), np.cos(a)
    X[:, 21] = agl
    y = np.zeros(n, dtype=int)
    y[:positives] = 1
    return list(X), list(y)


def test_summary_counts_regions_with_turkiye_first():
    m = tm.ThermalModel()
    X1, y1 = _rows(10, 39.81, 30.11, positives=2)          # İnönü
    X2, y2 = _rows(20, 46.8, 11.2, positives=5, day=201)   # Alps
    m._buffer_X, m._buffer_y = X1 + X2, y1 + y2

    s = m.training_summary()
    assert s["rows"] == 30 and s["positives"] == 7 and s["days"] == 2
    assert list(s["regions"])[0] == "Türkiye"
    assert s["regions"]["Türkiye"] == {"rows": 10, "positives": 2}
    assert s["regions"]["Alps"] == {"rows": 20, "positives": 5}
    assert s["benchmark_groups_needed"] == tm._MIN_BENCH_GROUPS


def test_summary_region_counts_exclude_rows_the_fit_drops():
    """Grounded rows never reach a fit, so they must not inflate the panel."""
    m = tm.ThermalModel()
    Xa, ya = _rows(10, 39.8, 30.1, agl=800.0)
    Xg, yg = _rows(5, 39.8, 30.1, agl=0.0)
    m._buffer_X, m._buffer_y = Xa + Xg, ya + yg
    s = m.training_summary()
    assert s["rows"] == 15 and s["usable_rows"] == 10
    assert s["regions"]["Türkiye"]["rows"] == 10


def test_empty_buffer_summary():
    s = tm.ThermalModel().training_summary()
    assert s["rows"] == 0 and s["benchmark_groups"] == 0
    assert s["regions"]["Türkiye"] == {"rows": 0, "positives": 0}


def test_summary_tolerates_lists_one_row_apart():
    """retrain() appends X then y on the event loop while this reads in a thread."""
    m = tm.ThermalModel()
    X, y = _rows(10, 46.8, 11.2)
    m._buffer_X, m._buffer_y = X, y[:9]
    assert m.training_summary()["rows"] == 9


def test_fit_outcome_is_recorded_when_skipped():
    m = tm.ThermalModel()
    m._buffer_X, m._buffer_y = _rows(5, 46.8, 11.2)
    m.fit_and_gate()
    assert m.last_fit["outcome"] == "too_few_samples"
    assert m.last_fit["need"] == tm._MIN_SAMPLES


def test_terrain_usage_counts_requests_and_rate_limits(monkeypatch):
    monkeypatch.setattr(terrain_client, "_usage", {"day": "", "requests": 0, "rate_limited": 0})
    terrain_client._count(200)
    terrain_client._count(429)
    u = terrain_client.api_usage()
    assert u["requests"] == 2 and u["rate_limited"] == 1 and u["quota"] == 1000


async def test_status_survives_a_broken_section(monkeypatch):
    def boom():
        raise RuntimeError("disk gone")

    async def no_gliders():
        return []

    monkeypatch.setattr(main, "_beacon_stats", boom)
    monkeypatch.setattr(main, "fetch_ogn_gliders", no_gliders)
    out = await main.training_status()
    assert "error" in out["feed"]["beacons"]
    # Everything else still reports.
    assert "rows" in out["buffer"]
    assert "requests" in out["terrain_api"]
    assert isinstance(out["disk_free_gb"], float)
