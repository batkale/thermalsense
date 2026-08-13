import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from datetime import datetime, timezone

from models.thermal_model import (
    ThermalModel, _centre_index, _plausible_coords, _above_thermal_base,
    _THERMAL_BASE_MARGIN_M,
)
from pipeline.feature_engineering import build_feature_matrix
from config import GRID_RADIUS

METEO = {
    "temp_2m": 24.0, "humidity": 0.5, "wind_speed": 10.0, "wind_dir": 180.0,
    "cape": 800.0, "cin": -20.0, "solar_ghi": 700.0, "lapse_rate": 7.0,
    "pbl_height": 1400.0, "soil_temp": 26.0,
}


def _grid(n=20):
    """Elevation grid with a distinct value at the true centre cell."""
    g = np.zeros((n, n))
    g[n // 2, n // 2] = 999.0
    return g


def test_centre_index_is_the_middle_cell_not_half_the_length():
    """n//2 on a flattened square grid lands on the western edge, not the centre."""
    assert _centre_index((200, 200)) == 20100        # row 100, col 100
    assert _centre_index((200, 200)) != 200 * 200 // 2
    for rows, cols in [(20, 20), (10, 30), (7, 5)]:
        idx = _centre_index((rows, cols))
        assert idx // cols == rows // 2
        assert idx % cols == cols // 2


def test_centre_index_picks_the_centre_elevation():
    n = 20
    feat = build_feature_matrix(METEO, _grid(n), dt=datetime.now(timezone.utc))
    assert feat[_centre_index((n, n)), 2] == 999.0    # col 2 = elevation


def test_training_sample_carries_real_coordinates():
    """
    Omitting lat_bounds makes build_feature_matrix emit row/col indices in the
    lat/lon columns, which is a different feature space than /predict serves.
    """
    lat, lon, n = 51.5, -1.0, 20
    feat = build_feature_matrix(
        METEO, _grid(n), dt=datetime.now(timezone.utc),
        lat_bounds=(lat - GRID_RADIUS, lat + GRID_RADIUS),
        lon_bounds=(lon - GRID_RADIUS, lon + GRID_RADIUS),
    )
    row = feat[_centre_index((n, n))]
    assert row[0] == pytest.approx(lat, abs=0.02)
    assert row[1] == pytest.approx(lon, abs=0.02)


def test_buffer_rows_do_not_retain_the_parent_matrix():
    """
    feat[i] is a view onto the full (rows*cols, 21) matrix. Buffering the view
    keeps ~6.4 MB alive per 168-byte sample, so rows must be copied.
    """
    n = 20
    feat = build_feature_matrix(METEO, _grid(n), dt=datetime.now(timezone.utc))
    assert feat[_centre_index((n, n))].base is not None, "sanity: indexing yields a view"
    assert feat[_centre_index((n, n))].copy().base is None

    m = ThermalModel()
    m._buffer_X.append(feat[_centre_index((n, n))].copy())
    assert all(row.base is None for row in m._buffer_X)


def test_above_thermal_base_compares_agl_not_amsl():
    """
    APRS altitude is AMSL, cape_base is a height above ground. Comparing them
    directly would reject every aircraft over high terrain.
    """
    ground, base = 1000.0, 1500.0
    # 2200 m AMSL over 1000 m ground = 1200 m AGL — below a 1500 m base
    assert not _above_thermal_base(2200.0, ground, base)
    # 3000 m AMSL = 2000 m AGL — above base + margin
    assert _above_thermal_base(3000.0, ground, base)


def test_above_thermal_base_allows_the_margin():
    base = 1500.0
    assert not _above_thermal_base(base + _THERMAL_BASE_MARGIN_M - 50, 0.0, base)
    assert _above_thermal_base(base + _THERMAL_BASE_MARGIN_M + 50, 0.0, base)


@pytest.mark.parametrize("base", [0, 0.0, None])
def test_no_thermal_base_estimate_keeps_the_sample(base):
    """Without a usable estimate we must not discard on a guess."""
    assert not _above_thermal_base(9000.0, 0.0, base)


def test_plausible_coords_rejects_grid_indices():
    X = np.zeros((3, 21))
    X[0, 0], X[0, 1] = 51.5, -1.0      # real
    X[1, 0], X[1, 1] = 100.0, 0.0      # grid indices — the pre-fix signature
    X[2, 0], X[2, 1] = 47.0, 8.0       # real
    assert list(_plausible_coords(X)) == [True, False, True]


def test_predict_varies_across_terrain_without_a_model():
    """Physics fallback must produce a spatially varying field, not a flat one."""
    n = 20
    rng = np.random.default_rng(0)
    feat = build_feature_matrix(METEO, rng.random((n, n)) * 500, dt=datetime.now(timezone.utc))
    mean, std = ThermalModel().predict(feat)
    assert len(mean) == n * n
    assert max(mean) > min(mean)
    assert all(0.0 <= v <= 1.0 for v in mean)
    assert len(std) == n * n


def test_terrain_multiplier_favours_south_facing_slopes():
    m = ThermalModel()
    feat = np.zeros((2, 21))
    feat[:, 3] = 10.0        # equal slope
    feat[0, 4] = 180.0       # south-facing
    feat[1, 4] = 0.0         # north-facing
    south, north = m._terrain_multiplier(feat)
    assert south > north
