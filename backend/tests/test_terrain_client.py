import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import data.terrain_client as tc
from data.terrain_client import fetch_elevation_batch, MAX_BATCH_POINTS


class _FakeResponse:
    def __init__(self, n):
        self._n = n
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": [{"elevation": 100.0} for _ in range(self._n)]}


@pytest.fixture(autouse=True)
def _clear_cache():
    tc._elev_cache.clear()
    yield
    tc._elev_cache.clear()


@pytest.fixture
def captured(monkeypatch):
    """Record the URL of every outbound request instead of making one."""
    urls = []

    async def fake_get(client, url, max_retries=3):
        urls.append(url)
        return _FakeResponse(url.count("|") + 1)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(tc, "_get_with_retry", fake_get)
    monkeypatch.setattr(tc, "_get_client", lambda: None)
    monkeypatch.setattr(tc.asyncio, "sleep", no_sleep)
    return urls


async def test_batch_never_exceeds_the_api_point_cap(captured):
    """A single oversized request returns 414 URI Too Long from OpenTopoData."""
    pairs = [(50.0 + i * 0.01, 1.0 + i * 0.01) for i in range(250)]
    await fetch_elevation_batch(pairs, max_requests=10)
    assert captured, "expected at least one request"
    for url in captured:
        assert url.count("|") + 1 <= MAX_BATCH_POINTS


async def test_batch_dedupes_on_the_cache_key(captured):
    """Points sharing a 2 dp cell must cost one slot, not one each."""
    pairs = [(50.0001 + i * 1e-6, 1.0001) for i in range(150)]
    out = await fetch_elevation_batch(pairs, max_requests=10)
    assert len(captured) == 1
    assert captured[0].count("|") + 1 == 1
    assert len(out) == len(pairs)
    assert all(v == 100 for v in out.values())


async def test_batch_respects_the_request_budget(captured):
    pairs = [(50.0 + i * 0.01, 1.0 + i * 0.01) for i in range(500)]
    out = await fetch_elevation_batch(pairs, max_requests=2)
    assert len(captured) == 2
    assert any(v is None for v in out.values())      # beyond budget
    assert any(v is not None for v in out.values())  # within budget


async def test_batch_returns_an_entry_for_every_input(captured):
    pairs = [(50.0 + i * 0.01, 1.0) for i in range(30)]
    out = await fetch_elevation_batch(pairs, max_requests=1)
    assert set(out) == set(pairs)


async def test_cached_points_skip_the_network(captured):
    pairs = [(50.0, 1.0), (51.0, 2.0)]
    await fetch_elevation_batch(pairs, max_requests=5)
    first = len(captured)
    await fetch_elevation_batch(pairs, max_requests=5)
    assert len(captured) == first, "second call should be served from cache"
