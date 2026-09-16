"""
Named regions for slicing samples and scores.

Shared by the offline harness and the live training status, so the two report
the same slices.  Türkiye is listed first and kept even while it is nearly
empty: measured on the feed it is ~0.01% of traffic against Germany's ~51% and
the Alps' ~26%, so any pooled number describes Central Europe and says nothing
about the market this product is for.  Its own slice has to exist, and has to
say "no data" out loud rather than be absorbed into an average.
"""

import numpy as np

# (name, lat_min, lat_max, lon_min, lon_max), checked in order; a row belongs to
# the first box that contains it.  Groups never straddle a region (a group is a
# single 0.5deg cell), so slicing rows by region slices groups cleanly.
REGIONS = [
    ("Türkiye",  35.8, 42.2,  25.6, 44.9),
    ("Alps",     44.0, 48.5,   5.0, 16.5),
    ("Germany",  47.2, 55.0,   5.8, 15.1),
    ("Iberia",   36.0, 43.8,  -9.5,  3.3),
]
OTHER = "other"
NAMES = [r[0] for r in REGIONS] + [OTHER]

_COL_LAT, _COL_LON = 0, 1


def region_of(X: np.ndarray) -> np.ndarray:
    """Region name per row."""
    out = np.full(len(X), OTHER, dtype=object)
    if len(X) == 0:
        return out
    lat, lon = X[:, _COL_LAT], X[:, _COL_LON]
    free = np.ones(len(X), dtype=bool)
    for name, la0, la1, lo0, lo1 in REGIONS:
        m = free & (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
        out[m] = name
        free &= ~m
    return out
