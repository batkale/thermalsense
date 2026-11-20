"""
Daily score history: the model as it stood before a day, scored on that day.

What has to hold for the history to mean anything:
  * a day is scored only by copies that have seen none of it — the copy taken
    before it, and older ones — never by the model serving when it is scored;
  * only that day's rows are scored, at the AGL floor the model trains at;
  * a copy that may have trained on the same day-of-year (a buffer older than
    a year) refuses rather than reporting a score it was handed;
  * the paired delta against an older copy is current − older, on the same rows.
"""

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
import xgboost as xgb

from models import score_history as sh
from pipeline.feature_engineering import FEATURE_COUNT, _cyclic

D = date(2026, 11, 19)


def _rows(day, n=600, seed=0, signal=True, agl=500.0):
    """Rows built on `day`: three 0.5° cells, flying hours, labels on column 5."""
    rng = np.random.default_rng(seed)
    X = rng.random((n, FEATURE_COUNT))
    X[:, 17], X[:, 18] = _cyclic(day.timetuple().tm_yday, 365)
    X[:, 15], X[:, 16] = _cyclic(rng.integers(9, 17, n), 24)
    X[:, 0] = 46.0 + rng.integers(0, 3, n) * 0.5
    X[:, 1] = 10.0
    X[:, 21] = agl
    if signal:
        y = (rng.random(n) < 1 / (1 + np.exp(-(X[:, 5] - 0.5) * 12))).astype(int)
    else:
        y = rng.integers(0, 2, n)
    return list(X), list(y)


def _fit(rows, labels):
    clf = xgb.XGBClassifier(n_estimators=40, max_depth=3, n_jobs=1, random_state=0)
    clf.fit(np.array(rows), np.array(labels))
    return clf


def _score(clf, X):
    return clf.predict_proba(X[:, :clf.n_features_in_])[:, 1]


def _terrain(X):
    return X[:, 3]                    # an uninformative column: chance


@pytest.fixture
def snaps(tmp_path):
    return tmp_path / "daily"


def test_rows_carry_the_key_of_the_day_they_were_built_on():
    from pipeline.feature_engineering import build_feature_matrix
    meteo = {"temp_2m": 20.0, "humidity": 0.5, "wind_speed": 3.0, "wind_dir": 0.0,
             "cape": 800.0, "cin": 5.0, "solar_ghi": 700.0, "temp_850": 10.0,
             "temp_500": -12.0, "lapse_rate": 6.3, "cape_base": 3000,
             "pbl_height": 1500.0, "soil_temp": 22.0}
    for day in (D, date(2026, 1, 1), date(2026, 12, 31)):
        row = build_feature_matrix(meteo, np.full((3, 3), 800.0),
                                   dt=datetime.combine(day, datetime.min.time(), timezone.utc),
                                   lat_bounds=(46.0, 46.1), lon_bounds=(11.0, 11.1),
                                   alt_amsl=2000.0)[4]
        assert sh._doy_keys([row])[0] == sh.doy_key(day)


def test_a_day_is_scored_by_the_copy_taken_before_it_against_an_older_one(snaps):
    old_rows, old_y = _rows(D - timedelta(days=9), signal=False, seed=1)
    good_rows, good_y = _rows(D - timedelta(days=2), seed=2)
    sh.take_snapshot(_fit(old_rows, old_y), old_rows, old_y, D - timedelta(days=7), snaps, 35)
    sh.take_snapshot(_fit(good_rows, good_y), good_rows, good_y, D, snaps, 35)

    day_rows, day_y = _rows(D, seed=3)
    other_rows, other_y = _rows(D - timedelta(days=1), seed=4)
    rec = sh.score_day(day_rows + other_rows, day_y + other_y, D, snaps, _score, _terrain,
                       compare_days=(7, 28), n_boot=300)

    assert rec["reason"] is None and rec["rows"] == 600, "only the day's own rows"
    assert rec["model"]["score"] > 0.7 and rec["model"]["train_rows"] == 600
    assert abs(rec["terrain"]["score"] - 0.5) < 0.1
    assert [c["days_older"] for c in rec["compare"]] == [7], "no 28-day copy exists"
    week = rec["compare"][0]
    assert week["delta"]["point"] > 0 and week["delta"]["lo"] > 0, "current − older, paired"
    assert week["delta"]["point"] == pytest.approx(rec["model"]["score"] - week["score"], abs=1e-3)


def test_rows_below_the_agl_floor_are_not_judged(snaps):
    rows, y = _rows(D - timedelta(days=2), seed=2)
    sh.take_snapshot(_fit(rows, y), rows, y, D, snaps, 35)
    high, yh = _rows(D, n=300, seed=3)
    low, yl = _rows(D, n=300, seed=4, agl=50.0)
    rec = sh.score_day(high + low, yh + yl, D, snaps, _score, _terrain, n_boot=100)
    assert rec["rows"] == 300


def test_a_copy_that_may_have_seen_the_day_of_year_refuses(snaps):
    """A buffer older than a year can hold this day-of-year from last year."""
    last_year, y1 = _rows(date(2025, 11, 19), seed=5)
    rows, y = _rows(D - timedelta(days=2), seed=2)
    sh.take_snapshot(_fit(rows + last_year, y + y1), rows + last_year, y + y1, D, snaps, 35)
    day_rows, day_y = _rows(D, seed=3)
    rec = sh.score_day(day_rows, day_y, D, snaps, _score, _terrain, n_boot=100)
    assert rec["reason"] == "seen_in_training" and rec["model"] is None


