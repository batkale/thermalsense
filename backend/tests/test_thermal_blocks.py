"""
3D thermal blocks (evaluation/thermal_blocks.py): the geometry, kk7's counting
rule, and — the property a score read off it depends on — that a buffer row
only ever sees traffic from other days.
"""

import math

import numpy as np
import pytest

from evaluation import thermal_blocks as tb
from evaluation.evaluate import row_datetimes
from pipeline.feature_engineering import _cyclic


def _east(lat, lon, metres):
    return lon + metres / (111_320.0 * math.cos(math.radians(lat)))


def test_blocks_are_about_100_m_on_a_side_and_100_m_tall():
    lat, lon, floor = tb.block_centre(tb.block_key(*tb.block_index(46.3, 10.2, 1150.0)))
    lat, lon, floor = float(lat), float(lon), float(floor)
    key = lambda la, lo, a: int(tb.block_key(*tb.block_index(la, lo, a)))
    here = key(lat, lon, floor + 50)
    assert key(lat, _east(lat, lon, 30), floor + 10) == here
    assert key(lat + 30 / 111_320.0, lon, floor + 90) == here
    assert key(lat, _east(lat, lon, 130), floor + 50) != here
    assert key(lat + 130 / 111_320.0, lon, floor + 50) != here
    assert key(lat, lon, floor + 110) != here
    assert floor == 1100.0


def test_climb_weight_follows_kk7():
    w = tb.climb_weight(np.array([0.4, 2.0, 2.0, -1.0]), np.array([1, 1, 0, 1]))
    assert w.tolist() == [pytest.approx(0.4), 1.0, 0.0, 0.0]


def test_an_aircraft_counts_once_per_block_with_its_strongest_climb():
    lat = np.full(12, 46.30001)
    lon = np.full(12, 10.20001)
    alt = np.full(12, 1120.0)
    ids = np.array(["A"] * 10 + ["B", "C"])
    vario = np.array([0.5] * 9 + [2.0] + [0.5, 3.0])
    circ = np.array([1] * 10 + [1, 0])          # C climbs fast but flies straight
    (k3, n3, c3), (k2, n2, c2) = tb.day_blocks(lat, lon, alt, vario, circ, ids)
    assert len(k3) == 1 and n3[0] == 3 and c3[0] == pytest.approx(1.5)
    assert len(k2) == 1 and n2[0] == 3 and c2[0] == pytest.approx(1.5)


def _row(lat, lon, elev, agl, doy, hour=12.0):
    x = np.zeros(22)
    x[0], x[1], x[2], x[21] = lat, lon, elev, agl
    x[15], x[16] = _cyclic(hour, 24)
    x[17], x[18] = _cyclic(doy, 365)
    return x


def _write_day(root, day, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = ["id", "lat", "lon", "alt", "vario", "circling", "ac_type", "under_tow"]
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    d = root / f"date={day.isoformat()}"
    d.mkdir(parents=True)
    pq.write_table(pa.table({
        "id": pa.array(data["id"], pa.string()), "lat": pa.array(data["lat"], pa.float32()),
        "lon": pa.array(data["lon"], pa.float32()), "alt": pa.array(data["alt"], pa.float32()),
        "vario": pa.array(data["vario"], pa.float32()), "circling": pa.array(data["circling"], pa.int8()),
        "ac_type": pa.array(data["ac_type"], pa.int16()), "under_tow": pa.array(data["under_tow"], pa.int8()),
    }), d / "part.parquet")


def test_a_row_reads_only_other_days(tmp_path):
    lat, lon = 46.30001, 10.20001
    X = np.stack([_row(lat, lon, 500.0, 620.0, 200), _row(lat, lon, 500.0, 620.0, 201)])
    day_a, day_b = (t.date() for t in row_datetimes(X))
    assert (day_b - day_a).days == 1
    far = lat + 0.02                              # ~2 km north: outside every neighbourhood
    _write_day(tmp_path, day_a, [(f"a{i}", lat, lon, 1125.0, 2.0 if i < 2 else -1.0, int(i < 2), 1, 0)
                                 for i in range(4)] + [("far", far, lon, 1125.0, 3.0, 1, 1, 0)])
    _write_day(tmp_path, day_b, [(f"b{i}", lat, lon, 1125.0, -1.0, 0, 1, 0) for i in range(10)])

    past, loo, report = tb.row_features(X, str(tmp_path))
    p0 = report["p0"]
    assert p0 == pytest.approx(3 / 15)            # 2 + 1 climbers of 15 aircraft-days
    share = lambda c, n: (c + tb.ALPHA * p0) / (n + tb.ALPHA)

    # day A: nothing came before it, and loo sees only day B's ten straight passes
    assert np.isnan(past[0, 0]) and past[0, 1] == 0.0
    assert loo[0, 0] == pytest.approx(share(0, 10)) and loo[0, 1] == pytest.approx(math.log1p(10))
    # day B: past and loo both see day A only — its own ten passes never count
    for f in (past, loo):
        assert f[1, 0] == pytest.approx(share(2, 4)) and f[1, 1] == pytest.approx(math.log1p(4))
        assert f[1, 2] == pytest.approx(share(2, 4))   # the 2D column agrees here


def test_the_ground_block_is_not_counted_twice(tmp_path):
    lat, lon = 46.30001, 10.20001
    X = np.stack([_row(lat, lon, 0.0, 30.0, 200), _row(lat, lon, 0.0, 30.0, 201)])
    day_a, day_b = (t.date() for t in row_datetimes(X))
    _write_day(tmp_path, day_a, [("a", lat, lon, 30.0, 2.0, 1, 1, 0)])
    _write_day(tmp_path, day_b, [])
    past, _, _ = tb.row_features(X, str(tmp_path))
    assert past[1, 1] == pytest.approx(math.log1p(1))
