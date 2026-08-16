import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import data.ogn_client as ogn_mod
from data.ogn_client import _parse_beacon

# Real OGN APRS beacon (sanitised callsign)
# lat=51°15.26'N → 51.2543°  lon=002°03.13'W → -2.0522°  alt=2516ft→767m
# vario=+156fpm→+0.79m/s  rot=+2.5→7.5°/s (not circling)
# id06 3E2D1A: type nibble (0x06>>2)=0x1 glider, so the parser admits it.  The
# id field must carry the full 8 hex digits — a beacon we cannot classify is
# rejected outright, so a truncated one would parse as None everywhere below.
SAMPLE = (
    "FLR3E2D1A>APRS,qAS,EGNH:/120800h5115.26N/00203.13W'"
    "182/067/A=002516 !W51! id063E2D1A +156fpm +2.5rot 7.5dB 1e gps3x5"
)

@pytest.fixture(autouse=True)
def clear_store():
    with ogn_mod._lock:
        ogn_mod._live_gliders.clear()
    yield
    with ogn_mod._lock:
        ogn_mod._live_gliders.clear()

# ---------------------------------------------------------------------------
# _parse_beacon — unit tests on the parser itself
# ---------------------------------------------------------------------------

def test_callsign_extracted():
    assert _parse_beacon(SAMPLE)["id"] == "FLR3E2D1A"

def test_latitude_parsed():
    b = _parse_beacon(SAMPLE)
    assert abs(b["lat"] - 51.2543) < 0.001

def test_longitude_parsed_west():
    b = _parse_beacon(SAMPLE)
    assert abs(b["lon"] - (-2.0522)) < 0.001

def test_altitude_converted_to_metres():
    b = _parse_beacon(SAMPLE)
    assert b["alt"] == round(2516 * 0.3048)   # 767 m

def test_vario_converted_to_ms():
    b = _parse_beacon(SAMPLE)
    assert abs(b["vario"] - round(156 * 0.00508, 2)) < 0.01

def test_heading_parsed():
    assert _parse_beacon(SAMPLE)["heading"] == 182

def test_speed_parsed():
    # 067 knots → round(67 * 1.852) = 124 km/h
    assert _parse_beacon(SAMPLE)["speed_kmh"] == round(67 * 1.852)

def test_heading_zero_becomes_none():
    # course 000 in APRS means unknown/stationary
    line = SAMPLE.replace("'182/067", "'000/000")
    assert _parse_beacon(line)["heading"] is None

def test_not_circling_below_threshold():
    # +2.5rot → 7.5 °/s — just below the 8 °/s threshold
    assert _parse_beacon(SAMPLE)["circling"] is False

def test_circling_above_threshold():
    # +3.5rot → 10.5 °/s — above threshold
    line = SAMPLE.replace("+2.5rot", "+3.5rot")
    assert _parse_beacon(line)["circling"] is True

def test_negative_turn_rate_circling():
    line = SAMPLE.replace("+2.5rot", "-3.5rot")
    assert _parse_beacon(line)["circling"] is True

def test_comment_lines_return_none():
    assert _parse_beacon("# aprsc 2.1.12 keepalive") is None

def test_status_beacon_return_none():
    assert _parse_beacon("EGNH>APRS,TCPIP*:>status message") is None

def test_empty_line_return_none():
    assert _parse_beacon("") is None

def test_malformed_line_return_none():
    assert _parse_beacon("this is not aprs") is None

# ---------------------------------------------------------------------------
# fetch_ogn_gliders — async snapshot of the in-memory store
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_returns_stored_gliders():
    import time
    from data.ogn_client import fetch_ogn_gliders
    with ogn_mod._lock:
        ogn_mod._live_gliders["G1"] = {"id": "G1", "lat": 51.5, "lon":  0.1, "alt": 500, "vario": 2.0, "circling": True,  "seen_at": time.monotonic()}
        ogn_mod._live_gliders["G2"] = {"id": "G2", "lat": 52.0, "lon":  1.0, "alt": 300, "vario": 0.5, "circling": False, "seen_at": time.monotonic()}
    result = await fetch_ogn_gliders()
    assert len(result) == 2
    assert {g["id"] for g in result} == {"G1", "G2"}

