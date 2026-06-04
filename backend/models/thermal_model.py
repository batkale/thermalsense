import asyncio
import logging
import xgboost as xgb
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from config import MODEL_PATH, BUFFER_PATH

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
                    self._buffer_X = list(data["X"])
                    self._buffer_y = list(data["y"].astype(int))
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
        from data.ogn_client import fetch_ogn_gliders
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
        # Cap at 20 to avoid hammering APIs per retrain cycle.
        # No vario pre-filter: straight-cruising gliders are valid negatives.
        candidates = gliders[:20]

        for g in candidates:
            try:
                meteo = await fetch_meteo_features(g["lat"], g["lon"], 0)
                elev  = await fetch_elevation_grid(g["lat"], g["lon"])
                feat  = build_feature_matrix(meteo, elev, dt=now)
                center = feat.shape[0] // 2
                label = int(g["circling"] and g["vario"] > 1.5)
                self._buffer_X.append(feat[center])
                self._buffer_y.append(label)
                await asyncio.sleep(1.1)  # terrain API: 1 req/s hard limit
            except Exception as exc:
                log.warning(f"[model] sample skipped: {exc}")

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
        )

        if n >= _VAL_MIN_SAMPLES:
            split = int(n * 0.8)
            clf.fit(
                X[:split], y[:split],
                eval_set=[(X[split:], y[split:])],
                early_stopping_rounds=20,
                verbose=False,
            )
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
        """
        from collections import defaultdict
        from datetime import datetime, timezone, timedelta
        import sqlite3
        from pathlib import Path
        from data.meteo_client import fetch_meteo_historical
        from data.terrain_client import fetch_elevation_grid

        db_path = Path("data/ogn_history.db")
        if not db_path.exists():
            return {"error": "no DB found"}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

        with sqlite3.connect(db_path) as con:
            total = con.execute(
                "SELECT COUNT(*) FROM beacons WHERE ts >= ? AND is_tow = 0",
                (cutoff,),
            ).fetchone()[0]
            sample_rate = max(1, total // limit)
            rows = con.execute(
                """
                SELECT ts, lat, lon, vario, circling
                FROM beacons
                WHERE ts >= ? AND is_tow = 0
                  AND (ROWID % ?) = 0
                LIMIT ?
                """,
                (cutoff, sample_rate, limit),
            ).fetchall()

        if not rows:
            return {"rows_scanned": 0, "added": 0, "buffer_total": len(self._buffer_X)}

        # Snap lat/lon to the nearest 0.5° grid for meteo bucketing
        def _snap(v: float) -> float:
            return round(round(v * 2) / 2, 1)

        buckets: dict = defaultdict(list)
        for ts, lat, lon, vario, circling in rows:
            dt  = datetime.fromisoformat(ts)
            key = (_snap(lat), _snap(lon), dt.strftime("%Y-%m-%d"), dt.hour)
            buckets[key].append((lat, lon, dt, float(vario), int(circling)))

        log.info(f"[seed] {len(rows)} beacons → {len(buckets)} meteo buckets")

        added = errors = 0

        for (lat_b, lon_b, date_str, hour), beacons in buckets.items():
            try:
                dt_b  = datetime.fromisoformat(f"{date_str}T{hour:02d}:00:00+00:00")
                meteo = await fetch_meteo_historical(lat_b, lon_b, dt_b)
            except Exception as exc:
                log.warning(f"[seed] meteo bucket ({lat_b},{lon_b},{date_str},{hour}) failed: {exc}")
                errors += len(beacons)
                continue

            for lat, lon, dt, vario, circling in beacons:
                try:
                    elev  = await fetch_elevation_grid(lat, lon)
                    feat  = build_feature_matrix(
                        meteo, elev, dt=dt,
                        lat_bounds=(lat - 0.05, lat + 0.05),
                        lon_bounds=(lon - 0.05, lon + 0.05),
                    )
                    center = feat.shape[0] // 2
                    label  = int(circling and vario > 1.5)
                    self._buffer_X.append(feat[center])
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
            "rows_scanned":  len(rows),
            "meteo_buckets": len(buckets),
            "added":         added,
            "errors":        errors,
            "buffer_total":  len(self._buffer_X),
        }
        log.info(f"[seed] complete: {result}")
        return result

    def predict(self, features: np.ndarray) -> tuple[list[float], list[float]]:
        scorer = (
            (lambda f: self.model.predict_proba(f)[:, 1])
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
        Per-cell multiplier derived from terrain so the trained model output
        varies spatially even when weather inputs are uniform across the grid.

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
