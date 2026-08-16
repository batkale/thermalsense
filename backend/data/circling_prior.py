"""
Per-cell climb prior built from this project's own OGN beacon history.

Every other feature describes conditions the model must translate into lift.
This one is closer to the answer itself: how often gliders have actually
climbed over a given patch of ground.  It is the strongest signal available
and costs no external API, because the beacon stream already writes it.

Two design choices carry the whole module.

**It is a rate, not a count.**  Raw circling counts mostly encode where gliders
fly at all — near airfields, along ridges, inside competition tasks — which is
also true of the negatives in the buffer, since every buffer row is an
observed glider.  The ratio of climbing beacons to all beacons in a cell
divides that traffic term out and leaves the part that is about the ground.

**It is strictly lagged.**  The label for a sample is "this glider was
circling here at time T", and the beacon that produced that label is in the
same database.  Any prior that can see it is reading the answer.  So the
cutoff is enforced structurally, in the query, rather than by convention: a
sample at T may only see aggregates whose hour bucket *ended* at or before
T - LAG_HOURS.  See test_circling_prior.py, which asserts that a cell's own
beacons cannot move its prior.

Sparse cells are shrunk toward the global rate rather than trusted: one
beacon that happened to be circling would otherwise read as a certainty.
"""

import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np

log = logging.getLogger(__name__)

# Cell size for the prior, in degrees.  Matches TERRAIN_RES (~1.1 km) so a
# prior cell lines up with a terrain lattice point instead of straddling
# several.  Finer would leave most cells with too few beacons to mean anything.
PRIOR_RES = 0.01

# Minimum gap between a sample and the newest history it may use.  A thermal
# persists for tens of minutes and a glider may work the same one for several
# climbs, so anything shorter risks the prior seeing the very circling that
# produced the label.  24 h also matches how a pilot would actually use this:
# "this field worked yesterday".
LAG_HOURS = 24

# Pseudo-counts for empirical-Bayes shrinkage toward the global climb rate.
# A cell needs roughly this many beacons before its own rate dominates the
# prior.  Set from typical occupancy: at ~1 beacon/4 s a glider crossing a
# 1 km cell contributes tens of beacons, so 50 means "a couple of visits".
SMOOTHING = 50.0

# Value used where no lagged history exists at all.  NaN rather than 0.0 or the
# global mean: XGBoost learns a default branch for missing values, so "unknown"
# stays distinguishable from "known to be poor".  Encoding it as 0.0 would
# claim every unvisited cell is a confirmed dead zone.
NO_DATA = float("nan")

_TABLE = "prior_cells"


def _cell(value: float) -> int:
    """Cell index for a coordinate. floor, so negative coordinates bucket correctly."""
    return int(math.floor(value / PRIOR_RES))


def _hour_key(dt: datetime) -> str:
    """Hour bucket label, UTC. Sorts lexicographically in time order."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def build_index(db_path, soaring_types: frozenset | None = None) -> dict:
    """
    Materialise hourly per-cell beacon aggregates into `prior_cells`.

    Aggregating hourly rather than daily is what lets the lag be enforced as a
    real elapsed-time cutoff.  Daily buckets would only guarantee "an earlier
    calendar day", which at 00:30 UTC is a gap of thirty minutes — well inside
    the life of a single thermal.

    Only untowed soaring aircraft are counted, matching how labels are derived.
    A tug climbing at 3 m/s under power is not evidence of lift, and neither is
    the glider on the other end of its rope.
    """
    if soaring_types is None:
        from data.ogn_client import SOARING_AC_TYPES
        soaring_types = SOARING_AC_TYPES

    placeholders = ",".join("?" * len(soaring_types))
    with sqlite3.connect(db_path) as con:
        cols = {r[1] for r in con.execute("PRAGMA table_info(beacons)")}
        if not cols:
            return {"error": "no beacons table", "cells": 0}
        # Rows predating aircraft-type and aerotow detection cannot be
        # classified, so they are excluded rather than assumed to be gliders.
        filters = ["ac_type IS NOT NULL"]
        params: list = []
        if "ac_type" in cols:
            filters = [f"ac_type IN ({placeholders})"]
            params = sorted(soaring_types)
        if "under_tow" in cols:
            filters.append("under_tow = 0")
        where = " AND ".join(filters)

        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                hour     TEXT    NOT NULL,
                lat_cell INTEGER NOT NULL,
                lon_cell INTEGER NOT NULL,
                climbs   INTEGER NOT NULL,
                total    INTEGER NOT NULL,
                PRIMARY KEY (hour, lat_cell, lon_cell)
            )
        """)
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_prior_hour ON {_TABLE}(hour)")
        con.execute(f"DELETE FROM {_TABLE}")
        con.execute(f"""
            INSERT INTO {_TABLE} (hour, lat_cell, lon_cell, climbs, total)
            SELECT substr(ts, 1, 13),
                   CAST(FLOOR(lat / ?) AS INTEGER),
                   CAST(FLOOR(lon / ?) AS INTEGER),
                   SUM(CASE WHEN circling = 1 AND vario > 1.5 THEN 1 ELSE 0 END),
                   COUNT(*)
            FROM beacons
            WHERE {where}
            GROUP BY 1, 2, 3
        """, (PRIOR_RES, PRIOR_RES, *params))
        con.commit()
        n_cells, n_hours = con.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT hour) FROM {_TABLE}"
        ).fetchone()
        climbs, total = con.execute(
            f"SELECT SUM(climbs), SUM(total) FROM {_TABLE}"
        ).fetchone()

    log.info(f"[prior] indexed {n_cells} (hour, cell) buckets over {n_hours} hours")
    return {
        "buckets": n_cells,
        "hours": n_hours,
        "climbs": climbs or 0,
        "beacons": total or 0,
        "global_rate": (climbs / total) if total else 0.0,
    }


