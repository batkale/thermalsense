"""
Full-resolution terrain columns: the shape of the ground at ~33 m.

The 22 original columns read elevation every TERRAIN_RES (0.01 deg, ~1.1 km)
and bilinearly fill the 50 m grid between, so every slope the model saw was a
plane between points a kilometre apart.  These eight columns read the real
Copernicus GLO-30 surface around each cell instead:

    slope_hr, northness_hr, eastness_hr   3x3 Horn gradient at STEP_M spacing
    rugged                                std of a 7x7 patch (~200 m across)
    tpi_150, tpi_400, tpi_1000, tpi_2500  centre minus the mean of a 12-point ring

TPI (topographic position index) is positive on hilltops and ridges and
negative in valleys.  Measured offline before shipping: +0.023 to +0.047 on the
served within-hour score over the 22 columns, every interval clear of zero,
holding on rows far from airfields (CLAUDE.md, "Full-resolution terrain").

This is the vectorised production twin of evaluation/hires_terrain.py, which
stays as the scalar reference the measurement was made with; a test pins the
two together.  Same offsets, same order, same rounding, same nearest-pixel
sampling — so what is served is what was measured.
"""

from __future__ import annotations

import numpy as np

STEP_M = 33.0                                  # ~ one GLO-30 pixel
RINGS_M = (150.0, 400.0, 1000.0, 2500.0)
N_RING = 12
COLUMNS = ("slope_hr", "northness_hr", "eastness_hr", "rugged",
           "tpi_150", "tpi_400", "tpi_1000", "tpi_2500")

_PATCH = [(i, j) for i in range(-3, 4) for j in range(-3, 4)]   # rows run south -> north
_ANGLES = np.linspace(0, 2 * np.pi, N_RING, endpoint=False)
N_PATCH = len(_PATCH)
N_POINTS = N_PATCH + len(RINGS_M) * N_RING

# Offsets of every sample point from the cell, in metres, in the order the
# features read them: the 7x7 patch row by row, then each ring by angle.
NORTH_M = np.array([i * STEP_M for i, _ in _PATCH]
                   + [r * np.cos(a) for r in RINGS_M for a in _ANGLES])
EAST_M = np.array([j * STEP_M for _, j in _PATCH]
                  + [r * np.sin(a) for r in RINGS_M for a in _ANGLES])
_M_PER_DEG = 111_320.0


def sample_points(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lats, lons), each (n, N_POINTS): where to read elevation for each cell."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    mlon = _M_PER_DEG * np.maximum(np.cos(np.radians(lat)), 1e-3)
    return (np.round(lat[:, None] + NORTH_M[None, :] / _M_PER_DEG, 6),
            np.round(lon[:, None] + EAST_M[None, :] / mlon[:, None], 6))


def features(elev: np.ndarray) -> np.ndarray:
    """
    COLUMNS from the elevations at sample_points, (n, N_POINTS) -> (n, 8).

    A cell with any gap in its 7x7 patch gets NaN throughout (no slope from a
    hole); a ring with some points missing averages the ones it has, and is NaN
    only when it has none.  NaN is "unknown", which the model handles — never
    a stand-in number.
    """
    elev = np.asarray(elev, dtype=float)
    n = len(elev)
    patch = elev[:, :N_PATCH].reshape(n, 7, 7)
    w = patch[:, 2:5, 2:5]
    dzdx = ((w[:, 0, 2] + 2 * w[:, 1, 2] + w[:, 2, 2])
            - (w[:, 0, 0] + 2 * w[:, 1, 0] + w[:, 2, 0])) / (8 * STEP_M)
    dzdy = ((w[:, 2, 0] + 2 * w[:, 2, 1] + w[:, 2, 2])
            - (w[:, 0, 0] + 2 * w[:, 0, 1] + w[:, 0, 2])) / (8 * STEP_M)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    # The bearing the slope faces (downhill), as feature_engineering._slope_aspect.
    aspect = np.radians(np.degrees(np.arctan2(-dzdx, -dzdy)) % 360)
    s = np.sin(np.radians(slope))
    out = np.full((n, len(COLUMNS)), np.nan)
    out[:, 0], out[:, 1], out[:, 2] = slope, np.cos(aspect) * s, np.sin(aspect) * s
    out[:, 3] = patch.reshape(n, -1).std(axis=1)
    centre = patch[:, 3, 3]
    for k in range(len(RINGS_M)):
        ring = elev[:, N_PATCH + k * N_RING:N_PATCH + (k + 1) * N_RING]
        have = np.isfinite(ring).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(have > 0, np.nansum(ring, axis=1) / np.maximum(have, 1), np.nan)
        out[:, 4 + k] = centre - mean
    out[np.isnan(patch).any(axis=(1, 2))] = np.nan
    return out


def grid_cells(clat: float, clon: float, radius: float, shape: tuple[int, int]):
    """(lat, lon) of every cell of a feature grid, flattened in build_feature_matrix order."""
    lats = np.linspace(clat - radius, clat + radius, shape[0])
    lons = np.linspace(clon - radius, clon + radius, shape[1])
    lat_g, lon_g = np.meshgrid(lats, lons, indexing="ij")
    return lat_g.ravel(), lon_g.ravel()
