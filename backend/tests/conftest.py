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