class ClimbPrior:
    """
    Lagged per-cell climb rates, cached by cutoff hour.

    Cutoffs are served in ascending order from a single accumulating pass, so
    scoring a whole training buffer costs one sweep of the aggregate table
    rather than one query per row.
    """

    def __init__(self, db_path, lag_hours: int = LAG_HOURS,
                 smoothing: float = SMOOTHING):
        self.db_path = db_path
        self.lag_hours = lag_hours
        self.smoothing = smoothing
        self._buckets: list[tuple[str, int, int, int, int]] | None = None
        self._cache: dict[str, tuple[dict, float]] = {}

    def _load(self) -> list:
        if self._buckets is None:
            with sqlite3.connect(self.db_path) as con:
                try:
                    self._buckets = con.execute(
                        f"SELECT hour, lat_cell, lon_cell, climbs, total "
                        f"FROM {_TABLE} ORDER BY hour"
                    ).fetchall()
                except sqlite3.OperationalError:
                    log.warning("[prior] no prior_cells table — run build_index first")
                    self._buckets = []
        return self._buckets

    def _state_before(self, cutoff: str) -> tuple[dict, float]:
        """
        Accumulated (climbs, total) per cell over every bucket strictly before
        `cutoff`, plus the global rate over that same window.

        The global rate is recomputed per cutoff rather than taken once over
        all data, because it is the shrinkage target: using a rate that
        includes the future would leak, quietly and across every sparse cell.
        """
        if cutoff in self._cache:
            return self._cache[cutoff]
        cells: dict[tuple[int, int], list[int]] = {}
        climbs = total = 0
        for hour, la, lo, c, t in self._load():
            if hour >= cutoff:
                break                       # ordered by hour, so nothing later qualifies
            entry = cells.setdefault((la, lo), [0, 0])
            entry[0] += c
            entry[1] += t
            climbs += c
            total += t
        state = (cells, (climbs / total) if total else 0.0)
        self._cache[cutoff] = state
        return state

    def cutoff_for(self, when: datetime) -> str:
        """Newest hour bucket a sample at `when` is allowed to see."""
        return _hour_key(when - timedelta(hours=self.lag_hours))

    def value(self, lat: float, lon: float, when: datetime) -> float:
        """Shrunk climb rate for one point, or NO_DATA when nothing precedes it."""
        cells, global_rate = self._state_before(self.cutoff_for(when))
        if not cells:
            return NO_DATA
        c, t = cells.get((_cell(lat), _cell(lon)), (0, 0))
        if t == 0:
            # The cell itself is unseen but the window is populated, so the
            # global rate is a real estimate rather than a fabrication.
            return global_rate
        return (c + self.smoothing * global_rate) / (t + self.smoothing)

    def values(self, lats, lons, whens) -> np.ndarray:
        """Vectorised `value` over parallel sequences."""
        out = np.empty(len(lats), dtype=float)
        for i, (la, lo, w) in enumerate(zip(lats, lons, whens)):
            out[i] = self.value(float(la), float(lo), w)
        return out

    def grid(self, lat_bounds, lon_bounds, shape, when: datetime) -> np.ndarray:
        """
        Prior over a prediction grid, matching build_feature_matrix's layout:
        row index increases north, column index increases east.
        """
        rows, cols = shape
        lats = np.linspace(lat_bounds[0], lat_bounds[1], rows)
        lons = np.linspace(lon_bounds[0], lon_bounds[1], cols)
        cells, global_rate = self._state_before(self.cutoff_for(when))
        if not cells:
            return np.full(shape, NO_DATA)

        out = np.empty(shape, dtype=float)
        # Cell indices repeat heavily across a 200x200 grid spanning ~0.1deg
        # (about 11 distinct cells per axis), so resolve each one once.
        row_cells = [_cell(v) for v in lats]
        col_cells = [_cell(v) for v in lons]
        lookup: dict[tuple[int, int], float] = {}
        for i, rc in enumerate(row_cells):
            for j, cc in enumerate(col_cells):
                key = (rc, cc)
                val = lookup.get(key)
                if val is None:
                    c, t = cells.get(key, (0, 0))
                    val = (global_rate if t == 0
                           else (c + self.smoothing * global_rate) / (t + self.smoothing))
                    lookup[key] = val
                out[i, j] = val
        return out
