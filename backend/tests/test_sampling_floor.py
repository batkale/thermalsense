"""
Two guards against the model scoring well for the wrong reason.

The buffer used to hold rows for aircraft sitting on the field — 18% of it at
exactly 0 m AGL, every one of them a negative — and the model duly learned that
aircraft on the ground are not thermalling.  It is a true rule and a useless
one: it says nothing about *where* the lift is, which is the only question the
heatmap exists to answer.  Measured on the frozen benchmark, `alt_agl` alone
scored 0.631 within-group against 0.664 for all 22 columns.

The second guard is the same problem at the other end of the pipeline.
Training rejects rows below `_MIN_SOLAR_GHI`, so the model has never seen a
dark grid — yet `/predict` served one anyway, and on the live site at 0 W/m² it
came back with a 54% peak.  An extrapolation that looks like a forecast is
worse than no answer.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import main
import models.thermal_model as tm
from pipeline.feature_engineering import FEATURE_COUNT


# ---------------------------------------------------------------------------
# Collection floor
# ---------------------------------------------------------------------------

def _buffer_with_ground_rows(n_air=400, n_ground=200, seed=0):
    """Rows split between soaring heights and the deck, labels only on the air."""
    rng = np.random.default_rng(seed)
    X = rng.random((n_air + n_ground, FEATURE_COUNT))
    X[:n_air, 21] = rng.uniform(400, 2000, n_air)
    X[n_air:, 21] = 0.0
    y = np.zeros(n_air + n_ground, dtype=int)
    y[:n_air] = (rng.random(n_air) < 0.3).astype(int)
    return X, y


def test_fit_excludes_rows_below_the_agl_floor(tmp_path, monkeypatch, caplog):
    """
    A buffer written before the floor existed is cleaned at fit time.

    Filtering only at collection would leave every existing deployment training
    on its old ground rows until someone paid for a full /seed rebuild — two
    rate-limited API calls per sample.
    """
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "m.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "b.npz"))

    X, y = _buffer_with_ground_rows()
    m = tm.ThermalModel()
    m._buffer_X = list(X)
    m._buffer_y = list(y)

    seen = {}
    real_split = tm.bench_split

    def spy(Xa, ya):
        seen["agl_min"] = float(Xa[:, 21].min())
        seen["rows"] = len(ya)
        return real_split(Xa, ya)

    monkeypatch.setattr(tm, "bench_split", spy)
    m.fit_and_gate()

    assert seen["agl_min"] >= tm._MIN_SAMPLE_AGL
    assert seen["rows"] == 400          # the 200 ground rows never reach the fit


def test_agl_floor_leaves_a_clean_buffer_untouched(tmp_path, monkeypatch):
    """The filter must be a no-op when there is nothing to filter."""
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "m.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "b.npz"))

    rng = np.random.default_rng(1)
    X = rng.random((400, FEATURE_COUNT))
    X[:, 21] = rng.uniform(400, 2000, 400)
    y = (rng.random(400) < 0.3).astype(int)

    m = tm.ThermalModel()
    m._buffer_X, m._buffer_y = list(X), list(y)

    seen = {}
    real_split = tm.bench_split
    monkeypatch.setattr(tm, "bench_split",
                        lambda Xa, ya: (seen.update(rows=len(ya)), real_split(Xa, ya))[1])
    m.fit_and_gate()
    assert seen["rows"] == 400


# ---------------------------------------------------------------------------
# True absence vs pseudo-absence
# ---------------------------------------------------------------------------

def test_confirmed_climb_is_positive():
    assert tm._label_for(circling=True, vario=2.0) == 1


def test_straight_and_sinking_is_a_true_absence():
    assert tm._label_for(circling=False, vario=-1.2) == 0


def test_dolphining_through_lift_is_not_a_negative():
    """
    Straight flight in *rising* air was the worst of the old pseudo-absences:
    the glider is in a thermal and choosing not to circle, and labelling it 0
    teaches the model that lift is absent exactly where it is present.
    """
    assert tm._label_for(circling=False, vario=+1.0) is None


def test_weak_circling_is_ambiguous_not_negative():
    """A slow turn at +0.5 m/s is neither a confirmed thermal nor a confirmed
    absence, and the old rule silently filed it under negative."""
    assert tm._label_for(circling=True, vario=0.5) is None


def test_the_two_thresholds_do_not_overlap():
    """A gap between them is what makes the middle band ambiguous at all."""
    assert tm._NEGATIVE_MAX_VARIO < tm._POSITIVE_MIN_VARIO


def test_negative_threshold_is_at_or_below_still_air_sink():
    """
    A glider sinks at roughly -0.6 to -1.0 m/s in still air.  A threshold above
    zero would call neutral air an absence; one far below would discard most
    real absences.
    """
    assert -1.5 <= tm._NEGATIVE_MAX_VARIO <= 0.0


# ---------------------------------------------------------------------------
# Label versioning
# ---------------------------------------------------------------------------

async def test_buffer_from_the_old_label_rule_is_set_aside(tmp_path, monkeypatch):
    """
    v1 and v2 buffers have the same width, so the `.vN` check cannot tell them
    apart. Mixing them puts two definitions of "no lift" into one fit and one
    frozen benchmark — which is exactly what the first training node started
    with, from the bundled repo buffer.
    """
    bp = tmp_path / "b.npz"
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(bp))
    X = np.random.default_rng(0).random((50, FEATURE_COUNT))
    np.savez(bp, X=X, y=np.zeros(50, dtype=int))          # no label_version -> v1

    m = tm.ThermalModel()
    await m.load()

    assert m._buffer_X == [], "a v1 buffer must not be loaded into a v2 model"
    assert (tmp_path / "b.npz.labels-v1").exists(), "the old samples must be kept"
    # An empty v2 buffer is left in place, so a bundled v1 copy is not re-seeded
    # on every restart.
    with np.load(bp) as d:
        assert int(d["label_version"]) == tm._LABEL_VERSION
        assert d["X"].shape == (0, FEATURE_COUNT)


async def test_current_version_buffer_round_trips(tmp_path, monkeypatch):
    bp = tmp_path / "b.npz"
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(bp))
    X = np.random.default_rng(1).random((30, FEATURE_COUNT))
    X[:, 0], X[:, 1] = 39.8, 30.1

    m = tm.ThermalModel()
    m._buffer_X, m._buffer_y = list(X), [0, 1] * 15
    m._save_buffer()

    m2 = tm.ThermalModel()
    await m2.load()
    assert len(m2._buffer_X) == 30
    assert not (tmp_path / "b.npz.labels-v1").exists()


async def test_empty_current_buffer_loads_cleanly(tmp_path, monkeypatch):
    """The placeholder left after a set-aside must itself load without error."""
    bp = tmp_path / "b.npz"
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(bp))
    np.savez(bp, X=np.empty((0, FEATURE_COUNT)), y=np.empty((0,), dtype=int),
             label_version=np.array(tm._LABEL_VERSION))
    m = tm.ThermalModel()
    await m.load()
    assert m._buffer_X == []


def test_floor_is_below_normal_soaring_heights():
    """
    A floor set too high would throw away the low saves the map is most useful
    for.  200 m is above the circuit and well below a working thermal.
    """
    assert 0 < tm._MIN_SAMPLE_AGL <= 300


# ---------------------------------------------------------------------------
# Night guard
# ---------------------------------------------------------------------------

def _meteo(solar_ghi):
    return {
        "temp_2m": 12.0, "humidity": 0.8, "wind_speed": 10.0, "wind_dir": 315.0,
        "cape": 10.0, "cin": 9.0, "solar_ghi": solar_ghi,
        "temp_850": 5.0, "temp_500": -18.0, "lapse_rate": 6.5,
        "cape_base": 4000, "pbl_height": 400.0, "soil_temp": 11.0,
    }


def _patch_predict_deps(monkeypatch, solar_ghi, peak=0.54):
    terrain = np.full((21, 21), 700.0)

    async def meteo(lat, lon, forecast_h):
        return _meteo(solar_ghi)

    async def elev(lat, lon):
        return terrain

    async def gliders():
        return []

    class _Model:
        def predict(self, features):
            n = features.shape[0]
            return np.full(n, peak), np.full(n, 0.1)

    monkeypatch.setattr(main, "fetch_meteo_features", meteo)
    monkeypatch.setattr(main, "fetch_elevation_grid", elev)
    monkeypatch.setattr(main, "fetch_ogn_gliders", gliders)
    monkeypatch.setattr(main, "model", _Model())


async def test_predict_zeroes_the_grid_without_insolation(monkeypatch):
    _patch_predict_deps(monkeypatch, solar_ghi=0.0)
    out = await main.predict(lat=47.1, lon=13.1)

    assert out["no_insolation"] is True
    assert max(out["heatmap"]) == 0.0
    assert max(out["heatmap_std"]) == 0.0


async def test_predict_is_unchanged_in_daylight(monkeypatch):
    _patch_predict_deps(monkeypatch, solar_ghi=600.0, peak=0.54)
    out = await main.predict(lat=47.1, lon=13.1)

    assert out["no_insolation"] is False
    assert max(out["heatmap"]) == pytest.approx(0.54, abs=1e-3)


async def test_guard_fires_exactly_at_the_training_floor(monkeypatch):
    """
    The serving guard and the collection filter must use one threshold.  Two
    numbers that drift apart would leave a band the model is served in but was
    never trained on — the precise gap this closes.
    """
    _patch_predict_deps(monkeypatch, solar_ghi=tm._MIN_SOLAR_GHI - 0.1)
    assert (await main.predict(lat=47.1, lon=13.1))["no_insolation"] is True

    _patch_predict_deps(monkeypatch, solar_ghi=tm._MIN_SOLAR_GHI)
    assert (await main.predict(lat=47.1, lon=13.1))["no_insolation"] is False
