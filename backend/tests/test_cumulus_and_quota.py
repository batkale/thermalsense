"""
Two things added on 19 Nov 2026 alongside the satellite cloud layer.

The cumulus outlook: whether thermals mark themselves with cumulus at the
forecast moment, and how high the cloud base is, from values the live request
already carries.  Display only — it must not reach the feature matrix.

The Open-Meteo counter: the quota is billed in calls, and a request for more
than 10 variables is more than one call.  The live request has 13, so the
counter that added 1 per request read ~30% low against the quota every budget
in the app is checked against.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import main
import models.thermal_model as tm
from data import meteo_client as mc
from tests.test_sampling_floor import _patch_predict_deps


# ---------------------------------------------------------------------------
# Billed calls
# ---------------------------------------------------------------------------

def test_call_weight_reproduces_the_pricing_page_examples():
    assert mc._call_weight(15, 14) == pytest.approx(1.5)
    assert mc._call_weight(15, 28) == pytest.approx(3.0)
    assert mc._call_weight(10, 1) == 1.0
    assert mc._call_weight(4, 2) == 1.0                  # never below one call


def test_the_live_request_is_billed_for_every_variable_it_asks_for():
    n = len(mc.HOURLY_VARS) + len(mc.DISPLAY_VARS)
    assert mc._LIVE_CALL_WEIGHT == pytest.approx(n / 10)
    assert mc._HIST_CALL_WEIGHT == pytest.approx(max(1.0, len(mc.HOURLY_VARS) / 10))


class _Resp:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        t = ["2026-09-17T00:00"] + [f"2026-09-17T{h:02d}:00" for h in range(1, 24)]
        hourly = {k: [10.0] * len(t) for k in mc.HOURLY_VARS}
        hourly["relativehumidity_2m"] = [50.0] * len(t)
        hourly["time"] = t
        return {"hourly": hourly}


@pytest.fixture
def offline_meteo(monkeypatch, tmp_path):
    class Client:
        async def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(mc, "_get_client", lambda: Client())
    monkeypatch.setattr(mc, "_live_cache", {})
    monkeypatch.setattr(mc, "_live_hour", "")
    monkeypatch.setattr(mc, "_live_usage", {"day": "", "requests": 0, "cache_hits": 0,
                                            "historical_requests": 0,
                                            "historical_cache_hits": 0})
    monkeypatch.setattr(mc, "_hist_cache", {})
    monkeypatch.setattr(mc, "_hist_loaded", True)
    monkeypatch.setattr(mc, "_hist_pending", [])
    monkeypatch.setattr(mc, "_hist_path", lambda: tmp_path / "meteo_historical.jsonl")


async def test_total_counts_billed_calls_not_requests(offline_meteo):
    from datetime import datetime, timezone
    await mc.fetch_meteo_features(39.8, 30.1, 0)
    await mc.fetch_meteo_features(39.8, 30.1, 0)        # cache hit: not billed
    await mc.fetch_meteo_historical(39.8, 30.1, datetime(2026, 9, 17, 12, tzinfo=timezone.utc))

    u = mc.live_usage()
    assert u["requests"] == 1 and u["historical_requests"] == 1 and u["cache_hits"] == 1
    assert u["total"] == pytest.approx(mc._LIVE_CALL_WEIGHT + mc._HIST_CALL_WEIGHT, abs=0.05)
    assert u["total"] > u["requests"] + u["historical_requests"]


def test_a_new_day_starts_from_zero(monkeypatch):
    monkeypatch.setattr(mc, "_live_usage", {"day": "2000-01-01", "requests": 9, "cache_hits": 9,
                                            "historical_requests": 9,
                                            "historical_cache_hits": 9,
                                            "calls": 99.0, "historical_calls": 99.0})
    u = mc.live_usage()
    assert u["total"] == 0 and u["requests"] == 0


# ---------------------------------------------------------------------------
# Cumulus outlook
# ---------------------------------------------------------------------------

def _day(t2m, rh, pbl, ghi=600.0):
    return {"temp_2m": t2m, "humidity": rh, "pbl_height": pbl, "solar_ghi": ghi}


def test_dew_point_matches_the_magnus_formula():
    assert mc.dew_point_c(20.0, 1.0) == pytest.approx(20.0)
    assert mc.dew_point_c(20.0, 0.5) == pytest.approx(9.26, abs=0.05)
    assert mc.dew_point_c(20.0, 0.0) is None


def test_cumulus_when_the_boundary_layer_clears_the_condensation_level():
    # 25 degC at 40%: dew point ~10.5, spread ~14.5, base ~1,800 m.
    out = mc.cumulus_outlook(_day(25.0, 0.40, 2200.0), tm._MIN_SOLAR_GHI)
    assert out["kind"] == "cumulus"
    assert out["base_agl"] == 1800
    assert out["margin_m"] > 0
    assert out["base_agl"] % 50 == 0


def test_blue_thermals_when_the_layer_stops_below_it():
    out = mc.cumulus_outlook(_day(25.0, 0.40, 1200.0), tm._MIN_SOLAR_GHI)
    assert out["kind"] == "blue"
    assert out["margin_m"] < 0


def test_no_thermals_below_the_night_guard_floor():
    out = mc.cumulus_outlook(_day(25.0, 0.40, 2200.0, ghi=tm._MIN_SOLAR_GHI - 1),
                             tm._MIN_SOLAR_GHI)
    assert out["kind"] == "none"


def test_saturated_air_puts_the_base_on_the_ground():
    out = mc.cumulus_outlook(_day(12.0, 1.0, 300.0), tm._MIN_SOLAR_GHI)
    assert out["base_agl"] == 0 and out["kind"] == "cumulus"


def test_the_synthetic_fallback_draws_no_cloud_base():
    """60% humidity and a 1,500 m layer are invented; so would the base be."""
    assert mc.cumulus_outlook(mc._FALLBACK.copy(), tm._MIN_SOLAR_GHI) is None


async def test_predict_carries_the_outlook_beside_the_weather(monkeypatch):
    _patch_predict_deps(monkeypatch, solar_ghi=600.0)
    out = await main.predict(lat=47.1, lon=13.1)
    cu = out["weather"]["cumulus"]
    # _meteo() in the night-guard tests: 12 degC at 80% condenses at ~420 m,
    # just above its 400 m layer — blue, by a margin that rounds to nothing.
    assert cu["kind"] == "blue"
    assert cu["base_agl"] == 400
    assert cu["dew_point"] == pytest.approx(8.7, abs=0.1)


async def test_the_outlook_is_not_a_feature(offline_meteo):
    """build_feature_matrix reads the meteo dict by name; nothing new goes in."""
    meteo = await mc.fetch_meteo_features(39.8, 30.1, 0)
    assert not {"cumulus", "dew_point", "base_agl"} & set(meteo)
