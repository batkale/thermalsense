import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import data.terrain_client as tc
from config import GRID_RADIUS, GRID_RES, TERRAIN_RES
from data.terrain_client import (
    fetch_elevation_batch, fetch_elevation_grid, grid_shape, snap_grid_centre,
    MAX_BATCH_POINTS,
)


class _FakeResponse:
    def __init__(self, n):
        self._n = n
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"elevation": 100.0} for _ in range(self._n)]}


@pytest.fixture(autouse=True)
def _clear_cache():
    tc._elev_cache.clear()
    tc._grid_cache.clear()
    yield
    tc._elev_cache.clear()
    tc._grid_cache.clear()


@pytest.fixture
def captured(monkeypatch):
    """Record the URL of every outbound request instead of making one."""
    urls = []

    async def fake_get(client, url, max_retries=3):
        urls.append(url)
        return _FakeResponse(url.count("|") + 1)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tc, "_get_with_retry", fake_get)
    monkeypatch.setattr(tc, "_get_client", lambda: None)
    monkeypatch.setattr(tc.asyncio, "sleep", no_sleep)
    return urls


async def test_batch_never_exceeds_the_api_point_cap(captured):
    """A single oversized request returns 414 URI Too Long from OpenTopoData."""
    pairs = [(50.0 + i * 0.01, 1.0 + i * 0.01) for i in range(250)]
    await fetch_elevation_batch(pairs, max_requests=10)
    assert captured, "expected at least one request"
    for url in captured:
        assert url.count("|") + 1 <= MAX_BATCH_POINTS


async def test_batch_dedupes_on_the_cache_key(captured):
    """Points sharing a 2 dp cell must cost one slot, not one each."""
    pairs = [(50.0001 + i * 1e-6, 1.0001) for i in range(150)]
    out = await fetch_elevation_batch(pairs, max_requests=10)
    assert len(captured) == 1
    assert captured[0].count("|") + 1 == 1
    assert len(out) == len(pairs)
    assert all(v == 100 for v in out.values())


async def test_batch_respects_the_request_budget(captured):
    pairs = [(50.0 + i * 0.01, 1.0 + i * 0.01) for i in range(500)]
    out = await fetch_elevation_batch(pairs, max_requests=2)
    assert len(captured) == 2
    assert any(v is None for v in out.values())      # beyond budget
    assert any(v is not None for v in out.values())  # within budget


async def test_batch_returns_an_entry_for_every_input(captured):
    pairs = [(50.0 + i * 0.01, 1.0) for i in range(30)]
    out = await fetch_elevation_batch(pairs, max_requests=1)
    assert set(out) == set(pairs)


async def test_cached_points_skip_the_network(captured):
    pairs = [(50.0, 1.0), (51.0, 2.0)]
    await fetch_elevation_batch(pairs, max_requests=5)
    first = len(captured)
    await fetch_elevation_batch(pairs, max_requests=5)
    assert len(captured) == first, "second call should be served from cache"


# --- grid alignment ----------------------------------------------------------
# These pin the property that the whole heatmap rests on: a cell's elevation and
# the coordinates it is labelled with must describe the same piece of ground.


def _elev_at(lat: float, lon: float) -> float:
    """Synthetic elevation that encodes position, so a cell's value identifies it.

    Linear in lat/lon on purpose: bilinear upsampling reproduces a linear field
    exactly, so every fine cell can be checked, not just the coarse nodes.
    """
    return round(lat, 5) * 1_000.0 + round(lon, 5) * 1_000_000.0


