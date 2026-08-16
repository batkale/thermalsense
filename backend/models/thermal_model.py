import asyncio
import logging
import time
import xgboost as xgb
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from config import MODEL_PATH, BUFFER_PATH, GRID_RADIUS, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX
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
    Boolean mask of rows whose lat/lon (cols 0,1) are real coordinates inside the
    configured bounding box, rather than the grid indices an unbounded
    build_feature_matrix call produces.

    Note this guard is only as tight as the box.  While the feed was Europe-only
    it also caught index pairs like (50, 100) — lon 100 was outside the box.  Now
    that the box is worldwide, (50, 100) reads as a valid point in Mongolia and
    survives; only the lat > 90 signature (the documented one, lat=100) is still
    caught.  That is inherent to accepting worldwide traffic — a sample really can
    come from anywhere — and it affects legacy pre-fix buffer rows only, not
    anything written since.
    """
    lat, lon = X[:, 0], X[:, 1]
    return (
        (lat >= LAT_MIN) & (lat <= LAT_MAX) & (lon >= LON_MIN) & (lon <= LON_MAX)
    )


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

# A fit needs enough of the positive class to be worth anything.  Below this the
# AUC comes back nan and the resulting model predicts ~0 everywhere; saving it
# would replace a working model with a degenerate one.
_MIN_POSITIVES = 20

_MIN_SAMPLES = 100
_MAX_BUFFER  = 21_024_000
_MC_SAMPLES  = 50

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
                self.model = candidate
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
        try:
            with np.load(str(bp), allow_pickle=False) as data:
                width = data["X"].shape[1]
                if width == FEATURE_COUNT:
                    X = data["X"]
                    y = data["y"].astype(int)
        except Exception as exc:
            log.warning(f"[model] buffer unreadable ({exc}) — setting it aside")
            _set_aside(bp, ".unreadable")
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
                f"[model] dropping {dropped} buffer samples with "
                f"non-geographic lat/lon — pre-fix online-retrain rows"
            )
            X, y = X[keep], y[keep]
        self._buffer_X = list(X)
        self._buffer_y = list(y)
        log.info(f"[model] buffer restored: {len(self._buffer_X)} samples from disk")

    def _save_buffer(self) -> None:
        """Write the sample buffer to disk. Never raises — a failed save must not
        abort a retrain that has already done the expensive collection work."""
        if not self._buffer_X:
            return
        try:
            Path(BUFFER_PATH).parent.mkdir(parents=True, exist_ok=True)
            np.savez(
                BUFFER_PATH,
                X=np.array(self._buffer_X),
                y=np.array(self._buffer_y),
            )
        except Exception as exc:
            log.warning(f"[model] could not save buffer ({exc})")

    async def retrain(self) -> None:
        """
        Fetch current OGN gliders, label each as thermal/no-thermal, accumulate
        into a rolling buffer, and refit the XGBoost classifier once enough
        samples are collected.
        """
        from data.ogn_client import fetch_ogn_gliders, is_thermal_evidence
        from data.meteo_client import fetch_meteo_features
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
        # Cap at 20 to avoid hammering APIs per retrain cycle.
        candidates = soaring[:20]
        above_base = dark = 0
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
                meteo = await fetch_meteo_features(g["lat"], g["lon"], 0)
                elev  = await fetch_elevation_grid(g["lat"], g["lon"])
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
                )
                if meteo["solar_ghi"] < _MIN_SOLAR_GHI:
                    dark += 1
                    await asyncio.sleep(1.1)
                    continue

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
                    if _above_thermal_base(o["alt"], row[2], meteo["cape_base"]):
                        above_base += 1
                        sampled.add(o["id"])   # judged and rejected; do not retry
                        continue
                    self._buffer_X.append(row)
                    self._buffer_y.append(int(o["circling"] and o["vario"] > 1.5))
                    sampled.add(o["id"])
                    if o["id"] != g["id"]:
                        offset_samples += 1
                await asyncio.sleep(1.1)  # terrain API: 1 req/s hard limit
            except Exception as exc:
                log.warning(f"[model] sample skipped: {exc}")

        if above_base:
            log.info(f"[model] {above_base}/{considered} dropped — above thermal base")
        if dark:
            log.info(f"[model] {dark}/{len(candidates)} grids dropped — solar below {_MIN_SOLAR_GHI:.0f} W/m2")
        if offset_samples:
            log.info(f"[model] {considered - offset_samples} centre + {offset_samples} off-centre "
                     f"samples from {len(candidates)} grids (no extra API calls)")

        # Rolling window
        if len(self._buffer_X) > _MAX_BUFFER:
            self._buffer_X = self._buffer_X[-_MAX_BUFFER:]
            self._buffer_y = self._buffer_y[-_MAX_BUFFER:]

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
            return

        X = np.array(self._buffer_X)
        y = np.array(self._buffer_y)
        pos = int(y.sum())

        # Refuse to fit on a buffer with almost no positives.  XGBoost will happily
        # return a model that predicts ~0 everywhere and an AUC of nan, and saving
        # that would overwrite a good model with a useless one.  Keep what we have.
        if pos < _MIN_POSITIVES:
            log.warning(
                f"[model] only {pos} positives in {n} samples (need {_MIN_POSITIVES}) — "
                f"skipping fit and keeping the existing model"
            )
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
            n_jobs=-1,
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
                self._save_buffer()
                return
            clf.get_booster().set_attr(**{_BENCH_ATTR: None})
            log.warning(
                f"[model] no model in service and benchmark not yet scoreable "
                f"({bench['groups']}/{_MIN_BENCH_GROUPS} groups) — accepting an "
                f"unverified fit on {len(yf)} samples"
            )
        else:
            challenger = self._score_served(clf, Xb)
            score = grouped_auc(challenger, yb, gb)["micro"]
            if not np.isfinite(score):
                log.warning("[model] benchmark score is not finite — discarding this fit")
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
                        self._save_buffer()   # samples are still worth keeping
                        return
                    # Re-baseline rather than stay frozen on a bar we can no
                    # longer clear: the conditions that produced it are gone.
                    log.warning(
                        f"[model] {self._skipped_fits} consecutive fits below the bar — "
                        f"accepting benchmark {score:.3f} and re-baselining "
                        f"from {baseline:.3f}"
                    )
            self._skipped_fits = 0
            clf.get_booster().set_attr(**{_BENCH_ATTR: f"{score:.6f}"})
            log.info(
                f"[model] retrained on {len(yf)} samples ({fit_pos} positives), "
                f"benchmark within-group AUC={score:.3f} over {bench['groups']} "
                f"groups / {bench['pairs']} pairs, saved to {MODEL_PATH}"
            )

        Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        clf.save_model(MODEL_PATH)
        self.model = clf

        self._save_buffer()

    async def seed_from_history(
        self,
        days_back: int = 3,
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
        from pipeline.feature_engineering import build_feature_matrix

        from config import DB_PATH as db_path
        if not db_path.exists():
            return {"error": "no DB found"}

        if reset:
            discarded = len(self._buffer_X)
            self._buffer_X = []
            self._buffer_y = []
            log.info(f"[seed] reset — discarded {discarded} existing buffer samples")

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
        where = f"ts >= ? AND ac_type IN ({placeholders}) AND under_tow = 0"

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

        added = errors = skipped_no_meteo = above_base = dark = 0
        done_buckets = 0

        for (lat_b, lon_b, date_str, hour), beacons in buckets.items():
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

            done_buckets += 1
            if done_buckets % 50 == 0:
                log.info(
                    f"[seed] {done_buckets}/{len(buckets)} buckets — "
                    f"{added} samples, {skipped_no_meteo} no-meteo, "
                    f"{dark} dark, {above_base} above-base, {errors} errors"
                )

            for lat, lon, alt, dt, vario, circling in beacons:
                try:
                    elev  = await fetch_elevation_grid(lat, lon)
                    # Snapped bounds — see retrain() for why raw coordinates
                    # mislabel every cell in the grid.
                    grid_lat, grid_lon = snap_grid_centre(lat, lon)
                    lat_bounds = (grid_lat - GRID_RADIUS, grid_lat + GRID_RADIUS)
                    lon_bounds = (grid_lon - GRID_RADIUS, grid_lon + GRID_RADIUS)
                    feat  = build_feature_matrix(
                        meteo, elev, dt=dt,
                        lat_bounds=lat_bounds,
                        lon_bounds=lon_bounds,
                        alt_amsl=alt,
                    )
                    # The beacon's own cell, not the grid centre: snapping moves
                    # the centre off the aircraft by up to half a coarse cell.
                    idx = _grid_index(lat, lon, lat_bounds, lon_bounds, elev.shape)
                    if idx is None:
                        continue
                    if _above_thermal_base(alt, feat[idx, 2], meteo["cape_base"]):
                        above_base += 1
                        continue
                    label  = int(circling and vario > 1.5)
                    # .copy() — see retrain(); a view here retains the whole matrix
                    self._buffer_X.append(feat[idx].copy())
                    self._buffer_y.append(label)
                    added += 1
                except Exception as exc:
                    log.warning(f"[seed] feature failed at ({lat:.3f},{lon:.3f}): {exc}")
                    errors += 1

            await asyncio.sleep(0.3)  # polite pacing for the Open-Meteo archive API

        # Rolling window trim
        if len(self._buffer_X) > _MAX_BUFFER:
            self._buffer_X = self._buffer_X[-_MAX_BUFFER:]
            self._buffer_y = self._buffer_y[-_MAX_BUFFER:]

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
