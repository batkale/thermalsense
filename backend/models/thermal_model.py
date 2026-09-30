import asyncio
import os
import logging
import tempfile
import time
import xgboost as xgb
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from config import (MODEL_PATH, BUFFER_PATH, GRID_RADIUS, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
                    XGB_FIT_THREADS, XGB_PREDICT_THREADS, ENABLE_LANDCOVER,
                    MAX_SAMPLE_GRIDS, SAMPLE_TIME_BUDGET_S, TERRAIN_SOURCE,
                    SEED_METEO_BUDGET, SEED_TIME_BUDGET_S)
from evaluation.holdout import split as bench_split
from evaluation.metrics import grouped_auc, paired_delta_ci
from pipeline.feature_engineering import FEATURE_COUNT


def _set_aside(path: Path, suffix: str) -> Path | None:
    """
    Move a buffer we can't use out of the way, keeping the data.

    Returns the new path, or None if it could not be moved — in which case the
    caller carries on with an empty buffer rather than failing startup over a
    file it was only trying to tidy up.
    """
    dest = path.with_name(path.name + suffix)
    try:
        if dest.exists():
            dest = path.with_name(f"{path.name}{suffix}.{int(time.time())}")
        path.replace(dest)
        return dest
    except OSError as exc:
        log.warning(f"[model] could not set aside {path.name} ({exc}) — leaving it in place")
        return None


async def _land_props(lat: float, lon: float, shape):
    """
    Per-cell (heat, albedo) for a training grid, or None when disabled.

    Training and serving must build identical feature spaces, so this is gated
    on the same flag /predict reads.  A failure returns None rather than
    propagating: a sample carrying the placeholder constants is still a usable
    sample, whereas losing it costs two rate-limited API calls to recollect.
    """
    if not ENABLE_LANDCOVER:
        return None
    try:
        from data.landcover_client import fetch_landcover_fine
        return await fetch_landcover_fine(lat, lon, shape)
    except Exception as exc:
        log.warning(f"[model] land cover unavailable at ({lat:.2f},{lon:.2f}): {exc}")
        return None


def _above_thermal_base(alt_amsl: float, ground_elev: float, thermal_base_agl: float) -> bool:
    """
    True when the aircraft was flying above the estimated thermal base.

    alt_amsl comes from APRS (metres above sea level); thermal_base_agl is
    meteo["cape_base"], a height above ground.  They must be compared in the
    same frame, hence subtracting the ground elevation under the aircraft.
    """
    if not thermal_base_agl or thermal_base_agl <= 0:
        return False        # no usable estimate — do not discard on a guess
    return (alt_amsl - ground_elev) > (thermal_base_agl + _THERMAL_BASE_MARGIN_M)


def _plausible_coords(X: np.ndarray) -> np.ndarray:
    """
    Boolean mask of rows whose lat/lon (cols 0,1) are real coordinates on Earth,
    rather than the grid indices an unbounded build_feature_matrix call produces
    (the documented pre-fix signature is lat=100).

    Deliberately *not* the configured feed box.  It used to be, and the box is an
    operational setting that moves: a training node narrowed to Europe dropped
    1,276 perfectly valid American samples on its first start, logged them as
    "pre-fix rows", and the next save made the loss permanent.  Moving the box
    south for the winter would then have erased every European sample the same
    way.  A labelled observation stays a valid observation wherever the feed is
    pointed now; each costs two rate-limited API calls to replace.

    The price is that a legacy index pair such as (50, 100) reads as a point in
    Mongolia and survives.  That was already true under the worldwide default,
    and it affects buffers written before the lat_bounds fix only.
    """
    lat, lon = X[:, 0], X[:, 1]
    return (np.abs(lat) <= 90.0) & (np.abs(lon) <= 180.0)


def _grid_index(lat: float, lon: float,
                lat_bounds: tuple[float, float],
                lon_bounds: tuple[float, float],
                shape: tuple[int, int]) -> int | None:
    """
    Flat index of the cell containing (lat, lon), or None if it falls outside.

    Mirrors build_feature_matrix's layout exactly: lats run south->north over
    `rows` points and lons west->east over `cols`, meshgrid(indexing="ij"), so
    the flat index is row*cols + col.  Any change to that layout has to change
    here too, which is why callers verify the result against columns 0/1 rather
    than trusting the arithmetic.
    """
    rows, cols = shape
    south, north = lat_bounds
    west,  east  = lon_bounds
    if not (south <= lat <= north and west <= lon <= east):
        return None
    # linspace endpoints are inclusive, hence rows-1 spacings across the span.
    i = int(round((lat - south) / (north - south) * (rows - 1)))
    j = int(round((lon - west) / (east - west) * (cols - 1)))
    i = min(max(i, 0), rows - 1)
    j = min(max(j, 0), cols - 1)
    return i * cols + j


