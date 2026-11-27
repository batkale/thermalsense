"""
Daily score history: does the model get better as the buffer grows?

The gate's benchmark score cannot answer that.  Each group's side of the split
is frozen, but every new day brings new held-out groups and eviction thins old
ones, so the score is measured on a slightly different set every day.  A
rising number could be easier days, not a better model.

So the question is asked forward, one finished day at a time:

* **Snapshot** (23:55 UTC): the serving model is copied to
  ``SCORE_SNAPSHOT_DIR/<next day>.json``.  At that moment it has seen nothing of
  the coming day.
* **Score** (03:00 UTC, after the nightly seed has added yesterday): every row
  of that day — training side and benchmark side alike, since no copy has seen
  any of them — is scored by that day's copy and by the copies from
  ``SCORE_COMPARE_DAYS`` earlier, paired on the same rows.  A positive paired
  delta whose interval clears zero is the claim "a week more data made it
  better"; the copy's own score is not, because days differ in difficulty.

Groups are ``decision_ids`` — one hour, one 0.5° area — the unit one heatmap
ranks over, and the unit the headline offline numbers in CLAUDE.md use.
Scoring is ``ThermalModel._score_served``: model output times the terrain
multiplier, exactly what /predict serves.

The day of a row is recovered from its day-of-year columns; the year is not.
Once the buffer spans more than a year, a copy may already have trained on the
same day-of-year from the year before, and that day is recorded as
``seen_in_training`` rather than scored.  Each copy carries the day-of-year
histogram of the buffer it was taken beside, which is how that is known.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from evaluation.holdout import _decode_cyclic, decision_ids
from evaluation.metrics import bootstrap_ci, grouped_auc, paired_delta_ci
from models.thermal_model import _MIN_SAMPLE_AGL
from pipeline.feature_engineering import _cyclic

_COL_DOY_SIN, _COL_DOY_COS = 17, 18
_COL_AGL = 21
_N_BOOT = 1000

# Score a missed day late, up to this far back: the copy for a day never saw
# that day however late it is scored.  Rows of an old day thin out under
# eviction, which the row count on the record shows.
CATCH_UP_DAYS = 7


# ---------------------------------------------------------------------------
# Day of a row
# ---------------------------------------------------------------------------

def _doy_keys(rows: Sequence[np.ndarray]) -> np.ndarray:
    """Day-of-year key per row, decoded the way evaluation/holdout.py does."""
    if not len(rows):
        return np.zeros(0, dtype=np.int64)
    s = np.fromiter((r[_COL_DOY_SIN] for r in rows), dtype=float, count=len(rows))
    c = np.fromiter((r[_COL_DOY_COS] for r in rows), dtype=float, count=len(rows))
    return np.round(_decode_cyclic(s, c, 365)).astype(np.int64) % 365


def doy_key(day: date) -> int:
    """The key rows built on `day` carry — encoded as build_feature_matrix does."""
    s, c = _cyclic(day.timetuple().tm_yday, 365)
    return int(np.round(_decode_cyclic(np.array([s]), np.array([c]), 365))[0]) % 365


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def _model_path(snapshot_dir: Path, day: date) -> Path:
    return Path(snapshot_dir) / f"{day.isoformat()}.json"


def _meta_path(snapshot_dir: Path, day: date) -> Path:
    return Path(snapshot_dir) / f"{day.isoformat()}.meta.json"


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def take_snapshot(clf, rows: Sequence[np.ndarray], labels: Sequence[int], day: date,
                  snapshot_dir: Path, keep_days: int, bench_score: float | None = None,
                  now: datetime | None = None) -> dict:
    """
    Save the serving model as the copy that will be scored on `day`.

    Called before `day` starts, so nothing the model was fitted on can come
    from it.  `rows` is the buffer beside it; only its size and its
    day-of-year histogram are kept.
    """
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    final = _model_path(snapshot_dir, day)
    # XGBoost picks the format from the extension, so the temporary name has
    # to end in .json as well.
    tmp = snapshot_dir / f"{day.isoformat()}.tmp.json"
    clf.save_model(str(tmp))
    os.replace(tmp, final)

    keys, counts = np.unique(_doy_keys(rows), return_counts=True)
    meta = {
        "day": day.isoformat(),
        "taken_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "rows": int(len(rows)),
        "positives": int(np.sum(labels)) if len(labels) else 0,
        "days": int(len(keys)),
        "width": getattr(clf, "n_features_in_", None),
        "bench_score": bench_score,
        "doy_rows": {str(int(k)): int(c) for k, c in zip(keys, counts)},
    }
    _write_json_atomic(_meta_path(snapshot_dir, day), meta)
    prune_snapshots(snapshot_dir, keep_days, today=day)
    return meta


def _snapshot_day(path: Path) -> date | None:
    stem = path.name.split(".", 1)[0]
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def prune_snapshots(snapshot_dir: Path, keep_days: int, today: date) -> int:
    """Delete copies older than keep_days; returns how many files went."""
    cutoff = today - timedelta(days=keep_days)
    gone = 0
    for p in Path(snapshot_dir).glob("*.json"):
        d = _snapshot_day(p)
        if d is not None and d < cutoff:
            p.unlink(missing_ok=True)
            gone += 1
    return gone


def snapshot_days(snapshot_dir: Path) -> list[str]:
    """Days a complete copy (model and meta) exists for, oldest first."""
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        return []
    out = []
    for p in snapshot_dir.glob("*.meta.json"):
        d = _snapshot_day(p)
        if d is not None and _model_path(snapshot_dir, d).exists():
            out.append(d.isoformat())
    return sorted(out)


def load_snapshot(snapshot_dir: Path, day: date, threads: int = 1):
    """(model, meta) for `day`, or None when either file is missing."""
    import xgboost as xgb
    mp, tp = _model_path(snapshot_dir, day), _meta_path(snapshot_dir, day)
    if not (mp.exists() and tp.exists()):
        return None
    clf = xgb.XGBClassifier()
    clf.load_model(str(mp))
    # save_model does not carry n_jobs; a reloaded model would take every core.
    clf.set_params(n_jobs=threads)
    return clf, json.loads(tp.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scoring one day
# ---------------------------------------------------------------------------

def _r(x) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 4)


def score_day(rows: Sequence[np.ndarray], labels: Sequence[int], day: date,
              snapshot_dir: Path, scorer: Callable, terrain: Callable,
              compare_days: Sequence[int] = (7, 28), min_agl: float = _MIN_SAMPLE_AGL,
              threads: int = 1, n_boot: int = _N_BOOT,
              now: datetime | None = None) -> dict:
    """
    Score `day` with the copy taken before it, and against older copies.

    scorer(clf, X) and terrain(X) are ThermalModel._score_served and
    ._terrain_multiplier.  The AGL floor is the one fit_and_gate applies, so
    the rows judged are the kind the model is trained on.
    """
    key = doy_key(day)
    keys = _doy_keys(rows)
    idx = np.flatnonzero(keys == key)
    X = np.array([rows[i] for i in idx]) if idx.size else np.zeros((0, 1))
    y = np.array([labels[i] for i in idx], dtype=int)
    if len(y):
        keep = X[:, _COL_AGL] >= min_agl
        X, y = X[keep], y[keep]

    rec = {"day": day.isoformat(),
           "scored_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "rows": int(len(y)), "positives": int(y.sum()) if len(y) else 0,
           "groups": 0, "model": None, "terrain": None, "compare": [], "reason": None}

    snap = load_snapshot(snapshot_dir, day, threads)
    if snap is None:
        rec["reason"] = "no_snapshot"
        return rec
    clf, meta = snap
    if meta.get("doy_rows", {}).get(str(key), 0) > 0:
        rec["reason"] = "seen_in_training"
        return rec
    if not len(y):
        rec["reason"] = "no_rows"
        return rec

    gids = decision_ids(X)
    current = scorer(clf, X)
    ci = bootstrap_ci(current, y, gids, n_boot=n_boot)
    if ci["groups"] == 0:
        rec["reason"] = "no_mixed_groups"
        return rec
    rec["groups"] = ci["groups"]
    rec["model"] = {"score": _r(ci["point"]), "lo": _r(ci["lo"]), "hi": _r(ci["hi"]),
                    "train_rows": meta.get("rows"), "train_days": meta.get("days"),
                    "width": meta.get("width")}
    tci = bootstrap_ci(terrain(X), y, gids, n_boot=n_boot)
    rec["terrain"] = {"score": _r(tci["point"]), "lo": _r(tci["lo"]), "hi": _r(tci["hi"])}

    for k in compare_days:
        old = load_snapshot(snapshot_dir, day - timedelta(days=k), threads)
        if old is None:
            continue
        oclf, ometa = old
        if ometa.get("doy_rows", {}).get(str(key), 0) > 0:
            continue
        older = scorer(oclf, X)
        d = paired_delta_ci(older, current, y, gids, n_boot=n_boot)   # current − older
        rec["compare"].append({
            "days_older": int(k),
            "score": _r(grouped_auc(older, y, gids)["micro"]),
            "delta": {"point": _r(d["point"]), "lo": _r(d["lo"]), "hi": _r(d["hi"])},
            "train_rows": ometa.get("rows"), "train_days": ometa.get("days"),
        })
    return rec


# ---------------------------------------------------------------------------
# The ledger's view of the same day
# ---------------------------------------------------------------------------

def ledger_window_end(day: date) -> datetime:
    """`now` for horizon_summary(days=1) covering exactly `day`.

    The summary's window is inclusive at both ends, so ending a microsecond
    before midnight keeps a target at exactly 00:00 in one day only.
    """
    return (datetime.combine(day + timedelta(days=1), time(0), tzinfo=timezone.utc)
            - timedelta(microseconds=1))


def compact_ledger(summary: dict) -> list[dict]:
    """The columns of a horizon_summary worth keeping per day."""
    out = []
    for h in summary.get("horizons", []):
        out.append({k: h.get(k) for k in ("horizon_min", "issued", "scored", "within_served",
                                          "within_terrain", "pooled_served", "hit_rate")})
    return out


# ---------------------------------------------------------------------------
# The history file
# ---------------------------------------------------------------------------

def _clean(obj):
    """NaN and inf become null: the response encoder refuses them."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def append_record(path: Path, rec: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(_clean(rec)) + "\n")


