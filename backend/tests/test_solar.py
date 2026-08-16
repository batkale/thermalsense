import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from pipeline.solar import sun_position, cos_incidence


def test_elevation_at_greenwich_equinox_noon():
    # Equinox (doy 80), 12:00 UTC at Greenwich: the sun sits at 90 - latitude.
    elev, _ = sun_position(51.5, 0.0, 80, 12.0)
    assert float(elev) == pytest.approx(38.5, abs=1.0)


def test_azimuth_is_due_south_at_local_solar_noon():
    # The original arccos formulation returned ~2 degrees here — due *north* —
    # which silently inverted every slope's illumination.  Worth pinning.
    _, azim = sun_position(51.5, 0.0, 80, 12.0)
    assert float(azim) == pytest.approx(180.0, abs=5.0)


def test_azimuth_tracks_east_to_west_through_the_day():
    lat, lon, doy = 39.8, 30.1, 172          # Eskisehir, summer solstice
    morning, _ = sun_position(lat, lon, doy, 6.0), None
    az_morning = float(sun_position(lat, lon, doy, 6.0)[1])
    az_noon = float(sun_position(lat, lon, doy, 10.0)[1])   # ~local solar noon
    az_evening = float(sun_position(lat, lon, doy, 14.0)[1])
    assert az_morning < az_noon < az_evening
    assert az_morning < 135.0      # east-ish
    assert az_evening > 225.0      # west-ish


def test_sun_is_below_horizon_at_local_midnight():
    elev, _ = sun_position(39.8, 30.1, 172, 21.0)
    assert float(elev) < 0


def test_max_elevation_matches_declination_geometry():
    # At local solar noon on the solstice, elevation = 90 - |lat - declination|.
    elev, _ = sun_position(39.8, 30.1, 172, 10.0)
    assert float(elev) == pytest.approx(90 - abs(39.8 - 23.44), abs=1.5)


def test_flat_ground_under_overhead_sun_is_fully_illuminated():
    assert float(cos_incidence(0.0, 0.0, 90.0, 180.0)) == pytest.approx(1.0, abs=1e-6)


def test_surface_turned_away_is_self_shadowed():
    # 45 degree slope facing north, sun low in the south -> no direct beam.
    assert float(cos_incidence(45.0, 0.0, 20.0, 180.0)) == 0.0


def test_south_facing_beats_north_facing_under_a_southern_sun():
    south = float(cos_incidence(30.0, 180.0, 40.0, 180.0))
    north = float(cos_incidence(30.0, 0.0, 40.0, 180.0))
    assert south > north


def test_night_is_zero_regardless_of_aspect():
    aspects = np.array([0.0, 90.0, 180.0, 270.0])
    out = cos_incidence(np.full(4, 20.0), aspects, np.full(4, -5.0), np.full(4, 180.0))
    assert np.all(out == 0.0)


def test_output_stays_within_unit_range():
    rng = np.random.default_rng(0)
    n = 500
    out = cos_incidence(
        rng.uniform(0, 60, n), rng.uniform(0, 360, n),
        rng.uniform(-20, 90, n), rng.uniform(0, 360, n),
    )
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_vectorised_matches_scalar():
    lats = np.array([51.5, 39.8, -33.9])
    lons = np.array([0.0, 30.1, 151.2])
    doys = np.array([80.0, 172.0, 355.0])
    hours = np.array([12.0, 10.0, 2.0])
    ev, av = sun_position(lats, lons, doys, hours)
    for i in range(3):
        e1, a1 = sun_position(lats[i], lons[i], doys[i], hours[i])
        assert float(e1) == pytest.approx(float(ev[i]), abs=1e-9)
        assert float(a1) == pytest.approx(float(av[i]), abs=1e-9)
