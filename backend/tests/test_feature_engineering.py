import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from datetime import datetime, timezone
from pipeline.feature_engineering import build_feature_matrix, _slope_aspect, _wind_components

METEO = {
    "temp_2m": 22.0,
    "humidity": 0.55,
    "wind_speed": 10.0,
    "wind_dir": 270.0,
    "cape": 800.0,
    "cin": -50.0,
    "solar_ghi": 600.0,
    "lapse_rate": 6.5,
    "pbl_height": 850.0,
    "soil_temp": 18.0,
}

ELEV = np.array([[100, 120, 110], [90, 105, 115], [80, 95, 100]], dtype=float)
DT   = datetime(2025, 6, 21, 12, 0, tzinfo=timezone.utc)


def test_output_shape():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert mat.shape == (9, 21), f"Expected (9, 21), got {mat.shape}"


def test_column_count():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert mat.shape[1] == 21


def test_cape_column_constant():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert np.all(mat[:, 9] == METEO["cape"])


def test_humidity_column_constant():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert np.allclose(mat[:, 6], METEO["humidity"])


def test_wind_components_westerly():
    # 270° wind — u should be positive (eastward), v near zero
    u, v = _wind_components(10.0, 270.0)
    assert u > 0
    assert abs(v) < 1e-6


def test_wind_components_northerly():
    # 0° / 360° wind — v should be negative (southward), u near zero
    u, v = _wind_components(10.0, 0.0)
    assert abs(u) < 1e-6
    assert v < 0


def test_slope_flat_grid():
    flat = np.ones((5, 5)) * 200.0
    slope, aspect = _slope_aspect(flat)
    assert np.allclose(slope, 0.0)


def test_slope_inclined_grid():
    # Uniform north-facing slope
    rows = np.tile(np.arange(5, dtype=float) * 100, (5, 1)).T
    slope, _ = _slope_aspect(rows)
    assert np.all(slope > 0)


def test_cyclic_time_columns_in_range():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    for col in (15, 16, 17, 18):  # hour_sin, hour_cos, doy_sin, doy_cos
        assert np.all(mat[:, col] >= -1.0) and np.all(mat[:, col] <= 1.0)


def test_land_use_heat_column():
    mat_urban  = build_feature_matrix(METEO, ELEV, land_use="urban", dt=DT)
    mat_forest = build_feature_matrix(METEO, ELEV, land_use="forest", dt=DT)
    assert mat_urban[0, 13] > mat_forest[0, 13]


def test_no_nan_in_output():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert not np.any(np.isnan(mat))


def test_lat_lon_bounds_applied():
    mat = build_feature_matrix(METEO, ELEV, dt=DT,
                               lat_bounds=(51.0, 52.0),
                               lon_bounds=(-0.5, 0.5))
    assert mat[0, 0]  == pytest.approx(51.0)   # south edge
    assert mat[-1, 0] == pytest.approx(52.0)   # north edge
    assert mat[0, 1]  == pytest.approx(-0.5)   # west edge
    assert mat[-1, 1] == pytest.approx(0.5)    # east edge


def test_default_bounds_are_index_placeholders():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert mat[0, 0] == pytest.approx(0.0)
    assert mat[0, 1] == pytest.approx(0.0)
