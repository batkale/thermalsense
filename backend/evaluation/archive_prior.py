"""
The climb prior, built from a Parquet beacon archive and looked up fast.

data/circling_prior.py builds `prior_cells` from the hot SQLite DB and its
ClimbPrior walks every bucket once per distinct cutoff hour.  That is fine on a
training node's own DB and does not finish on a laptop holding weeks of
archive: 4M buckets times hundreds of cutoffs.  This module

  * builds the same `prior_cells` table straight from archive Parquet
    (`build`), with the same soaring/untowed filter, cell size and climb
    threshold as circling_prior.build_index, and
  * answers the same question as ClimbPrior.value with cumulative sums and a
    binary search (`FastPrior`) — same strict lag, same shrinkage, same
    NaN-before-any-history rule.  test_archive_prior pins the two together.

    python -m evaluation.archive_prior --archive <dir with date=*/part.parquet> --out prior.db
    python -m evaluation.evaluate --prior-db prior.db --variant base --variant prior --by-hour
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
from datetime import datetime, timedelta

import numpy as np

from data.circling_prior import LAG_HOURS, PRIOR_RES, SMOOTHING, _hour_key


def build(archive_dir, out_path, soaring_types=None) -> dict:
    import pandas as pd
    import pyarrow.parquet as pq
    if soaring_types is None:
        from data.ogn_client import SOARING_AC_TYPES
        soaring_types = SOARING_AC_TYPES
    if os.path.exists(out_path):
        os.remove(out_path)
    con = sqlite3.connect(out_path)
    con.execute("""CREATE TABLE prior_cells (hour TEXT NOT NULL, lat_cell INTEGER NOT NULL,
                   lon_cell INTEGER NOT NULL, climbs INTEGER NOT NULL, total INTEGER NOT NULL,
                   PRIMARY KEY (hour, lat_cell, lon_cell))""")
    soar = sorted(soaring_types)
    beacons = buckets = 0
    for f in sorted(glob.glob(os.path.join(archive_dir, "date=*", "part.parquet"))):
        t = pq.read_table(f, columns=["ts", "lat", "lon", "vario", "circling",
                                      "ac_type", "under_tow"]).to_pandas()
        t = t[t.ac_type.isin(soar) & (t.under_tow == 0)]
        g = pd.DataFrame({
            "hour": t.ts.dt.strftime("%Y-%m-%dT%H"),
            "la": np.floor(t.lat.astype(float) / PRIOR_RES).astype(int),
            "lo": np.floor(t.lon.astype(float) / PRIOR_RES).astype(int),
            "c": ((t.circling == 1) & (t.vario > 1.5)).astype(int),
        }).groupby(["hour", "la", "lo"]).c.agg(["sum", "size"]).reset_index()
        con.executemany("INSERT OR REPLACE INTO prior_cells VALUES (?,?,?,?,?)",
                        g.itertuples(index=False, name=None))
        con.commit()
        beacons += int(g["size"].sum())
        buckets += len(g)
        print(f"{os.path.basename(os.path.dirname(f))}: {len(t)} beacons -> {len(g)} buckets",
              flush=True)
    con.execute("CREATE INDEX idx_prior_hour ON prior_cells(hour)")
    con.commit()
    con.close()
    return {"beacons": beacons, "buckets": buckets}


class FastPrior:
    """ClimbPrior.value semantics over a whole prior_cells table, in memory."""

    def __init__(self, db_path, lag_hours: int = LAG_HOURS, smoothing: float = SMOOTHING):
        import pandas as pd
        con = sqlite3.connect(db_path)
        b = pd.read_sql("SELECT hour, lat_cell, lon_cell, climbs, total FROM prior_cells", con)
        con.close()
        self.lag_hours, self.smoothing = lag_hours, smoothing
        self.hours = np.array(sorted(b.hour.unique()))
        b["hi"] = np.searchsorted(self.hours, b.hour.values)
        gh = b.groupby("hi")[["climbs", "total"]].sum().reindex(range(len(self.hours)), fill_value=0)
        self.g_c, self.g_t = gh.climbs.cumsum().values, gh.total.cumsum().values
        b = b.sort_values(["lat_cell", "lon_cell", "hi"])
        b["cc"] = b.groupby(["lat_cell", "lon_cell"]).climbs.cumsum()
        b["ct"] = b.groupby(["lat_cell", "lon_cell"]).total.cumsum()
        self.cells = {k: (g.hi.values, g.cc.values, g.ct.values)
                      for k, g in b.groupby(["lat_cell", "lon_cell"])}
        self._memo: dict = {}

    def value(self, lat: float, lon: float, when: datetime) -> float:
        key = (lat, lon, when)
        v = self._memo.get(key)
        if v is not None:
            return v
        cutoff = _hour_key(when - timedelta(hours=self.lag_hours))
        n = int(np.searchsorted(self.hours, cutoff, side="left"))   # buckets strictly before
        if n == 0 or self.g_t[n - 1] == 0:
            v = float("nan")
        else:
            grate = self.g_c[n - 1] / self.g_t[n - 1]
            c = self.cells.get((int(np.floor(lat / PRIOR_RES)), int(np.floor(lon / PRIOR_RES))))
            k = 0 if c is None else int(np.searchsorted(c[0], n, side="left"))
            v = grate if k == 0 else (c[1][k - 1] + self.smoothing * grate) / (c[2][k - 1] + self.smoothing)
        self._memo[key] = v
        return v

    def values(self, lats, lons, whens) -> np.ndarray:
        return np.array([self.value(float(a), float(b), w) for a, b, w in zip(lats, lons, whens)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    print(build(args.archive, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
