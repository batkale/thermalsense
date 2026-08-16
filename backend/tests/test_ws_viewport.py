import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from main import (_parse_bounds, _in_bounds, _round_probs, _positions_to_wire,
                  _attach_cached_agl, _to_wire)
from data import terrain_client

# ---------------------------------------------------------------------------
# _parse_bounds — validation of the client's viewport message
#
# A bad message must fall back to "send everything" rather than "send nothing":
# a viewer with a broken or outdated client should see too many gliders, never
# an empty map with no indication why.
# ---------------------------------------------------------------------------

def test_valid_bounds_parsed():
    assert _parse_bounds({
        "lat_min": 39.0, "lat_max": 40.5, "lon_min": 29.5, "lon_max": 31.5
    }) == (39.0, 40.5, 29.5, 31.5)


def test_string_numbers_accepted():
    # JSON from a client can carry numbers as strings; floats are what matter.
    assert _parse_bounds({
        "lat_min": "39", "lat_max": "40.5", "lon_min": "29.5", "lon_max": "31.5"
    }) == (39.0, 40.5, 29.5, 31.5)


@pytest.mark.parametrize("raw", [
    None,
    "not a dict",
    {},
    {"lat_min": 39.0, "lat_max": 40.5},                                  # missing lon
    {"lat_min": 39.0, "lat_max": 40.5, "lon_min": 29.5, "lon_max": "x"}, # unparseable
    {"lat_min": 40.5, "lat_max": 39.0, "lon_min": 29.5, "lon_max": 31.5},# inverted lat
    {"lat_min": 39.0, "lat_max": 40.5, "lon_min": 31.5, "lon_max": 29.5},# inverted lon
])
def test_malformed_bounds_mean_no_filter(raw):
    assert _parse_bounds(raw) is None


def test_full_world_span_means_no_filter():
    """A 360-degree span selects everything, so filtering would only cost CPU."""
    assert _parse_bounds({
        "lat_min": -90.0, "lat_max": 90.0, "lon_min": -180.0, "lon_max": 180.0
    }) is None

# ---------------------------------------------------------------------------
# _in_bounds
# ---------------------------------------------------------------------------

BOX = (39.0, 40.5, 29.5, 31.5)   # İnönü / Eskişehir


def test_glider_inside_box():
    assert _in_bounds({"lat": 39.8, "lon": 30.1}, BOX)


def test_glider_outside_box_by_latitude():
    assert not _in_bounds({"lat": 51.5, "lon": 30.1}, BOX)


def test_glider_outside_box_by_longitude():
    assert not _in_bounds({"lat": 39.8, "lon": 2.0}, BOX)


def test_boundary_is_inclusive():
    assert _in_bounds({"lat": 39.0, "lon": 29.5}, BOX)
    assert _in_bounds({"lat": 40.5, "lon": 31.5}, BOX)


def test_antimeridian_view_still_matches():
    """Leaflet reports lon past ±180 across the date line; beacons never do.

    A view spanning 170°E to 190°E must match a glider reported at -175°, which
    is the same meridian expressed the other way round.  Without the wrap the
    Pacific edge of the map goes blank for no visible reason.
    """
    box = (-10.0, 10.0, 170.0, 190.0)
    assert _in_bounds({"lat": 0.0, "lon": 175.0},  box)   # direct
    assert _in_bounds({"lat": 0.0, "lon": -175.0}, box)   # wrapped (=185)
    assert not _in_bounds({"lat": 0.0, "lon": 100.0}, box)


def test_negative_antimeridian_view_still_matches():
    box = (-10.0, 10.0, -190.0, -170.0)
    assert _in_bounds({"lat": 0.0, "lon": 175.0}, box)    # wrapped (=-185)
    assert _in_bounds({"lat": 0.0, "lon": -175.0}, box)   # direct

# ---------------------------------------------------------------------------
# _round_probs — payload trimming at the serialisation boundary
# ---------------------------------------------------------------------------

def test_probabilities_rounded_to_three_places():
    assert _round_probs([0.32412345678901234, 0.9999999]) == [0.324, 1.0]


def test_rounding_shortens_the_serialised_payload():
    """The point of the exercise: fewer bytes on the wire, not prettier numbers."""
    import json
    raw = [0.32412345678901234] * 1000
    assert len(json.dumps(_round_probs(raw))) < len(json.dumps(raw)) / 2


def test_rounding_preserves_length_and_range():
    out = _round_probs([0.0, 0.5, 1.0, 0.123456])
    assert len(out) == 4
    assert all(0.0 <= v <= 1.0 for v in out)

# ---------------------------------------------------------------------------
# _positions_to_wire — the seq stamp is internal bookkeeping
# ---------------------------------------------------------------------------

def test_seq_is_stripped_from_the_wire():
    out = _positions_to_wire({"G1": [{"lat": 39.812345678, "lon": 30.1, "alt": 900, "seq": 42}]})
    assert out["G1"][0] == {"lat": 39.81235, "lon": 30.1, "alt": 900}


# ---------------------------------------------------------------------------
# _attach_cached_agl — the live stream has to carry height above ground
#
# _to_wire only copies keys that exist, so a glider with no agl key reaches the
# client as `undefined`, not null.  The airborne-only filter reads that as "no
# reading available" and falls through to its ground-speed fallback, which
# happily passes a glider still sitting on the runway.
# ---------------------------------------------------------------------------

@pytest.fixture
def warm_cache(monkeypatch):
    """Elevation cache holding a single known 2 dp cell."""
    monkeypatch.setattr(terrain_client, "_elev_cache", {(39.81, 30.52): 800})


def _glider(lat, lon, alt):
    return {"id": "G1", "lat": lat, "lon": lon, "alt": alt, "speed_kmh": 95.0}


def test_agl_is_alt_above_the_cached_ground(warm_cache):
    g, = _attach_cached_agl([_glider(39.8123, 30.5187, 1400)])
    assert g["agl"] == 600


def test_glider_on_the_ground_gets_a_non_positive_agl(warm_cache):
    """The reported bug: a parked or rolling glider must be filterable."""
    g, = _attach_cached_agl([_glider(39.8123, 30.5187, 795)])
    assert g["agl"] == -5


def test_agl_reaches_the_wire(warm_cache):
    """A key present on the dict but dropped by _to_wire would fix nothing."""
    wire, = _to_wire(_attach_cached_agl([_glider(39.8123, 30.5187, 1400)]))
    assert wire["agl"] == 600


def test_cache_miss_sends_an_explicit_null(monkeypatch):
    """Null is 'unknown', and must be distinguishable from a real reading."""
    monkeypatch.setattr(terrain_client, "_elev_cache", {})
    # No running loop here, so the background warm must not be what raises.
    monkeypatch.setattr("main.asyncio.create_task", lambda coro: coro.close())
    wire, = _to_wire(_attach_cached_agl([_glider(39.8123, 30.5187, 1400)]))
    assert "agl" in wire and wire["agl"] is None


def test_stale_agl_is_overwritten_when_the_cache_goes_cold(monkeypatch):
    """fetch_ogn_gliders hands out live dicts, so a previous frame's agl persists."""
    monkeypatch.setattr(terrain_client, "_elev_cache", {})
    monkeypatch.setattr("main.asyncio.create_task", lambda coro: coro.close())
    g = _glider(39.8123, 30.5187, 1400)
    g["agl"] = 600
    _attach_cached_agl([g])
    assert g["agl"] is None
