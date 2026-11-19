"""
Satellite land-surface temperature (evaluation/lst.py): which image a row reads,
the morning series its heating rate comes from, the masks that keep cloud and
fill out of a value, and the geometry that says how coarse a pixel really is.
"""

from datetime import date, datetime, timezone

import numpy as np
import pytest

from evaluation import lst
from evaluation.evaluate import row_datetimes
from evaluation.holdout import decision_ids
from pipeline.feature_engineering import _cyclic


def _row(lat, lon, doy, hour, t2m=15.0, agl=600.0):
    x = np.zeros(22)
    x[0], x[1], x[5], x[21] = lat, lon, t2m, agl
    x[15], x[16] = _cyclic(hour, 24)
    x[17], x[18] = _cyclic(doy, 365)
    return x


def _ts(X):
    return np.array([t.timestamp() for t in row_datetimes(X)])


def test_every_row_of_a_unit_reads_the_same_image():
    # 10:29 and 09:31 are one unit (hour rounds to 10); 10:31 is the next one.
    X = np.stack([_row(46.3, 10.2, 283, h) for h in (10 + 29 / 60, 9 + 31 / 60, 10 + 31 / 60)])
    slot = lst.unit_slots(_ts(X))
    ge = decision_ids(X)
    assert ge[0] == ge[1] != ge[2]
    assert slot[0] == slot[1] != slot[2]
    hours = [datetime.fromtimestamp(s, tz=timezone.utc).strftime("%H:%M") for s in slot]
    assert hours == ["10:00", "10:00", "11:00"]


def test_morning_series_runs_from_sunrise_to_the_unit_slot():
    X = np.stack([_row(46.3, 10.2, 283, 12.0), _row(46.3, 10.2, 283, 2.0)])
    slot = lst.unit_slots(_ts(X))
    noon, night = lst.morning_series(X[:, 0], X[:, 1], slot)
    assert len(night) == 0
    assert noon[-1] == slot[0]
    assert np.all(np.diff(noon) == 3600.0)
    first = lst._sun_elev(np.array([46.3]), np.array([10.2]), np.array([noon[0]]))[0]
    before = lst._sun_elev(np.array([46.3]), np.array([10.2]), np.array([noon[0] - 3600]))[0]
    assert first > 0 >= before


def test_heating_starts_at_the_first_clear_slot_at_least_two_hours_back():
    h = 3600.0
    t = np.arange(6, 12) * h
    v = np.array([np.nan, 280.0, 282.0, 285.0, np.nan, 290.0])
    rise, rate = lst.heating(t, v, 12 * h, 292.0)
    assert rise == pytest.approx(12.0) and rate == pytest.approx(12.0 / 5)
    # only an hour of clear sky before now: not a heating rate
    only_recent = np.array([np.nan, np.nan, np.nan, np.nan, np.nan, 290.0])
    assert np.isnan(lst.heating(t, only_recent, 12 * h, 292.0)[0])
    assert np.isnan(lst.heating(t, v, 12 * h, np.nan)[1])


def test_anomaly_is_against_valid_box_pixels_only():
    box = np.array([[290.0, np.nan], [292.0, 294.0]])
    assert lst.anomaly(295.0, box) == pytest.approx(3.0)
    assert np.isnan(lst.anomaly(295.0, np.array([290.0, np.nan, np.nan])))
    assert np.isnan(lst.anomaly(np.nan, box))


def test_a_pixel_is_its_nadir_size_only_at_nadir():
    vza, slant = lst.geo_view(0.0, 0.0)
    assert vza == pytest.approx(0.0, abs=1e-6)
    assert slant == pytest.approx(lst.R_GEO_KM - lst.R_EARTH_KM)
    across, along = lst.pixel_km(0.0, 0.0, 2.0)
    assert across == pytest.approx(2.0) and along == pytest.approx(2.0)
    across, along = lst.pixel_km(47.0, 10.0, 2.0)          # the Alps from 0 deg
    assert 2.0 < across < 2.3 and 3.3 < along < 4.0
    assert np.isnan(lst.pixel_km(0.0, 120.0, 2.0)[0])       # behind the disk edge


def test_sinusoidal_tile_matches_planetary_computer():
    # The STAC search for (47 N, 11 E) returns h18v04, whose COG tiepoint puts
    # its top-left corner at x = 0, y = 5559752.598833 m.
    h, v, row, col = lst.sinusoidal(47.0, 11.0)
    assert (int(h), int(v)) == (18, 4)
    y = lst._R_SIN * np.radians(47.0)
    x = lst._R_SIN * np.radians(11.0) * np.cos(np.radians(47.0))
    assert col == pytest.approx(x / lst._PIX_M)
    assert row == pytest.approx((5559752.598833 - y) / lst._PIX_M)
    assert 0 <= row < 1200 and 0 <= col < 1200


