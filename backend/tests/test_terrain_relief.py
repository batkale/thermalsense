"""
/terrain/relief: the surface the map's 3D view is built on.

The view draws the heatmap over this surface and stands thermal columns and
aircraft on it, so it has to be the same patch of ground as the heatmap — same
snapped centre, same radius, same row order — or every column lands on the
wrong hillside.  And it must say what it is: real GLO-30 pixels, or the
interpolated lattice the model's original columns read.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from collections import OrderedDict

import numpy as np
import pytest
from fastapi import HTTPException

import main
from config import GRID_RADIUS, GRID_RES, RELIEF_STEP
from data import terrain_client as tc

N = round(2 * GRID_RADIUS / RELIEF_STEP) + 1


def _plane(window, lats, lons):
    """Rises 1 m per 0.001 deg north and 0.001 m per 0.001 deg east: position-coded."""
    lats, lons = np.asarray(lats), np.asarray(lons)
    return (1000.0 + (lats - 46.0) * 1000.0 + (lons - 10.0) * 1.0).astype(np.float32)


@pytest.fixture
def copernicus(monkeypatch):
    monkeypatch.setattr(tc, "TERRAIN_SOURCE", "copernicus")
    monkeypatch.setattr(tc, "_relief_cache", OrderedDict())
    calls = {"windows": [], "lattice": 0}

    async def window(*box):
        calls["windows"].append(box)
        return {}

    async def lattice(lat, lon, radius=GRID_RADIUS, strict=False):
        calls["lattice"] += 1
        n = round(2 * radius / GRID_RES) + 1
        return np.full((n, n), 77.0)

    monkeypatch.setattr(tc.dem_client, "load_window", window)
    monkeypatch.setattr(tc.dem_client, "sample_window", _plane)
    monkeypatch.setattr(tc, "fetch_elevation_grid", lattice)
    return calls


async def test_relief_covers_the_heatmaps_patch_south_west_first(copernicus):
    elev, source, filled = await tc.fetch_relief_grid(46.31, 10.22)
    clat, clon = tc.snap_grid_centre(46.31, 10.22)
    assert elev.shape == (N, N) and source == "copernicus" and filled == 0
    # The window read is exactly the heatmap's box, edges included.
    (south, north, west, east), = copernicus["windows"]
    assert np.isclose(south, clat - GRID_RADIUS) and np.isclose(north, clat + GRID_RADIUS)
    assert np.isclose(west, clon - GRID_RADIUS) and np.isclose(east, clon + GRID_RADIUS)
    # Row 0 is the southern edge, column 0 the western one.
    assert np.isclose(elev[0, 0], _plane(None, clat - GRID_RADIUS, clon - GRID_RADIUS), atol=1e-3)
    assert np.isclose(elev[-1, -1], _plane(None, clat + GRID_RADIUS, clon + GRID_RADIUS), atol=1e-3)
    assert elev[1, 0] > elev[0, 0] and elev[0, 1] > elev[0, 0]
    assert copernicus["lattice"] == 0          # nothing missing, so the lattice is never read


async def test_a_relief_is_read_once_then_served_from_a_bounded_cache(copernicus, monkeypatch):
    monkeypatch.setattr(tc, "TERRAIN_RELIEF_CACHE", 1)
    a = await tc.fetch_relief_grid(46.31, 10.22)
    b = await tc.fetch_relief_grid(46.31, 10.22)
    assert b is a and len(copernicus["windows"]) == 1
    await tc.fetch_relief_grid(47.3, 11.2)          # evicts the first
    assert len(tc._relief_cache) == 1
    cached = tc.source_usage()["cached"]
    assert cached["relief_grids"] == 1 and cached["relief_grids_max"] == 1


async def test_points_without_a_tile_come_from_the_lattice_and_are_counted(copernicus, monkeypatch):
    def coast(window, lats, lons):
        out = _plane(window, lats, lons)
        out[: 10] = np.nan                         # the southern ten rows are open sea
        return out

    monkeypatch.setattr(tc.dem_client, "sample_window", coast)
    elev, source, filled = await tc.fetch_relief_grid(46.31, 10.22)
    assert source == "copernicus" and filled == 10 * N
    assert np.isfinite(elev).all()
    assert (elev[:10] == 77.0).all() and (elev[10:] != 77.0).all()


async def test_a_failed_read_falls_back_to_the_lattice_and_is_not_cached(copernicus, monkeypatch):
    async def broken(*box):
        raise RuntimeError("S3 unreachable")

    monkeypatch.setattr(tc.dem_client, "load_window", broken)
    elev, source, filled = await tc.fetch_relief_grid(46.31, 10.22)
    assert source == "lattice" and filled == 0 and elev.shape == (N, N)
    assert (elev == 77.0).all()
    assert len(tc._relief_cache) == 0          # retried next time, not pinned


async def test_no_terrain_at_all_is_a_503_not_a_flat_plane(copernicus, monkeypatch):
    async def broken(*box):
        raise RuntimeError("S3 unreachable")

    async def no_lattice(*a, **k):
        return None

    monkeypatch.setattr(tc.dem_client, "load_window", broken)
    monkeypatch.setattr(tc, "fetch_elevation_grid", no_lattice)
    assert await tc.fetch_relief_grid(46.31, 10.22) is None
    with pytest.raises(HTTPException) as err:
        await main.terrain_relief(lat=46.31, lon=10.22)
    assert err.value.status_code == 503


async def test_the_endpoint_names_its_centre_and_source(copernicus):
    out = await main.terrain_relief(lat=46.31, lon=10.22)
    assert (out["grid_lat"], out["grid_lon"]) == tc.snap_grid_centre(46.31, 10.22)
    assert out["radius"] == GRID_RADIUS
    assert (out["rows"], out["cols"]) == (N, N) and len(out["elevation"]) == N * N
    assert out["source"] == "copernicus" and out["filled"] == 0
    assert all(isinstance(v, int) for v in out["elevation"][:5])
