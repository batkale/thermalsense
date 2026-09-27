"""
Append-only cold archive of the beacon feed, as date-partitioned Parquet.

The hot SQLite DB is a two-day cache and has to stay one: the 17 Aug 2026 outage
was a 4.26 GB database against ~165 MB of page cache putting the host at 96.6%
iowait.  But `BEACON_RETENTION_DAYS = 2` currently *destroys* the data as well as
evicting it, which is why the project has no climatology, no seasonal coverage,
and a benchmark that silently changes as the buffer rolls.

Splitting the two fixes both.  SQLite keeps serving the last two days; every
completed day is written here first and kept forever.

Measured on this project's own feed: 243.5 bytes/row in SQLite (with its WAL)
against 10.2 bytes/row in Parquet+zstd — 24x, or ~7 GB for three months of the
worldwide feed against ~169 GB.  Beacon data compresses this well because a
columnar layout can exploit what it is: monotonic timestamps, a small repeating
set of aircraft ids, and coordinates that barely move between rows.

Two properties matter as much as the size:

  * **Immutable files.** Deleting from SQLite reclaims nothing on a database
    created before `auto_vacuum=INCREMENTAL`, which is a trap this deployment
    has already fallen into.  A finished Parquet file is never rewritten.
  * **Queryable in place.** Partition pruning and column projection mean a
    climatology aggregate reads only the columns and days it needs, so the
    archive does not have to be loaded to be used.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Written once per UTC day as archive/beacons/date=YYYY-MM-DD/part.parquet.
# Hive-style so pyarrow.dataset reads the partition key from the path and can
# skip whole days without opening them.
_PART_NAME = "part.parquet"

# zstd at level 9 rather than the default 3: measured 0.86 MB vs 0.90 MB on the
# same rows, and this file is written once and read for years, so the write cost
# is paid a single time.  Nothing here is latency-sensitive — the export runs
# nightly on data that is already complete.
_COMPRESSION = "zstd"
_COMPRESSION_LEVEL = 9

# Rows pulled from SQLite per batch.  The export must stream: a day of the
# worldwide feed is ~7.7M rows and the deployment target has 896 MB of RAM, so
# materialising a day at once is exactly the kind of thing that took the box
# down before.
# Rows pulled out of SQLite per Parquet write.  The export was always streamed
# — ParquetWriter appends batch by batch — but the batch itself was the problem:
# 200,000 rows of 10-column tuples is 100-140 MB of CPython objects, held while
# the Arrow table built from them is also live, so one cycle peaked near 200 MB
# against a 500 MB container.  That is what OOM-killed the app hourly between
# 24 and 27 Sep 2026, each kill leaving another .part behind.  20,000 rows is
# ~12 MB and costs only a few more writes per day.
_BATCH = 20_000

_SCHEMA_FIELDS = [
    ("ts", "timestamp"), ("id", "string"), ("lat", "float32"),
    ("lon", "float32"), ("alt", "float32"), ("vario", "float32"),
    ("circling", "int8"), ("is_tow", "int8"), ("ac_type", "int16"),
    ("under_tow", "int8"),
]


def _pa():
    """
    Import pyarrow lazily.

    The archive is optional: an install without pyarrow must still run the app,
    just without cold storage.  Importing at module scope would make a missing
    optional dependency a startup failure for the whole service.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    return pa, pq


def available() -> bool:
    try:
        _pa()
        return True
    except Exception:
        return False


def _schema():
    pa, _ = _pa()
    types = {
        "timestamp": pa.timestamp("us", tz="UTC"), "string": pa.string(),
        "float32": pa.float32(), "int8": pa.int8(), "int16": pa.int16(),
    }
    return pa.schema([(n, types[t]) for n, t in _SCHEMA_FIELDS])


def day_path(root: Path, day: date) -> Path:
    return Path(root) / f"date={day.isoformat()}" / _PART_NAME


def archived_days(root: Path) -> set[date]:
    """Days already written. Used to make the export idempotent and the purge safe."""
    root = Path(root)
    if not root.exists():
        return set()
    out = set()
    for d in root.iterdir():
        if d.is_dir() and d.name.startswith("date=") and (d / _PART_NAME).exists():
            try:
                out.add(date.fromisoformat(d.name[5:]))
            except ValueError:
                continue
    return out


