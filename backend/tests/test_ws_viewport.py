import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

import main
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
    """Elevation cache holding the same spot at both resolutions.

    The ~1 km cell and the ~11 m cell inside it disagree by 50 m on purpose:
    that is the real spread measured at Rieti, where the coarse lattice samples
    the valley floor while the aircraft sits on the knoll above it.
    """
    monkeypatch.setattr(terrain_client, "_elev_cache",
                        {(39.81, 30.52): 800, (39.8123, 30.5187): 850})


def _glider(lat, lon, alt, speed_kmh=95.0):
    return {"id": "G1", "lat": lat, "lon": lon, "alt": alt, "speed_kmh": speed_kmh}


def _parked(lat, lon, alt):
    return _glider(lat, lon, alt, speed_kmh=0.0)


def test_agl_is_alt_above_the_cached_ground(warm_cache):
    """Well clear of the terrain, the coarse lattice is the answer."""
    g, = _attach_cached_agl([_glider(39.8123, 30.5187, 1400)])
    assert g["agl"] == 600


def test_glider_on_the_ground_gets_a_non_positive_agl(warm_cache):
    """The reported bug: a parked or rolling glider must be filterable."""
    g, = _attach_cached_agl([_parked(39.8123, 30.5187, 845)])
    assert g["agl"] == -5


def test_near_the_ground_the_precise_sample_wins(warm_cache):
    """A glider parked on the knoll must not read as airborne over the valley.

    850 m of ground under the aircraft, 800 m a kilometre away in the coarse
    cell.  Believing the coarse cell reports +50 m AGL for an aircraft standing
    still on the ground, which is exactly what the client's 10 m airborne filter
    was passing when NAV405393 stayed on the map.
    """
    g, = _attach_cached_agl([_parked(39.8123, 30.5187, 850)])
    assert g["agl"] == 0


def test_high_gliders_are_not_precision_sampled(warm_cache, monkeypatch):
    """Fine terrain is only fetched where the threshold lives, or the daily
    OpenTopoData budget goes on sharpening numbers nothing reads."""
    warmed = []
    monkeypatch.setattr("main.asyncio.create_task",
                        lambda coro: (warmed.append(coro), coro.close()))
    # Only the fine entry is missing, so a resample would have to warm.
    terrain_client._elev_cache.pop((39.8123, 30.5187))
    g, = _attach_cached_agl([_glider(39.8123, 30.5187, 1400)])
    assert g["agl"] == 600 and warmed == []


def test_fast_traffic_low_down_is_not_precision_sampled(warm_cache, monkeypatch):
    """The height gate alone would not bound this.

    A ridge-soaring glider spends an hour inside the band at 100 km/h, crossing
    a fresh 11 m cell several times a second — precision there would cost a
    terrain fetch per frame to confirm something the coarse reading already
    gets right.
    """
    warmed = []
    monkeypatch.setattr("main.asyncio.create_task",
                        lambda coro: (warmed.append(coro), coro.close()))
    terrain_client._elev_cache.pop((39.8123, 30.5187))
    g, = _attach_cached_agl([_glider(39.8123, 30.5187, 900)])
    assert g["agl"] == 100 and warmed == []


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


def test_a_pending_precise_sample_keeps_the_coarse_reading(monkeypatch):
    """Approximate beats absent while the fine terrain warms.

    Sending null here instead would drop the client onto its ground-speed
    fallback, which passes anything faster than 30 km/h — including the takeoff
    roll this whole path exists to filter out.
    """
    monkeypatch.setattr(terrain_client, "_elev_cache", {(39.81, 30.52): 800})
    monkeypatch.setattr("main.asyncio.create_task", lambda coro: coro.close())
    g, = _attach_cached_agl([_parked(39.8123, 30.5187, 850)])
    assert g["agl"] == 50


# ---------------------------------------------------------------------------
# /ws/live — the *first* frame has to respect the viewport too
#
# The filter is per-connection state the client declares over the socket, so
# there is a window where the server knows nothing about the viewport.  Sending
# during that window is worse than sending nothing: the client draws every
# glider on the planet, then the next frame arrives scoped and the map wipes
# itself.  That reads as "all the gliders disappeared", not as a filter working.
# ---------------------------------------------------------------------------

INONU = {"lat_min": 39.0, "lat_max": 40.5, "lon_min": 29.5, "lon_max": 31.5}