def test_a_day_without_a_copy_or_rows_says_so(snaps):
    rows, y = _rows(D, seed=3)
    assert sh.score_day(rows, y, D, snaps, _score, _terrain)["reason"] == "no_snapshot"
    train, ty = _rows(D - timedelta(days=2), seed=2)
    sh.take_snapshot(_fit(train, ty), train, ty, D, snaps, 35)
    assert sh.score_day(train, ty, D, snaps, _score, _terrain)["reason"] == "no_rows"


def test_run_daily_catches_up_once_and_carries_the_ledger(snaps, tmp_path):
    hist = tmp_path / "score_history.jsonl"
    train, ty = _rows(D - timedelta(days=5), seed=2)
    clf = _fit(train, ty)
    for day in (D - timedelta(days=1), D):
        sh.take_snapshot(clf, train, ty, day, snaps, 35)
    r1, y1 = _rows(D - timedelta(days=1), seed=6)
    r2, y2 = _rows(D, seed=7)
    today = D + timedelta(days=1)

    def ledger_fn(day):
        if day == D:
            raise RuntimeError("ledger locked")
        return [{"horizon_min": 15}]

    out = sh.run_daily(r1 + r2, y1 + y2, today, snaps, hist, _score, _terrain,
                       compare_days=(7,), ledger_fn=ledger_fn)
    assert [r["day"] for r in out] == [str(D - timedelta(days=1)), str(D)], "oldest first"
    assert out[0]["ledger"] == [{"horizon_min": 15}]
    assert out[1]["ledger"] is None and "ledger locked" in out[1]["ledger_error"]
    assert sh.run_daily(r1 + r2, y1 + y2, today, snaps, hist, _score, _terrain) == [], \
        "a recorded day is not scored twice"
    assert [r["day"] for r in sh.read_history(hist)] == [str(D), str(D - timedelta(days=1))]


def test_history_keeps_the_last_record_per_day_and_survives_a_torn_line(tmp_path):
    hist = tmp_path / "h.jsonl"
    sh.append_record(hist, {"day": "2026-11-18", "rows": 1})
    sh.append_record(hist, {"day": "2026-11-19", "rows": 2})
    sh.append_record(hist, {"day": "2026-11-18", "rows": 3, "score": float("nan")})
    with open(hist, "a", encoding="utf-8") as f:
        f.write('{"day": "2026-11-20", "ro')          # a crash mid-write
    out = sh.read_history(hist)
    assert [(r["day"], r["rows"]) for r in out] == [("2026-11-19", 2), ("2026-11-18", 3)]
    assert out[1]["score"] is None
    assert "NaN" not in hist.read_text(encoding="utf-8")
    assert sh.read_history(hist, limit=1)[0]["day"] == "2026-11-19"


def test_old_copies_are_pruned(snaps):
    rows, y = _rows(D - timedelta(days=2), seed=2)
    clf = _fit(rows, y)
    for back in (40, 36, 35, 1, 0):
        sh.take_snapshot(clf, rows, y, D - timedelta(days=back), snaps, keep_days=35)
    assert sh.snapshot_days(snaps) == [str(D - timedelta(days=b)) for b in (35, 1, 0)]
    meta = json.loads((snaps / f"{D}.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 600 and meta["days"] == 1 and meta["width"] == FEATURE_COUNT


async def test_the_snapshot_job_copies_the_serving_model_for_the_next_day(tmp_path, monkeypatch):
    import main

    snaps = tmp_path / "daily"
    monkeypatch.setattr(main, "SCORE_SNAPSHOT_DIR", snaps)
    rows, y = _rows(D - timedelta(days=2), seed=2)
    monkeypatch.setattr(main.model, "model", _fit(rows, y))
    monkeypatch.setattr(main.model, "_buffer_X", rows)
    monkeypatch.setattr(main.model, "_buffer_y", y)

    def at(h, m):
        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 18, h, m, tzinfo=timezone.utc)
        monkeypatch.setattr(main, "datetime", Fixed)

    at(0, 3)                         # past midnight: a fit may have seen the new day
    await main._score_snapshot_job()
    assert sh.snapshot_days(snaps) == []
    at(23, 55)
    await main._score_snapshot_job()
    assert sh.snapshot_days(snaps) == ["2026-11-19"]


async def test_the_history_endpoint_returns_newest_first(tmp_path, monkeypatch):
    import main

    hist = tmp_path / "score_history.jsonl"
    monkeypatch.setattr(main, "SCORE_HISTORY_PATH", hist)
    monkeypatch.setattr(main, "SCORE_SNAPSHOT_DIR", tmp_path / "daily")
    for d in ("2026-11-17", "2026-11-18", "2026-11-19"):
        sh.append_record(hist, {"day": d, "rows": 1})
    out = await main.training_history(days=2)
    assert [r["day"] for r in out["days"]] == ["2026-11-19", "2026-11-18"]
    assert out["snapshots"] == [] and out["compare_days"] == main.SCORE_COMPARE_DAYS