@pytest.mark.asyncio
async def test_fetch_empty_store():
    from data.ogn_client import fetch_ogn_gliders
    assert await fetch_ogn_gliders() == []

# ---------------------------------------------------------------------------
# Beacon replay buffers — cursor semantics
#
# These exist because the buffer used to be a destructive queue, which made it
# correct for exactly one consumer.  Every test below is about what happens with
# two, which is the normal case for a deployed URL and was never the case on a
# dev machine with one browser tab open.
# ---------------------------------------------------------------------------

@pytest.fixture
def clear_buffers():
    def _reset():
        with ogn_mod._bbuf_lock:
            ogn_mod._beacon_buffers.clear()
            ogn_mod._bbuf_seq = 0
            ogn_mod._last_drain = 0.0
    _reset()
    yield
    _reset()


def _push(gid: str, lat: float = 51.0) -> None:
    ogn_mod._buffer_position({"id": gid, "lat": lat, "lon": 0.1, "alt": 800})


def test_drain_returns_positions_since_cursor(clear_buffers):
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    start = beacon_cursor()
    _push("G1"); _push("G1")
    out, cursor = drain_beacon_buffers(start)
    assert len(out["G1"]) == 2
    assert cursor > start


def test_drain_is_idempotent_at_the_same_cursor(clear_buffers):
    """Draining twice from one cursor yields the same positions both times."""
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    start = beacon_cursor()
    _push("G1")
    first,  _ = drain_beacon_buffers(start)
    second, _ = drain_beacon_buffers(start)
    assert first == second


def test_second_drain_from_new_cursor_is_empty(clear_buffers):
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    _push("G1")
    _, cursor = drain_beacon_buffers(beacon_cursor() - 1)
    out, _ = drain_beacon_buffers(cursor)
    assert out == {}


def test_two_subscribers_both_receive_every_position(clear_buffers):
    """The regression this replaced: one viewer's drain used to eat the other's."""
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    a = b = beacon_cursor()
    _push("G1"); _push("G1"); _push("G1")

    out_a, a = drain_beacon_buffers(a)
    out_b, b = drain_beacon_buffers(b)
    assert len(out_a["G1"]) == 3
    assert len(out_b["G1"]) == 3

    # And they stay in step across the next frame.
    _push("G1")
    out_a, a = drain_beacon_buffers(a)
    out_b, b = drain_beacon_buffers(b)
    assert len(out_a["G1"]) == 1 and len(out_b["G1"]) == 1


def test_late_subscriber_starts_from_head(clear_buffers):
    """A viewer connecting mid-flight gets no backlog — it backfills from SQLite."""
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    _push("G1"); _push("G1")
    late = beacon_cursor()
    out, _ = drain_beacon_buffers(late)
    assert out == {}


def test_slow_subscriber_only_misses_trimmed_overflow(clear_buffers):
    """Falling behind the buffer cap loses the overflow, not the whole tail."""
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    start = beacon_cursor()
    ogn_mod._last_drain = ogn_mod.time.monotonic()   # mark a consumer as active
    for _ in range(ogn_mod._BBUF_MAX + 50):
        _push("G1")
    out, _ = drain_beacon_buffers(start)
    assert len(out["G1"]) == ogn_mod._BBUF_MAX


def test_positions_carry_seq_for_the_wire_layer_to_strip(clear_buffers):
    from data.ogn_client import beacon_cursor, drain_beacon_buffers
    start = beacon_cursor()
    _push("G1")
    out, _ = drain_beacon_buffers(start)
    assert set(out["G1"][0]) == {"lat", "lon", "alt", "seq"}
