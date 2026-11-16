"""
Forecast ledger: write a forecast down before its time, score it after.

Why this exists
---------------
The offline benchmark (evaluation/) scores the model given the weather that
actually happened at each sample.  That measures where, inside one hour and one
patch of ground, the model puts the lift.  It never asks whether a map issued
two hours ahead came true, and it cannot: the meteo columns are constant across
a grid, so a wrong weather forecast leaves the within-grid ranking almost
untouched while making the map wrong about whether there is lift at all.  The
only way to know how skill falls with the horizon is to issue forecasts, keep
them, and check them against what gliders later did.  That is this file.

The cycle
---------
1. **Issue** (every LEDGER_ISSUE_EVERY_MIN): for each site and each horizon in
   LEDGER_HORIZONS_MIN, build the exact map /predict would serve and write it
   here with everything needed to judge it later — model version, the weather
   it was given, whether that weather was the synthetic fallback, how much of
   the live thermals were blended in, and where gliders were circling at issue
   time (the persistence baseline).
2. **Wait** until the target time plus the match window has passed.
3. **Score** against the beacons logged around the target time, with rules
   fixed in this file and versioned (RULES_VERSION).  A forecast gets exactly
   one of four statuses:

   * ``supported``     at least half of the observed climbs fell in the map's
                       top HOTSPOT_FRAC of cells
   * ``contradicted``  climbs were observed but mostly outside the hotspots,
                       or only sink was observed and some of it inside them
   * ``unverifiable``  not enough soaring traffic to say either way
   * ``excluded``      the forecast or the observation is not fit to judge:
                       synthetic weather, a feed outage, or the beacons for the
                       window are no longer in the hot DB

   Nobody flying somewhere is not evidence of no thermal, which is why
   ``unverifiable`` exists and why it is reported, not dropped.

Integrity
---------
Forecast rows cannot be updated or deleted — SQLite triggers refuse it — so a
forecast cannot be quietly improved after its outcome is known.  Outcomes live
in their own table.  Grids are the only thing ever pruned (after
LEDGER_GRID_KEEP_DAYS), and never for a forecast that came true: those are the
gallery.

What a single "supported" means
-------------------------------
Little on its own.  With one observed climb, a map with no skill at all lands it
in the top 20% one time in five.  The horizon summary therefore reports, per
horizon, a within-forecast pairwise score with a bootstrap interval for the
served map, the model alone, terrain alone and persistence, plus the hotspot
hit rate against its chance level.  Examples are for showing; the summary is
the claim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# --- Rules: change any of these and bump RULES_VERSION ------------------------
RULES_VERSION = 1
POOL = 4                     # fine cells per stored cell side: 50 m -> ~200 m
MATCH_WINDOW_MIN = 10        # beacons within +/- this of the target time count
SETTLE_MIN = 5               # beacons are batch-written; let the last batch land
HOTSPOT_FRAC = 0.20          # "the map said here": its top 20% of cells
SUPPORT_SHARE = 0.5          # share of observed climbs that must be in hotspots
MIN_NEG_IN_HOTSPOT = 2       # sink-only forecasts need this much sink in hotspots
MIN_AGL = 200.0              # same floor as training (_MIN_SAMPLE_AGL)
MAX_AGL = 3000.0             # above this a climb is rarely a thermal
MAX_UNITS_STORED = 300       # per forecast, to keep outcome rows small


# --- Storage ------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    id             INTEGER PRIMARY KEY,
    issued_at      TEXT NOT NULL,
    target_at      TEXT NOT NULL,
    horizon_min    INTEGER NOT NULL,
    site           TEXT NOT NULL,
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    grid_lat       REAL NOT NULL,
    grid_lon       REAL NOT NULL,
    radius         REAL NOT NULL,
    grid_res       REAL NOT NULL,
    rows           INTEGER NOT NULL,
    cols           INTEGER NOT NULL,
    alt_amsl       REAL NOT NULL,
    alt_agl        REAL NOT NULL,
    model_version  TEXT NOT NULL,
    meteo_fallback INTEGER NOT NULL,
    no_insolation  INTEGER NOT NULL,
    fusion_weight  REAL NOT NULL,
    peak           REAL NOT NULL,
    meteo          TEXT NOT NULL,
    thermals       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fc_target  ON forecasts(target_at);
CREATE INDEX IF NOT EXISTS idx_fc_horizon ON forecasts(horizon_min, target_at);

CREATE TRIGGER IF NOT EXISTS forecasts_no_update BEFORE UPDATE ON forecasts
BEGIN SELECT RAISE(ABORT, 'forecasts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS forecasts_no_delete BEFORE DELETE ON forecasts
BEGIN SELECT RAISE(ABORT, 'forecasts are immutable'); END;

CREATE TABLE IF NOT EXISTS grids (
    forecast_id INTEGER PRIMARY KEY REFERENCES forecasts(id),
    prow        INTEGER NOT NULL,
    pcol        INTEGER NOT NULL,
    served      BLOB NOT NULL,
    model       BLOB NOT NULL,
    terrain     BLOB NOT NULL,
    elev        BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    forecast_id   INTEGER PRIMARY KEY REFERENCES forecasts(id),
    scored_at     TEXT NOT NULL,
    rules_version INTEGER NOT NULL,
    status        TEXT NOT NULL,
    reason        TEXT,
    metrics       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_out_status ON outcomes(status);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30)
    con.executescript(_SCHEMA)
    return con


def _iso(dt: datetime) -> str:
    # Microseconds always, so string order is time order — the beacon DB writes
    # datetime.isoformat(), which includes them whenever they are non-zero.
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def pool(arr: np.ndarray, f: int = POOL) -> np.ndarray:
    """Block mean over f x f cells, edge-padded so 201 cells pool to 51."""
    r, c = arr.shape
    pr, pc = (-r) % f, (-c) % f
    a = np.pad(arr.astype(float), ((0, pr), (0, pc)), mode="edge")
    return a.reshape(a.shape[0] // f, f, a.shape[1] // f, f).mean(axis=(1, 3))


def _enc_prob(a: np.ndarray) -> bytes:
    return zlib.compress(np.clip(np.rint(np.asarray(a) * 255), 0, 255).astype(np.uint8).tobytes())


def _dec_prob(b: bytes, shape) -> np.ndarray:
    return np.frombuffer(zlib.decompress(b), dtype=np.uint8).reshape(shape) / 255.0


def _enc_elev(a: np.ndarray) -> bytes:
    return zlib.compress(np.rint(np.asarray(a)).astype(np.int16).tobytes())


def _dec_elev(b: bytes, shape) -> np.ndarray:
    return np.frombuffer(zlib.decompress(b), dtype=np.int16).reshape(shape).astype(float)


_version_cache: tuple[int, str] | None = None


def model_version(model) -> str:
    """Short content hash of the serving booster, or 'physics' without one.

    Hashing is ~10 ms for a 1 MB booster and is cached on object identity: a
    promotion replaces the object, so a new model always gets a new hash.
    """
    global _version_cache
    clf = getattr(model, "model", None)
    if clf is None:
        return "physics"
    if _version_cache and _version_cache[0] == id(clf):
        return _version_cache[1]
    try:
        raw = bytes(clf.get_booster().save_raw("json"))
        v = hashlib.sha1(raw).hexdigest()[:12]
    except Exception:
        v = f"obj{id(clf):x}"
    _version_cache = (id(clf), v)
    return v


# --- Issue --------------------------------------------------------------------
def choose_sites(gliders: list[dict], fixed: list[tuple[str, float, float]],
                 k: int, min_aircraft: int, evidence) -> list[tuple[str, float, float]]:
    """Fixed sites plus the k busiest 0.1-degree cells right now.

    Busy means distinct soaring aircraft airborne at issue time — known before
    the target, so choosing on it cannot leak the outcome.  A dynamic cell that
    duplicates a fixed site's cell is dropped.
    """
    def cell(lat, lon):
        return (math.floor(lat * 10), math.floor(lon * 10))

    counts: dict[tuple[int, int], set] = {}
    for g in gliders:
        if not evidence(g):
            continue
        airborne = g.get("circling") or (g.get("speed_kmh") or 0) > 30
        if not airborne:
            continue
        counts.setdefault(cell(g["lat"], g["lon"]), set()).add(g.get("id"))
    taken = {cell(lat, lon) for _, lat, lon in fixed}
    ranked = sorted(((len(ids), c) for c, ids in counts.items()
                     if len(ids) >= min_aircraft and c not in taken), reverse=True)
    dynamic = [(f"dyn:{(c[0] + 0.5) / 10:.2f},{(c[1] + 0.5) / 10:.2f}",
                (c[0] + 0.5) / 10, (c[1] + 0.5) / 10) for _, c in ranked[:k]]
    return list(fixed) + dynamic


def record_forecast(con: sqlite3.Connection, site: str, lat: float, lon: float,
                    horizon_min: int, p: dict, terrain_mult: np.ndarray | None,
                    version: str, thermals: list[dict], grid_res: float) -> int:
    """Write one forecast (from main.compute_prediction) and its pooled grids."""
    rows, cols = p["rows"], p["cols"]
    served = pool(np.asarray(p["heatmap"], float).reshape(rows, cols))
    model_only = pool(np.asarray(p["model_heatmap"], float).reshape(rows, cols))
    terr = (pool(np.asarray(terrain_mult, float).reshape(rows, cols))
            if terrain_mult is not None else np.zeros_like(served))
    elev = pool(np.asarray(p["terrain"], float))
    m = p["meteo"]
    meteo = {k: (round(float(v), 3) if isinstance(v, (int, float)) else v)
             for k, v in m.items()}
    cur = con.execute(
        """INSERT INTO forecasts (issued_at, target_at, horizon_min, site, lat, lon,
               grid_lat, grid_lon, radius, grid_res, rows, cols, alt_amsl, alt_agl,
               model_version, meteo_fallback, no_insolation, fusion_weight, peak,
               meteo, thermals)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (_iso(p["issued_at"]), _iso(p["target_at"]), int(horizon_min), site, lat, lon,
         p["grid_lat"], p["grid_lon"], p["radius"], grid_res, rows, cols,
         float(p["alt_amsl"]), float(p["alt_amsl"] - p["terrain_mean"]),
         version, int(bool(p["meteo_fallback"])), int(bool(p["no_insolation"])),
         float(p["fusion_weight"]), float(np.max(served)) if served.size else 0.0,
         json.dumps(meteo), json.dumps(thermals)),
    )
    fid = cur.lastrowid
    con.execute(
        "INSERT INTO grids (forecast_id, prow, pcol, served, model, terrain, elev) "
        "VALUES (?,?,?,?,?,?,?)",
        (fid, served.shape[0], served.shape[1], _enc_prob(served),
         _enc_prob(model_only), _enc_prob(np.clip(terr, 0, 1)), _enc_elev(elev)),
    )
    con.commit()
    return fid


