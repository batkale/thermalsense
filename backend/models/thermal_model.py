import asyncio
import logging
import xgboost as xgb
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from config import MODEL_PATH, BUFFER_PATH, GRID_RADIUS, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX


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
    """
    lat, lon = X[:, 0], X[:, 1]
    return (
        (lat >= LAT_MIN) & (lat <= LAT_MAX) & (lon >= LON_MIN) & (lon <= LON_MAX)
    )


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
# It is a 21-element feature vector (lat, lon, elevation, slope, aspect,
# temp, humidity, wind_u, wind_v, cape, cin, solar_ghi, lapse_rate,
# land_use_heat, land_use_albedo, hour_sin, hour_cos, doy_sin, doy_cos,
# pbl_height, soil_temp)
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

_MIN_SAMPLES     = 100
_VAL_MIN_SAMPLES = 500   # 20% holdout ≥ 100 points only above this
_MAX_BUFFER      = 21_024_000
_MC_SAMPLES      = 50

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

    @property
    def is_loaded(self) -> bool:
        """True once a trained model is in memory; False while on physics fallback."""
        return self.model is not None

    async def load(self) -> None:
        p = Path(MODEL_PATH)
        if p.exists():
            self.model = xgb.XGBClassifier()
            self.model.load_model(str(p))
            log.info(f"[model] loaded from {MODEL_PATH}")
        else:
            log.info("[model] no trained model found — using physics fallback")

        bp = Path(BUFFER_PATH)
        if bp.exists():
            try:
                data = np.load(str(bp), allow_pickle=False)
                if data["X"].shape[1] != 21:
                    log.warning("[model] buffer feature count mismatch — discarding stale buffer")
                    bp.unlink(missing_ok=True)
                else:
                    X = data["X"]
                    y = data["y"].astype(int)
                    keep = _plausible_coords(X)
                    dropped = int((~keep).sum())
                    if dropped:
                        # Samples written before the lat_bounds fix carry grid indices
                        # (lat=100, lon=0) instead of degrees.  They describe a
                        # different feature space than anything we serve, so drop them.
                        log.warning(
                            f"[model] dropping {dropped} buffer samples with "
                            f"non-geographic lat/lon — pre-fix online-retrain rows"
                        )
                        X, y = X[keep], y[keep]
                    self._buffer_X = list(X)
                    self._buffer_y = list(y)
                    log.info(f"[model] buffer restored: {len(self._buffer_X)} samples from disk")
            except Exception as exc:
                log.warning(f"[model] buffer unreadable ({exc}) — discarding")
                bp.unlink(missing_ok=True)

    async def retrain(self) -> None:
        """
        Fetch current OGN gliders, label each as thermal/no-thermal, accumulate
        into a rolling buffer, and refit the XGBoost classifier once enough
        samples are collected.
        """
        from data.ogn_client import fetch_ogn_gliders, is_soaring
        from data.meteo_client import fetch_meteo_features
        from data.terrain_client import fetch_elevation_grid
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
        # Soaring aircraft only.  A powered aircraft circling in a climb would be
        # labelled a confirmed thermal, teaching the model lift that never existed.
        # No vario pre-filter beyond that: straight-cruising gliders are valid negatives.
        soaring = [g for g in gliders if is_soaring(g)]
        if not soaring:
            log.info("[model] no soaring aircraft in view — skipping")
            return
        # Cap at 20 to avoid hammering APIs per retrain cycle.
        candidates = soaring[:20]
        above_base = 0

        for g in candidates:
            try:
                meteo = await fetch_meteo_features(g["lat"], g["lon"], 0)
                elev  = await fetch_elevation_grid(g["lat"], g["lon"])
                # Bounds are mandatory: without them build_feature_matrix falls back
                # to row/column indices for the lat/lon columns, so online samples
                # would carry lat=100, lon=0 while /predict serves real degrees.
                feat  = build_feature_matrix(
                    meteo, elev, dt=now,
                    lat_bounds=(g["lat"] - GRID_RADIUS, g["lat"] + GRID_RADIUS),
                    lon_bounds=(g["lon"] - GRID_RADIUS, g["lon"] + GRID_RADIUS),
                )
                center = _centre_index(elev.shape)
                if _above_thermal_base(g["alt"], feat[center, 2], meteo["cape_base"]):
                    above_base += 1
                    await asyncio.sleep(1.1)
                    continue
                label = int(g["circling"] and g["vario"] > 1.5)
                # .copy() is load-bearing: feat[center] is a view that pins the whole
                # (rows*cols, 21) matrix — ~6.4 MB per 168-byte sample.
                self._buffer_X.append(feat[center].copy())
                self._buffer_y.append(label)
                await asyncio.sleep(1.1)  # terrain API: 1 req/s hard limit
            except Exception as exc:
                log.warning(f"[model] sample skipped: {exc}")

        if above_base:
            log.info(f"[model] {above_base}/{len(candidates)} dropped — above thermal base")

        # Rolling window
        if len(self._buffer_X) > _MAX_BUFFER:
            self._buffer_X = self._buffer_X[-_MAX_BUFFER:]
            self._buffer_y = self._buffer_y[-_MAX_BUFFER:]

        n = len(self._buffer_X)
        if n < _MIN_SAMPLES:
            log.info(f"[model] buffer {n}/{_MIN_SAMPLES} samples — skipping fit")
            return

        X = np.array(self._buffer_X)
        y = np.array(self._buffer_y)
        pos = int(y.sum())

        use_holdout = n >= _VAL_MIN_SAMPLES

        clf = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            scale_pos_weight=max(1.0, (n - pos) / max(1, pos)),
            tree_method="hist",
            n_jobs=-1,
            eval_metric="auc",
            random_state=42,
            # XGBoost >= 2.0 takes early stopping on the constructor; passing it to
            # fit() raises TypeError.  Only valid alongside an eval_set.
            early_stopping_rounds=20 if use_holdout else None,
        )

        if use_holdout:
            # Chronological split — the buffer is appended in time order, so a
            # random split would leak future weather into the training half.
            split = int(n * 0.8)
            clf.fit(
                X[:split], y[:split],
                eval_set=[(X[split:], y[split:])],
                verbose=False,
            )
            val_auc = getattr(clf, "best_score", None)
            if val_auc is None:
                val_auc = clf.evals_result()["validation_0"]["auc"][-1]
            log.info(
                f"[model] retrained on {split} samples, val AUC={val_auc:.3f} "
                f"({pos} positives, {n - split} holdout), saved to {MODEL_PATH}"
            )
        else:
            clf.fit(X, y)
            log.info(
                f"[model] retrained on {n} samples ({pos} positives) — "
                f"buffer below {_VAL_MIN_SAMPLES}, no holdout yet, saved to {MODEL_PATH}"
            )

        Path(MODEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        clf.save_model(MODEL_PATH)
        self.model = clf

        np.savez(BUFFER_PATH, X=X, y=np.array(self._buffer_y))

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
        Terrain is fetched via fetch_elevation_grid which caches at the 0.1° level,
        so repeated calls within the same grid area are near-free.

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
        from data.terrain_client import fetch_elevation_grid
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
        placeholders = ",".join("?" * len(SOARING_AC_TYPES))
        soaring_ids  = tuple(sorted(SOARING_AC_TYPES))
        where = f"ts >= ? AND ac_type IN ({placeholders})"

        with sqlite3.connect(db_path) as con:
            cols = {r[1] for r in con.execute("PRAGMA table_info(beacons)")}
            if "ac_type" not in cols:
                return {
                    "error": "beacons table has no ac_type column — "
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
                    "[seed] no classified soaring beacons in the window — history "
                    "predates aircraft-type filtering; let the stream collect first"
                )
                return {
                    "rows_scanned": 0, "added": 0,
                    "reason": "no rows with a soaring ac_type",
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

        added = errors = skipped_no_meteo = above_base = 0
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

            done_buckets += 1
            if done_buckets % 50 == 0:
                log.info(
                    f"[seed] {done_buckets}/{len(buckets)} buckets — "
                    f"{added} samples, {skipped_no_meteo} no-meteo, "
                    f"{above_base} above-base, {errors} errors"
                )

            for lat, lon, alt, dt, vario, circling in beacons:
                try:
                    elev  = await fetch_elevation_grid(lat, lon)
                    feat  = build_feature_matrix(
                        meteo, elev, dt=dt,
                        lat_bounds=(lat - GRID_RADIUS, lat + GRID_RADIUS),
                        lon_bounds=(lon - GRID_RADIUS, lon + GRID_RADIUS),
                    )
                    center = _centre_index(elev.shape)
                    if _above_thermal_base(alt, feat[center, 2], meteo["cape_base"]):
                        above_base += 1
                        continue
                    label  = int(circling and vario > 1.5)
                    # .copy() — see retrain(); a view here retains the whole matrix
                    self._buffer_X.append(feat[center].copy())
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
            Path(BUFFER_PATH).parent.mkdir(parents=True, exist_ok=True)
            np.savez(BUFFER_PATH, X=np.array(self._buffer_X), y=np.array(self._buffer_y))

        result = {
            "rows_scanned":     len(rows),
            "meteo_buckets":    len(buckets),
            "added":                added,
            "errors":               errors,
            "skipped_no_meteo":     skipped_no_meteo,
            "skipped_above_base":   above_base,
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