_FEED = [
    {"id": "NEAR", "lat": 39.8, "lon": 30.1, "alt": 1400, "vario": 1.2,
     "heading": 90, "speed_kmh": 95, "circling": False, "is_tow": False,
     "under_tow": False, "ac_type": 1, "ac_type_name": "glider"},
    {"id": "FRANCE", "lat": 45.3, "lon": 5.9, "alt": 1800, "vario": 0.4,
     "heading": 270, "speed_kmh": 110, "circling": False, "is_tow": False,
     "under_tow": False, "ac_type": 1, "ac_type_name": "glider"},
]


@pytest.fixture
def stub_feed(monkeypatch):
    """A two-glider world, no elevation lookups, no beacon buffers."""
    async def _feed():
        return [dict(g) for g in _FEED]

    monkeypatch.setattr(main, "fetch_ogn_gliders", _feed)
    monkeypatch.setattr(main, "drain_beacon_buffers", lambda cursor: ({}, cursor))
    monkeypatch.setattr(main, "beacon_cursor", lambda: 0)
    # A hit for every point keeps the background elevation warm from firing.
    monkeypatch.setattr(main, "cached_elevation", lambda lat, lon, dp=2: 800)


def test_first_frame_is_already_scoped_to_the_viewport(stub_feed):
    """The bug: frame 1 used to carry the whole planet, frame 2 only the view."""
    with TestClient(main.app).websocket_connect("/ws/live") as ws:
        ws.send_json({"bounds": INONU})
        frame = ws.receive_json()
    assert [g["id"] for g in frame["gliders"]] == ["NEAR"]


def test_a_client_that_never_declares_bounds_still_gets_the_full_feed(
        stub_feed, monkeypatch):
    """An older frontend degrades to the previous behaviour, not to a blank map."""
    monkeypatch.setattr(main, "_BOUNDS_GRACE_S", 0.05)
    with TestClient(main.app).websocket_connect("/ws/live") as ws:
        frame = ws.receive_json()
    assert {g["id"] for g in frame["gliders"]} == {"NEAR", "FRANCE"}


def test_a_later_move_still_rescopes_the_stream(stub_feed):
    """The grace period must not turn the viewport into connect-time-only state."""
    with TestClient(main.app).websocket_connect("/ws/live") as ws:
        ws.send_json({"bounds": INONU})
        assert [g["id"] for g in ws.receive_json()["gliders"]] == ["NEAR"]
        ws.send_json({"bounds": {"lat_min": 44.0, "lat_max": 46.0,
                                 "lon_min": 5.0, "lon_max": 7.0}})
        # Frames are 2 s apart, so allow a couple before the new view lands.
        for _ in range(3):
            ids = [g["id"] for g in ws.receive_json()["gliders"]]
            if ids == ["FRANCE"]:
                return
        pytest.fail(f"viewport update never applied, last frame: {ids}")


# ---------------------------------------------------------------------------
# /ogn/glider/{id} — the viewport-independent lookup behind the pinned card
#
# The stream is scoped to the client's bounds, so a followed aircraft stops
# appearing in it the moment it leaves them.  This endpoint is what keeps the
# card current in that case, so what matters is that it ignores the viewport
# entirely, matches the id exactly, and treats "gone" as a normal answer.
# ---------------------------------------------------------------------------

def test_glider_lookup_ignores_the_viewport(stub_feed):
    """FRANCE is outside İnönü, which is exactly when this endpoint is needed."""
    res = TestClient(main.app).get("/ogn/glider/FRANCE")
    assert res.status_code == 200
    assert res.json()["glider"]["id"] == "FRANCE"


def test_glider_lookup_returns_wire_shape(stub_feed):
    """The card reads the same fields the socket delivers, agl included."""
    body = TestClient(main.app).get("/ogn/glider/NEAR").json()["glider"]
    assert body["lat"] == 39.8 and body["alt"] == 1400
    assert "agl" in body


def test_unknown_glider_is_null_not_an_error(stub_feed):
    """Landing or ageing out is ordinary: the client keeps its last state."""
    res = TestClient(main.app).get("/ogn/glider/NOSUCH")
    assert res.status_code == 200
    assert res.json()["glider"] is None


def test_glider_lookup_does_not_match_on_substring(stub_feed):
    """Following one aircraft must never silently switch to another.

    /ogn/search matches substrings by design; this endpoint must not, or a
    pinned "NEAR" could be answered with any id that happens to contain it.
    """
    assert TestClient(main.app).get("/ogn/glider/EAR").json()["glider"] is None
    assert TestClient(main.app).get("/ogn/glider/near").json()["glider"] is None
