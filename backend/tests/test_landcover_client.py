import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import struct
import zlib

import numpy as np
import pytest

from data.landcover_client import (
    DEFAULT_PROPS, _block_average, _geo, _parse_ifds, _pick_level,
    properties, tile_name, tile_url,
)


# --- tile addressing ----------------------------------------------------------

def test_tile_name_uses_the_south_west_corner():
    # The N39E030 tile spans 39-42N, 30-33E, so every point inside it resolves
    # to the same name regardless of where in the tile it sits.
    assert tile_name(39.82, 30.12) == "N39E030"
    assert tile_name(41.99, 32.99) == "N39E030"
    assert tile_name(42.01, 33.01) == "N42E033"


def test_tile_name_floors_in_the_southern_and_western_hemispheres():
    # Truncation toward zero would name the tile north/east of the real one.
    assert tile_name(-0.5, -0.5) == "S03W003"
    assert tile_name(-33.9, 151.2) == "S36E150"


def test_tile_url_is_wellformed():
    assert tile_url("N39E030").endswith("ESA_WorldCover_10m_2021_v200_N39E030_Map.tif")


# --- class mapping ------------------------------------------------------------

def test_bare_ground_outranks_forest_and_water_for_heat():
    heat, _ = properties(np.array([60, 40, 10, 80], dtype=np.uint8))
    assert heat[0] > heat[1] > heat[2] > heat[3]


def test_snow_is_bright_and_produces_no_heat():
    heat, albedo = properties(np.array([70], dtype=np.uint8))
    assert heat[0] == 0.0
    assert albedo[0] > 0.5


def test_unmapped_class_falls_back_to_the_old_constant():
    # Class 0 is WorldCover's no-data value; it must land exactly where the
    # placeholder table used to, so a missing tile changes nothing.
    heat, albedo = properties(np.array([0, 255], dtype=np.uint8))
    assert (float(heat[0]), float(albedo[0])) == DEFAULT_PROPS
    assert (float(heat[1]), float(albedo[1])) == DEFAULT_PROPS


# --- block averaging ----------------------------------------------------------

def test_block_average_means_each_output_cell():
    values = np.array([[1.0, 3.0], [5.0, 7.0]])
    py = np.array([0, 0])
    px = np.array([0, 0])
    out = _block_average(values, py, px, (1, 1))
    assert out.shape == (1, 1)
    assert out[0, 0] == pytest.approx(4.0)


def test_block_average_keeps_cells_separate():
    values = np.array([[1.0, 9.0], [1.0, 9.0]])
    out = _block_average(values, np.array([0, 0]), np.array([0, 1]), (1, 2))
    assert out[0, 0] == pytest.approx(1.0)
    assert out[0, 1] == pytest.approx(9.0)


def test_block_average_preserves_mixture_rather_than_picking_one_class():
    # Half forest (0.20), half bare (0.90) must average to 0.55, not collapse
    # onto whichever class happened to sit under the sample point.
    heat, _ = properties(np.array([[10, 60], [10, 60]], dtype=np.uint8))
    out = _block_average(heat, np.array([0, 0]), np.array([0, 0]), (1, 1))
    assert out[0, 0] == pytest.approx(0.55)


def test_block_average_does_not_divide_by_zero_for_empty_cells():
    out = _block_average(np.array([[2.0]]), np.array([0]), np.array([0]), (2, 2))
    assert np.isfinite(out).all()


# --- TIFF parsing -------------------------------------------------------------

