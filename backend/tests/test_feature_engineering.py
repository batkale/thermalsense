import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from datetime import datetime, timezone
from pipeline.feature_engineering import (
    build_feature_matrix, _slope_aspect, _wind_components, FEATURE_COUNT,
)

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
    assert mat.shape == (9, FEATURE_COUNT), f"Expected (9, {FEATURE_COUNT}), got {mat.shape}"


def test_column_count():
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert mat.shape[1] == FEATURE_COUNT


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
    # High in the north, falling away to the south — a south-facing slope
    rows = np.tile(np.arange(5, dtype=float) * 100, (5, 1)).T
    slope, _ = _slope_aspect(rows)
    assert np.all(slope > 0)


@pytest.mark.parametrize("build, expected_aspect", [
    (lambda r, c: r,   180.0),   # high north  → faces south
    (lambda r, c: -r,    0.0),   # high south  → faces north
    (lambda r, c: -c,   90.0),   # high west   → faces east
    (lambda r, c: c,   270.0),   # high east   → faces west
])
def test_aspect_points_downhill(build, expected_aspect):
    """
    Aspect must be the compass bearing the slope faces (row=north, col=east).
    Negating only the east gradient mirrors north and south, which makes the
    terrain multiplier boost shaded slopes instead of sun-facing ones.
    """
    n = 5
    r, c = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    _, aspect = _slope_aspect(build(r.astype(float), c.astype(float)))
    centre = aspect[n // 2, n // 2]                       # clear of edge effects
    assert abs((centre - expected_aspect + 180) % 360 - 180) < 1.0


def test_east_west_spacing_scales_with_latitude():
    """A longitude degree shrinks as cos(lat), so an east-west slope steepens with latitude."""
    grid = np.tile(np.arange(5, dtype=float) * 100, (5, 1))   # varies along columns (east)
    slope_equator, _ = _slope_aspect(grid, lat_deg=0.0)
    slope_high, _    = _slope_aspect(grid, lat_deg=60.0)
    assert np.all(slope_high > slope_equator)


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


def test_land_use_columns_are_constant_without_real_cover():
    """The placeholder path: one value repeated over every cell."""
    mat = build_feature_matrix(METEO, ELEV, dt=DT)
    assert len(np.unique(mat[:, 13])) == 1
    assert len(np.unique(mat[:, 14])) == 1


def test_land_use_props_make_columns_13_and_14_per_cell():
    heat = np.linspace(0.1, 0.9, ELEV.size).reshape(ELEV.shape)
    albedo = np.linspace(0.05, 0.4, ELEV.size).reshape(ELEV.shape)
    mat = build_feature_matrix(METEO, ELEV, dt=DT, land_use_props=(heat, albedo))
    assert np.allclose(mat[:, 13], heat.ravel())
    assert np.allclose(mat[:, 14], albedo.ravel())
    assert len(np.unique(mat[:, 13])) == ELEV.size


def test_land_use_props_follow_the_same_cell_order_as_elevation():
    """
    Column 13 must line up with column 2 cell for cell. A ravel-order mismatch
    would attach each cell's ground cover to different ground entirely, which is
    worse than the constant it replaces.
    """
    heat = np.arange(ELEV.size, dtype=float).reshape(ELEV.shape)
    mat = build_feature_matrix(METEO, ELEV, dt=DT,
                               land_use_props=(heat, np.zeros_like(heat)))
    for row in range(mat.shape[0]):
        i, j = divmod(int(mat[row, 13]), ELEV.shape[1])
        assert mat[row, 2] == ELEV[i, j]


def test_mismatched_land_use_props_raise():
    bad = np.zeros((2, 2))
    with pytest.raises(ValueError, match="does not match elevation grid"):
        build_feature_matrix(METEO, ELEV, dt=DT, land_use_props=(bad, bad))


def test_land_use_props_do_not_change_the_matrix_width():
    """Columns 13/14 change meaning, not count — so no FEATURE_COUNT bump."""
    heat = np.full(ELEV.shape, 0.7)
    mat = build_feature_matrix(METEO, ELEV, dt=DT, land_use_props=(heat, heat))
    assert mat.shape[1] == FEATURE_COUNT