def test_qc_keeps_good_retrievals_and_errors_up_to_two_kelvin():
    good = 0b00000000
    other_2k = 0b01000001          # other quality, LST error <= 2 K
    other_3k = 0b10000001          # other quality, LST error <= 3 K
    cloud = 0b00000010
    other_reason = 0b00000011
    assert lst.qc_ok([good, other_2k, other_3k, cloud, other_reason]).tolist() == [
        True, True, False, False, False]


def test_modis_values_drop_fill_and_failed_qc():
    raw = np.full((5, 5), 15000, dtype=np.uint16)          # 300 K
    raw[0, 0] = 0                                          # fill
    raw[2, 2] = 15100                                      # 302 K at the row
    qc = np.zeros((5, 5), dtype=np.uint8)
    qc[4, 4] = 0b10                                        # cloud
    view = np.full((5, 5), 133, dtype=np.uint8)
    k = lst.modis_lst(raw, qc)
    val, anom, vt = lst.modis_values(k, view, 2, 2, 2, 2)
    assert val == pytest.approx(302.0)
    assert anom == pytest.approx(302.0 - (300.0 * 22 + 302.0) / 23)
    assert vt == pytest.approx(13.3)
    assert np.isnan(lst.modis_values(k, view, 0, 0, 1, 1)[0])


def test_item_ids_name_platform_tile_and_day():
    assert lst._item_key("MYD11A1.A2026283.h18v04.061.2026286062857") == (
        "aqua", 18, 4, date(2026, 10, 10), "2026286062857")
    assert lst._item_key("MOD11A1.A2026001.h20v05.061.2026003000000")[0] == "terra"
    assert lst._item_key("not a modis id") is None


def test_file_urls_follow_the_lsa_saf_layout():
    t = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()
    assert lst.file_url("mtlst", t) == (
        "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTLST/NATIVE/2026/10/10/"
        "LSA-007_MTG_MTLST_MTG-FD_202610101200.nc")
    assert lst.file_name("mlstas", t) == "NETCDF4_LSASAF_MSG_MLST-ASv2_MSG-Disk_202610101200.nc"


def test_geogrid_finds_the_nearest_pixel_and_refuses_points_off_the_grid():
    lat = np.linspace(50.0, 40.0, 101)[:, None] * np.ones((1, 121))
    lon = np.ones((101, 1)) * np.linspace(0.0, 12.0, 121)[None, :]
    lat[0, :] = np.nan                                     # a row of space pixels
    g = lst.GeoGrid(lat, lon, coarse=4)
    rc = g.locate(np.array([45.03, 40.0, 60.0]), np.array([6.04, 12.0, 6.0]), max_km=10.0)
    assert rc[0].tolist() == [50, 60]
    assert rc[1].tolist() == [100, 120]
    assert rc[2].tolist() == [-1, -1]
    rr, cc = g.box(50, 60, 45.0, 6.0)
    assert len(rr) == 1                                    # 0.1 deg spacing: only itself
    assert (rr[0], cc[0]) == (50, 60)


def test_lapse_is_fitted_within_units_and_taken_out():
    rng = np.random.default_rng(0)
    ge = np.repeat(np.arange(40), 25)
    z = rng.uniform(300, 2500, ge.size)
    hot = rng.normal(0, 0.3, ge.size)
    v = 290.0 + 3.0 * ge - 0.005 * z + hot             # each unit its own level
    assert lst.unit_lapse(v, z, ge) == pytest.approx(0.005, abs=2e-4)
    relief = rng.normal(0, 200, ge.size)
    anom = -0.005 * relief + hot
    adj, cols, lapses = lst.lapse_adjusted(np.column_stack([v, anom]), ["a_lst", "a_lst_anom"],
                                           z, relief, ge)
    assert cols == ["a_lst_z", "a_lst_anom_z"]
    assert np.corrcoef(adj[:, 0] - 3.0 * ge, z)[0, 1] == pytest.approx(0.0, abs=0.05)
    assert np.corrcoef(adj[:, 1], hot)[0, 1] > 0.95


def test_coverage_counts_units_the_source_can_rank():
    y = np.array([1, 0, 1, 0, 1, 1])
    ge = np.array([1, 1, 2, 2, 3, 3])                      # unit 3 is not mixed
    f = np.array([[300.0], [301.0], [302.0], [np.nan], [np.nan], [np.nan]])
    c = lst.coverage(f, ["x_lst"], y, ge)
    assert c["mixed_units"] == 2
    assert c["units_any"] == pytest.approx(1.0)
    assert c["units_both"] == pytest.approx(0.5)
    assert c["rows"] == pytest.approx(0.5)
