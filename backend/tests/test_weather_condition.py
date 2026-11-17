"""
The map shows the sky and the local clock at a clicked point or a pinned glider.

What these tests pin:

- WMO weather codes map to a small set of condition keys, and an unknown or
  missing code is None rather than a guessed "clear";
- the local UTC offset comes from the IANA zone at the *target* moment, so a
  forecast across a DST change reads the right clock;
- the live request asks for timezone=auto with unix times, and the slot lookup
  still reads exactly the UTC hour it always read;
- a categorical code takes the nearer hour instead of being interpolated;
- the display fields never reach the feature matrix;
- /predict and /weather carry them, and the fallback carries none of them.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

import main
from data import meteo_client as mc
from pipeline.feature_engineering import build_feature_matrix, FEATURE_COUNT


# ---------------------------------------------------------------------------
# Code -> condition
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code,want", [
    (0, "clear"), (1, "mostly_clear"), (2, "partly_cloudy"), (3, "cloudy"),
    (45, "fog"), (48, "fog"),
    (51, "drizzle"), (55, "drizzle"), (57, "drizzle"),
    (61, "rain"), (65, "rain"), (67, "rain"), (80, "rain"), (82, "rain"),
    (71, "snow"), (75, "snow"), (77, "snow"), (85, "snow"), (86, "snow"),
    (95, "thunderstorm"), (96, "thunderstorm"), (99, "thunderstorm"),
    (3.0, "cloudy"),          # Open-Meteo sends floats through some paths
])
def test_wmo_codes_map_to_a_condition(code, want):
    assert mc.weather_condition(code) == want


@pytest.mark.parametrize("code", [None, 4, 50, 100, -1, "x"])
def test_an_unknown_or_missing_code_is_no_condition(code):
    assert mc.weather_condition(code) is None


def test_every_condition_is_one_the_frontend_translates():
    """The values are translation keys; keep the set closed and known."""
    assert set(mc._CONDITION_BY_CODE.values()) == {
        "clear", "mostly_clear", "partly_cloudy", "cloudy", "fog",
        "drizzle", "rain", "snow", "thunderstorm"}
    strings = open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend",
                                "src", "i18n", "strings.js"), encoding="utf-8").read()
    for cond in set(mc._CONDITION_BY_CODE.values()):
        # once in tr, once in en
        assert strings.count(f"weather_cond_{cond}:") == 2, cond


# ---------------------------------------------------------------------------
# Local offset
# ---------------------------------------------------------------------------

def test_istanbul_is_three_hours_ahead_all_year():
    for month in (1, 7):
        at = datetime(2026, month, 15, 12, tzinfo=timezone.utc)
        assert mc.local_utc_offset_s("Europe/Istanbul", None, at) == 3 * 3600


def test_the_offset_follows_dst_at_the_target_moment_not_now():
    """Berlin leaves summer time at 01:00 UTC on 25 Oct 2026."""
    before = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    after  = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)
    # The plain offset Open-Meteo reported "now" (summer time) is overridden.
    assert mc.local_utc_offset_s("Europe/Berlin", 7200, before) == 7200
    assert mc.local_utc_offset_s("Europe/Berlin", 7200, after) == 3600


def test_half_hour_zones_survive():
    at = datetime(2026, 11, 17, 6, tzinfo=timezone.utc)
    assert mc.local_utc_offset_s("Asia/Kolkata", None, at) == 19800


def test_an_unknown_zone_falls_back_to_the_reported_offset():
    at = datetime(2026, 11, 17, tzinfo=timezone.utc)
    assert mc.local_utc_offset_s("Not/AZone", -18000, at) == -18000
    assert mc.local_utc_offset_s(None, 10800, at) == 10800
    assert mc.local_utc_offset_s(None, None, at) is None


# ---------------------------------------------------------------------------
# fetch_meteo_features over a timezone=auto, unixtime response
# ---------------------------------------------------------------------------

def _unix_hours(n: int = 48) -> list[int]:
    # Local midnight in Istanbul (21:00 UTC the previous day), as timezone=auto
    # would start the array.
    now = datetime.now(timezone.utc)
    local_mid = (now + timedelta(hours=3)).replace(hour=0, minute=0, second=0,
                                                   microsecond=0) - timedelta(hours=3)
    return [int((local_mid + timedelta(hours=h)).timestamp()) for h in range(n)]


class _AutoTz:
    status_code = 200
    def raise_for_status(self): pass
    def json(self):
        t = _unix_hours()
        # Each value is the UTC hour of its slot, so a value says where it came from.
        utc_hour = [float(datetime.fromtimestamp(x, timezone.utc).hour) for x in t]
        hourly = {k: list(utc_hour) for k in mc.HOURLY_VARS}
        hourly["relativehumidity_2m"] = [50.0] * len(t)
        hourly["time"] = t
        # Code alternates cloudy (3) / rain (61) per hour; is_day likewise.
        hourly["weathercode"] = [3 if i % 2 == 0 else 61 for i in range(len(t))]
        hourly["is_day"] = [1 if i % 2 == 0 else 0 for i in range(len(t))]
        return {"hourly": hourly, "timezone": "Europe/Istanbul",
                "utc_offset_seconds": 10800}


@pytest.fixture
def autotz(monkeypatch):
    calls = []

    class Client:
        async def get(self, url, params=None):
            calls.append(params)
            return _AutoTz()

    monkeypatch.setattr(mc, "_get_client", lambda: Client())
    monkeypatch.setattr(mc, "_live_cache", {})
    monkeypatch.setattr(mc, "_live_hour", "")
    return calls


async def test_the_request_asks_for_the_local_clock_in_the_same_call(autotz):
    out = await mc.fetch_meteo_features(39.8, 30.1, 0)
    assert len(autotz) == 1
    p = autotz[0]
    assert p["timezone"] == "auto" and p["timeformat"] == "unixtime"
    assert "weathercode" in p["hourly"] and "is_day" in p["hourly"]
    assert out["timezone"] == "Europe/Istanbul"
    assert out["utc_offset_s"] == 10800


async def test_unix_times_still_read_the_current_utc_hour(autotz):
    """The sampler trains on forecast_h = 0: the slot must not move."""
    out = await mc.fetch_meteo_features(39.8, 30.1, 0, strict=True)
    assert out["temp_2m"] == datetime.now(timezone.utc).hour


async def test_a_code_takes_the_nearer_hour_not_an_average(autotz):
    a = await mc.fetch_meteo_features(39.8, 30.1, 0)
    b = await mc.fetch_meteo_features(39.8, 30.1, 0.25)
    c = await mc.fetch_meteo_features(39.8, 30.1, 0.75)
    d = await mc.fetch_meteo_features(39.8, 30.1, 1)
    assert a["weather_code"] in (3, 61)
    assert b["weather_code"] == a["weather_code"]
    assert c["weather_code"] == d["weather_code"] != a["weather_code"]
    assert mc.weather_condition(c["weather_code"]) in ("cloudy", "rain")


def test_display_fields_do_not_reach_the_feature_matrix():
    base = dict(mc._FALLBACK)
    extra = dict(base, weather_code=61, is_day=0, timezone="Europe/Istanbul",
                 utc_offset_s=10800)
    elev = np.full((11, 11), 800.0)
    kw = dict(dt=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
              lat_bounds=(39.7, 39.9), lon_bounds=(30.0, 30.2), alt_amsl=1800.0)
    a = build_feature_matrix(base, elev, **kw)
    b = build_feature_matrix(extra, elev, **kw)
    assert a.shape[1] == b.shape[1] == FEATURE_COUNT
    assert np.array_equal(a, b)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_sky_and_clock_from_a_forecast():
    m = dict(mc._FALLBACK, weather_code=2, is_day=1, timezone="Europe/Istanbul",
             utc_offset_s=10800)
    assert main._sky_and_clock(m) == {
        "condition": "partly_cloudy", "weather_code": 2, "is_day": True,
        "timezone": "Europe/Istanbul", "utc_offset_s": 10800}


def test_the_fallback_claims_no_sky_and_no_clock():
    out = main._sky_and_clock(mc._FALLBACK.copy())
    assert out["condition"] is None and out["utc_offset_s"] is None
    assert out["is_day"] is None


def test_weather_endpoint(monkeypatch):
    seen = []

    async def fake(lat, lon, fh, strict=False):
        seen.append(fh)
        return dict(mc._FALLBACK, weather_code=71, is_day=0,
                    timezone="Europe/Istanbul", utc_offset_s=10800)

    monkeypatch.setattr(main, "fetch_meteo_features", fake)
    client = TestClient(main.app)
    r = client.get("/weather", params={"lat": 39.8, "lon": 30.1, "forecast_h": 1.1})
    assert r.status_code == 200
    j = r.json()
    assert seen == [1.0]                      # rounded to the quarter hour
    assert j["condition"] == "snow" and j["is_day"] is False
    assert j["utc_offset_s"] == 10800 and j["meteo_fallback"] is False
    assert "target_at" in j


def test_weather_endpoint_on_fallback(monkeypatch):
    async def fake(lat, lon, fh, strict=False):
        return mc._FALLBACK.copy()

    monkeypatch.setattr(main, "fetch_meteo_features", fake)
    j = TestClient(main.app).get("/weather", params={"lat": 1, "lon": 2}).json()
    assert j["meteo_fallback"] is True
    assert j["condition"] is None and j["utc_offset_s"] is None and j["temp_2m"] is None