def prune_grids(con: sqlite3.Connection, keep_days: int, now: datetime | None = None) -> int:
    """Drop grids past keep_days, except for forecasts that came true."""
    now = now or datetime.now(timezone.utc)
    cutoff = _iso(now - timedelta(days=keep_days))
    cur = con.execute(
        """DELETE FROM grids WHERE forecast_id IN (
               SELECT f.id FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id = f.id
               WHERE f.target_at < ? AND (o.status IS NULL OR o.status != 'supported'))""",
        (cutoff,),
    )
    con.commit()
    return cur.rowcount


# --- Score --------------------------------------------------------------------
def _percentile_of(values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Mid-rank percentile of each value within the grid, 0..1."""
    flat = np.sort(grid.ravel())
    lo = np.searchsorted(flat, values, side="left")
    hi = np.searchsorted(flat, values, side="right")
    return (lo + hi) / 2.0 / flat.size


def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def build_units(fc: dict, grids: dict, beacons: list[tuple], label_for) -> list[dict]:
    """Turn beacons into scoring units: one per (aircraft, stored cell).

    A glider circling in one thermal reports dozens of times; counting each
    report would let a single climb outvote a dozen independent ones.  So
    every aircraft contributes at most one unit per ~200 m cell, positive if it
    climbed there at all.
    """
    rows, cols = fc["rows"], fc["cols"]
    lat0 = fc["grid_lat"] - fc["radius"]
    lon0 = fc["grid_lon"] - fc["radius"]
    res = fc["grid_res"]
    elev = grids["elev"]
    pr, pc = elev.shape
    units: dict[tuple, dict] = {}
    for gid, lat, lon, alt, vario, circling in beacons:
        r = (lat - lat0) / res
        c = (lon - lon0) / res
        if not (-0.5 <= r <= rows - 0.5 and -0.5 <= c <= cols - 0.5):
            continue
        label = label_for(bool(circling), float(vario))
        if label is None:
            continue
        i = min(pr - 1, max(0, int(round(r)) // POOL))
        j = min(pc - 1, max(0, int(round(c)) // POOL))
        agl = alt - elev[i, j]
        if not (MIN_AGL <= agl <= MAX_AGL):
            continue
        key = (gid, i, j)
        u = units.get(key)
        if u is None:
            units[key] = {"id": gid, "i": i, "j": j, "label": label,
                          "lat": round(lat, 5), "lon": round(lon, 5), "agl": round(agl)}
        elif label > u["label"]:
            u.update(label=label, lat=round(lat, 5), lon=round(lon, 5), agl=round(agl))
    return list(units.values())


def score_forecast(fc: dict, grids: dict | None, units: list[dict]) -> tuple[str, str | None, dict]:
    """Status, reason and metrics for one forecast.  Pure: no I/O."""
    if fc["meteo_fallback"]:
        return "excluded", "meteo_fallback", {}
    if fc["no_insolation"]:
        return "excluded", "no_insolation", {}
    if grids is None:
        return "excluded", "grid_missing", {}

    served, model_g, terr = grids["served"], grids["model"], grids["terrain"]
    pr, pc = served.shape
    lat0 = fc["grid_lat"] - fc["radius"]
    lon0 = fc["grid_lon"] - fc["radius"]
    step = fc["grid_res"] * POOL
    thermals = fc["thermals"]

    rows_out = []
    for u in units:
        i, j = u["i"], u["j"]
        clat = lat0 + (i + 0.5) * step
        clon = lon0 + (j + 0.5) * step
        if thermals:
            d = float(np.min(_haversine_km(clat, clon,
                                           np.array([t["lat"] for t in thermals]),
                                           np.array([t["lon"] for t in thermals]))))
            persist = -d
        else:
            persist = 0.0
        rows_out.append([u["label"], float(served[i, j]), float(model_g[i, j]),
                         float(terr[i, j]), round(persist, 3), u["lat"], u["lon"],
                         u["agl"], u["id"]])

    labels = np.array([r[0] for r in rows_out], dtype=int)
    served_v = np.array([r[1] for r in rows_out], dtype=float)
    pct = _percentile_of(served_v, served) if len(served_v) else np.array([])
    hot = pct >= 1.0 - HOTSPOT_FRAC
    n_pos = int((labels == 1).sum())
    n_neg = int((labels == 0).sum())
    pos_hot = int((hot & (labels == 1)).sum())
    neg_hot = int((hot & (labels == 0)).sum())

    metrics = {
        "n_pos": n_pos, "n_neg": n_neg,
        "n_aircraft": len({r[8] for r in rows_out}),
        "pos_hot": pos_hot, "neg_hot": neg_hot,
        "hotspot_frac": HOTSPOT_FRAC,
        # label, served, model, terrain, persistence, lat, lon, agl, served pct
        "units": [r[:8] + [round(float(p), 3)] for r, p in
                  zip(rows_out[:MAX_UNITS_STORED], pct[:MAX_UNITS_STORED])],
    }
    if n_pos >= 1:
        if pos_hot / n_pos >= SUPPORT_SHARE:
            return "supported", None, metrics
        return "contradicted", "climbs_outside_hotspots", metrics
    if neg_hot >= MIN_NEG_IN_HOTSPOT:
        return "contradicted", "sink_in_hotspots", metrics
    return "unverifiable", "too_little_traffic", metrics


def _load_grids(con, fid: int) -> dict | None:
    row = con.execute("SELECT prow, pcol, served, model, terrain, elev FROM grids "
                      "WHERE forecast_id = ?", (fid,)).fetchone()
    if row is None:
        return None
    shape = (row[0], row[1])
    return {"served": _dec_prob(row[2], shape), "model": _dec_prob(row[3], shape),
            "terrain": _dec_prob(row[4], shape), "elev": _dec_elev(row[5], shape)}


_FC_COLS = ("id, issued_at, target_at, horizon_min, site, lat, lon, grid_lat, grid_lon, "
            "radius, grid_res, rows, cols, alt_amsl, alt_agl, model_version, "
            "meteo_fallback, no_insolation, fusion_weight, peak, meteo, thermals")


def _fc_dict(row) -> dict:
    keys = [k.strip() for k in _FC_COLS.split(",")]
    d = dict(zip(keys, row))
    d["meteo"] = json.loads(d["meteo"])
    d["thermals"] = json.loads(d["thermals"])
    return d


def score_due(ledger_path, beacon_db_path, soaring_types, label_for,
              pos_min_vario: float, neg_max_vario: float,
              now: datetime | None = None, limit: int = 500) -> dict:
    """Score every forecast whose window has closed.  Sync: run in a thread.

    Forecasts sharing a target time share one beacon query, so the hot DB is
    read once per window rather than once per site and horizon.
    """
    now = now or datetime.now(timezone.utc)
    closed_before = _iso(now - timedelta(minutes=MATCH_WINDOW_MIN + SETTLE_MIN))
    con = connect(ledger_path)
    try:
        due = [_fc_dict(r) for r in con.execute(
            f"""SELECT {_FC_COLS} FROM forecasts f
                WHERE target_at < ? AND NOT EXISTS
                      (SELECT 1 FROM outcomes o WHERE o.forecast_id = f.id)
                ORDER BY target_at LIMIT ?""", (closed_before, limit))]
        if not due:
            return {"scored": 0}

        by_target: dict[str, list[dict]] = {}
        for fc in due:
            by_target.setdefault(fc["target_at"], []).append(fc)

        counts: dict[str, int] = {}
        hot_first = None
        db_ok = Path(beacon_db_path).exists()
        bcon = None
        if db_ok:
            bcon = sqlite3.connect(f"file:{beacon_db_path}?mode=ro", uri=True, timeout=30)
            first = bcon.execute("SELECT MIN(ts) FROM beacons").fetchone()[0]
            hot_first = first
        try:
            placeholders = ",".join("?" * len(soaring_types))
            labelable = (f"((circling = 1 AND vario > {pos_min_vario}) OR "
                         f"(circling = 0 AND vario < {neg_max_vario}))")
            for target, fcs in by_target.items():
                t = datetime.fromisoformat(target)
                t0 = _iso(t - timedelta(minutes=MATCH_WINDOW_MIN))
                t1 = _iso(t + timedelta(minutes=MATCH_WINDOW_MIN))
                beacons, reason = [], None
                if bcon is None or hot_first is None or hot_first > t0:
                    reason = "no_observation_data"
                else:
                    alive = bcon.execute("SELECT 1 FROM beacons WHERE ts >= ? AND ts <= ? "
                                         "LIMIT 1", (t0, t1)).fetchone()
                    if alive is None:
                        reason = "feed_outage"
                    else:
                        beacons = bcon.execute(
                            f"""SELECT id, lat, lon, alt, vario, circling FROM beacons
                                WHERE ts >= ? AND ts <= ? AND ac_type IN ({placeholders})
                                  AND under_tow = 0 AND {labelable}""",
                            (t0, t1, *sorted(soaring_types))).fetchall()
                for fc in fcs:
                    if reason:
                        status, why, metrics = "excluded", reason, {}
                    else:
                        grids = _load_grids(con, fc["id"])
                        units = build_units(fc, grids, beacons, label_for) if grids else []
                        status, why, metrics = score_forecast(fc, grids, units)
                    con.execute(
                        "INSERT INTO outcomes (forecast_id, scored_at, rules_version, "
                        "status, reason, metrics) VALUES (?,?,?,?,?,?)",
                        (fc["id"], _iso(now), RULES_VERSION, status, why,
                         json.dumps(metrics, separators=(",", ":"))))
                    counts[status] = counts.get(status, 0) + 1
                con.commit()
        finally:
            if bcon is not None:
                bcon.close()
        return {"scored": len(due), **counts}
    finally:
        con.close()


# --- Summarise ----------------------------------------------------------------
def _concordance(pos: np.ndarray, neg: np.ndarray) -> tuple[float, int]:
    """Pairs where a positive outranks a negative (ties half), and pair count."""
    if pos.size == 0 or neg.size == 0:
        return 0.0, 0
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    sv = allv[order]
    # average ranks for ties
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    r_pos = ranks[:pos.size].sum()
    u = r_pos - pos.size * (pos.size + 1) / 2.0
    return float(u), int(pos.size * neg.size)


def _auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    c, n = _concordance(scores[labels == 1], scores[labels == 0])
    return c / n if n else None


def _ci(stat_fn, n_items: int, boot: int, rng) -> tuple[float | None, float | None]:
    if n_items < 2:
        return None, None
    vals = []
    for _ in range(boot):
        idx = rng.integers(0, n_items, n_items)
        v = stat_fn(idx)
        if v is not None:
            vals.append(v)
    if len(vals) < boot // 2:
        return None, None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


_RANKERS = {"served": 1, "model": 2, "terrain": 3, "persistence": 4}


def horizon_summary(ledger_path, days: float = 30, boot: int = 1000,
                    now: datetime | None = None, seed: int = 0) -> dict:
    """Per-horizon scorecard over the last `days` of scored forecasts.

    within_*  : pairwise score inside each forecast (does the map put climbs
                above sink on the same map), pooled over forecasts.  Where.
    pooled    : the served probability ranked across every unit of every
                forecast at that horizon.  Where *and* whether — this is the
                column a wrong weather forecast moves.
    hit_rate  : share of observed climbs inside the hotspots; chance is
                HOTSPOT_FRAC.
    Intervals are 95% bootstrap over forecasts, never over units, because the
    units of one forecast share its weather and are not independent.
    """
    now = now or datetime.now(timezone.utc)
    since = _iso(now - timedelta(days=days))
    rng = np.random.default_rng(seed)
    con = connect(ledger_path)
    try:
        rows = con.execute(
            """SELECT f.horizon_min, o.status, o.reason, o.metrics
               FROM forecasts f JOIN outcomes o ON o.forecast_id = f.id
               WHERE f.target_at >= ?""", (since,)).fetchall()
        issued = dict(con.execute(
            "SELECT horizon_min, COUNT(*) FROM forecasts WHERE target_at >= ? "
            "GROUP BY horizon_min", (since,)).fetchall())
    finally:
        con.close()

    by_h: dict[int, list] = {}
    for h, status, reason, metrics in rows:
        by_h.setdefault(h, []).append((status, reason, json.loads(metrics)))

    out = []
    for h in sorted(set(issued) | set(by_h)):
        recs = by_h.get(h, [])
        status_counts: dict[str, int] = {}
        for s, _, _ in recs:
            status_counts[s] = status_counts.get(s, 0) + 1
        judged = [m for s, _, m in recs if s in ("supported", "contradicted", "unverifiable")
                  and m.get("units")]
        row = {"horizon_min": h, "issued": issued.get(h, 0), "scored": len(recs),
               "status": status_counts,
               "units_pos": sum(m["n_pos"] for m in judged),
               "units_neg": sum(m["n_neg"] for m in judged),
               "forecasts_with_units": len(judged)}

        # within-forecast concordance per ranker
        for name, col in _RANKERS.items():
            per = []
            for m in judged:
                u = np.array([x[:5] for x in m["units"]], dtype=float)
                c, n = _concordance(u[u[:, 0] == 1, col], u[u[:, 0] == 0, col])
                if n:
                    per.append((c, n))
            if per:
                arr = np.array(per)
                point = arr[:, 0].sum() / arr[:, 1].sum()
                lo, hi = _ci(lambda idx: arr[idx, 0].sum() / arr[idx, 1].sum(),
                             len(arr), boot, rng)
                row[f"within_{name}"] = {"value": round(point, 3),
                                         "lo": None if lo is None else round(lo, 3),
                                         "hi": None if hi is None else round(hi, 3),
                                         "forecasts": len(arr)}
            else:
                row[f"within_{name}"] = None

        # hotspot hit rate
        hits = np.array([[m["pos_hot"], m["n_pos"]] for m in judged if m["n_pos"]], dtype=float)
        if hits.size:
            lo, hi = _ci(lambda idx: hits[idx, 0].sum() / hits[idx, 1].sum(),
                         len(hits), boot, rng)
            row["hit_rate"] = {"value": round(hits[:, 0].sum() / hits[:, 1].sum(), 3),
                               "lo": None if lo is None else round(lo, 3),
                               "hi": None if hi is None else round(hi, 3),
                               "chance": HOTSPOT_FRAC}
        else:
            row["hit_rate"] = None

        # pooled across forecasts (where + whether), Brier, base rate
        per_fc = [np.array([x[:2] for x in m["units"]], dtype=float) for m in judged]
        if per_fc:
            allu = np.vstack(per_fc)
            labels, probs = allu[:, 0].astype(int), allu[:, 1]
            auc = _auc(labels, probs)

            def pooled(idx):
                sel = np.vstack([per_fc[k] for k in idx])
                return _auc(sel[:, 0].astype(int), sel[:, 1])
            lo, hi = _ci(pooled, len(per_fc), min(boot, 300), rng)
            row["pooled_served"] = None if auc is None else {
                "value": round(auc, 3),
                "lo": None if lo is None else round(lo, 3),
                "hi": None if hi is None else round(hi, 3)}
            row["brier"] = round(float(np.mean((probs - labels) ** 2)), 4)
            row["base_rate"] = round(float(labels.mean()), 3)
            row["mean_forecast"] = round(float(probs.mean()), 3)
        else:
            row["pooled_served"] = row["brier"] = row["base_rate"] = row["mean_forecast"] = None
        out.append(row)
    return {"days": days, "rules_version": RULES_VERSION,
            "hotspot_frac": HOTSPOT_FRAC, "horizons": out}


def examples(ledger_path, status: str = "supported", horizon_min: int | None = None,
             limit: int = 20) -> dict:
    """Most recent forecasts with a given status, with the denominator beside them."""
    con = connect(ledger_path)
    try:
        q = (f"SELECT {', '.join('f.' + c.strip() for c in _FC_COLS.split(','))}, "
             "o.status, o.reason, o.metrics, (g.forecast_id IS NOT NULL) "
             "FROM forecasts f JOIN outcomes o ON o.forecast_id = f.id "
             "LEFT JOIN grids g ON g.forecast_id = f.id WHERE o.status = ?")
        args: list = [status]
        if horizon_min is not None:
            q += " AND f.horizon_min = ?"
            args.append(horizon_min)
        q += " ORDER BY f.target_at DESC LIMIT ?"
        args.append(limit)
        items = []
        for r in con.execute(q, args).fetchall():
            fc = _fc_dict(r[:22])
            m = json.loads(r[24])
            items.append({k: fc[k] for k in ("id", "issued_at", "target_at", "horizon_min",
                                             "site", "grid_lat", "grid_lon", "model_version",
                                             "peak", "fusion_weight")}
                         | {"status": r[22], "reason": r[23], "has_grid": bool(r[25]),
                            "n_pos": m.get("n_pos"), "n_neg": m.get("n_neg"),
                            "pos_hot": m.get("pos_hot")})
        dq = "SELECT o.status, COUNT(*) FROM outcomes o JOIN forecasts f ON f.id = o.forecast_id"
        dargs: list = []
        if horizon_min is not None:
            dq += " WHERE f.horizon_min = ?"
            dargs.append(horizon_min)
        denominator = dict(con.execute(dq + " GROUP BY o.status", dargs).fetchall())
        issued = con.execute(
            "SELECT COUNT(*) FROM forecasts" + (" WHERE horizon_min = ?" if horizon_min is not None else ""),
            dargs).fetchone()[0]
    finally:
        con.close()
    return {"items": items, "issued": issued, "scored_by_status": denominator}


def forecast_detail(ledger_path, fid: int) -> dict | None:
    """One forecast as recorded, its outcome, and its stored grid for replay."""
    con = connect(ledger_path)
    try:
        row = con.execute(f"SELECT {_FC_COLS} FROM forecasts WHERE id = ?", (fid,)).fetchone()
        if row is None:
            return None
        fc = _fc_dict(row)
        out = con.execute("SELECT scored_at, rules_version, status, reason, metrics "
                          "FROM outcomes WHERE forecast_id = ?", (fid,)).fetchone()
        grids = _load_grids(con, fid)
    finally:
        con.close()
    step = fc["grid_res"] * POOL
    detail = {**fc, "outcome": None, "grid": None}
    if out:
        detail["outcome"] = {"scored_at": out[0], "rules_version": out[1],
                             "status": out[2], "reason": out[3], "metrics": json.loads(out[4])}
    if grids:
        detail["grid"] = {
            "rows": grids["served"].shape[0], "cols": grids["served"].shape[1],
            "lat_min": fc["grid_lat"] - fc["radius"], "lon_min": fc["grid_lon"] - fc["radius"],
            "cell_deg": step,
            "served": np.round(grids["served"], 3).ravel().tolist(),
            "model": np.round(grids["model"], 3).ravel().tolist(),
        }
    return detail