@pytest.fixture
def geo(monkeypatch, tmp_path):
    """Serve position-encoded elevations and keep all caching inside tmp_path."""
    urls = []

    class _R:
        def __init__(self, pts):
            self._pts = pts
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"elevation": _elev_at(la, lo)} for la, lo in self._pts]}

    async def fake_get(client, url, max_retries=3):
        urls.append(url)
        raw = url.split("locations=")[1]
        return _R([tuple(float(v) for v in p.split(",")) for p in raw.split("|")])

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tc, "_get_with_retry", fake_get)
    monkeypatch.setattr(tc, "_get_client", lambda: None)
    monkeypatch.setattr(tc.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tc, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(tc, "_POINT_CACHE_PATH", tmp_path / "points.json")
    return urls


def test_snap_lands_on_the_lattice_and_is_idempotent():
    for lat, lon in [(45.6234, 6.0891), (-33.3812, -70.7), (0.0, 0.0)]:
        snapped = snap_grid_centre(lat, lon)
        assert snap_grid_centre(*snapped) == snapped
        for v in snapped:
            assert abs(v / TERRAIN_RES - round(v / TERRAIN_RES)) < 1e-6
        assert abs(snapped[0] - lat) <= TERRAIN_RES / 2 + 1e-9
        assert abs(snapped[1] - lon) <= TERRAIN_RES / 2 + 1e-9


async def test_grid_describes_the_ground_its_centre_names(geo):
    """The regression: a cached grid used to be whatever the first caller in a
    ~10 km cell fetched, so the terrain could belong 5.5 km from the request."""
    for lat, lon in [(45.62, 6.0), (45.58, 6.0)]:
        grid = await fetch_elevation_grid(lat, lon)
        clat, clon = snap_grid_centre(lat, lon)
        rows, cols = grid.shape
        assert grid[rows // 2, cols // 2] == pytest.approx(_elev_at(clat, clon))


async def test_points_in_the_same_10km_cell_no_longer_share_one_grid(geo):
    """(45.62, 6.0) and (45.58, 6.0) both rounded to 45.6 under the old key, so
    whichever was requested first served its terrain to the other."""
    a = await fetch_elevation_grid(45.62, 6.0)
    b = await fetch_elevation_grid(45.58, 6.0)
    # Same longitudes, 0.04 deg apart in latitude: every cell must differ by
    # exactly the elevation that offset encodes, not by nothing.
    assert np.allclose(a - b, 0.04 * 1_000.0, rtol=0, atol=1e-6)


async def test_grid_spans_the_full_declared_radius(geo):
    """Sampling with endpoint=False covered 2*radius - TERRAIN_RES of ground while
    the caller labelled it as the full 2*radius, stretching terrain across the map."""
    clat, clon = snap_grid_centre(45.62, 6.0)
    grid = await fetch_elevation_grid(45.62, 6.0)

    assert grid.shape == grid_shape()
    assert grid[0, 0] == pytest.approx(_elev_at(clat - GRID_RADIUS, clon - GRID_RADIUS))
    assert grid[-1, -1] == pytest.approx(_elev_at(clat + GRID_RADIUS, clon + GRID_RADIUS))


async def test_every_cell_sits_exactly_one_grid_res_from_its_neighbour(geo):
    clat, clon = snap_grid_centre(45.62, 6.0)
    grid = await fetch_elevation_grid(45.62, 6.0)
    rows, cols = grid.shape

    for i, j in [(0, 0), (1, 0), (0, 1), (57, 133), (rows - 1, cols - 1)]:
        expect = _elev_at(clat - GRID_RADIUS + i * GRID_RES,
                          clon - GRID_RADIUS + j * GRID_RES)
        assert grid[i, j] == pytest.approx(expect), f"cell ({i},{j}) is off its lattice point"


async def test_neighbouring_grids_reuse_cached_coarse_points(geo):
    """Snapping to the lattice is what keeps the extra grids affordable: a grid
    one step over shares all but one row of coarse points with its neighbour."""
    await fetch_elevation_grid(45.62, 6.0)
    cold = len(geo)
    assert cold > 0

    tc._grid_cache.clear()                     # force a rebuild, keep the point cache
    await fetch_elevation_grid(45.63, 6.0)
    assert len(geo) - cold <= 1, "a one-step shift should not refetch the whole window"
