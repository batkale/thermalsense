import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture(autouse=True)
def _terrain_source_offline(monkeypatch):
    """
    Keep terrain lookups on the stubbable API path during tests.

    Copernicus is the production default, and it reads real S3 objects.  Most
    terrain tests stub OpenTopoData's request helper; with the default left in
    place they would silently go to the network first.  Tests about Copernicus
    set the source explicitly and stub its reader.
    """
    from data import terrain_client
    monkeypatch.setattr(terrain_client, "TERRAIN_SOURCE", "opentopodata")


@pytest.fixture(autouse=True)
def _model_files_in_tmp(tmp_path, monkeypatch):
    """
    Point every model and buffer write at the test's own tmp_path.

    A retrain that collects even one sample saves the buffer, and the defaults
    are the real backend/models files.  test_cycle_stops_at_the_grid_cap did
    exactly that, replacing a 28,090-row buffer with its 3 stub positives —
    which is the 3-row training_buffer.npz committed from 17 Sep onwards.
    Tests that need a specific path still set their own; theirs wins.
    """
    from models import thermal_model as tm
    monkeypatch.setattr(tm, "MODEL_PATH", str(tmp_path / "thermal_xgb.json"))
    monkeypatch.setattr(tm, "BUFFER_PATH", str(tmp_path / "training_buffer.npz"))


@pytest.fixture(autouse=True)
def _beacon_store_in_tmp(tmp_path, monkeypatch):
    """
    Same for the beacon DB and the Parquet archive.

    Left at the defaults, a test run built the prior_cells index inside a real
    1.2 GB ogn_history.db (a 512 MB WAL) and exported its days into a real
    backend/archive — after which seed_from_history saw an archive, stopped
    clamping days_back, and test_seed_clamps_days_back_to_the_retention_window
    failed on any machine holding a local DB.  main and ogn_client bind both
    paths at import, so their copies are redirected too.
    """
    import config
    from data import ogn_client as oc
    db, arch = tmp_path / "ogn_history.db", tmp_path / "archive" / "beacons"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "ARCHIVE_DIR", arch)
    monkeypatch.setattr(oc, "_DB_PATH", db)
    main = sys.modules.get("main")
    if main is not None:
        monkeypatch.setattr(main, "DB_PATH", db, raising=False)
        monkeypatch.setattr(main, "ARCHIVE_DIR", arch, raising=False)