def _rows_for_day(con: sqlite3.Connection, day: date):
    """Yield SQLite batches for one UTC day, oldest first."""
    lo = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    hi = (datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
          + timedelta(days=1)).isoformat()
    # idx_id_ts covers ORDER BY id, ts, so SQLite streams this rather than
    # materialising the day — that was checked, not assumed.  These two pragmas
    # are a floor under the case where it cannot: a planner choosing a different
    # path, or a DB restored without its indexes.  There are tens of GB of disk
    # and ~300 MB of RAM to spare, so the scarce resource is the one to spend
    # last, and the cost when the index is used is nil.
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-8000")        # 8 MB, not the 2 GB default share
    cur = con.execute(
        "SELECT ts, id, lat, lon, alt, vario, circling, is_tow, ac_type, under_tow "
        "FROM beacons WHERE ts >= ? AND ts < ? ORDER BY id, ts",
        (lo, hi),
    )
    while True:
        rows = cur.fetchmany(_BATCH)
        if not rows:
            return
        yield rows


def _to_table(rows):
    pa, _ = _pa()
    cols = list(zip(*rows))
    names = [n for n, _ in _SCHEMA_FIELDS]
    data = {}
    for name, col in zip(names, cols):
        if name == "ts":
            data[name] = [datetime.fromisoformat(v) if isinstance(v, str) else v
                          for v in col]
        else:
            data[name] = list(col)
    return pa.Table.from_pydict(data, schema=_schema())


def export_day(db_path: Path, root: Path, day: date, overwrite: bool = False) -> dict:
    """
    Write one complete UTC day to the archive.  Idempotent.

    Sorted by (id, ts) before writing: consecutive rows are then the same
    aircraft moving a few metres, which is what makes the delta and dictionary
    encodings pay.  The sort happens in SQLite rather than in memory.

    The file is built under a temporary name and renamed into place, so a crash
    mid-write cannot leave a truncated part that later looks archived — the
    purge trusts this file's existence before deleting the source rows.
    """
    _, pq = _pa()
    dest = day_path(root, day)
    if dest.exists() and not overwrite:
        return {"day": day.isoformat(), "skipped": "already archived", "rows": 0}

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
    os.close(tmp_fd)
    tmp = Path(tmp_name)

    rows_written = 0
    writer = None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as con:
            for batch in _rows_for_day(con, day):
                table = _to_table(batch)
                if writer is None:
                    writer = pq.ParquetWriter(
                        tmp, table.schema, compression=_COMPRESSION,
                        compression_level=_COMPRESSION_LEVEL,
                    )
                writer.write_table(table)
                rows_written += len(batch)
    except Exception:
        if writer is not None:
            writer.close()
        tmp.unlink(missing_ok=True)
        raise
    if writer is not None:
        writer.close()

    if rows_written == 0:
        tmp.unlink(missing_ok=True)
        return {"day": day.isoformat(), "skipped": "no rows", "rows": 0}

    os.replace(tmp, dest)
    size = dest.stat().st_size
    return {
        "day": day.isoformat(),
        "rows": rows_written,
        "bytes": size,
        "bytes_per_row": round(size / rows_written, 1),
    }


def export_completed_days(db_path: Path, root: Path, today: date | None = None) -> list[dict]:
    """
    Archive every finished day still present in SQLite.

    Today is deliberately excluded: it is still being written, and a partial file
    would be indistinguishable from a complete one afterwards — and the purge
    treats an archived day as safe to delete.
    """
    today = today or datetime.now(timezone.utc).date()
    if not Path(db_path).exists():
        return []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as con:
        try:
            days = [r[0] for r in con.execute(
                "SELECT DISTINCT substr(ts, 1, 10) FROM beacons ORDER BY 1")]
        except sqlite3.Error:
            return []
    done = archived_days(root)
    out = []
    for s in days:
        try:
            d = date.fromisoformat(s)
        except ValueError:
            continue
        if d >= today or d in done:
            continue
        out.append(export_day(db_path, root, d))
    return out