def _synthetic_cog(levels=((64, 32), (32, 32))):
    """
    A minimal classic little-endian tiled TIFF with an overview IFD.

    Built by hand rather than downloaded so the parser is exercised offline and
    a format regression fails loudly instead of only showing up against S3.
    """
    header = bytearray(b"II" + struct.pack("<HI", 42, 8))
    ifd_blobs = []
    # Reserve space: each IFD is 2 + 12*n + 4 bytes.  Must match the number of
    # tag() calls below exactly, or every out-of-line array offset lands short.
    tags_per = 12
    ifd_size = 2 + 12 * tags_per + 4
    scratch_off = 8 + ifd_size * len(levels)
    scratch = bytearray()

    def put(data: bytes) -> int:
        nonlocal scratch
        off = scratch_off + len(scratch)
        scratch += data
        return off

    for i, (width, tile) in enumerate(levels):
        entries = []
        n_tiles = (width // tile) ** 2
        offs = put(struct.pack(f"<{n_tiles}I", *[0] * n_tiles)) if n_tiles > 1 else None
        cnts = put(struct.pack(f"<{n_tiles}I", *[0] * n_tiles)) if n_tiles > 1 else None

        def tag(code, typ, count, value):
            entries.append(struct.pack("<HHI", code, typ, count) + value)

        tag(254, 4, 1, struct.pack("<I", 0 if i == 0 else 1))
        tag(256, 4, 1, struct.pack("<I", width))
        tag(257, 4, 1, struct.pack("<I", width))
        tag(258, 3, 1, struct.pack("<HH", 8, 0))
        tag(259, 3, 1, struct.pack("<HH", 8, 0))
        tag(317, 3, 1, struct.pack("<HH", 1, 0))
        tag(322, 4, 1, struct.pack("<I", tile))
        tag(323, 4, 1, struct.pack("<I", tile))
        tag(324, 4, n_tiles, struct.pack("<I", offs if offs else 0))
        tag(325, 4, n_tiles, struct.pack("<I", cnts if cnts else 0))
        if i == 0:
            scale = put(struct.pack("<3d", 0.001, 0.001, 0.0))
            tag(33550, 12, 3, struct.pack("<I", scale))
            tie = put(struct.pack("<6d", 0, 0, 0, 30.0, 42.0, 0))
            tag(33922, 12, 6, struct.pack("<I", tie))
        else:
            tag(33550, 4, 1, struct.pack("<I", 0))
            tag(33922, 4, 1, struct.pack("<I", 0))

        nxt = 8 + ifd_size * (i + 1) if i + 1 < len(levels) else 0
        ifd_blobs.append(struct.pack("<H", len(entries)) + b"".join(entries)
                         + struct.pack("<I", nxt))

    return bytes(header) + b"".join(ifd_blobs) + bytes(scratch)


def test_parse_ifds_reads_every_level():
    levels = _parse_ifds(_synthetic_cog())
    assert len(levels) == 2
    assert levels[0]["width"][0] == 64
    assert levels[1]["width"][0] == 32
    assert levels[0]["tile_w"][0] == 32


def test_parse_ifds_rejects_bigtiff():
    buf = bytearray(_synthetic_cog())
    buf[2:4] = struct.pack("<H", 43)
    with pytest.raises(ValueError, match="BigTIFF"):
        _parse_ifds(bytes(buf))


def test_parse_ifds_rejects_big_endian():
    buf = bytearray(_synthetic_cog())
    buf[0:2] = b"MM"
    with pytest.raises(ValueError, match="little-endian"):
        _parse_ifds(bytes(buf))


def test_geo_scales_resolution_with_overview_level():
    levels = _parse_ifds(_synthetic_cog())
    lon0, lat0, dpp0 = _geo(levels, 0)
    _, _, dpp1 = _geo(levels, 1)
    assert (lon0, lat0) == (30.0, 42.0)
    assert dpp0 == pytest.approx(0.001)
    assert dpp1 == pytest.approx(0.002), "half the width means twice the ground per pixel"


def test_pick_level_takes_the_finest_within_the_target():
    levels = _parse_ifds(_synthetic_cog())
    assert _pick_level(levels, 0.0015) == 0     # only level 0 is fine enough
    assert _pick_level(levels, 0.005) == 1      # both qualify; prefer the cheaper
