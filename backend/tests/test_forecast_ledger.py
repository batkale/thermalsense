"""
The forecast ledger: forecasts are written before their time, cannot be edited
afterwards, and are scored by fixed rules against the gliders that were there.

Plus the two /predict fixes the ledger depends on: live thermals fade with the
horizon instead of being pasted onto every future map, and a map built on the
synthetic weather fallback says so.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

import main
from config import GRID_RADIUS, GRID_RES
from data import forecast_ledger as ledger
from data import meteo_client as mc
from data.ogn_client import SOARING_AC_TYPES, is_thermal_evidence
from models.thermal_model import _label_for, _POSITIVE_MIN_VARIO, _NEGATIVE_MAX_VARIO

N = round(2 * GRID_RADIUS / GRID_RES) + 1          # 201
GLIDER = min(SOARING_AC_TYPES)
T_ISSUE = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
TGT = T_ISSUE + timedelta(minutes=120)


# --- /predict fixes -----------------------------------------------------------

def test_live_fusion_weight_fades_and_stops():
    assert main.live_fusion_weight(0) == pytest.approx(1.0)
    w15, w30 = main.live_fusion_weight(0.25), main.live_fusion_weight(0.5)
    assert 1.0 > w15 > w30 > 0
    assert main.live_fusion_weight(1.0) == 0.0
    assert main.live_fusion_weight(6.0) == 0.0


def test_fusion_at_zero_weight_leaves_the_map_alone():
    heat = [0.2] * (N * N)
    glider = {"lat": 0.0, "lon": 0.0, "circling": True, "vario": 3.0,
              "ac_type": GLIDER, "under_tow": False}
    assert main._apply_ogn_fusion(heat, [glider], 0.0, 0.0, GRID_RADIUS, N, N,
                                  weight=0.0) == heat
    fused = main._apply_ogn_fusion(heat, [glider], 0.0, 0.0, GRID_RADIUS, N, N)
    assert max(fused) > 0.2


_REAL = {"temp_2m": 24.0, "humidity": 0.4, "wind_speed": 6.0, "wind_dir": 270.0,
         "cape": 400.0, "cin": 5.0, "solar_ghi": 650.0, "temp_850": 12.0,
         "temp_500": -14.0, "lapse_rate": 7.5, "cape_base": 2200,
         "pbl_height": 1800.0, "soil_temp": 26.0}


def _patch_predict(monkeypatch, meteo, gliders=()):
    async def fake_meteo(lat, lon, forecast_h):
        return dict(meteo)

    async def elev(lat, lon):
        return np.full((N, N), 500.0)

    async def live():
        return list(gliders)

    class _Model:
        def predict(self, features):
            n = features.shape[0]
            return np.full(n, 0.3), np.full(n, 0.05)

    monkeypatch.setattr(main, "fetch_meteo_features", fake_meteo)
    monkeypatch.setattr(main, "fetch_elevation_grid", elev)
    monkeypatch.setattr(main, "fetch_ogn_gliders", live)
    monkeypatch.setattr(main, "model", _Model())


async def test_predict_flags_the_synthetic_weather(monkeypatch):
    _patch_predict(monkeypatch, mc._FALLBACK)
    assert (await main.predict(lat=39.8, lon=30.1))["meteo_fallback"] is True
    _patch_predict(monkeypatch, _REAL)
    assert (await main.predict(lat=39.8, lon=30.1))["meteo_fallback"] is False


async def test_live_thermals_reach_now_but_not_two_hours_out(monkeypatch):
    lat, lon = main.snap_grid_centre(39.8, 30.1)
    glider = {"id": "g1", "lat": lat, "lon": lon, "alt": 1500, "circling": True,
              "vario": 3.0, "ac_type": GLIDER, "under_tow": False}
    _patch_predict(monkeypatch, _REAL, [glider])
    now = await main.predict(lat=39.8, lon=30.1, forecast_h=0)
    later = await main.predict(lat=39.8, lon=30.1, forecast_h=2)
    assert max(now["heatmap"]) > 0.3
    assert max(later["heatmap"]) == pytest.approx(0.3)
    assert later["live_fusion_weight"] == 0.0


# --- ledger fixtures ----------------------------------------------------------

def _prediction(hot_corner=True, fallback=False, horizon_min=120):
    """A served map whose north-east quarter is hot and everything else cold."""
    heat = np.full((N, N), 0.1)
    if hot_corner:
        heat[N // 2:, N // 2:] = 0.8
    return {
        "issued_at": T_ISSUE,
        "target_at": T_ISSUE + timedelta(minutes=horizon_min),
        "forecast_h": horizon_min / 60,
        "grid_lat": 46.0, "grid_lon": 10.0, "radius": GRID_RADIUS,
        "rows": N, "cols": N,
        "terrain": np.full((N, N), 500.0), "terrain_mean": 500.0,
        "alt_amsl": 1500.0, "alt_source": "default",
        "meteo": mc._FALLBACK.copy() if fallback else dict(_REAL),
        "meteo_fallback": fallback,
        "heatmap": heat.ravel(), "model_heatmap": heat.ravel(),
        "heatmap_std": np.zeros(N * N),
        "fusion_weight": 0.0, "no_insolation": False,
    }


def _record(con, p, horizon_min=120, thermals=()):
    return ledger.record_forecast(con, "test", 46.0, 10.0, horizon_min, p,
                                  np.full(N * N, 0.5), "v1", list(thermals), GRID_RES)


def _beacon_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL, alt REAL,
                   vario REAL, circling INTEGER, is_tow INTEGER, ac_type INTEGER,
                   under_tow INTEGER)""")
    con.executemany("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _b(t, gid, lat, lon, vario, circling, alt=1500.0):
    return (t.isoformat(timespec="microseconds"), gid, lat, lon, alt, vario,
            int(circling), 0, GLIDER, 0)


def _early(t):
    """A beacon long before the window, so the hot DB is known to cover it."""
    return _b(t - timedelta(hours=3), "early", 0.0, 0.0, 0.0, False)


NE = (46.0 + 0.03, 10.0 + 0.03)    # inside the hot quarter
SW = (46.0 - 0.03, 10.0 - 0.03)    # cold


def _score_all(lpath, bpath, now):
    return ledger.score_due(lpath, bpath, SOARING_AC_TYPES, _label_for,
                            _POSITIVE_MIN_VARIO, _NEGATIVE_MAX_VARIO, now=now)


def _score(tmp_path, beacons, p=None, now=None):
    lpath, bpath = tmp_path / "ledger.db", tmp_path / "beacons.db"
    con = ledger.connect(lpath)
    fid = _record(con, p or _prediction())
    con.close()
    _beacon_db(bpath, beacons)
    res = _score_all(lpath, bpath, now or TGT + timedelta(minutes=30))
    return res, ledger.forecast_detail(lpath, fid)


# --- ledger behaviour ---------------------------------------------------------

def test_forecasts_cannot_be_edited_or_deleted(tmp_path):
    con = ledger.connect(tmp_path / "ledger.db")
    fid = _record(con, _prediction())
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        con.execute("UPDATE forecasts SET peak = 1.0 WHERE id = ?", (fid,))
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        con.execute("DELETE FROM forecasts WHERE id = ?", (fid,))
    con.close()


def test_climbs_in_the_hotspot_support_the_forecast(tmp_path):
    beacons = [_early(TGT)] + [
        _b(TGT + timedelta(seconds=s), "g1", NE[0], NE[1], 2.5, True) for s in range(0, 120, 4)
    ]
    _, d = _score(tmp_path, beacons)
    assert d["outcome"]["status"] == "supported"
    m = d["outcome"]["metrics"]
    # thirty reports from one glider in one cell are one unit, not thirty
    assert m["n_pos"] == 1 and m["pos_hot"] == 1


def test_climbs_outside_the_hotspot_contradict_it(tmp_path):
    _, d = _score(tmp_path, [_early(TGT), _b(TGT, "g1", SW[0], SW[1], 2.5, True)])
    assert d["outcome"]["status"] == "contradicted"
    assert d["outcome"]["reason"] == "climbs_outside_hotspots"


def test_sink_inside_the_hotspot_contradicts_it(tmp_path):
    beacons = [_early(TGT),
               _b(TGT, "g1", NE[0], NE[1], -1.5, False),
               _b(TGT, "g2", NE[0] + 0.01, NE[1] + 0.01, -1.2, False)]
    _, d = _score(tmp_path, beacons)
    assert d["outcome"]["status"] == "contradicted"
    assert d["outcome"]["reason"] == "sink_in_hotspots"


def test_nobody_flying_is_unverifiable_not_wrong(tmp_path):
    # the feed is alive, just not over this site
    _, d = _score(tmp_path, [_early(TGT), _b(TGT, "far", 10.0, 10.0, 2.5, True)])
    assert d["outcome"]["status"] == "unverifiable"


def test_beacons_outside_the_window_or_too_low_do_not_count(tmp_path):
    beacons = [_early(TGT),
               _b(TGT + timedelta(minutes=25), "late", NE[0], NE[1], 2.5, True),
               _b(TGT, "low", NE[0], NE[1], 2.5, True, alt=600.0)]   # 100 m AGL
    _, d = _score(tmp_path, beacons)
    assert d["outcome"]["status"] == "unverifiable"


def test_synthetic_weather_is_excluded(tmp_path):
    beacons = [_early(TGT), _b(TGT, "g1", NE[0], NE[1], 2.5, True)]
    _, d = _score(tmp_path, beacons, p=_prediction(fallback=True))
    assert (d["outcome"]["status"], d["outcome"]["reason"]) == ("excluded", "meteo_fallback")


def test_a_feed_outage_is_excluded(tmp_path):
    _, d = _score(tmp_path, [_early(TGT)])
    assert (d["outcome"]["status"], d["outcome"]["reason"]) == ("excluded", "feed_outage")


def test_a_window_the_hot_db_no_longer_holds_is_excluded(tmp_path):
    _, d = _score(tmp_path, [_b(TGT + timedelta(hours=5), "g1", NE[0], NE[1], 2.5, True)])
    assert d["outcome"]["reason"] == "no_observation_data"


def test_nothing_is_scored_before_its_window_closes(tmp_path):
    res, d = _score(tmp_path, [_early(TGT)], now=TGT + timedelta(minutes=5))
    assert res["scored"] == 0 and d["outcome"] is None


def test_summary_reports_hit_rate_against_chance(tmp_path):
    lpath, bpath = tmp_path / "ledger.db", tmp_path / "beacons.db"
    con = ledger.connect(lpath)
    for _ in range(3):
        _record(con, _prediction())
    con.close()
    _beacon_db(bpath, [_early(TGT),
                       _b(TGT, "g1", NE[0], NE[1], 2.5, True),
                       _b(TGT, "g2", SW[0], SW[1], -1.5, False)])
    _score_all(lpath, bpath, TGT + timedelta(minutes=30))
    s = ledger.horizon_summary(lpath, days=3650, boot=200, now=TGT)
    row = s["horizons"][0]
    assert row["horizon_min"] == 120 and row["issued"] == 3
    assert row["status"] == {"supported": 3}
    assert row["hit_rate"]["value"] == 1.0 and row["hit_rate"]["chance"] == ledger.HOTSPOT_FRAC
    assert row["within_served"]["value"] == 1.0
    # no thermals at issue time: persistence cannot separate anything
    assert row["within_persistence"]["value"] == 0.5


def test_examples_carry_their_denominator(tmp_path):
    _score(tmp_path, [_early(TGT), _b(TGT, "g1", NE[0], NE[1], 2.5, True)])
    ex = ledger.examples(tmp_path / "ledger.db", "supported")
    assert len(ex["items"]) == 1 and ex["issued"] == 1
    assert ex["scored_by_status"] == {"supported": 1}


def test_prune_keeps_the_grids_of_forecasts_that_came_true(tmp_path):
    lpath, bpath = tmp_path / "ledger.db", tmp_path / "beacons.db"
    con = ledger.connect(lpath)
    good = _record(con, _prediction())
    _record(con, _prediction(hot_corner=False))
    con.close()
    _beacon_db(bpath, [_early(TGT), _b(TGT, "g1", NE[0], NE[1], 2.5, True)])
    _score_all(lpath, bpath, TGT + timedelta(minutes=30))
    con = ledger.connect(lpath)
    assert ledger.prune_grids(con, keep_days=1, now=TGT + timedelta(days=5)) == 1
    left = {r[0] for r in con.execute("SELECT forecast_id FROM grids")}
    con.close()
    assert left == {good}


def test_dynamic_sites_follow_traffic_and_skip_fixed_cells():
    def g(i, lat, lon):
        return {"id": i, "lat": lat, "lon": lon, "circling": True, "vario": 2,
                "ac_type": GLIDER, "under_tow": False}
    gliders = ([g(f"a{i}", 46.05, 10.05) for i in range(5)]
               + [g(f"b{i}", 45.55, 9.55) for i in range(3)]
               + [g("lonely", 44.05, 8.05)]
               + [g(f"f{i}", 39.85, 30.15) for i in range(9)])
    sites = ledger.choose_sites(gliders, [("Inonu", 39.81, 30.14)], k=5,
                                min_aircraft=2, evidence=is_thermal_evidence)
    names = [s[0] for s in sites]
    assert names == ["Inonu", "dyn:46.05,10.05", "dyn:45.55,9.55"]


def test_pooling_keeps_the_shape_honest():
    a = np.arange(N * N, dtype=float).reshape(N, N)
    p = ledger.pool(a)
    assert p.shape == (51, 51)
    assert p[0, 0] == pytest.approx(a[:4, :4].mean())


async def test_issue_job_records_one_forecast_per_horizon(monkeypatch):
    _patch_predict(monkeypatch, _REAL)
    monkeypatch.setattr(main, "LEDGER_FIXED_SITES", [("Inonu", 39.81, 30.14)])
    monkeypatch.setattr(main, "LEDGER_HORIZONS_MIN", [15, 120])
    await main._ledger_issue_job()
    con = ledger.connect(main.LEDGER_DB_PATH)
    rows = con.execute("SELECT site, horizon_min, fusion_weight FROM forecasts "
                       "ORDER BY horizon_min").fetchall()
    con.close()
    assert [(r[0], r[1]) for r in rows] == [("Inonu", 15), ("Inonu", 120)]
    assert rows[0][2] > 0 and rows[1][2] == 0
