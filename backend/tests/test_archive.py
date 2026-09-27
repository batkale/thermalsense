"""
Cold archive, day-stratified eviction, and the accumulating climatology index.

All three exist to undo the same design decision: that old data is disposable.
Retention at two days is right for the *hot* DB — a 4.26 GB SQLite against
~165 MB of page cache took the deployment down on 17 Aug 2026 — but it was also
destroying the only copy, which is why the project has no climatology, no
seasonal coverage, and a benchmark that moves as the buffer rolls.

The riskiest part is the interaction: export and purge are separate jobs, so a
failed export followed by a successful purge would delete a day that exists
nowhere else. Those tests come first.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from data import archive
import models.thermal_model as tm
from pipeline.feature_engineering import FEATURE_COUNT

pytestmark = pytest.mark.skipif(not archive.available(),
                                reason="pyarrow not installed")


def _db(path, days, per_day=50):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    for d in days:
        base = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
        for i in range(per_day):
            con.execute(
                "INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                ((base + timedelta(minutes=i)).isoformat(), f"AC{i % 5}",
                 47.0 + i * 1e-3, 13.0 + i * 1e-3, 1500.0,
                 2.0 if i % 3 == 0 else -1.0, 1 if i % 3 == 0 else 0,
                 0, 1, 0),
            )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_writes_one_file_per_day(tmp_path):
    db = tmp_path / "b.db"
    days = [date(2026, 8, 1), date(2026, 8, 2)]
    _db(db, days)
    root = tmp_path / "arch"

    out = archive.export_completed_days(db, root, today=date(2026, 8, 3))
    assert {r["day"] for r in out} == {"2026-08-01", "2026-08-02"}
    assert archive.archived_days(root) == set(days)
    for d in days:
        assert archive.day_path(root, d).exists()


def test_today_is_never_archived(tmp_path):
    """A part-written day would be indistinguishable from a complete one, and
    the purge treats an archived day as safe to delete."""
    db = tmp_path / "b.db"
    _db(db, [date(2026, 8, 1), date(2026, 8, 2)])
    root = tmp_path / "arch"

    archive.export_completed_days(db, root, today=date(2026, 8, 2))
    assert archive.archived_days(root) == {date(2026, 8, 1)}


def test_export_is_idempotent(tmp_path):
    db = tmp_path / "b.db"
    _db(db, [date(2026, 8, 1)])
    root = tmp_path / "arch"

    first = archive.export_day(db, root, date(2026, 8, 1))
    again = archive.export_day(db, root, date(2026, 8, 1))
    assert first["rows"] == 50
    assert again["rows"] == 0 and "already archived" in again["skipped"]


def test_archive_is_much_smaller_than_sqlite(tmp_path):
    """The whole premise. Measured on the live feed it is ~24x; the synthetic
    rows here are far more compressible, so this only pins the direction."""
    db = tmp_path / "b.db"
    _db(db, [date(2026, 8, 1)], per_day=2000)
    root = tmp_path / "arch"
    r = archive.export_day(db, root, date(2026, 8, 1))
    assert r["bytes"] < os.path.getsize(db)


# ---------------------------------------------------------------------------
# Purge must be subordinate to the archive
# ---------------------------------------------------------------------------

def test_purge_cutoff_is_held_back_for_unarchived_days(tmp_path):
    root = tmp_path / "arch"
    root.mkdir(parents=True)
    # 8-01 archived, 8-02 and 8-03 are not.
    archive.day_path(root, date(2026, 8, 1)).parent.mkdir(parents=True)
    archive.day_path(root, date(2026, 8, 1)).write_bytes(b"x")

    want = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    safe = archive.safe_purge_cutoff(root, want)
    # Must not delete into 8-02, the oldest unarchived day.
    assert safe <= datetime(2026, 8, 2, tzinfo=timezone.utc)


def test_purge_cutoff_untouched_when_the_window_is_archived(tmp_path):
    """The normal case: retention puts the cutoff inside the archived prefix,
    so the guard changes nothing."""
    root = tmp_path / "arch"
    for d in (date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    want = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
    assert archive.safe_purge_cutoff(root, want) == want


def test_purge_stops_at_the_start_of_the_unarchived_day(tmp_path):
    """A cutoff reaching into today — which is never archived — is pulled back
    to midnight rather than deleting half a day that exists only in SQLite."""
    root = tmp_path / "arch"
    for d in (date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    want = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
    assert archive.safe_purge_cutoff(root, want) == datetime(2026, 8, 4, tzinfo=timezone.utc)


def test_a_gap_blocks_the_purge_at_the_gap(tmp_path):
    """08-02 failed to export. Nothing from 08-02 onward may be deleted, even
    though 08-03 archived fine."""
    root = tmp_path / "arch"
    for d in (date(2026, 8, 1), date(2026, 8, 3)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    want = datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert archive.safe_purge_cutoff(root, want) == datetime(2026, 8, 2, tzinfo=timezone.utc)


def test_a_gap_before_the_db_starts_does_not_freeze_the_purge(tmp_path):
    """
    The 27 Sep 2026 bug, in one assertion.

    08-02 was never archived — it is the day the container was dead — but the
    hot DB now begins at 08-03, so no surviving row depends on that export.
    Anchoring the unbroken run at the archive's own start put the ceiling at
    08-02, behind the first row that existed; every hourly purge then deleted
    nothing and the DB grew to 5.9 GB under a one-day retention setting.
    """
    root = tmp_path / "arch"
    for d in (date(2026, 8, 1), date(2026, 8, 3), date(2026, 8, 4)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")

    want = datetime(2026, 8, 5, tzinfo=timezone.utc)

    # Anchored at the archive: the old behaviour, and still right when the
    # caller cannot say what the DB holds.
    assert archive.safe_purge_cutoff(root, want) == datetime(2026, 8, 2, tzinfo=timezone.utc)

    # Anchored at the DB's oldest day: 08-03 and 08-04 are both archived, so the
    # ceiling moves to 08-05 and the purge is free again.
    assert archive.safe_purge_cutoff(
        root, want, date(2026, 8, 3)) == datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_an_unarchived_oldest_day_still_blocks_everything(tmp_path):
    """The interlock's whole point survives the change: when the oldest day the
    DB holds is itself single-copy, nothing may be deleted."""
    root = tmp_path / "arch"
    for d in (date(2026, 8, 1), date(2026, 8, 4)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    want = datetime(2026, 8, 5, tzinfo=timezone.utc)
    # DB starts at 08-03, which is not archived.
    assert archive.safe_purge_cutoff(root, want, date(2026, 8, 3)) < want
    assert archive.safe_purge_cutoff(root, want, date(2026, 8, 3)).year == 1


def test_a_gap_after_the_db_start_still_blocks_at_the_gap(tmp_path):
    """Moving the anchor must not let a hole be stepped over: 08-04 is missing
    and the DB starts at 08-03, so the ceiling is 08-04, not 08-06."""
    root = tmp_path / "arch"
    for d in (date(2026, 8, 3), date(2026, 8, 5)):
        p = archive.day_path(root, d)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    want = datetime(2026, 8, 6, tzinfo=timezone.utc)
    assert archive.safe_purge_cutoff(
        root, want, date(2026, 8, 3)) == datetime(2026, 8, 4, tzinfo=timezone.utc)


def test_a_locked_chunk_does_not_abandon_the_purge(tmp_path, monkeypatch):
    """
    Measured on 27 Sep 2026: a run deleted 2.15M rows over 19 minutes, then hit
    one "database is locked" from the stream thread and dropped the remaining
    30M. On a backlog that never clears, giving up is the same outcome as the
    interlock bug that created the backlog.
    """
    import sqlite3 as sq
    import data.ogn_client as oc

    db = tmp_path / "b.db"
    con = sq.connect(db)
    con.execute("""CREATE TABLE beacons (ts TEXT, id TEXT, lat REAL, lon REAL,
                   alt REAL, vario REAL, circling INT, is_tow INT,
                   ac_type INT, under_tow INT)""")
    old = "2020-01-01T00:00:00+00:00"
    con.executemany("INSERT INTO beacons VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(old, f"A{i}", 46.0, 11.0, 1000.0, 0.0, 0, 0, 1, 0)
                     for i in range(40)])
    con.commit()
    con.close()

    monkeypatch.setattr(oc, "_DB_PATH", db)
    monkeypatch.setattr(oc, "_PURGE_STALL_SLEEP_S", 0.0)
    monkeypatch.setattr(oc, "_PURGE_CHUNK_SLEEP_S", 0.0)

    real_connect = sq.connect
    state = {"fired": False}

    class Flaky:
        """Raises 'database is locked' once, mid-run, then behaves."""
        def __init__(self, con): self._con = con
        def __enter__(self): return self
        def __exit__(self, *a): return self._con.__exit__(*a)
        def execute(self, sql, *args):
            if sql.startswith("DELETE") and not state["fired"]:
                state["fired"] = True
                raise sq.OperationalError("database is locked")
            return self._con.execute(sql, *args)
        def commit(self): return self._con.commit()

    def fake_connect(path, *a, **kw):
        con = real_connect(path, *a, **kw)
        return Flaky(con) if str(path) == str(db) else con

    monkeypatch.setattr(oc.sqlite3, "connect", fake_connect)
    # No archive, so the interlock would normally block everything — this is
    # about the retry, so take the emergency path that bypasses it.
    deleted = oc.purge_old_beacons(1, chunk=10, emergency=True)

    assert state["fired"], "the test did not exercise the lock path"
    assert deleted == 40, f"the retry must finish the backlog, got {deleted}"


def test_the_export_batch_stays_small_enough_for_the_container():
    """
    200,000 rows of 10-column tuples is 100-140 MB of CPython objects, live at
    the same time as the Arrow table built from them, which is what OOM-killed
    the app every hour between 24 and 27 Sep 2026.  This is a memory budget, so
    it is pinned rather than left to whoever next tunes throughput.
    """
    assert archive._BATCH <= 50_000, (
        f"_BATCH={archive._BATCH:,} — at ~600 B/row that is "
        f"{archive._BATCH * 600 / 1e6:.0f} MB per batch against a 500 MB container"
    )


def test_seed_skips_archive_days_the_hot_db_still_holds(tmp_path, monkeypatch):
    """
    The boundary is what SQLite still holds, not BEACON_RETENTION_DAYS.  They
    agree only while the purge is keeping up; when it is not — a failed export,
    an app that was down — every overlapping day is read twice and lands in the
    buffer twice.  Observed on real data: a 10,000-row seed became 20,000.
    """
    import asyncio
    import models.thermal_model as t

    db = tmp_path / "b.db"
    day = datetime.now(timezone.utc).date() - timedelta(days=6)
    _db(db, [day], per_day=60)
    root = tmp_path / "arch"
    archive.export_day(db, root, day)          # same day in both places

    monkeypatch.setattr("config.DB_PATH", db, raising=False)
    monkeypatch.setattr("config.ARCHIVE_DIR", root, raising=False)
    monkeypatch.setattr(t, "BUFFER_PATH", str(tmp_path / "buf.npz"))

    seen = {}

    async def no_meteo(lat, lon, dt, strict=False):
        seen["rows"] = seen.get("rows", 0) + 1
        return None                            # drop every bucket; we only count

    monkeypatch.setattr("data.meteo_client.fetch_meteo_historical", no_meteo)

    m = t.ThermalModel()
    r = asyncio.run(m.seed_from_history(days_back=10, limit=60, reset=True))
    # 60 rows exist. Reading the archive as well would scan 120.
    assert r["rows_scanned"] <= 60, f"archive double-counted: {r['rows_scanned']}"


def test_emergency_purge_bypasses_the_archive_interlock(tmp_path, monkeypatch):
    """
    The deadlock the interlock can build: a full disk makes the export fail, the
    failed export blocks the purge, and the DB grows into space that is already
    gone.  _disk_guard is the last thing between that and the 17 Aug 2026 outage,
    so it has to be able to delete unarchived rows.  Data loss is the lesser
    failure — losing the box takes the live feed with it anyway.
    """
    import data.ogn_client as oc

    db = tmp_path / "b.db"
    old = datetime.now(timezone.utc).date() - timedelta(days=10)
    _db(db, [old], per_day=200)
    monkeypatch.setattr(oc, "_DB_PATH", str(db))
    # An archive that holds nothing: the interlock would normally refuse to
    # delete anything at all.
    monkeypatch.setattr("config.ARCHIVE_DIR", tmp_path / "empty", raising=False)

    assert oc.purge_old_beacons(days=1) == 0, "normal purge should be held back"
    assert oc.purge_old_beacons(days=1, emergency=True) == 200


def test_empty_archive_blocks_the_purge_entirely(tmp_path):
    """A fresh deployment and a wholly broken export look the same here, and in
    both an oversized DB is recoverable while a deleted day is not."""
    want = datetime(2026, 8, 4, tzinfo=timezone.utc)
    assert archive.safe_purge_cutoff(tmp_path / "empty", want) < want


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------

def test_training_rows_filters_and_samples(tmp_path):
    db = tmp_path / "b.db"
    _db(db, [date(2026, 8, 1)], per_day=300)
    root = tmp_path / "arch"
    archive.export_day(db, root, date(2026, 8, 1))

    rows = archive.training_rows(
        root, date(2026, 8, 1), date(2026, 8, 1), [1],
        tm._POSITIVE_MIN_VARIO, tm._NEGATIVE_MAX_VARIO, limit=50)
    assert 0 < len(rows) <= 50
    # Every returned row must be labelable — the point of pushing the filter down.
    for _, _, _, _, vario, circling in rows:
        assert tm._label_for(bool(circling), float(vario)) is not None


# ---------------------------------------------------------------------------
# Day-stratified eviction
# ---------------------------------------------------------------------------

def _rows_for_days(day_values, per_day, positives_per_day=2, seed=0):
    rng = np.random.default_rng(seed)
    X, y = [], []
    for dv in day_values:
        for i in range(per_day):
            row = rng.random(FEATURE_COUNT)
            angle = 2 * np.pi * dv / 365
            row[17], row[18] = np.sin(angle), np.cos(angle)
            row[21] = 800.0
            X.append(row)
            y.append(1 if i < positives_per_day else 0)
    return X, y


def test_eviction_keeps_every_day_represented(monkeypatch):
    """
    The failure this replaces: keeping the newest N rows means a full buffer
    holds one stretch of weather, so the model can only know the season it is
    standing in.
    """
    monkeypatch.setattr(tm, "_MAX_BUFFER", 100)
    X, y = _rows_for_days([100, 150, 200, 250], per_day=200)
    kX, kY, note = tm._evict_to_cap(X, y)

    assert len(kX) <= 100
    days = {(round(float(r[17]), 9), round(float(r[18]), 9)) for r in kX}
    assert len(days) == 4, "an entire day was evicted"
    assert "4 days" in note


def test_eviction_prefers_positives(monkeypatch):
    """Positives are ~4% of the feed and each cost two rate-limited API calls."""
    monkeypatch.setattr(tm, "_MAX_BUFFER", 40)
    X, y = _rows_for_days([100, 200], per_day=100, positives_per_day=10)
    _, kY, _ = tm._evict_to_cap(X, y)
    assert sum(kY) == 20, "positives should survive eviction ahead of negatives"


def test_eviction_is_a_noop_below_the_cap(monkeypatch):
    monkeypatch.setattr(tm, "_MAX_BUFFER", 10_000)
    X, y = _rows_for_days([100], per_day=50)
    kX, kY, note = tm._evict_to_cap(X, y)
    assert len(kX) == 50 and note == ""


# ---------------------------------------------------------------------------
# Climatology index accumulates past retention
# ---------------------------------------------------------------------------

def test_prior_index_survives_a_purge(tmp_path):
    """
    The reason `build_index` was not enough: it truncates and re-aggregates from
    `beacons`, so the climatology could never describe more than the retention
    window.  The aggregate has to outlive the rows it came from.
    """
    from data.circling_prior import update_index

    db = str(tmp_path / "b.db")
    old, new = date(2026, 8, 1), datetime.now(timezone.utc).date() - timedelta(days=1)
    _db(db, [old, new])

    first = update_index(db, soaring_types=frozenset({1}))
    assert first["buckets"] > 0

    # Purge everything from the old day, as retention would.
    con = sqlite3.connect(db)
    con.execute("DELETE FROM beacons WHERE ts < ?", (new.isoformat(),))
    con.commit()
    con.close()

    after = update_index(db, soaring_types=frozenset({1}))
    assert after["buckets"] == first["buckets"], "purge erased indexed history"
    assert after["first_hour"].startswith("2026-08-01")


def test_prior_index_skips_the_incomplete_current_hour(tmp_path):
    """Freezing a partial count would understate every cell still being flown,
    and the `hour > max` cursor never revisits it."""
    from data.circling_prior import update_index

    db = str(tmp_path / "b.db")
    _db(db, [datetime.now(timezone.utc).date()])
    r = update_index(db, soaring_types=frozenset({1}))
    now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    assert r["last_hour"] is None or r["last_hour"] < now_hour