def read_history(path: Path, limit: int | None = None) -> list[dict]:
    """One record per day, newest first; a later line for a day replaces an earlier one."""
    path = Path(path)
    if not path.exists():
        return []
    by_day: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue          # a torn last line from a crash mid-write
            if isinstance(rec, dict) and "day" in rec:
                by_day[rec["day"]] = rec
    out = [by_day[d] for d in sorted(by_day, reverse=True)]
    return out[:limit] if limit else out


def run_daily(rows: Sequence[np.ndarray], labels: Sequence[int], today: date,
              snapshot_dir: Path, history_path: Path, scorer: Callable, terrain: Callable,
              compare_days: Sequence[int] = (7, 28), ledger_fn: Callable | None = None,
              threads: int = 1, catch_up_days: int = CATCH_UP_DAYS) -> list[dict]:
    """
    Score every finished day that has a copy and no record yet, oldest first.

    Days before the first copy are simply skipped, not recorded: there is no
    model that stood before them, so there is nothing to say.
    """
    done = {r["day"] for r in read_history(history_path)}
    out = []
    for back in range(catch_up_days, 0, -1):
        day = today - timedelta(days=back)
        if day.isoformat() in done or not _model_path(snapshot_dir, day).exists():
            continue
        rec = score_day(rows, labels, day, snapshot_dir, scorer, terrain,
                        compare_days=compare_days, threads=threads)
        if ledger_fn is not None:
            try:
                rec["ledger"] = ledger_fn(day)
            except Exception as exc:
                rec["ledger"] = None
                rec["ledger_error"] = f"{type(exc).__name__}: {exc}"
        append_record(history_path, rec)
        out.append(rec)
    return out
