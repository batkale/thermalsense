"""
Minimal Cloud-Optimized GeoTIFF reading, shared by land cover and elevation.

Deliberately narrow: classic little-endian TIFF, tiled, DEFLATE, one band.
That covers every ESA WorldCover tile (8-bit classes) and every Copernicus
GLO-30 tile (32-bit float, floating-point predictor).  Anything else raises,
and each caller decides what a missing value means.  GDAL/rasterio would read
the general case, but the wheel alone is larger than this application and the
deployment box has 1 GiB of RAM.
"""

import struct
import zlib

import numpy as np

TAG_NAMES = {
    254: "subfile", 256: "width", 257: "height", 258: "bits", 259: "compression",
    317: "predictor", 322: "tile_w", 323: "tile_h", 324: "offsets", 325: "counts",
    339: "sample_format", 33550: "scale", 33922: "tiepoint", 34735: "geokeys",
    42113: "nodata",
}

_FMT = {1: "B", 3: "H", 4: "I", 11: "f", 12: "d"}

# GeoKey GTRasterTypeGeoKey: 1 = PixelIsArea, 2 = PixelIsPoint.
_RASTER_TYPE_KEY = 1025


def parse_ifds(buf: bytes) -> list[dict]:
    """
    Parse the IFD chain of a classic little-endian TIFF into level descriptors.

    Level 0 is full resolution; the rest are the COG's internal overviews, each
    roughly half the previous. Only tags in TAG_NAMES are decoded.
    """
    if buf[:2] != b"II":
        raise ValueError("not a little-endian TIFF")
    if struct.unpack("<H", buf[2:4])[0] != 42:
        raise ValueError("BigTIFF is not supported by this reader")

    levels: list[dict] = []
    off = struct.unpack("<I", buf[4:8])[0]

    while off and off + 2 <= len(buf):
        count = struct.unpack("<H", buf[off:off + 2])[0]
        if count == 0 or count > 200:
            break
        entry = {}
        for i in range(count):
            e = off + 2 + i * 12
            tag, typ, n = struct.unpack("<HHI", buf[e:e + 8])
            name = TAG_NAMES.get(tag)
            if name is None:
                continue
            if typ == 2:                                   # ASCII, e.g. nodata
                ptr = e + 8 if n <= 4 else struct.unpack("<I", buf[e + 8:e + 12])[0]
                if ptr + n > len(buf):
                    raise ValueError(f"tag {tag} string beyond header")
                entry[name] = buf[ptr:ptr + n].rstrip(b"\0").decode("ascii", "replace")
                continue
            fmt = _FMT.get(typ)
            if fmt is None:
                continue
            item = struct.calcsize(fmt)
            if item * n <= 4:
                entry[name] = struct.unpack(f"<{n}{fmt}", buf[e + 8:e + 8 + item * n])
            else:
                ptr = struct.unpack("<I", buf[e + 8:e + 12])[0]
                end = ptr + item * n
                if end > len(buf):
                    raise ValueError(f"tag {tag} array at {ptr}..{end} beyond header")
                entry[name] = struct.unpack(f"<{n}{fmt}", buf[ptr:end])
        levels.append(entry)
        off = struct.unpack("<I", buf[off + 2 + count * 12: off + 6 + count * 12])[0]

    if not levels:
        raise ValueError("no IFDs found")
    return levels


def pixel_is_point(levels: list[dict]) -> bool:
    """
    Whether the tiepoint names a pixel centre (Point) rather than its corner (Area).

    It decides where a coordinate lands: under Point the nearest pixel is found
    by rounding, under Area by flooring.  Getting it wrong shifts every sample
    by half a pixel, which on a steep slope is metres of elevation.
    """
    keys = levels[0].get("geokeys")
    if not keys or len(keys) < 4:
        return False
    for i in range(4, 4 + 4 * keys[3], 4):
        if i + 3 < len(keys) and keys[i] == _RASTER_TYPE_KEY:
            return keys[i + 3] == 2
    return False


def decode_block(raw: bytes, tile_w: int, tile_h: int, bits: int,
                 sample_format: int = 1, predictor: int = 1) -> np.ndarray:
    """
    Inflate and un-predict one DEFLATE-compressed tile into a (tile_h, tile_w) array.

    Predictor 3 is the TIFF floating-point predictor (Adobe Photoshop technical
    note 3): each row's samples are split into byte planes, most significant
    first regardless of file byte order, and the whole row of bytes is then
    horizontally differenced.  Undoing it is a running byte sum along the row
    followed by reassembling big-endian floats from the planes.
    """
    data = zlib.decompress(raw)
    nbytes = bits // 8

    if predictor == 3:
        if sample_format != 3:
            raise ValueError("floating-point predictor on non-float samples")
        b = np.frombuffer(data, dtype=np.uint8)
        if b.size != tile_w * tile_h * nbytes:
            raise ValueError(f"tile decoded to {b.size} bytes, expected {tile_w * tile_h * nbytes}")
        b = np.cumsum(b.reshape(tile_h, tile_w * nbytes), axis=1, dtype=np.uint8)
        planes = b.reshape(tile_h, nbytes, tile_w).transpose(0, 2, 1)
        return np.ascontiguousarray(planes).view(f">f{nbytes}").reshape(tile_h, tile_w).astype(np.float32)

    if sample_format == 3:
        dtype = np.dtype(f"<f{nbytes}")
    elif sample_format == 2:
        dtype = np.dtype(f"<i{nbytes}")
    else:
        dtype = np.dtype(f"<u{nbytes}")
    arr = np.frombuffer(data, dtype=dtype)
    if arr.size != tile_w * tile_h:
        raise ValueError(f"tile decoded to {arr.size} samples, expected {tile_w * tile_h}")
    arr = arr.reshape(tile_h, tile_w)

    if predictor == 2:
        if sample_format == 3:
            raise ValueError("horizontal integer predictor on float samples")
        arr = np.cumsum(arr, axis=1, dtype=dtype)
    elif predictor not in (1, None):
        raise ValueError(f"unsupported predictor {predictor}")
    return arr