def _centre_index(shape: tuple[int, int]) -> int:
    """
    Flat index of the middle cell of a (rows, cols) grid.

    n // 2 is NOT the centre: for 200x200 it gives 20000 = row 100, col 0 — the
    westernmost column, roughly 3.5 km from the point the grid is centred on.
    """
    rows, cols = shape
    return (rows // 2) * cols + cols // 2

log = logging.getLogger(__name__)

# One buffer sample = one glider observation captured during a retrain cycle.
# It is a FEATURE_COUNT-element feature vector (lat, lon, elevation, slope, aspect,
# temp, humidity, wind_u, wind_v, cape, cin, solar_ghi, lapse_rate,
# land_use_heat, land_use_albedo, hour_sin, hour_cos, doy_sin, doy_cos,
# pbl_height, soil_temp, alt_agl)
# taken from the grid cell directly under the glider at the moment it was seen,
# paired with a binary label: 1 = glider was circling AND climbing > 1.5 m/s
# (confirmed thermal), 0 = glider was present but not thermalling.
# At 176 bytes per sample (21 × float64 + 1 × int64) the full buffer is ~3.5 GB on disk.
# Sized for 10 years of continuous collection (20 samples × 12 cycles/hr × 24 hr × 365 days × 10 yr).
# A thermal cannot exist above the convective condensation level, so an aircraft
# circling higher than the estimated thermal base is climbing on something else —
# wave, ridge lift, or an engine.  Its position says nothing reliable about lift
# over the ground cell below, so the sample is dropped rather than labelled.
# cape_base is an estimate, hence the margin.
_THERMAL_BASE_MARGIN_M = 300

# Below this there is no meaningful surface heating, so no thermal can exist and
# the observation teaches nothing.  The scheduler runs around the clock; without
# this an overnight run buries the buffer in negatives (1,640 samples / 1 positive
# in one night) and drowns out the daytime signal.
_MIN_SOLAR_GHI = 50.0     # W/m2 — roughly deep twilight

# An aircraft this close to the ground is not evidence about lift over it: it is
# on the field, on the roll, or on approach.  Such a row can only ever be a
# negative, and it is a negative for a reason that has nothing to do with the
# terrain underneath.
#
# Without this floor the buffer was 18% rows at *exactly* 0 m AGL, every one of
# them negative, and the model learned the wrong thing: measured on the frozen
# benchmark, alt_agl alone scored 0.631 within-group against 0.664 for the whole
# 22-column model, and dropping alt_agl collapsed the model to 0.530 — i.e. the
# score was almost entirely "aircraft on the ground are not thermalling".  True,
# and useless, because it says nothing about *where* the lift is.
#
# 200 m is deliberately mild.  It removes 38% of rows while costing 4 of 249
# positives, and leaves the frozen benchmark with 31 mixed-label groups — one
# above _MIN_BENCH_GROUPS, so the gate keeps working.  A tighter band (500-2500 m)
# or per-positive altitude matching removes the confound more completely but
# starves the benchmark to 28-30 groups, which switches the gate off; revisit
# both once the buffer holds more days.
_MIN_SAMPLE_AGL = 200.0   # metres above ground level

# Vario at or below which a straight-flying glider is taken as evidence that this
# cell held no usable lift at this height and time.
#
# The old rule made a negative out of every aircraft that was not confirmed
# thermalling, which is a *pseudo*-absence: it lumps in the glider dolphining
# through lift without circling and the one running a ridge, both of which are
# in rising air.  Training on those as negatives teaches the model that lift is
# absent exactly where it is present.
#
# A glider's still-air sink is roughly -0.6 to -1.0 m/s, so a straight aircraft
# reading below -0.5 is in air that is neutral at best.  That is a *true*
# absence — the observation the presence-only literature calls the expensive,
# missing half of the data, and which this project can read straight off its own
# beacon feed.  Everything between the two thresholds is ambiguous and is now
# discarded rather than mislabelled.
_NEGATIVE_MAX_VARIO = -0.5   # m/s

# Circling *and* climbing faster than this is a confirmed thermal.
_POSITIVE_MIN_VARIO = 1.5    # m/s

# Meaning of the labels a buffer was collected under, stored alongside it.
#   1 — negative = any aircraft present and not thermalling (pseudo-absence)
#   2 — negative = straight and sinking; the ambiguous band is discarded
# The feature width did not change between the two, so the `.vN` width check
# cannot tell them apart, and mixing them puts two definitions of "no lift" into
# one fit and one frozen benchmark.  A buffer from an older version is set aside
# on load, the same way a width mismatch is.  Bump this whenever _label_for
# changes what a 0 or a 1 means.
_LABEL_VERSION = 2


def _label_for(circling: bool, vario: float) -> int | None:
    """
    1 = confirmed thermal, 0 = confirmed absence of lift, None = ambiguous.

    Returning None rather than 0 for the middle band is the whole point: it is
    the difference between a pseudo-absence and a real one.  Both sampling paths
    go through here so the live loop and /seed cannot drift apart — they had
    already duplicated the old expression once each.
    """
    if circling and vario > _POSITIVE_MIN_VARIO:
        return 1
    if not circling and vario < _NEGATIVE_MAX_VARIO:
        return 0
    return None

# A fit needs enough of the positive class to be worth anything.  Below this the
# AUC comes back nan and the resulting model predicts ~0 everywhere; saving it
# would replace a working model with a degenerate one.
_MIN_POSITIVES = 20

_MIN_SAMPLES = 100
_MC_SAMPLES  = 50

# Rolling-window cap on the training buffer.  The old value of 21,024,000 was a
# cap in name only: online collection adds at most 20 samples per 300 s cycle
# (~5,760/day), so the window would not have begun to roll for a decade, and
# _buffer_X is a Python list of row arrays whose per-object overhead dwarfs the
# 176 bytes of float64 each row actually carries.  On an 896 MB box that is the
# slow leak that eventually matters.
#
# 200,000 rows is ~35 days of pure live collection, or several /seed rebuilds,
# and keeps the buffer in the tens of MB.  It also bounds how stale the oldest
# training data can get, which the chronological growth otherwise leaves open.
_MAX_BUFFER  = 200_000

# Distinct grid centres the seed keeps a built feature matrix for while working
# one bucket.  A bucket is 0.5 deg square and the grids snap to a 0.01 deg
# lattice, so a handful covers nearly every beacon in it.  Each entry is an
# elevation grid (~320 KB) plus its matrix (~7 MB), so this is the largest thing
# the seed holds: 4 is ~29 MB, which the container has, and a dict with no
# ceiling is what it does not.
_SEED_GRID_CACHE = 4


# Columns holding the cyclic day-of-year encoding.  Every row collected on one
# day carries the identical pair, so they group a buffer by day exactly, with no
# decoding and no rounding error.
_COL_DOY_SIN, _COL_DOY_COS = 17, 18


def _evict_to_cap(buf_X: list, buf_y: list) -> tuple[list, list, str]:
    """
    Trim the buffer to _MAX_BUFFER while preserving day coverage and positives.

    The cap itself is not negotiable — _buffer_X is a Python list of row arrays
    held in RAM on an 896 MB box.  What was wrong is *which* rows it dropped.
    Keeping the newest N is a chronological window, so a buffer that fills up
    holds one recent stretch of weather and nothing else, and the model can only
    ever know the season it is standing in.  Measured on this project's data,
    subsampling within a single 5-day stretch moves the score by less than its
    own noise (0.635 at 25% of groups against 0.664 at 100%): volume inside one
    regime buys nothing, so spending the whole cap on one is pure waste.

    So the cap is spent on breadth instead.  Every distinct day gets an equal
    quota; inside a day positives are kept first — they are 4% of the feed and
    each one cost two rate-limited API calls — and negatives fill the rest,
    newest first.  Chronological order is preserved in the result because other
    code reads the tail as "most recent".

    The cold archive holds everything regardless; this only decides what stays
    resident for the fit.
    """
    n = len(buf_X)
    if n <= _MAX_BUFFER:
        return buf_X, buf_y, ""

    X = np.asarray(buf_X)
    y = np.asarray(buf_y)
    # Exact grouping: rows from one day share the encoded pair bit for bit.
    _, day = np.unique(X[:, [_COL_DOY_SIN, _COL_DOY_COS]], axis=0,
                       return_inverse=True)
    n_days = int(day.max()) + 1
    quota = max(1, _MAX_BUFFER // n_days)

    keep = np.zeros(n, dtype=bool)
    for d in range(n_days):
        idx = np.flatnonzero(day == d)
        pos = idx[y[idx] == 1]
        neg = idx[y[idx] == 0]
        keep[pos[-quota:]] = True                  # positives first
        room = quota - min(len(pos), quota)
        if room > 0:
            keep[neg[-room:]] = True               # then the newest negatives

    # An unequal day mix can leave the total either side of the cap: days with
    # few rows do not use their quota, days that are all positives can exceed
    # it.  Settle it chronologically, which is the honest tie-break once every
    # day is already represented.
    chosen = np.flatnonzero(keep)
    if len(chosen) > _MAX_BUFFER:
        chosen = chosen[-_MAX_BUFFER:]

    note = (f"{n - len(chosen)} rows evicted across {n_days} days "
            f"(quota {quota}/day, positives kept first)")
    return [buf_X[i] for i in chosen], [buf_y[i] for i in chosen], note


def _for_serving(clf: "xgb.XGBClassifier") -> "xgb.XGBClassifier":
    """Retune a model from fit-time threads to inference-time threads.

    fit_and_gate builds its challenger with n_jobs=XGB_FIT_THREADS and, if the
    gate accepts it, promotes that same object to self.model.  The booster keeps
    the thread count it was fitted under (verified: nthread=1 in its config
    afterwards), so without this every prediction from that promotion until the
    next restart runs single-threaded — silently, since only the latency
    changes.  The fit yields cores precisely because nobody is waiting on it;
    serving always has someone waiting on it.

    save_model does *not* carry n_jobs across — a reloaded model comes back with
    n_jobs=None and nthread=0, meaning all cores — so load() is not exposed to
    the same trap.  It is retuned anyway to make the setting explicit and
    honour XGB_PREDICT_THREADS rather than silently taking every core.
    """
    clf.set_params(n_jobs=XGB_PREDICT_THREADS)
    return clf

# Mixed-label groups the frozen benchmark must contain before its score is
# trusted as a gate.  Only groups holding both a positive and a negative
# support a ranking comparison, and measured across fold seeds the score moved
# by ±0.05 on ~20 of them — as much as any improvement worth shipping.  Below
# this the comparison would be gating on noise, so it abstains instead.
_MIN_BENCH_GROUPS = 30

# Marks a model whose recorded score came from the frozen benchmark.  Models
# saved before this harness were fitted on a positional split, so their
# training data overlaps today's benchmark groups and they would score
# unfairly well on it.  Absent attribute => score is not comparable.
_BENCH_ATTR = "bench_micro"

# A fixed AUC tolerance used to stand here, chosen to match the observed noise
# band.  It is gone because the comparison it guarded is now paired — both
# models are scored on the same benchmark rows, so shared sampling noise
# cancels and the bootstrap reports the residual directly.  A rejection needs
# the whole interval below zero, which adapts to however much data exists
# instead of hard-coding one era's noise level.
#
# A lucky high score must still not freeze the model forever.  After this many
# consecutive rejections (~1 hour at the 300s cycle) accept the current fit and
# re-baseline to it, so online learning keeps adapting to the season and the
# bar cannot be set by a fluke that never recurs.
_MAX_CONSECUTIVE_SKIPS = 12

# (column, kind, scale) — meteo-only columns; terrain cols 0-4 are never perturbed
# kind="rel": multiplicative noise (scale = 1-sigma fraction of value)
# kind="abs": additive noise      (scale = 1-sigma in feature units)
_MC_NOISE: list[tuple[int, str, float]] = [
    (5,  "abs", 1.0),   # temp_2m     ±1 °C
    (6,  "abs", 0.05),  # humidity    ±5 pp
    (7,  "abs", 2.0),   # wind_u      ±2 m/s
    (8,  "abs", 2.0),   # wind_v      ±2 m/s
    (9,  "rel", 0.15),  # cape        ±15 %
    (10, "rel", 0.20),  # cin         ±20 %
    (11, "rel", 0.10),  # solar_ghi   ±10 %
    (12, "abs", 0.5),   # lapse_rate  ±0.5 K/km
    (19, "rel", 0.15),  # pbl_height  ±15 %
    (20, "abs", 2.0),   # soil_temp   ±2 °C
]

class ThermalModel:
    def __init__(self):
        self.model = None
        self._buffer_X: list[np.ndarray] = []
        self._buffer_y: list[int] = []
        self._skipped_fits = 0
        # Outcome of the most recent fit_and_gate call, for /training/status.
        # The log already says this, but a training node is watched through a
        # panel, not by tailing a container — and "why has the model not changed
        # in three days" has one answer that should be on screen.
        self.last_fit: dict | None = None
        self.last_seed: dict | None = None
        self._summary_cache: tuple[int, dict] | None = None

    def _note_fit(self, outcome: str, **detail) -> None:
        self.last_fit = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "outcome": outcome,
            **detail,
        }

    def training_summary(self) -> dict:
        """
        What the buffer holds, sliced the way decisions are made.

        Cached on buffer length: the benchmark grouping hashes every group, and a
        status panel polling every 30 s should not redo that on an unchanged
        buffer.  Runs in a worker thread from the endpoint.
        """
        from evaluation.regions import NAMES, region_of

        n = len(self._buffer_X)
        if self._summary_cache and self._summary_cache[0] == n:
            return self._summary_cache[1]

        if n == 0:
            out = {"rows": 0, "positives": 0, "days": 0, "label_version": _LABEL_VERSION,
                   "usable_rows": 0, "benchmark_groups": 0,
                   "benchmark_groups_needed": _MIN_BENCH_GROUPS,
                   "regions": {name: {"rows": 0, "positives": 0} for name in NAMES}}
            self._summary_cache = (n, out)
            return out

        # Snapshot first. This runs in a worker thread while retrain() appends on
        # the event loop, so the two lists can be one row apart between reads;
        # trimming to the shorter keeps rows and labels aligned.
        bx, by = list(self._buffer_X), list(self._buffer_y)
        k = min(len(bx), len(by))
        X = np.asarray(bx[:k])
        y = np.asarray(by[:k], dtype=int)
        days = len(np.unique(X[:, [_COL_DOY_SIN, _COL_DOY_COS]], axis=0))

        # Same population fit_and_gate trains on, so the group count shown is
        # the one the gate will actually see.
        keep = X[:, 21] >= _MIN_SAMPLE_AGL
        Xu, yu = X[keep], y[keep]
        groups = 0
        if len(yu):
            _, _, (_, yb, gb) = bench_split(Xu, yu)
            if len(yb):
                groups = grouped_auc(np.zeros(len(yb)), yb, gb)["groups"]

        reg = region_of(Xu)
        regions = {name: {"rows": int((reg == name).sum()),
                          "positives": int(yu[reg == name].sum())} for name in NAMES}
        out = {
            "rows": int(k), "positives": int(y.sum()), "days": int(days),
            "label_version": _LABEL_VERSION,
            "usable_rows": int(len(yu)),
            "benchmark_groups": int(groups),
            "benchmark_groups_needed": _MIN_BENCH_GROUPS,
            "regions": regions,
        }
        self._summary_cache = (n, out)
        return out

    def _saved_bench_score(self) -> float | None:
        """
        Benchmark score the in-memory model recorded, if it earned one here.

        Stored as a booster attribute rather than a sidecar file so the score
        travels inside thermal_xgb.json — the two can never disagree about which
        model earned it, including across a redeploy that ships a new model.

        None means "not comparable", which covers both no model and a model
        predating the frozen benchmark.  The gate treats that as no baseline
        rather than as a zero, so a legacy model is replaced once rather than
        defended with a score it never earned on these rows.
        """
        if self.model is None:
            return None
        try:
            raw = self.model.get_booster().attr(_BENCH_ATTR)
        except Exception:
            return None
        try:
            return float(raw) if raw is not None else None
        except ValueError:
            return None

    def _score_served(self, clf, X: np.ndarray) -> np.ndarray:
        """
        Score X exactly as production would.

        predict() multiplies model output by _terrain_multiplier, so that
        product — not the bare classifier — is what has to be evaluated.  The
        old gate scored the classifier alone and therefore never measured the
        artefact being served.  MC noise is skipped deliberately: it perturbs
        meteo columns, which are constant within a group, so it cannot change
        within-group ranking and would only add variance.
        """
        return clf.predict_proba(X)[:, 1] * self._terrain_multiplier(X)

    @property
    def is_loaded(self) -> bool:
        """True once a trained model is in memory; False while on physics fallback."""
        return self.model is not None

    async def load(self) -> None:
        p = Path(MODEL_PATH)
        if p.exists():
            candidate = xgb.XGBClassifier()
            candidate.load_model(str(p))
            width = getattr(candidate, "n_features_in_", None)
            if width is not None and width != FEATURE_COUNT:
                # Serving with a stale model is worse than not serving with one:
                # predict_proba raises a shape mismatch deep inside /predict,
                # which reaches the client as a bare 500 and an empty map. The
                # file is left on disk — retraining overwrites it in place.
                log.warning(
                    f"[model] trained on {width} features, expected {FEATURE_COUNT} — "
                    f"ignoring it and using physics fallback; POST /train to rebuild"
                )
            else:
                self.model = _for_serving(candidate)
                log.info(f"[model] loaded from {MODEL_PATH}")
        else:
            log.info("[model] no trained model found — using physics fallback")

        bp = Path(BUFFER_PATH)
        if not bp.exists():
            return

        # np.load returns a lazily-read NpzFile that holds the file open, and
        # Windows refuses to rename or delete an open file.  Read everything
        # inside the context manager and only touch the file after it closes.
        X = y = None
        width = None
        label_version = 1
        try:
            with np.load(str(bp), allow_pickle=False) as data:
                width = data["X"].shape[1]
                # Absent means written before labels were versioned, i.e. v1.
                if "label_version" in data.files:
                    label_version = int(data["label_version"])
                if width == FEATURE_COUNT:
                    X = data["X"]
                    y = data["y"].astype(int)
        except Exception as exc:
            log.warning(f"[model] buffer unreadable ({exc}) — setting it aside")
            _set_aside(bp, ".unreadable")
            return

        if width == FEATURE_COUNT and label_version != _LABEL_VERSION:
            dest = _set_aside(bp, f".labels-v{label_version}")
            log.warning(
                f"[model] buffer was labelled under rule v{label_version}, current is "
                f"v{_LABEL_VERSION} — set aside as {dest.name if dest else 'n/a'}; "
                f"starting a fresh buffer"
            )
            # Leave an empty current-version buffer behind.  Without it the file
            # is simply missing, and _seed_data_dir copies the image's bundled
            # (v1) buffer straight back in on the next start — to be set aside
            # again, one timestamped copy per restart, until something is saved.
            try:
                np.savez(bp, X=np.empty((0, FEATURE_COUNT)), y=np.empty((0,), dtype=int),
                         label_version=np.array(_LABEL_VERSION))
            except Exception as exc:
                log.warning(f"[model] could not write an empty buffer ({exc})")
            return

        if width != FEATURE_COUNT:
            # Set aside, not deleted: the samples are still real observations and
            # are expensive to re-collect, even though the feature layout has moved
            # on and they can no longer be mixed with new ones.
            dest = _set_aside(bp, f".v{width}")
            log.warning(
                f"[model] buffer has {width} features, expected {FEATURE_COUNT} — "
                f"set aside as {dest.name if dest else 'n/a'}; starting a fresh buffer"
            )
            return

        keep = _plausible_coords(X)
        dropped = int((~keep).sum())
        if dropped:
            # Samples written before the lat_bounds fix carry grid indices
            # (lat=100, lon=0) instead of degrees.  They describe a different
            # feature space than anything we serve, so drop them.
            log.warning(
                f"[model] dropping {dropped} buffer samples whose lat/lon are "
                f"not coordinates on Earth — pre-fix grid-index rows"
            )
            X, y = X[keep], y[keep]
        self._buffer_X = list(X)
        self._buffer_y = list(y)
        log.info(f"[model] buffer restored: {len(self._buffer_X)} samples from disk")

    def _save_buffer(self) -> None:
        """
        Write the sample buffer to disk, atomically.  Never raises — a failed
        save must not abort a retrain that has already done the expensive
        collection work.

        Written under a temporary name in the same directory and renamed into
        place, so an interrupted write cannot leave a half-file where the buffer
        was.  That is not hypothetical: on 28 Sep 2026 an OOM kill landed inside
        np.savez and the next start found "File is not a zip file", set the
        buffer aside and began again from zero — 12,381 samples and 10 days of
        weather down to nothing, and the benchmark from 77 groups to 3.
        np.savez writes a zip, so a truncated one is not partially usable; it is
        unreadable.

        This box OOM-kills the container by design (mem_limit is a blast radius
        and the restart is the recovery), so a non-atomic write to the one file
        that cannot be recollected was a real hazard, not a remote one.
        os.replace is atomic within a filesystem, and the temp file is created
        beside the target to guarantee that.
        """
        if not self._buffer_X:
            return
        tmp = None
        try:
            dest = Path(BUFFER_PATH)
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".npz.part")
            os.close(fd)
            tmp = Path(tmp_name)
            # np.savez appends .npz unless the target is a file object, and the
            # temp name already ends in one — so hand it the handle.
            with open(tmp, "wb") as fh:
                np.savez(
                    fh,
                    X=np.array(self._buffer_X),
                    y=np.array(self._buffer_y),
                    label_version=np.array(_LABEL_VERSION),
                )
                fh.flush()
                # The rename is atomic but says nothing about the bytes having
                # reached the platter; a host that loses power between the two
                # would publish a truncated file under the real name.
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
            tmp = None
        except Exception as exc:
            log.warning(f"[model] could not save buffer ({exc})")
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    async def retrain(self) -> None:
        """
        Fetch current OGN gliders, label each as thermal/no-thermal, accumulate
        into a rolling buffer, and refit the XGBoost classifier once enough
        samples are collected.
        """
        from data.ogn_client import fetch_ogn_gliders, is_thermal_evidence
        from data.meteo_client import fetch_meteo_features, live_usage as meteo_live_usage
        from data.terrain_client import fetch_elevation_grid, snap_grid_centre
        from pipeline.feature_engineering import build_feature_matrix

        try:
            gliders = await fetch_ogn_gliders()
        except Exception as exc:
            log.warning(f"[model] OGN unreachable ({exc}) — skipping retrain")
            return
        if not gliders:
            log.info("[model] no gliders returned — skipping")
            return

        now = datetime.now(timezone.utc)
        # Soaring aircraft not on aerotow.  A powered aircraft circling in a climb
        # would be labelled a confirmed thermal, teaching the model lift that never
        # existed — and a glider under tow climbs at 2-3 m/s for the same reason.
        # No vario pre-filter beyond that: straight-cruising gliders are valid negatives.
        soaring = [g for g in gliders if is_thermal_evidence(g)]
        if not soaring:
            log.info("[model] no soaring aircraft in view — skipping")
            return
        candidates = soaring[:MAX_SAMPLE_GRIDS]
        # Stop on whichever bound arrives first — see MAX_SAMPLE_GRIDS.
        deadline = time.monotonic() + SAMPLE_TIME_BUDGET_S
        out_of_time = out_of_quota = False
        above_base = dark = on_ground = ambiguous = no_terrain = 0
        # Ids already sampled this cycle.  A glider can be both a candidate and
        # a co-occupant of another candidate's grid; sampling it twice would
        # duplicate a row and let the same aircraft sit on both sides of the
        # train/holdout split.
        sampled: set[str] = set()
        offset_samples = 0
        considered = 0        # aircraft examined, which now exceeds len(candidates)

        for g in candidates:
            try:
                if g["id"] in sampled:
                    continue          # already captured inside an earlier grid
                if time.monotonic() > deadline:
                    out_of_time = True
                    break
                usage = meteo_live_usage()
                # total, not just the forecast count: the nightly seed spends
                # the same allowance from the archive endpoint.
                if usage["total"] >= usage["quota"]:
                    out_of_quota = True
                    break
                meteo = await fetch_meteo_features(g["lat"], g["lon"], 0)
                # Checked before the 7 MB feature matrix is built, not after:
                # at night every candidate is dropped here, and building first
                # spent the memory and the CPU to throw the result away.
                if meteo["solar_ghi"] < _MIN_SOLAR_GHI:
                    dark += 1
                    continue
                # strict: a failed fetch must skip the grid, not train on the
                # flat-zero stand-in (see fetch_elevation_grid).
                elev  = await fetch_elevation_grid(g["lat"], g["lon"], strict=True)
                if elev is None:
                    no_terrain += 1
                    continue
                # Bounds must come from the SNAPPED centre, not the raw glider
                # position: fetch_elevation_grid builds and caches the array for
                # the snapped point, so pairing it with unsnapped bounds labels
                # every cell with coordinates up to ~550 m from the ground it
                # actually describes.  /predict already snaps; training did not,
                # which made served and trained features disagree.
                grid_lat, grid_lon = snap_grid_centre(g["lat"], g["lon"])
                lat_bounds = (grid_lat - GRID_RADIUS, grid_lat + GRID_RADIUS)
                lon_bounds = (grid_lon - GRID_RADIUS, grid_lon + GRID_RADIUS)
                feat  = build_feature_matrix(
                    meteo, elev, dt=now,
                    lat_bounds=lat_bounds,
                    lon_bounds=lon_bounds,
                    alt_amsl=g["alt"],
                    land_use_props=await _land_props(g["lat"], g["lon"], elev.shape),
                )
                # Every soaring aircraft inside this grid, not just the one it is
                # centred on.  They share the grid's weather and terrain, so they
                # cost no extra API calls, and they land on *different cells* —
                # which is the only way to observe whether terrain variation
                # within one weather regime tracks lift.  Sampling only the centre
                # gave one terrain value per weather sample, so the two could
                # never be separated.
                occupants = [
                    o for o in soaring
                    if o["id"] not in sampled
                    and lat_bounds[0] <= o["lat"] <= lat_bounds[1]
                    and lon_bounds[0] <= o["lon"] <= lon_bounds[1]
                ]
                for o in occupants:
                    idx = _grid_index(o["lat"], o["lon"], lat_bounds, lon_bounds, elev.shape)
                    if idx is None:
                        continue
                    considered += 1
                    row = feat[idx].copy()   # copy: a view pins the whole ~6.4 MB matrix
                    # alt_agl (col 21) was built from the *candidate's* altitude for
                    # every cell, so it is wrong for anyone else.  Recompute against
                    # this aircraft's own altitude and the elevation under its cell.
                    row[21] = max(0.0, float(o["alt"]) - float(row[2]))
                    # Too low to say anything about lift over this ground — see
                    # _MIN_SAMPLE_AGL.  Rejected before the thermal-base test
                    # because it is the cheaper check and the two are disjoint.
                    if row[21] < _MIN_SAMPLE_AGL:
                        on_ground += 1
                        sampled.add(o["id"])   # judged and rejected; do not retry
                        continue
                    if _above_thermal_base(o["alt"], row[2], meteo["cape_base"]):
                        above_base += 1
                        sampled.add(o["id"])   # judged and rejected; do not retry
                        continue
                    label = _label_for(bool(o["circling"]), float(o["vario"]))
                    if label is None:
                        ambiguous += 1
                        sampled.add(o["id"])   # judged and rejected; do not retry
                        continue
                    self._buffer_X.append(row)
                    self._buffer_y.append(label)
                    sampled.add(o["id"])
                    if o["id"] != g["id"]:
                        offset_samples += 1
                # Pacing for OpenTopoData's 1 req/s.  Copernicus is range reads
                # against object storage with no such limit, and at 60 candidates
                # the sleep alone would eat two thirds of the cycle's budget.
                if TERRAIN_SOURCE != "copernicus":
                    await asyncio.sleep(1.1)
            except Exception as exc:
                log.warning(f"[model] sample skipped: {exc}")

        if out_of_time or out_of_quota:
            log.info(f"[model] cycle stopped early — "
                     f"{'time budget' if out_of_time else 'Open-Meteo quota'} reached "
                     f"after {considered} aircraft")
        if on_ground:
            log.info(f"[model] {on_ground}/{considered} dropped — below "
                     f"{_MIN_SAMPLE_AGL:.0f} m AGL")
        if ambiguous:
            log.info(f"[model] {ambiguous}/{considered} dropped — neither a "
                     f"confirmed climb nor confirmed sink")
        if no_terrain:
            log.warning(f"[model] {no_terrain}/{len(candidates)} grids skipped — terrain "
                        f"unavailable (quota or upstream); not trained on a flat stand-in")
        if above_base:
            log.info(f"[model] {above_base}/{considered} dropped — above thermal base")
        if dark:
            log.info(f"[model] {dark}/{len(candidates)} grids dropped — solar below {_MIN_SOLAR_GHI:.0f} W/m2")
        if offset_samples:
            log.info(f"[model] {considered - offset_samples} centre + {offset_samples} off-centre "
                     f"samples from {len(candidates)} grids (no extra API calls)")

        # Cap the resident buffer, spending it on day coverage rather than recency
        self._buffer_X, self._buffer_y, note = _evict_to_cap(
            self._buffer_X, self._buffer_y)
        if note:
            log.info(f"[model] {note}")

        # Persist before the fit-threshold check.  Collecting a sample costs two
        # rate-limited API calls, so losing a partial buffer to a restart throws
        # away real work; durability must not depend on reaching the fit threshold.
        self._save_buffer()

        # to_thread for the same reason as predict(): the fit is pure CPU and the
        # app runs a single worker, so calling it inline froze every request for
        # the duration of the boosting run.  Only one fit runs at a time — both
        # callers hold _training_lock in main.py — and rebinding self.model is a
        # single attribute assignment, so a concurrent predict() sees either the
        # old model or the new one, never a half-built state.
        await asyncio.to_thread(self.fit_and_gate)

    def fit_and_gate(self) -> None:
        """
        Fit on the current buffer and decide whether the result may replace the
        model in service.

        Split out from retrain() so the decision can be exercised without
        standing up the whole collection path — the gate is the part with the
        subtle failure modes, and mocking OGN, meteo and terrain to reach it
        made those tests slow and indirect.
        """
        n = len(self._buffer_X)
        if n < _MIN_SAMPLES:
            log.info(f"[model] buffer {n}/{_MIN_SAMPLES} samples — skipping fit")
            self._note_fit("too_few_samples", samples=n, need=_MIN_SAMPLES)
            return

        X = np.array(self._buffer_X)
        y = np.array(self._buffer_y)

        # Apply the ground floor here as well as at collection.  Buffers written
        # before _MIN_SAMPLE_AGL existed are full of the rows it excludes, and a
        # rebuild costs two rate-limited API calls per sample — far too expensive
        # to demand before the fix takes effect.  Filtering at fit time cleans an
        # old buffer on the next cycle and costs one boolean mask.  Collection
        # still drops them so they stop consuming the rolling window.
        keep = X[:, 21] >= _MIN_SAMPLE_AGL
        if not keep.all():
            log.info(f"[model] {int((~keep).sum())}/{len(y)} buffer rows below "
                     f"{_MIN_SAMPLE_AGL:.0f} m AGL excluded from this fit")
            X, y = X[keep], y[keep]
            n = len(y)
            if n < _MIN_SAMPLES:
                log.info(f"[model] {n}/{_MIN_SAMPLES} samples after the AGL floor — "
                         f"skipping fit")
                self._note_fit("too_few_samples", samples=n, need=_MIN_SAMPLES)
                return

        pos = int(y.sum())

        # Refuse to fit on a buffer with almost no positives.  XGBoost will happily
        # return a model that predicts ~0 everywhere and an AUC of nan, and saving
        # that would overwrite a good model with a useless one.  Keep what we have.
        if pos < _MIN_POSITIVES:
            log.warning(
                f"[model] only {pos} positives in {n} samples (need {_MIN_POSITIVES}) — "
                f"skipping fit and keeping the existing model"
            )
            self._note_fit("too_few_positives", samples=n, positives=pos,
                           need=_MIN_POSITIVES)
            return

        # Frozen, group-disjoint three-way split (see evaluation/holdout.py).
        # The old positional split scored each fit on the newest 20% and compared
        # that to a number the saved model earned on a *different* newest 20%, so
        # the gate was differencing two measurements taken on different samples —
        # no tolerance value can rescue that.  Here every fit meets the same
        # benchmark rows, and whole (day, region) groups move together so the
        # near-duplicate rows that occupants of one grid produce cannot straddle
        # the boundary.
        (Xf, yf, _), (Xes, yes, _), (Xb, yb, gb) = bench_split(X, y)

        bench = (grouped_auc(np.zeros(len(yb)), yb, gb) if len(yb)
                 else {"groups": 0, "pairs": 0})
        gated = bench["groups"] >= _MIN_BENCH_GROUPS

        # Early stopping picks the boosting round, so it needs its own slice.
        # Reading the reported score off the same rows that chose the round
        # reports the maximum of a noisy series over ~200 rounds as if it were
        # an estimate, which inflated both the level and the swing.
        use_es = len(yes) > 0 and 0 < int(yes.sum()) < len(yes)
        fit_pos = int(yf.sum())

        clf = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=max(1.0, (len(yf) - fit_pos) / max(1, fit_pos)),
            tree_method="hist",
            n_jobs=XGB_FIT_THREADS,
            eval_metric="auc",
            random_state=42,
            # XGBoost >= 2.0 takes early stopping on the constructor; passing it to
            # fit() raises TypeError.  Only valid alongside an eval_set.
            early_stopping_rounds=20 if use_es else None,
        )
        if use_es:
            clf.fit(Xf, yf, eval_set=[(Xes, yes)], verbose=False)
        else:
            clf.fit(Xf, yf)

        if not gated:
            # Not enough of the benchmark is comparable to judge this fit.
            # Replacing a working model on an unverifiable basis is exactly the
            # failure this gate exists to stop, so keep what is already serving
            # and let the buffer grow.  With no model at all, an unverified one
            # still beats the physics fallback.
            if self.model is not None:
                log.info(
                    f"[model] benchmark has {bench['groups']} mixed-label groups "
                    f"(need {_MIN_BENCH_GROUPS}) — cannot verify this fit, "
                    f"keeping the model already in service"
                )
                self._note_fit("benchmark_too_small", samples=n, positives=pos,
                               groups=bench["groups"], need=_MIN_BENCH_GROUPS)
                self._save_buffer()
                return
            clf.get_booster().set_attr(**{_BENCH_ATTR: None})
            log.warning(
                f"[model] no model in service and benchmark not yet scoreable "
                f"({bench['groups']}/{_MIN_BENCH_GROUPS} groups) — accepting an "
                f"unverified fit on {len(yf)} samples"
            )
            self._note_fit("accepted_unverified", samples=n, positives=pos,
                           groups=bench["groups"], need=_MIN_BENCH_GROUPS)
        else:
            challenger = self._score_served(clf, Xb)
            score = grouped_auc(challenger, yb, gb)["micro"]
            if not np.isfinite(score):
                log.warning("[model] benchmark score is not finite — discarding this fit")
                self._note_fit("score_not_finite", samples=n, positives=pos,
                               groups=bench["groups"])
                return

            baseline = self._saved_bench_score()
            if baseline is not None:
                # Both models scored on the *same* rows, so the comparison is
                # paired: shared sampling noise cancels in the difference, which
                # is far more sensitive than differencing two independent
                # intervals.  Reject only when the evidence says the challenger
                # is genuinely worse, not merely worse on the point estimate.
                delta = paired_delta_ci(
                    self._score_served(self.model, Xb), challenger, yb, gb,
                    n_boot=1000,
                )
                if delta["hi"] < 0:
                    self._skipped_fits += 1
                    if self._skipped_fits < _MAX_CONSECUTIVE_SKIPS:
                        log.info(
                            f"[model] benchmark {score:.3f} vs serving {baseline:.3f}, "
                            f"paired delta {delta['point']:+.3f} "
                            f"[{delta['lo']:+.3f}, {delta['hi']:+.3f}] over "
                            f"{delta['groups']} groups — reliably worse, keeping the "
                            f"serving model [{self._skipped_fits}/{_MAX_CONSECUTIVE_SKIPS}]"
                        )
                        self._note_fit(
                            "rejected", samples=n, positives=pos,
                            groups=bench["groups"], score=round(score, 4),
                            serving=round(baseline, 4),
                            delta=[round(delta["lo"], 4), round(delta["point"], 4),
                                   round(delta["hi"], 4)],
                            skips=self._skipped_fits, max_skips=_MAX_CONSECUTIVE_SKIPS,
                        )
                        self._save_buffer()   # samples are still worth keeping
                        return
                    # Re-baseline rather than stay frozen on a bar we can no
                    # longer clear: the conditions that produced it are gone.
                    log.warning(
                        f"[model] {self._skipped_fits} consecutive fits below the bar — "
                        f"accepting benchmark {score:.3f} and re-baselining "
                        f"from {baseline:.3f}"
                    )
            rebaselined = self._skipped_fits >= _MAX_CONSECUTIVE_SKIPS
            self._skipped_fits = 0
            self._note_fit("rebaselined" if rebaselined else "accepted",
                           samples=n, positives=pos, groups=bench["groups"],
                           score=round(score, 4),
                           serving=None if baseline is None else round(baseline, 4))
            clf.get_booster().set_attr(**{_BENCH_ATTR: f"{score:.6f}"})
            log.info(
                f"[model] retrained on {len(yf)} samples ({fit_pos} positives), "
                f"benchmark within-group AUC={score:.3f} over {bench['groups']} "
                f"groups / {bench['pairs']} pairs, saved to {MODEL_PATH}"
            )

        Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        # Retune before saving, so the file on disk carries the serving setting
        # too — load_model restores whatever n_jobs was saved with it.
        _for_serving(clf)
        clf.save_model(MODEL_PATH)
        self.model = clf

        self._save_buffer()

    async def seed_from_history(
        self,
        days_back: float | None = None,
        limit: int = 5000,
        reset: bool = False,
    ) -> dict:
        """
        Backfill the training buffer from the historical beacons DB.

        Groups beacons into (0.5° lat × 0.5° lon × UTC hour) buckets so that
        historical meteo is fetched once per bucket rather than once per beacon.
        Terrain is fetched via fetch_elevation_grid, which snaps to the 0.01°
        lattice and caches per-point, so beacons in the same area reuse each
        other's coarse elevations and repeated calls are near-free.

        Labels are derived the same way as online retraining:
          1 = circling AND vario > 1.5 m/s  (confirmed thermal)
          0 = glider present but not thermalling

        reset=True discards the existing buffer first, so the rebuild replaces
        rather than appends.  Use it when samples were captured under a feature
        pipeline that has since been corrected and are no longer comparable.
        """
        from collections import defaultdict
        from datetime import datetime, timezone, timedelta
        import sqlite3
        from data.meteo_client import fetch_meteo_historical
        from data.terrain_client import fetch_elevation_grid, snap_grid_centre
        from data.ogn_client import SOARING_AC_TYPES
        from pipeline.feature_engineering import (build_feature_matrix, cyclic,
                                                  COL_ALT_AGL, COL_HOUR_SIN,
                                                  COL_HOUR_COS)

        from config import DB_PATH as db_path, BEACON_RETENTION_DAYS
        if not db_path.exists():
            return {"error": "no DB found"}

        if reset:
            discarded = len(self._buffer_X)
            self._buffer_X = []
            self._buffer_y = []
            log.info(f"[seed] reset — discarded {discarded} existing buffer samples")

        # Retention decides what actually exists, so it is the ceiling here.  A
        # days_back past the window is not an error the caller ever sees: the
        # query just matches fewer rows and the seed reports success on a short
        # sample.  Clamp it and say so, rather than seeding on history that was
        # purged.  None means "whatever the window still holds".
        # The hot DB is a cache, not the record.  Anything past its window is
        # served from the cold archive, so days_back is only clamped when there
        # is no archive to reach into — which is what made a rebuild unable to
        # see more than one weather regime.
        from data import archive as _archive
        from config import ARCHIVE_DIR
        archive_days = _archive.archived_days(ARCHIVE_DIR) if _archive.available() else set()

        if days_back is None:
            days_back = BEACON_RETENTION_DAYS
        elif days_back > BEACON_RETENTION_DAYS and not archive_days:
            log.warning(
                f"[seed] days_back={days_back:g} exceeds the "
                f"{BEACON_RETENTION_DAYS:g}-day retention window and no archive "
                f"is present — clamped; older beacons were already purged"
            )
            days_back = BEACON_RETENTION_DAYS

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

        # Soaring aircraft only.  Rows written before aircraft-type filtering have
        # ac_type NULL and cannot be classified — they may be jets, helicopters or
        # ground receivers, so they are excluded rather than trusted.
        #
        # under_tow = 0 drops gliders climbing on a rope.  NULL is excluded too:
        # it marks rows logged before tow planes were admitted to the feed, when
        # an aerotow was invisible and its climb looked exactly like a thermal.
        placeholders = ",".join("?" * len(SOARING_AC_TYPES))
        soaring_ids  = tuple(sorted(SOARING_AC_TYPES))
        # Select only beacons _label_for can actually label.  Sampling uniformly
        # and discarding the ambiguous ones afterwards would spend the rate-limited
        # terrain and meteo budget on rows that never reach the buffer — and the
        # ambiguous band is the majority of the feed, so most of the budget.
        # The thresholds come from the constants so this cannot drift away from
        # the labelling rule it is meant to mirror.
        labelable = (f"((circling = 1 AND vario > {_POSITIVE_MIN_VARIO}) OR "
                     f"(circling = 0 AND vario < {_NEGATIVE_MAX_VARIO}))")
        where = (f"ts >= ? AND ac_type IN ({placeholders}) AND under_tow = 0 "
                 f"AND {labelable}")

        with sqlite3.connect(db_path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(beacons)")}
            missing = {"ac_type", "under_tow"} - cols
            if missing:
                return {
                    "error": f"beacons table has no {'/'.join(sorted(missing))} column(s) — "
                             "restart the app to migrate, then collect fresh data",
                    "added": 0,
                    "buffer_total": len(self._buffer_X),
                }
            total = con.execute(
                f"SELECT COUNT(*) FROM beacons WHERE {where}",
                (cutoff, *soaring_ids),
            ).fetchone()[0]
            if total == 0:
                log.warning(
                    "[seed] no untowed classified soaring beacons in the window — "
                    "history predates aircraft-type or aerotow filtering; let the "
                    "stream collect first"
                )
                return {
                    "rows_scanned": 0, "added": 0,
                    "reason": "no rows with a soaring ac_type and under_tow = 0",
                    "buffer_total": len(self._buffer_X),
                }
            sample_rate = max(1, total // limit)
            rows = con.execute(
                f"""
                SELECT ts, lat, lon, alt, vario, circling
                FROM beacons
                WHERE {where}
                  AND (ROWID % ?) = 0
                LIMIT ?
                """,
                (cutoff, *soaring_ids, sample_rate, limit),
            ).fetchall()

        # Days the archive holds that the hot DB no longer does.  The boundary
        # comes from what the DB *actually* still holds, not from
        # BEACON_RETENTION_DAYS: the two agree only while the purge is keeping
        # up, and when it is not — a failed export, an app that was down, a
        # fresh restore — every overlapping day would be read twice and land in
        # the buffer twice.  Measured once: a DB holding both days the archive
        # held turned a 10,000-row seed into 20,000 rows of half-duplicates.
        #
        # Requested and archived days are also intersected rather than assumed
        # contiguous: a gap from a failed export must not silently become a
        # claim of coverage.
        want_from = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()
        hot_first = None
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30) as con:
                first = con.execute("SELECT MIN(substr(ts, 1, 10)) FROM beacons").fetchone()[0]
                if first:
                    hot_first = datetime.fromisoformat(first).date()
        except Exception as exc:
            log.warning(f"[seed] could not read the hot DB's oldest day ({exc}) — "
                        f"skipping the archive to avoid double-counting")
        older = sorted(d for d in archive_days
                       if hot_first is not None and want_from <= d < hot_first)
        if older:
            try:
                cold = _archive.training_rows(
                    ARCHIVE_DIR, older[0], older[-1], sorted(SOARING_AC_TYPES),
                    _POSITIVE_MIN_VARIO, _NEGATIVE_MAX_VARIO, limit,
                )
                log.info(f"[seed] archive contributed {len(cold):,} rows over "
                         f"{len(older)} day(s) {older[0]}..{older[-1]}")
                rows = list(rows) + cold
            except Exception as exc:
                log.warning(f"[seed] archive read failed, using hot DB only: {exc}")

        if not rows:
            return {"rows_scanned": 0, "added": 0, "buffer_total": len(self._buffer_X)}

        # Snap lat/lon to the nearest 0.5° grid for meteo bucketing
        def _snap(v: float) -> float:
            return round(round(v * 2) / 2, 1)

        buckets: dict = defaultdict(list)
        for ts, lat, lon, alt, vario, circling in rows:
            dt  = datetime.fromisoformat(ts)
            key = (_snap(lat), _snap(lon), dt.strftime("%Y-%m-%d"), dt.hour)
            buckets[key].append((lat, lon, float(alt), dt, float(vario), int(circling)))

        log.info(f"[seed] {len(rows)} beacons → {len(buckets)} meteo buckets")

        added = errors = skipped_no_meteo = above_base = dark = on_ground = 0
        ambiguous = no_terrain = 0
        done_buckets = 0

        from data.meteo_client import live_usage as _meteo_usage
        quota_stopped = False
        time_stopped = False

        # Two ceilings, and the wall clock is the one that matters on a small
        # box.  The meteo budget bounds what this job costs Open-Meteo; it does
        # not bound what it costs the machine, and on 18 Sep 2026 that was the
        # difference between a long night and 36 h of thrash — 1,430 buckets
        # never came near 6,000 calls, so nothing ever stopped the run.
        deadline = time.monotonic() + SEED_TIME_BUDGET_S

        for (lat_b, lon_b, date_str, hour), beacons in buckets.items():
            if time.monotonic() >= deadline:
                time_stopped = True
                log.warning(f"[seed] stopping at the {SEED_TIME_BUDGET_S:.0f}s time budget — "
                            f"{len(buckets) - done_buckets} buckets left for next time")
                break
            # One Open-Meteo allowance covers this job and the live sampler, and
            # the live one has to keep working tomorrow morning: stop at the
            # budget rather than at the hard quota.
            u = _meteo_usage()
            if u["total"] >= min(SEED_METEO_BUDGET, u["quota"]):
                quota_stopped = True
                log.warning(f"[seed] stopping at the meteo budget "
                            f"({u['total']}/{SEED_METEO_BUDGET}) — "
                            f"{len(buckets) - done_buckets} buckets left for next time")
                break
            try:
                dt_b  = datetime.fromisoformat(f"{date_str}T{hour:02d}:00:00+00:00")
                # strict: a failed fetch returns None rather than synthetic
                # constants, so we drop the bucket instead of training on invented weather
                meteo = await fetch_meteo_historical(lat_b, lon_b, dt_b, strict=True)
            except Exception as exc:
                log.warning(f"[seed] meteo bucket ({lat_b},{lon_b},{date_str},{hour}) failed: {exc}")
                errors += len(beacons)
                continue
            if meteo is None:
                skipped_no_meteo += len(beacons)
                continue
            if meteo["solar_ghi"] < _MIN_SOLAR_GHI:
                # Whole bucket is one hour at one place — if it was dark there,
                # no beacon in it can represent a thermal.
                dark += len(beacons)
                continue

            # One bucket's worth, discarded with the bucket: beacons in the
            # next 0.5 deg square share neither its terrain nor its weather.
            grid_cache: dict = {}

            done_buckets += 1
            if done_buckets % 50 == 0:
                log.info(
                    f"[seed] {done_buckets}/{len(buckets)} buckets — "
                    f"{added} samples, {skipped_no_meteo} no-meteo, "
                    f"{dark} dark, {above_base} above-base, {errors} errors"
                )

            for lat, lon, alt, dt, vario, circling in beacons:
                try:
                    # Snapped bounds — see retrain() for why raw coordinates
                    # mislabel every cell in the grid.
                    grid_lat, grid_lon = snap_grid_centre(lat, lon)
                    # Beacons cluster: one bucket is a 0.5 deg square in a single
                    # hour, so most of them snap to a handful of grid centres and
                    # re-read the same 201x201 elevation grid.  fetch_elevation_grid
                    # caches to disk, but rebuilding the array per beacon is still
                    # ~320 KB of allocation apiece for a value already in hand.
                    # Bounded on purpose — an unbounded cache is the kind of slow
                    # growth that only shows up on the box, at night.
                    lat_bounds = (grid_lat - GRID_RADIUS, grid_lat + GRID_RADIUS)
                    lon_bounds = (grid_lon - GRID_RADIUS, grid_lon + GRID_RADIUS)
                    # One matrix per grid centre per bucket, not one per beacon.
                    #
                    # A 201x201x22 matrix is ~7 MB and the seed keeps a single
                    # row of it.  Beacons in a bucket are one 0.5 deg square in
                    # one hour, so they share a handful of centres; the live
                    # sampler has always built one matrix per grid and read every
                    # aircraft inside it, and this is the same move.  Doing it
                    # per beacon is what drove the container to its cap and the
                    # host into swap on 18 and 21 Sep 2026 -- the DB was 0.12 GB
                    # by the second time, so the 2 GB scan was never the whole
                    # story.
                    #
                    # The matrix is built with alt_amsl=None, which is the grid's
                    # own features with COL_ALT_AGL zeroed; the three columns that
                    # belong to the beacon rather than to the grid are corrected
                    # on its row below.  test_seed_row_matches_a_full_build pins
                    # that the result is identical to the per-beacon build.
                    cached = grid_cache.get((grid_lat, grid_lon))
                    if cached is None:
                        elev = await fetch_elevation_grid(lat, lon, strict=True)
                        if elev is None:
                            no_terrain += 1
                            continue
                        base = build_feature_matrix(
                            meteo, elev, dt=dt,
                            lat_bounds=lat_bounds,
                            lon_bounds=lon_bounds,
                            alt_amsl=None,
                            land_use_props=await _land_props(lat, lon, elev.shape),
                        )
                        if len(grid_cache) >= _SEED_GRID_CACHE:
                            grid_cache.clear()
                        cached = (elev, base)
                        grid_cache[(grid_lat, grid_lon)] = cached
                    elev, base = cached
                    # The beacon's own cell, not the grid centre: snapping moves
                    # the centre off the aircraft by up to half a coarse cell.
                    idx = _grid_index(lat, lon, lat_bounds, lon_bounds, elev.shape)
                    if idx is None:
                        continue
                    row = base[idx].copy()
                    # Height above the ground under *this* cell, clipped at 0 for
                    # the same reason build_feature_matrix clips it.
                    row[COL_ALT_AGL] = max(float(alt) - float(elev.ravel()[idx]), 0.0)
                    # The bucket is one hour; its beacons are spread across the
                    # minutes, and the encoding has minute resolution.
                    row[COL_HOUR_SIN], row[COL_HOUR_COS] = cyclic(
                        dt.hour + dt.minute / 60, 24)
                    # Same ground floor the live path applies — see
                    # _MIN_SAMPLE_AGL.  A backfill that skipped it would refill
                    # the buffer with exactly the rows the live path now drops.
                    if row[COL_ALT_AGL] < _MIN_SAMPLE_AGL:
                        on_ground += 1
                        continue
                    if _above_thermal_base(alt, row[2], meteo["cape_base"]):
                        above_base += 1
                        continue
                    label = _label_for(bool(circling), float(vario))
                    if label is None:
                        ambiguous += 1
                        continue
                    # Already a copy of one row — never a view, which would
                    # retain the whole cached matrix.  See retrain().
                    self._buffer_X.append(row)
                    self._buffer_y.append(label)
                    added += 1
                except Exception as exc:
                    log.warning(f"[seed] feature failed at ({lat:.3f},{lon:.3f}): {exc}")
                    errors += 1

            await asyncio.sleep(0.3)  # polite pacing for the Open-Meteo archive API

        # Same day-stratified eviction as the live path — a /seed rebuild is
        # exactly the case where one long stretch could otherwise crowd out
        # every other day the archive still holds.
        self._buffer_X, self._buffer_y, note = _evict_to_cap(
            self._buffer_X, self._buffer_y)
        if note:
            log.info(f"[seed] {note}")

        if added > 0:
            self._save_buffer()

        result = {
            "rows_scanned":     len(rows),
            "meteo_buckets":    len(buckets),
            "added":                added,
            "errors":               errors,
            "skipped_no_meteo":     skipped_no_meteo,
            "skipped_above_base":   above_base,
            "skipped_dark":         dark,
            "skipped_on_ground":    on_ground,
            "skipped_ambiguous":    ambiguous,
            "skipped_no_terrain":   no_terrain,
            "stopped_at_meteo_budget": quota_stopped,
            "stopped_at_time_budget":  time_stopped,
            "buffer_total":     len(self._buffer_X),
        }
        log.info(f"[seed] complete: {result}")
        return result

    def predict(self, features: np.ndarray) -> tuple[list[float], list[float]]:
        # Terrain columns (0-4) are never perturbed by the MC noise below, so the
        # multiplier is constant across runs — compute it once.  It is applied to
        # the trained model's output because the model is fitted on grid-centre
        # samples only and so carries no spatial signal of its own; without this
        # the heatmap is uniform across every cell.  _physics_fallback already
        # applies it internally, so it is not re-applied there.
        terrain = self._terrain_multiplier(features)
        scorer = (
            (lambda f: self.model.predict_proba(f)[:, 1] * terrain)
            if self.model
            else (lambda f: np.array(self._physics_fallback(f)))
        )
        rng  = np.random.default_rng()
        runs = np.empty((_MC_SAMPLES, features.shape[0]))
        for i in range(_MC_SAMPLES):
            f = features.copy()
            for col, kind, scale in _MC_NOISE:
                if col >= f.shape[1]:
                    continue
                noise = rng.standard_normal() * scale
                if kind == "rel":
                    f[:, col] *= (1.0 + noise)
                else:
                    f[:, col] += noise
            runs[i] = scorer(f)
        mean = np.clip(runs.mean(axis=0), 0, 1)
        std  = runs.std(axis=0)
        return mean.tolist(), std.tolist()

    def _terrain_multiplier(self, features: np.ndarray) -> np.ndarray:
        """
        Per-cell multiplier derived from terrain so model output varies
        spatially even when weather inputs are uniform across the grid.
        Applied by both predict() and _physics_fallback().

        Feature columns (see feature_engineering.py):
          2 = elevation (m)   3 = slope (deg)   4 = aspect (deg, 0=N clockwise)
        """
        elev   = features[:, 2]
        slope  = features[:, 3]
        aspect = features[:, 4]

        # South-facing slopes (aspect ≈ 180°) heat faster → stronger thermals
        south = 0.75 + 0.25 * np.cos(np.radians(aspect - 180))   # 0.5–1.0

        # Steeper slope → more orographic mixing, capped at 1.4×
        steep = np.clip(1.0 + slope / 45.0, 1.0, 1.4)

        # Moderate elevation boost (ridge tops trigger thermals earlier)
        elev_boost = np.clip(1.0 + elev / 800.0, 1.0, 1.25)

        return south * steep * elev_boost

    def _physics_fallback(self, features: np.ndarray) -> list[float]:
        """
        Terrain-aware physics fallback used before the model is trained.
        Feature columns: 2=elev 3=slope 4=aspect 9=cape 10=cin 11=solar_ghi
        """
        cape  = features[:, 9]
        cin   = np.abs(features[:, 10])
        solar = features[:, 11]

        cape_score  = np.clip(cape / 1500, 0, 1)
        solar_boost = np.clip(solar / 600, 0, 1)
        cin_penalty = np.clip(1 - cin / 200, 0, 1)

        base = cape_score * (0.4 + 0.6 * solar_boost) * cin_penalty
        return np.clip(base * self._terrain_multiplier(features), 0, 1).tolist()