def safe_purge_cutoff(root: Path, requested_cutoff: datetime,
                      db_first_day: date | None = None) -> datetime:
    """
    Pull a purge cutoff back to the start of the oldest unarchived day.

    Without this the purge and the export are two independent jobs racing over
    the same rows: one failed export followed by a successful purge deletes a
    day that exists nowhere else, permanently and silently.  Archiving is the
    whole reason retention is allowed to be as short as it is, so the delete has
    to be subordinate to it.

    `db_first_day` is the oldest day the hot DB still holds, and the unbroken run
    is measured **from there**, not from the oldest day the archive happens to
    contain.  Anchoring it at the archive's own start was wrong in a way that
    took six days to show: a gap on 20 Sep 2026 (the day the container was dead)
    ended the run at 19 Sep, while the DB began at 21 Sep — so the ceiling sat
    *behind* the first row that existed, every hourly purge deleted nothing, and
    the DB grew to 5.9 GB under a one-day retention setting.  A day missing from
    the archive cannot protect rows the DB does not have.

    Passing None keeps the old epoch-anchored behaviour, which is still the
    right answer when the caller cannot say what the DB holds: it errs towards
    deleting nothing.
    """
    done = archived_days(root)
    if not done:
        # Nothing is archived, so nothing may be deleted.  This is the state a
        # fresh deployment starts in and the one a broken export leaves behind;
        # both are cases where growing the DB is recoverable and deleting is not.
        return datetime.min.replace(tzinfo=timezone.utc)

    # Walk forward from the first day that is actually at risk, so a hole cannot
    # be stepped over but a hole in days already gone cannot freeze the purge.
    day = min(done) if db_first_day is None else db_first_day
    if day not in done:
        # The oldest day still in the DB is itself unarchived — nothing may go.
        return datetime.min.replace(tzinfo=timezone.utc)
    while day in done:
        day += timedelta(days=1)
    floor = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    return min(requested_cutoff, floor)


def scan(root: Path, columns: list[str] | None = None,
         since: date | None = None, until: date | None = None,
         bbox: tuple[float, float, float, float] | None = None):
    """
    Read archived beacons as a pyarrow Table.

    columns/since/until/bbox all push down: a day outside the range is never
    opened, and an unrequested column is never decompressed.  That is what makes
    a climatology over months affordable on this box.

    bbox is (lat_min, lat_max, lon_min, lon_max).
    """
    pa, _ = _pa()
    import pyarrow.dataset as ds

    root = Path(root)
    if not root.exists():
        return pa.table({})
    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")

    filt = None
    if since is not None:
        filt = ds.field("date") >= since.isoformat()
    if until is not None:
        f = ds.field("date") <= until.isoformat()
        filt = f if filt is None else filt & f
    if bbox is not None:
        la0, la1, lo0, lo1 = bbox
        f = ((ds.field("lat") >= la0) & (ds.field("lat") <= la1)
             & (ds.field("lon") >= lo0) & (ds.field("lon") <= lo1))
        filt = f if filt is None else filt & f
    return dataset.to_table(columns=columns, filter=filt)


def training_rows(root: Path, since: date, until: date, ac_types,
                  positive_min_vario: float, negative_max_vario: float,
                  limit: int) -> list[tuple]:
    """
    Labelable beacons from the archive, shaped like the seed's SQLite query.

    Every filter pushes into the Parquet scan, so a year of archive costs only
    the days and columns asked for.  The same aircraft-type, aerotow and
    labelability conditions as the hot query — a row the labeller would throw
    away must not be paid for here either.

    Sampled with a stride rather than truncated: taking the first `limit` rows
    of a date-ordered scan would return the oldest day and nothing else, which
    is the exact failure the day-stratified buffer eviction exists to avoid.
    """
    pa, _ = _pa()
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    root = Path(root)
    if not root.exists():
        return []
    dataset = ds.dataset(str(root), format="parquet", partitioning="hive")

    labelable = (((ds.field("circling") == 1)
                  & (ds.field("vario") > positive_min_vario))
                 | ((ds.field("circling") == 0)
                    & (ds.field("vario") < negative_max_vario)))
    filt = ((ds.field("date") >= since.isoformat())
            & (ds.field("date") <= until.isoformat())
            & (ds.field("under_tow") == 0)
            & ds.field("ac_type").isin(list(ac_types))
            & labelable)

    table = dataset.to_table(
        columns=["ts", "lat", "lon", "alt", "vario", "circling"], filter=filt)
    n = table.num_rows
    if n == 0:
        return []
    if n > limit:
        stride = max(1, n // limit)
        table = table.take(pa.array(range(0, n, stride)[:limit]))

    ts = table.column("ts").to_pylist()
    return list(zip(
        [t.isoformat() if hasattr(t, "isoformat") else t for t in ts],
        table.column("lat").to_pylist(),
        table.column("lon").to_pylist(),
        table.column("alt").to_pylist(),
        table.column("vario").to_pylist(),
        table.column("circling").to_pylist(),
    ))


def stats(root: Path) -> dict:
    """Size and coverage, for /healthz and the operator."""
    days = sorted(archived_days(root))
    total = 0
    for d in days:
        p = day_path(root, d)
        if p.exists():
            total += p.stat().st_size
    return {
        "days": len(days),
        "first_day": days[0].isoformat() if days else None,
        "last_day": days[-1].isoformat() if days else None,
        "bytes": total,
    }
