import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timedelta, timezone

from data.meteo_client import _hour_index, _FALLBACK


def _times(start: datetime, hours: int = 48) -> list[str]:
    """Open-Meteo style hourly UTC stamps starting at midnight of start's day."""
    base = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return [(base + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(hours)]


def test_hour_index_resolves_to_now_not_midnight():
    """
    The array starts at 00:00, so indexing by forecast_h alone returns midnight —
    zero solar radiation in the middle of the day.
    """
    now = datetime.now(timezone.utc)
    idx = _hour_index(_times(now), 0)
    assert idx == now.hour


@pytest.mark.parametrize("offset", [0, 1, 6, 12])
def test_hour_index_applies_forecast_offset(offset):
    now = datetime.now(timezone.utc)
    idx = _hour_index(_times(now), offset)
    assert idx == now.hour + offset


def test_hour_index_clamps_past_end_of_array():
    """A large offset must clamp to the last slot rather than raise IndexError."""
    now = datetime.now(timezone.utc)
    times = _times(now, hours=24)
    assert _hour_index(times, 500) == len(times) - 1


def test_hour_index_falls_back_when_stamp_absent():
    """Shifted/ragged arrays fall back to arithmetic from the first timestamp."""
    now = datetime.now(timezone.utc)
    times = _times(now)[3:]          # array no longer starts at midnight
    idx = _hour_index(times, 0)
    assert 0 <= idx < len(times)
    assert times[idx].endswith(f"T{now.hour:02d}:00")


def test_humidity_fallback_is_percent_scale():
    """
    The API returns 0-100 and the caller divides by 100, so the fallback used in
    that expression must also be 0-100 — otherwise a fallback yields 0.006.
    """
    assert _FALLBACK["humidity_pct"] / 100 == pytest.approx(_FALLBACK["humidity"])
    assert 0.0 <= _FALLBACK["humidity"] <= 1.0
    assert 1.0 < _FALLBACK["humidity_pct"] <= 100.0
