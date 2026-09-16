"""
Copernicus GLO-30 reader and its place in the terrain chain.

The reader was verified against GDAL on 65 real points (Türkiye, the Alps,
above 50°N, both hemispheres, integer-degree edges): largest difference
0.0000 m.  These tests pin the pieces that made that true, on a synthetic COG,
so none of them needs the network.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import struct
import zlib

import httpx
import numpy as np
import pytest

from data import cog, dem_client as dc, terrain_client as tc


# ---------------------------------------------------------------------------
# Synthetic float32 COG with the floating-point predictor
# ---------------------------------------------------------------------------

def _encode_block(arr: np.ndarray) -> bytes:
    """Inverse of cog.decode_block for predictor 3."""
    th, tw = arr.shape
    be = arr.astype(">f4").view(np.uint8).reshape(th, tw, 4)
    planes = be.transpose(0, 2, 1).reshape(th, 4 * tw)
    diff = planes.copy()
    diff[:, 1:] = planes[:, 1:] - planes[:, :-1]          # uint8, wraps
    return zlib.compress(diff.tobytes())


def _cog(data: np.ndarray, tile: int, lon0: float, lat0: float,
         dx: float, dy: float, point: bool = True) -> bytes:
    h, w = data.shape
    across, down = -(-w // tile), -(-h // tile)
    blocks = []
    for ty in range(down):
        for tx in range(across):
            b = np.zeros((tile, tile), dtype=np.float32)
            part = data[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
            b[:part.shape[0], :part.shape[1]] = part
            blocks.append(_encode_block(b))

    n = len(blocks)
    arrays = {
        324: struct.pack(f"<{n}I", *([0] * n)),               # patched below
        325: struct.pack(f"<{n}I", *[len(b) for b in blocks]),
        33550: struct.pack("<3d", dx, dy, 0.0),
        33922: struct.pack("<6d", 0, 0, 0, lon0, lat0, 0),
        34735: struct.pack("<8H", 1, 1, 0, 1, 1025, 0, 1, 2 if point else 1),
    }
    entries = [  # tag, type, count, inline value or array tag
        (256, 4, 1, w), (257, 4, 1, h), (258, 3, 1, 32), (259, 3, 1, 8),
        (277, 3, 1, 1), (317, 3, 1, 3), (322, 4, 1, tile), (323, 4, 1, tile),
        (324, 4, n, None), (325, 4, n, None), (339, 3, 1, 3),
        (33550, 12, 3, None), (33922, 12, 6, None), (34735, 3, 8, None),
    ]
    # Layout: header | arrays | IFD | blocks
    pos = 8
    arr_off = {}
    for tag, blob in arrays.items():
        arr_off[tag] = pos
        pos += len(blob)
    ifd_off = pos
    ifd_len = 2 + 12 * len(entries) + 4
    block_off = []
    p = ifd_off + ifd_len
    for b in blocks:
        block_off.append(p)
        p += len(b)
    arrays[324] = struct.pack(f"<{n}I", *block_off)

    out = bytearray(b"II*\x00" + struct.pack("<I", ifd_off))
    for tag in arrays:
        out += arrays[tag]
    out += struct.pack("<H", len(entries))
    for tag, typ, cnt, val in entries:
        if val is None:
            out += struct.pack("<HHII", tag, typ, cnt, arr_off[tag])
        elif typ == 3:
            out += struct.pack("<HHIHH", tag, typ, cnt, val, 0)
        else:
            out += struct.pack("<HHII", tag, typ, cnt, val)
    out += struct.pack("<I", 0)
    for b in blocks:
        out += b
    return bytes(out)


def test_floating_point_predictor_round_trips():
    rng = np.random.default_rng(0)
    arr = (rng.random((16, 16)) * 3000 - 50).astype(np.float32)
    got = cog.decode_block(_encode_block(arr), 16, 16, 32, sample_format=3, predictor=3)
    assert np.array_equal(got, arr)


def test_pixel_is_point_is_read_from_geokeys():
    raw = _cog(np.zeros((4, 4), np.float32), 4, 30.0, 40.0, 0.25, 0.25, point=True)
    assert cog.pixel_is_point(cog.parse_ifds(raw)) is True
    raw = _cog(np.zeros((4, 4), np.float32), 4, 30.0, 40.0, 0.25, 0.25, point=False)
    assert cog.pixel_is_point(cog.parse_ifds(raw)) is False


# ---------------------------------------------------------------------------
# Tile naming and pixel addressing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lat,lon,expected", [
    (39.81, 30.11, (39, 30)),     # İnönü
    (40.0, 30.0, (39, 30)),       # integer latitude is the top row of the tile below
    (-33.9, 18.4, (-34, 18)),
    (35.0, -106.0, (34, -106)),
])
def test_tile_origin(lat, lon, expected):
    assert dc.tile_origin(lat, lon) == expected


def test_tile_name_matches_the_published_layout():
    assert dc.tile_name(39, 30) == "Copernicus_DSM_COG_10_N39_00_E030_00_DEM"
    assert dc.tile_name(-34, -106) == "Copernicus_DSM_COG_10_S34_00_W106_00_DEM"


def test_pixel_index_uses_separate_x_and_y_sizes_and_clamps():
    """Above 50°N Copernicus tiles are 2400 wide, not 3600 — square pixels would
    sample the wrong ground across Germany."""
    meta = {"lon0": 12.0, "lat0": 56.0, "dx": 1 / 2400, "dy": 1 / 3600,
            "width": 2400, "height": 3600, "point": True}
    assert dc.pixel_index(meta, 55.5, 12.5) == (1800, 1200)
    assert dc.pixel_index(meta, 55.0, 13.0) == (3599, 2399)   # clamped at the far edges


# ---------------------------------------------------------------------------
# Reading through a mocked S3
# ---------------------------------------------------------------------------

@pytest.fixture
def s3(monkeypatch):
    """A tile N39/E030 of 8x8 pixels at 1/8 degree, in 4x4 blocks."""
    data = np.arange(64, dtype=np.float32).reshape(8, 8) * 10 + 500
    raw = _cog(data, 4, 30.0, 40.0, 1 / 8, 1 / 8, point=False)
    served = {"requests": 0}
    name = dc.tile_name(39, 30)

    def handler(request):
        served["requests"] += 1
        if name not in str(request.url):
            return httpx.Response(403)
        lo, hi = request.headers["Range"].split("=")[1].split("-")
        return httpx.Response(206, content=raw[int(lo):int(hi) + 1])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(dc, "_get_client", lambda: client)
    monkeypatch.setattr(dc, "_headers", {})
    monkeypatch.setattr(dc, "_blocks", dc.OrderedDict())
    monkeypatch.setattr(dc, "_lock", None)
    return data, served


async def test_fetch_reads_the_right_pixels(s3):
    data, _ = s3
    # Pixel (row, col) spans lat 40 - row/8 .. , lon 30 + col/8 ..  (PixelIsArea)
    got = await dc.fetch_elevations([(39.99, 30.01), (39.30, 30.70), (39.01, 30.99)])
    assert got[(39.99, 30.01)] == data[0, 0]
    assert got[(39.30, 30.70)] == data[5, 5]
    assert got[(39.01, 30.99)] == data[7, 7]


async def test_each_block_is_downloaded_once(s3):
    _, served = s3
    pts = [(39.99, 30.01), (39.90, 30.10), (39.80, 30.20)]      # all in block 0
    await dc.fetch_elevations(pts)
    first = served["requests"]                                  # header + one block
    await dc.fetch_elevations(pts)
    assert first == 2 and served["requests"] == first


async def test_unpublished_tile_is_none_not_sea_level(s3):
    """A missing tile must not become 0 m: a withheld land tile read as sea
    level would be a fabricated value with a real label on it."""
    got = await dc.fetch_elevations([(10.5, 10.5)])
    assert got[(10.5, 10.5)] is None


# ---------------------------------------------------------------------------
# Terrain chain: Copernicus first, API only for what it cannot answer
# ---------------------------------------------------------------------------

async def test_batch_prefers_copernicus_and_falls_back_per_point(monkeypatch):
    monkeypatch.setattr(tc, "TERRAIN_SOURCE", "copernicus")
    monkeypatch.setattr(tc, "_elev_cache", {})
    monkeypatch.setattr(tc, "flush_point_cache", lambda force=False: None)

    async def dem(points):
        return {p: (1234.4 if p[0] > 0 else None) for p in points}

    api_calls = []

    class R:
        status_code = 200
        def __init__(self, pts): self.pts = pts
        def raise_for_status(self): pass
        def json(self): return {"results": [{"elevation": 77} for _ in self.pts]}

    async def fake_get(client, url, max_retries=3):
        pts = url.split("locations=")[1].split("|")
        api_calls.append(pts)
        return R(pts)

    async def no_sleep(*_): pass

    monkeypatch.setattr(tc.dem_client, "fetch_elevations", dem)
    monkeypatch.setattr(tc, "_get_with_retry", fake_get)
    monkeypatch.setattr(tc, "_get_client", lambda: None)
    monkeypatch.setattr(tc.asyncio, "sleep", no_sleep)

    out = await tc.fetch_elevation_batch([(39.81, 30.11), (-33.9, 18.4)])
    assert out[(39.81, 30.11)] == 1234                     # from Copernicus
    assert out[(-33.9, 18.4)] == 77                        # API, only this point
    assert len(api_calls) == 1 and len(api_calls[0]) == 1


def test_sources_never_share_a_cache_directory():
    """SRTM and Copernicus differ by metres; a grid stitched from both would put
    a seam through its slope."""
    assert tc._CACHE_DIR.name in ("terrain", "terrain-copernicus")
    from config import TERRAIN_SOURCE
    assert tc._CACHE_DIR.name == ("terrain-copernicus" if TERRAIN_SOURCE == "copernicus" else "terrain")
