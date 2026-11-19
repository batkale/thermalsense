"""
Does a measurement of how hot the ground is rank lift better than terrain?

Every candidate that modelled surface heating scored what the sun angle alone
scores (CLAUDE.md: energy budget, 14 h insolation): the model knows where the
sun falls, not what the ground did with it.  Satellite land-surface
temperature (LST) is that measurement — the radiative skin temperature of each
pixel, cloud-masked.  This attaches it to every buffer row from two sources and
scores it with the protocol the hires-terrain and energy-budget runs used.

  modis   MOD11A1 / MYD11A1 v6.1 on Microsoft Planetary Computer: 1 km, one
          daytime pass a day (Terra ~10:30, Aqua ~13:30 local solar time),
          QC-masked to an LST error <= 2 K.  No account: the STAC API and the
          SAS token endpoint are anonymous.
  mtlst   LSA SAF MTG/FCI LST (LSA-007): 2 km at nadir, every 10 min, full
  mlst    disk from 0 deg.  SEVIRI MLST (LSA-004, 3 km, 15 min) and the
  mlstas  all-sky MLST-ASv2 (30 min) from the same server.  Its archive needs a
          free login, so `need` writes the file list and someone with an
          account downloads it; `geo` then reads the files from disk.

Columns per row, NaN where there is no valid value:

  lst            LST at the row's pixel, K
  lst_anom       that minus the mean valid LST over the row's grid box
                 (+-GRID_RADIUS in lat and lon, the patch one served map covers)
  lst_minus_t2m  LST minus the row's 2 m air temperature (feature column 5)
  rise, rate     geostationary only: LST now minus LST at the first clear slot
                 of the morning series, and that per hour — the measured form of
                 the integrated-insolation candidate that failed when modelled

Every row of one (day, cell, hour) unit reads the same geostationary image: the
slot at the unit's own rounded hour, HH:00.  The row's true time is up to 30
min away from it, but a unit is what one map ranks over, and comparing its rows
on two images 10 min apart would put the clock into the ranking.  The morning
series is hourly HH:00 from the first slot with the sun above the horizon.

    python -m evaluation.lst need  --buffer X.npz --out-dir DIR [--download-dir D]
    python -m evaluation.lst modis --buffer X.npz --out modis.npz --cache DIR
    uv pip install --target PYLIB --no-deps --python .venv/Scripts/python.exe h5py
    PYTHONPATH=PYLIB python -m evaluation.lst geo --product mtlst --buffer X.npz \\
        --files DIR --geoloc LATLON.nc --out mtlst.npz      # MSG: --geoloc LAT --geoloc LON
    python -m evaluation.lst score --buffer X.npz --hires drift.npz \\
        --lst modis.npz [--lst mtlst.npz] --seed 0 --seed 1 --seed 2 --boot 1500

`geo` needs h5py (NetCDF4 is HDF5), which the app does not, so it is installed
into a scratch folder rather than the venv; the output is a plain .npz.  Its
reader was checked end to end on synthetic files (exact up to int16 rounding),
not yet on a real MTLST file: variable names are guessed from the MSG products
(`LST`, CF scale / offset / fill), and it raises listing what the file does hold
if the guess is wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

_COL_LAT, _COL_LON, _COL_T2M, _COL_AGL = 0, 1, 5, 21
WINDOW_DEG = 0.05            # config.GRID_RADIUS; copied so `geo` needs no app config
_MIN_WINDOW_VALID = 3        # pixels a box mean needs before an anomaly is reported
_MIN_RISE_H = 2.0            # a "rise" over less than this is noise, not heating
_SUN_UP_DEG = 0.0
_MAX_MORNING_H = 14

LST_COLUMNS = ("lst", "lst_anom", "lst_minus_t2m")
GEO_COLUMNS = LST_COLUMNS + ("rise", "rate")


def _load_buffer(path: str):
    with np.load(path, allow_pickle=False) as d:
        return d["X"], d["y"].astype(int)


def _fingerprint(X: np.ndarray) -> str:
    # Same key as drift_terrain / energy_budget, so caches can be cross-checked.
    return hashlib.sha1(np.ascontiguousarray(X[:, [0, 1, 7, 8, 21]]).tobytes()).hexdigest()


def _timestamps(X: np.ndarray) -> np.ndarray:
    """Row times as UTC epoch seconds (year assumed current, as row_datetimes does)."""
    from evaluation.evaluate import row_datetimes
    return np.array([t.timestamp() for t in row_datetimes(X)])


def unit_slots(ts: np.ndarray) -> np.ndarray:
    """The HH:00 slot of each row's unit: decision_ids rounds the hour, so does this."""
    return np.round(np.asarray(ts, dtype=float) / 3600.0) * 3600.0


def _sun_elev(lat, lon, ts):
    from pipeline.solar import sun_position
    dt = [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in np.ravel(ts)]
    doy = np.array([d.timetuple().tm_yday for d in dt], dtype=float).reshape(np.shape(ts))
    hour = np.array([d.hour + d.minute / 60 + d.second / 3600 for d in dt]).reshape(np.shape(ts))
    el, _ = sun_position(lat, lon, doy, hour)
    return el


def morning_series(lat, lon, slot) -> list[np.ndarray]:
    """
    Hourly slots per row from the first one with the sun up to the unit's slot.

    Walks back from the unit slot and stops at the first hour the sun is down,
    so the series is one unbroken morning.  Returned oldest first.
    """
    lat, lon, slot = (np.asarray(v, dtype=float) for v in (lat, lon, slot))
    k = np.arange(_MAX_MORNING_H + 1, dtype=float)
    cand = slot[:, None] - k[None, :] * 3600.0
    el = _sun_elev(np.repeat(lat[:, None], len(k), 1), np.repeat(lon[:, None], len(k), 1), cand)
    up = el > _SUN_UP_DEG
    # number of leading True values from k = 0
    run = np.where(up.all(1), len(k), np.argmin(up, axis=1))
    return [cand[i, :run[i]][::-1] for i in range(len(slot))]


def heating(series_t: np.ndarray, series_v: np.ndarray, now_t: float, now_v: float):
    """(rise K, rate K/h) since the first clear slot of the morning, NaN if none.

    The first clear slot must be at least _MIN_RISE_H before now; a value one
    slot back would report noise in the retrieval as heating.
    """
    if not np.isfinite(now_v):
        return np.nan, np.nan
    ok = np.isfinite(series_v) & (series_t <= now_t - _MIN_RISE_H * 3600.0)
    if not ok.any():
        return np.nan, np.nan
    j = int(np.argmax(ok))
    rise = float(now_v - series_v[j])
    return rise, rise / ((now_t - series_t[j]) / 3600.0)


def anomaly(centre: float, box: np.ndarray) -> float:
    """centre minus the mean of the valid values in its grid box."""
    box = np.asarray(box, dtype=float)
    box = box[np.isfinite(box)]
    if not np.isfinite(centre) or box.size < _MIN_WINDOW_VALID:
        return np.nan
    return float(centre - box.mean())


# --- geostationary viewing geometry -------------------------------------------

R_EARTH_KM = 6371.0
R_GEO_KM = 42164.0


def _unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def geo_view(lat, lon, sub_lon: float = 0.0):
    """(view zenith angle deg, slant range km) from a geostationary satellite."""
    p = R_EARTH_KM * _unit(np.asarray(lat, float), np.asarray(lon, float))
    s = R_GEO_KM * _unit(0.0, sub_lon)
    d = s - p
    slant = np.linalg.norm(d, axis=-1)
    cos_vza = np.sum(d * p, axis=-1) / (slant * R_EARTH_KM)
    return np.degrees(np.arccos(np.clip(cos_vza, -1, 1))), slant


def pixel_km(lat, lon, nadir_km: float, sub_lon: float = 0.0):
    """
    Ground footprint of one sample: (across, along) km.

    The angular step is fixed, so the footprint grows with slant range in both
    directions and is stretched by 1 / cos(VZA) along the direction the view is
    tilted (north-south over Europe).  NaN where the point is not in view.
    """
    vza, slant = geo_view(lat, lon, sub_lon)
    across = slant * nadir_km / (R_GEO_KM - R_EARTH_KM)
    with np.errstate(divide="ignore", invalid="ignore"):
        along = across / np.cos(np.radians(vza))
    bad = vza >= 89.0
    return np.where(bad, np.nan, across), np.where(bad, np.nan, along)


# --- products ------------------------------------------------------------------

_LSA = "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS"
PRODUCTS = {
    # name: (url dir, file pattern, step min, nadir km, approx MB per file)
    "mtlst":  ("MTG/MTLST/NATIVE", "LSA-007_MTG_MTLST_MTG-FD_{stamp}.nc", 10, 2.0, 10.7),
    "mlst":   ("MSG/MLST/NETCDF", "NETCDF4_LSASAF_MSG_LST_MSG-Disk_{stamp}.nc", 15, 3.0, 3.7),
    "mlstas": ("MSG/MLST-ASv2/NETCDF", "NETCDF4_LSASAF_MSG_MLST-ASv2_MSG-Disk_{stamp}.nc", 30, 3.0, 3.2),
}
GEOLOC = {
    "mtlst": [f"{_LSA}/MTG/STATIC/NETCDF/LSA_MTG_LATLON_MTG-FD_202312040900.nc"],
    "mlst": [f"{_LSA}/MSG/STATIC/HDF5/HDF5_LSASAF_MSG_LAT_MSG-Disk_202303210900",
             f"{_LSA}/MSG/STATIC/HDF5/HDF5_LSASAF_MSG_LON_MSG-Disk_202303210900"],
}
GEOLOC["mlstas"] = GEOLOC["mlst"]
_MAX_VZA = 80.0              # beyond this nothing is retrieved; skip asking for it


def file_name(product: str, t: float) -> str:
    stamp = datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%Y%m%d%H%M")
    return PRODUCTS[product][1].format(stamp=stamp)


def file_url(product: str, t: float) -> str:
    d = datetime.fromtimestamp(float(t), tz=timezone.utc)
    return f"{_LSA}/{PRODUCTS[product][0]}/{d:%Y/%m/%d}/{file_name(product, t)}"


def _kept(X: np.ndarray) -> np.ndarray:
    from data.meteo_client import is_fallback_row
    from models.thermal_model import _MIN_SAMPLE_AGL
    return ~is_fallback_row(X) & (X[:, _COL_AGL] >= _MIN_SAMPLE_AGL)


def geo_need(X: np.ndarray, ts: np.ndarray):
    """Rows in view, their unit slots, and their morning series."""
    vza, _ = geo_view(X[:, _COL_LAT], X[:, _COL_LON])
    slot = unit_slots(ts)
    rows = np.flatnonzero(vza < _MAX_VZA)
    series = morning_series(X[rows, _COL_LAT], X[rows, _COL_LON], slot[rows])
    return rows, slot, series


def cmd_need(args) -> int:
    from evaluation.holdout import decision_ids
    from evaluation.metrics import grouped_auc

    X, y = _load_buffer(args.buffer)
    keep = _kept(X)
    X, y = X[keep], y[keep]
    ts = _timestamps(X)
    rows, slot, series = geo_need(X, ts)
    ge = decision_ids(X)
    print(f"rows kept {len(X)}; in view of 0 deg (VZA < {_MAX_VZA:.0f}) {len(rows)} "
          f"({len(rows) / len(X):.1%}); mixed-label units {grouped_auc(np.zeros(len(y)), y, ge)['groups']}, "
          f"in view {grouped_auc(np.zeros(len(rows)), y[rows], ge[rows])['groups']}")

    unit = {float(slot[i]) for i in rows}
    morning = set()
    for s in series:
        morning.update(float(v) for v in s)
    days = sorted({datetime.fromtimestamp(t, tz=timezone.utc).date() for t in unit})
    print(f"unit slots {len(unit)} over {len(days)} days ({days[0]} .. {days[-1]}); "
          f"morning-series slots {len(morning)}, of which {len(morning - unit)} are extra")

    # Effective pixel size where the rows are.
    from evaluation.regions import NAMES, region_of
    reg = region_of(X[rows])
    lat, lon = X[rows, _COL_LAT], X[rows, _COL_LON]
    vza, _ = geo_view(lat, lon)
    print("\neffective pixel at the rows (median; across x along, km):")
    for name in NAMES:
        m = reg == name
        if not m.any():
            continue
        parts = []
        for prod, nadir in (("FCI", 2.0), ("SEVIRI", 3.0)):
            a, b = pixel_km(lat[m], lon[m], nadir)
            parts.append(f"{prod} {np.median(a):.1f} x {np.median(b):.1f}")
        print(f"  {name:<8} {int(m.sum()):>6} rows  VZA {np.median(vza[m]):4.0f} deg   " + "   ".join(parts))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    allslots = sorted(unit | morning)
    for prod, (_, _, step, _, mb) in PRODUCTS.items():
        lines = [f'url = "{u}"' for u in GEOLOC[prod]]
        lines += [f'url = "{file_url(prod, t)}"' for t in allslots]
        (out / f"lsasaf_{prod}.curl").write_text("\n".join(lines) + "\n")
        (out / f"lsasaf_{prod}_unit_only.curl").write_text(
            "\n".join([f'url = "{u}"' for u in GEOLOC[prod]]
                      + [f'url = "{file_url(prod, t)}"' for t in sorted(unit)]) + "\n")
        print(f"\n{prod}: {len(allslots)} files, ~{len(allslots) * mb / 1024:.1f} GB "
              f"(unit slots only: {len(unit)}, ~{len(unit) * mb / 1024:.1f} GB) "
              f"-> {out / f'lsasaf_{prod}.curl'}")
    (out / "lsasaf_slots.json").write_text(json.dumps({
        "unit": [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d%H%M") for t in sorted(unit)],
        "morning_extra": [datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y%m%d%H%M")
                          for t in sorted(morning - unit)],
    }, indent=0))
    return 0


# --- MODIS on Planetary Computer -------------------------------------------------

_PC = "https://planetarycomputer.microsoft.com/api"
_COLLECTION = "modis-11A1-061"
_ASSETS = ("LST_Day_1km", "QC_Day", "Day_view_time")
_R_SIN = 6371007.181
_TILE_M = 1111950.5197665
_X0, _Y0 = -20015109.355798, 10007554.677899
_NPIX = 1200
_PIX_M = _TILE_M / _NPIX


def sinusoidal(lat, lon):
    """MODIS tile (h, v) and fractional (row, col) inside it, pixel centres at .5."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    x = _R_SIN * np.radians(lon) * np.cos(np.radians(lat))
    y = _R_SIN * np.radians(lat)
    h = np.floor((x - _X0) / _TILE_M).astype(int)
    v = np.floor((_Y0 - y) / _TILE_M).astype(int)
    col = (x - (_X0 + h * _TILE_M)) / _PIX_M
    row = ((_Y0 - v * _TILE_M) - y) / _PIX_M
    return h, v, row, col


def qc_ok(qc) -> np.ndarray:
    """MOD11 QC_Day: produced and good (00), or other quality with LST error <= 2 K."""
    qc = np.asarray(qc).astype(np.uint8)
    mandatory = qc & 0b11
    lst_err = (qc >> 6) & 0b11
    return (mandatory == 0) | ((mandatory == 1) & (lst_err <= 1))


def modis_lst(lst_raw, qc) -> np.ndarray:
    """LST in K for one tile, NaN where it is fill or fails qc_ok."""
    return np.where((lst_raw > 0) & qc_ok(qc), lst_raw * 0.02, np.nan)


def modis_values(lst, view_raw, r: int, c: int, wr: int, wc: int):
    """(LST K, box anomaly K, local solar view time h) at (r, c) from one tile's modis_lst."""
    n = lst.shape[0]
    if not (0 <= r < n and 0 <= c < lst.shape[1]):
        return np.nan, np.nan, np.nan
    box = lst[max(r - wr, 0):r + wr + 1, max(c - wc, 0):c + wc + 1]
    centre = float(lst[r, c])
    vt = float(view_raw[r, c]) * 0.1 if view_raw[r, c] != 255 else np.nan
    return centre, anomaly(centre, box), (vt if np.isfinite(centre) else np.nan)


def _item_key(item_id: str):
    """('aqua'|'terra', h, v, date, processing stamp) from a MOD11A1/MYD11A1 id."""
    m = re.match(r"(M[OY])D11A1\.A(\d{4})(\d{3})\.h(\d\d)v(\d\d)\.061\.(\d+)", item_id)
    if not m:
        return None
    plat = "terra" if m.group(1) == "MO" else "aqua"
    d = (datetime(int(m.group(2)), 1, 1) + timedelta(days=int(m.group(3)) - 1)).date()
    return plat, int(m.group(4)), int(m.group(5)), d, m.group(6)


class _Signed:
    """Anonymous SAS-signed range reads from Planetary Computer, cached on disk.

    The cache key is the unsigned URL plus the byte range, so a refreshed token
    still finds what an earlier run fetched.
    """

    def __init__(self, client, root: Path):
        self.client, self.root = client, root
        self.tokens: dict[str, tuple[str, float]] = {}
        self.reads = self.hits = self.fails = 0
        root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def _token(self, account: str, container: str) -> str:
        key = f"{account}/{container}"
        async with self._lock:
            tok = self.tokens.get(key)
            if tok and tok[1] - time.time() > 300:
                return tok[0]
            r = await self.client.get(f"{_PC}/sas/v1/token/{account}/{container}")
            r.raise_for_status()
            js = r.json()
            exp = datetime.fromisoformat(js["msft:expiry"].replace("Z", "+00:00")).timestamp()
            self.tokens[key] = (js["token"], exp)
            return js["token"]

    async def get(self, url: str, start: int, end: int) -> bytes | None:
        f = self.root / hashlib.sha1(f"{url}|{start}-{end}".encode()).hexdigest()
        if f.exists():
            self.hits += 1
            return f.read_bytes()
        m = re.match(r"https://([^.]+)\.blob\.core\.windows\.net/([^/]+)/", url)
        import httpx
        for wait in (2, 5, 15, 30, None):
            try:
                tok = await self._token(m.group(1), m.group(2))
                r = await self.client.get(f"{url}?{tok}", headers={"Range": f"bytes={start}-{end}"})
                if r.status_code in (200, 206):
                    tmp = f.with_suffix(".tmp")
                    tmp.write_bytes(r.content)
                    tmp.replace(f)
                    self.reads += 1
                    return r.content
                if r.status_code == 416:            # range past the end: short file
                    return b""
                if r.status_code == 403:
                    self.tokens.clear()
                if r.status_code not in (403, 429, 500, 502, 503, 504):
                    self.fails += 1
                    return None
            except httpx.TransportError:
                pass
            if wait is None:
                self.fails += 1
                return None
            await asyncio.sleep(wait)


async def _cog_level0(blob: _Signed, url: str):
    from data import cog
    for size in (65536, 262144):
        head = await blob.get(url, 0, size - 1)
        if not head:
            return None
        try:
            return cog.parse_ifds(head)[0]
        except ValueError:
            continue
    return None


async def _cog_read(blob: _Signed, url: str, blocks_needed: set[int]):
    """Level-0 array of one COG with only the listed 512-blocks filled (rest 0)."""
    from data import cog
    lv = await _cog_level0(blob, url)
    if lv is None:
        return None
    tw, th = lv["tile_w"][0], lv["tile_h"][0]
    w, h = lv["width"][0], lv["height"][0]
    bits = lv["bits"][0]
    across = -(-w // tw)
    out = np.zeros((-(-h // th) * th, across * tw), dtype=np.dtype(f"<u{bits // 8}"))
    for idx in blocks_needed:
        off, cnt = lv["offsets"][idx], lv["counts"][idx]
        if cnt == 0:
            continue
        raw = await blob.get(url, off, off + cnt - 1)
        if raw is None:
            return None
        arr = cog.decode_block(raw, tw, th, bits, lv.get("sample_format", (1,))[0],
                               lv.get("predictor", (1,))[0])
        r0, c0 = (idx // across) * th, (idx % across) * tw
        out[r0:r0 + th, c0:c0 + tw] = arr
    return out[:h, :w]


async def _stac_items(client, h: int, v: int, d0, d1) -> list[dict]:
    body = {"collections": [_COLLECTION], "limit": 250,
            "datetime": f"{d0.isoformat()}T00:00:00Z/{d1.isoformat()}T23:59:59Z",
            "query": {"modis:horizontal-tile": {"eq": h}, "modis:vertical-tile": {"eq": v}}}
    out, url = [], f"{_PC}/stac/v1/search"
    for _ in range(20):
        for wait in (2, 5, 15, 30, None):
            try:
                r = await client.post(url, json=body)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            if wait is None:
                raise RuntimeError(f"STAC search failed for h{h}v{v}")
            await asyncio.sleep(wait)
        js = r.json()
        out += js.get("features", [])
        nxt = [l for l in js.get("links", []) if l.get("rel") == "next"]
        if not nxt:
            break
        body = nxt[0].get("body", body)
        url = nxt[0].get("href", url)
    return out


async def _modis(X: np.ndarray, ts: np.ndarray, cache: Path, concurrency: int = 12):
    import httpx
    n = len(X)
    lat, lon = X[:, _COL_LAT].astype(float), X[:, _COL_LON].astype(float)
    h, v, rowf, colf = sinusoidal(lat, lon)
    r, c = np.floor(rowf).astype(int), np.floor(colf).astype(int)
    # The daytime pass is near local noon, so the row's local solar date names it.
    local = [datetime.fromtimestamp(t + lo / 15.0 * 3600.0, tz=timezone.utc).date()
             for t, lo in zip(ts, lon)]
    wr = np.full(n, int(round(WINDOW_DEG * 111_320.0 / _PIX_M)))
    wc = np.maximum(1, np.round(WINDOW_DEG * 111_320.0 * np.cos(np.radians(lat)) / _PIX_M)).astype(int)

    groups = defaultdict(list)
    for i in range(n):
        groups[(int(h[i]), int(v[i]), local[i])].append(i)
    tiles = defaultdict(list)
    for (hh, vv, d) in groups:
        tiles[(hh, vv)].append(d)
    print(f"{n} rows in {len(groups)} tile-days over {len(tiles)} MODIS tiles", flush=True)

    feats = {p: np.full((n, 4), np.nan) for p in ("aqua", "terra")}
    stats = defaultdict(int)
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=concurrency + 4)) as client:
        catalogue, searched = {}, {}
        cat_file = cache / "stac_catalogue.json"
        if cat_file.exists():
            js = json.loads(cat_file.read_text())
            catalogue = {tuple(json.loads(k)): val for k, val in js.get("items", {}).items()}
            searched = {tuple(json.loads(k)): val for k, val in js.get("searched", {}).items()}
        # A tile searched before counts only for the dates that search spanned.
        todo = [t for t in tiles
                if t not in searched or not (searched[t][0] <= min(tiles[t]).isoformat()
                                             and max(tiles[t]).isoformat() <= searched[t][1])]
        sem = asyncio.Semaphore(4)

        async def search(t):
            async with sem:
                items = await _stac_items(client, t[0], t[1], min(tiles[t]), max(tiles[t]))
            searched[t] = [min(tiles[t]).isoformat(), max(tiles[t]).isoformat()]
            for it in items:
                key = _item_key(it["id"])
                if key is None:
                    continue
                plat, hh, vv, d, stamp = key
                k = (plat, hh, vv, d.isoformat())
                if k not in catalogue or catalogue[k]["stamp"] < stamp:
                    catalogue[k] = {"stamp": stamp,
                                    **{a: it["assets"][a]["href"] for a in _ASSETS if a in it["assets"]}}
        await asyncio.gather(*(search(t) for t in todo))
        cache.mkdir(parents=True, exist_ok=True)
        cat_file.write_text(json.dumps({
            "items": {json.dumps(list(k)): val for k, val in catalogue.items()},
            "searched": {json.dumps(list(k)): val for k, val in searched.items()}}))
        print(f"catalogue: {len(catalogue)} items ({len(todo)} tiles searched)", flush=True)

        blob = _Signed(client, cache / "blocks")
        sem = asyncio.Semaphore(concurrency)
        done = 0
        t0 = time.time()

        async def one(key, idx, plat):
            nonlocal done
            hh, vv, d = key
            item = catalogue.get((plat, hh, vv, d.isoformat()))
            if item is None or not all(a in item for a in _ASSETS):
                stats[f"{plat}_no_item"] += len(idx)
                return
            idx = np.array(idx)
            need = set()
            for i in idx:
                for rr in (r[i] - wr[i], r[i] + wr[i]):
                    for cc in (c[i] - wc[i], c[i] + wc[i]):
                        rr2, cc2 = min(max(rr, 0), _NPIX - 1), min(max(cc, 0), _NPIX - 1)
                        need.add((rr2 // 512) * 3 + cc2 // 512)
            async with sem:
                arrs = [await _cog_read(blob, item[a], need) for a in _ASSETS]
            if any(a is None for a in arrs):
                stats[f"{plat}_read_failed"] += len(idx)
                return
            lst_raw, qc, view = arrs
            stats[f"{plat}_pixels_fill"] += int((lst_raw[r[idx], c[idx]] == 0).sum())
            stats[f"{plat}_pixels_qc_drop"] += int(((lst_raw[r[idx], c[idx]] > 0)
                                                    & ~qc_ok(qc[r[idx], c[idx]])).sum())
            lst_k = modis_lst(lst_raw, qc)
            for i in idx:
                val, anom, vt = modis_values(lst_k, view, int(r[i]), int(c[i]), int(wr[i]), int(wc[i]))
                feats[plat][i] = (val, anom, val - (X[i, _COL_T2M] + 273.15), vt)
            done += 1
            if done % 200 == 0:
                print(f"  {done} tile-day reads  {time.time() - t0:.0f}s  blocks fetched {blob.reads} "
                      f"cached {blob.hits} failed {blob.fails}", flush=True)

        await asyncio.gather(*(one(k, idx, p) for k, idx in groups.items() for p in ("aqua", "terra")))
    print(f"blocks fetched {blob.reads}, from cache {blob.hits}, failed {blob.fails}; {dict(stats)}")
    return feats


def cmd_modis(args) -> int:
    X, _ = _load_buffer(args.buffer)
    feats = asyncio.run(_modis(X, _timestamps(X), Path(args.cache)))
    a, t = feats["aqua"], feats["terra"]
    model = np.column_stack([a[:, :3], t[:, :3]])
    np.savez(args.out, fingerprint=np.array(_fingerprint(X)), source=np.array("modis"),
             columns=np.array([f"aqua_{c}" for c in LST_COLUMNS] + [f"terra_{c}" for c in LST_COLUMNS]),
             feats=model, extra_columns=np.array(["aqua_view_time_h", "terra_view_time_h"]),
             extra=np.column_stack([a[:, 3], t[:, 3]]))
    keep = _kept(X)
    for name, f in (("aqua", a), ("terra", t)):
        print(f"  {name}: lst valid {np.isfinite(f[keep, 0]).mean():.1%} of kept rows, "
              f"anomaly valid {np.isfinite(f[keep, 1]).mean():.1%}, "
              f"view time median {np.nanmedian(f[keep, 3]):.1f} h local solar")
    print(f"wrote {args.out}")
    return 0


# --- LSA SAF geostationary files ---------------------------------------------------

def _cf_decode(ds) -> np.ndarray:
    """An HDF5/NetCDF4 variable as float with CF scale, offset and fill applied."""
    raw = ds[()]
    a = ds.attrs
    out = raw.astype(np.float64)
    bad = np.zeros(raw.shape, dtype=bool)
    for key in ("_FillValue", "missing_value", "MISSING_VALUE"):
        if key in a:
            bad |= raw == np.asarray(a[key]).ravel()[0]
    scale = float(np.asarray(a.get("scale_factor", a.get("SCALING_FACTOR", 1.0))).ravel()[0])
    offset = float(np.asarray(a.get("add_offset", a.get("OFFSET", 0.0))).ravel()[0])
    if "SCALING_FACTOR" in a and "scale_factor" not in a:
        out = (out - offset) / scale        # LSA SAF HDF5 convention: physical = raw / SF
    else:
        out = out * scale + offset
    out[bad] = np.nan
    return np.squeeze(out)


def _find_var(f, names) -> object:
    for n in names:
        if n in f:
            return f[n]
    found = []
    f.visit(lambda k: found.append(k))
    for n in names:
        for k in found:
            if k.split("/")[-1].lower() == n.lower():
                return f[k]
    raise KeyError(f"none of {names} in file; it holds {found[:30]}")


class GeoGrid:
    """Nearest pixel of a geostationary product for (lat, lon), from its lat/lon arrays."""

    def __init__(self, lat: np.ndarray, lon: np.ndarray, coarse: int = 4):
        from scipy.spatial import cKDTree
        self.lat, self.lon = lat, lon
        ok = np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90)
        sub = np.zeros_like(ok)
        sub[::coarse, ::coarse] = True
        rr, cc = np.nonzero(ok & sub)
        self.rc = np.column_stack([rr, cc])
        self.tree = cKDTree(_unit(lat[rr, cc], lon[rr, cc]))
        self.coarse = coarse

    def locate(self, la: np.ndarray, lo: np.ndarray, max_km: float):
        """(row, col) per point, -1 where the nearest pixel centre is further than max_km."""
        la, lo = np.asarray(la, float), np.asarray(lo, float)
        _, j = self.tree.query(_unit(la, lo))
        out = np.full((len(la), 2), -1, dtype=int)
        k = self.coarse
        H, W = self.lat.shape
        for i, (r0, c0) in enumerate(self.rc[j]):
            r1, r2 = max(r0 - k, 0), min(r0 + k + 1, H)
            c1, c2 = max(c0 - k, 0), min(c0 + k + 1, W)
            plat, plon = self.lat[r1:r2, c1:c2], self.lon[r1:r2, c1:c2]
            d = np.linalg.norm(_unit(plat, plon) - _unit(la[i], lo[i]), axis=-1) * R_EARTH_KM
            d = np.where(np.isfinite(d), d, np.inf)
            a = np.unravel_index(np.argmin(d), d.shape)
            if d[a] <= max_km:
                out[i] = (r1 + a[0], c1 + a[1])
        return out

    def box(self, r: int, c: int, la: float, lo: float, half: int = 6):
        """Pixel indices around (r, c) whose centres fall in the row's grid box."""
        H, W = self.lat.shape
        r1, r2 = max(r - half, 0), min(r + half + 1, H)
        c1, c2 = max(c - half, 0), min(c + half + 1, W)
        m = ((np.abs(self.lat[r1:r2, c1:c2] - la) <= WINDOW_DEG)
             & (np.abs(self.lon[r1:r2, c1:c2] - lo) <= WINDOW_DEG))
        rr, cc = np.nonzero(m)
        return rr + r1, cc + c1


def _read_geoloc(product: str, paths: list[str]):
    import h5py
    if len(paths) == 1:
        with h5py.File(paths[0], "r") as f:
            return _cf_decode(_find_var(f, ["lat", "latitude", "LAT"])), \
                   _cf_decode(_find_var(f, ["lon", "longitude", "LON"]))
    with h5py.File(paths[0], "r") as f:
        lat = _cf_decode(_find_var(f, ["LAT", "lat", "latitude"]))
    with h5py.File(paths[1], "r") as f:
        lon = _cf_decode(_find_var(f, ["LON", "lon", "longitude"]))
    return lat, lon


_LST_NAMES = {"mtlst": ["LST", "lst"], "mlst": ["LST", "lst"], "mlstas": ["MLST-AS", "LST", "lst"]}


def cmd_geo(args) -> int:
    import h5py
    X, _ = _load_buffer(args.buffer)
    ts = _timestamps(X)
    rows, slot, series = geo_need(X, ts)
    lat, lon = _read_geoloc(args.product, args.geoloc)
    print(f"{args.product}: grid {lat.shape}, {len(rows)} rows in view", flush=True)
    grid = GeoGrid(lat, lon)
    nadir = PRODUCTS[args.product][3]
    _, along = pixel_km(X[rows, _COL_LAT], X[rows, _COL_LON], nadir)
    pix = grid.locate(X[rows, _COL_LAT], X[rows, _COL_LON], max_km=np.nanmax(along))

    needs = defaultdict(list)         # slot -> [(k, kind)] kind 0 = unit slot, 1 = morning
    for k, i in enumerate(rows):
        if pix[k, 0] < 0:
            continue
        needs[float(slot[i])].append((k, 0))
        for t in series[k]:
            if t != slot[i]:
                needs[float(t)].append((k, 1))
    files = Path(args.files)
    n = len(X)
    now = np.full(n, np.nan)
    anom = np.full(n, np.nan)
    morning = defaultdict(dict)       # k -> {t: value}
    missing = 0
    t0 = time.time()
    names = _LST_NAMES[args.product]
    for s_i, (t, items) in enumerate(sorted(needs.items())):
        fn = files / file_name(args.product, t)
        if not fn.exists():
            hits = list(files.rglob(fn.name))
            fn = hits[0] if hits else None
        if fn is None:
            missing += 1
            continue
        with h5py.File(fn, "r") as f:
            lst = _cf_decode(_find_var(f, names))
        if lst.shape != lat.shape:
            raise SystemExit(f"{fn.name}: LST grid {lst.shape} is not the geolocation grid {lat.shape}")
        if np.nanmedian(lst) < 150:                       # stored in Celsius
            lst = lst + 273.15
        for k, kind in items:
            r, c = pix[k]
            v = float(lst[r, c])
            if kind == 1:
                morning[k][t] = v
                continue
            i = rows[k]
            morning[k][t] = v
            now[i] = v
            br, bc = grid.box(r, c, X[i, _COL_LAT], X[i, _COL_LON])
            anom[i] = anomaly(v, lst[br, bc])
        if s_i % 25 == 0:
            print(f"  {s_i + 1}/{len(needs)} slots  {time.time() - t0:.0f}s", flush=True)
    rise = np.full(n, np.nan)
    rate = np.full(n, np.nan)
    for k, i in enumerate(rows):
        st = np.array(series[k]) if len(series[k]) else np.zeros(0)
        sv = np.array([morning[k].get(float(tt), np.nan) for tt in st])
        rise[i], rate[i] = heating(st, sv, float(slot[i]), now[i])
    vza = np.full(n, np.nan)
    vza[rows], _ = geo_view(X[rows, _COL_LAT], X[rows, _COL_LON])
    feats = np.column_stack([now, anom, now - (X[:, _COL_T2M] + 273.15), rise, rate])
    np.savez(args.out, fingerprint=np.array(_fingerprint(X)), source=np.array(args.product),
             columns=np.array([f"{args.product}_{c}" for c in GEO_COLUMNS]), feats=feats,
             extra_columns=np.array(["vza", "slot_offset_min"]),
             extra=np.column_stack([vza, (ts - slot) / 60.0]))
    keep = _kept(X)
    print(f"slots missing on disk {missing} of {len(needs)}")
    for j, cname in enumerate(GEO_COLUMNS):
        print(f"  {cname:<14} valid {np.isfinite(feats[keep, j]).mean():.1%} of kept rows")
    print(f"wrote {args.out}")
    return 0


# --- scoring ---------------------------------------------------------------------

def unit_lapse(v: np.ndarray, z: np.ndarray, ge: np.ndarray) -> float:
    """
    How fast LST falls with height inside one unit, K/m (positive = cooler higher).

    Skin temperature drops with altitude, and gliders climb over high ground,
    so within an hour the raw value mostly says how high a pixel is — measured
    on the 6 Nov buffer it ranked lift *below* chance.  The slope is fitted on
    unit-demeaned values with no label involved, so it removes height without
    looking at the answer.
    """
    m = np.isfinite(v) & np.isfinite(z)
    _, inv = np.unique(ge[m], return_inverse=True)
    n = np.bincount(inv)

    def demean(a):
        return a - (np.bincount(inv, a) / n)[inv]

    vz, zz = demean(v[m]), demean(z[m])
    return float(-(vz * zz).sum() / max((zz * zz).sum(), 1e-9))


def lapse_adjusted(feats: np.ndarray, cols: list[str], z: np.ndarray, relief: np.ndarray,
                   ge: np.ndarray):
    """
    Each *_lst and *_lst_anom column with height taken out.

    lst + lapse * z puts every pixel at sea level; the anomaly's box mean sits
    at the box's mean height, approximated by the ring in tpi_2500 (centre minus
    the ground ~2.5 km round), so it gets lapse * relief.  One lapse per
    platform / product, from unit_lapse.
    """
    out, names, lapses = [], [], {}
    for j, c in enumerate(cols):
        if not c.endswith("_lst"):
            continue
        p = c[:-len("_lst")]
        g = unit_lapse(feats[:, j], z, ge)
        lapses[p] = g
        out.append(feats[:, j] + g * z)
        names.append(f"{p}_lst_z")
        if f"{p}_lst_anom" in cols:
            out.append(feats[:, cols.index(f"{p}_lst_anom")] + g * relief)
            names.append(f"{p}_lst_anom_z")
    return np.column_stack(out), names, lapses


def _load_lst(path: str, X: np.ndarray):
    with np.load(path, allow_pickle=False) as d:
        if str(d["fingerprint"]) != _fingerprint(X):
            raise SystemExit(f"{path} was computed for a different buffer")
        return str(d["source"]), [str(c) for c in d["columns"]], d["feats"]


def coverage(feats: np.ndarray, columns: list[str], y: np.ndarray, ge: np.ndarray) -> dict:
    """Shares of rows and of mixed-label units the source says anything about."""
    from evaluation.metrics import grouped_auc
    lst_cols = [j for j, c in enumerate(columns) if c.endswith("_lst")]
    covered = np.isfinite(feats[:, lst_cols]).any(axis=1)
    pos = defaultdict(int)
    neg = defaultdict(int)
    any_ = defaultdict(int)
    for g, yy, cv in zip(ge, y, covered):
        any_[g] |= int(cv)
        if cv:
            if yy:
                pos[g] += 1
            else:
                neg[g] += 1
    mixed = {g for g in set(ge.tolist())}
    lab = defaultdict(set)
    for g, yy in zip(ge, y):
        lab[g].add(int(yy))
    mixed = [g for g in mixed if len(lab[g]) == 2]
    return {
        "rows": float(covered.mean()),
        "mixed_units": len(mixed),
        "units_any": float(np.mean([any_[g] for g in mixed])) if mixed else 0.0,
        "units_both": float(np.mean([pos[g] > 0 and neg[g] > 0 for g in mixed])) if mixed else 0.0,
        "covered": covered,
        "auc_groups": grouped_auc(np.zeros(int(covered.sum())), y[covered], ge[covered])["groups"],
    }


def cmd_score(args) -> int:
    from evaluation.evaluate import _terrain_multiplier, oof_scores
    from evaluation.hires_terrain import COLUMNS as HC
    from evaluation.holdout import decision_ids, group_ids
    from evaluation.metrics import grouped_auc, paired_delta_ci

    X, y = _load_buffer(args.buffer)
    with np.load(args.hires, allow_pickle=False) as c:
        if str(c["fingerprint"]) != _fingerprint(X):
            raise SystemExit(f"{args.hires} was computed for a different buffer")
        hires = c["f_under"]
    if np.isnan(hires[:, 0]).mean() > 0.02:
        raise SystemExit(f"{args.hires}: more than 2% of rows have no terrain")
    sources = {}
    for p in args.lst:
        name, cols, f = _load_lst(p, X)
        sources[name] = (cols, f)

    keep = _kept(X)
    X, y, hires = X[keep], y[keep], hires[keep]
    sources = {k: (cols, f[keep]) for k, (cols, f) in sources.items()}
    gfold, ge = group_ids(X), decision_ids(X)
    tm = _terrain_multiplier(X)
    print(f"rows {len(y)}, positives {int(y.sum())}, mixed-label units "
          f"{grouped_auc(np.zeros(len(y)), y, ge)['groups']}")

    cov = {}
    print("\ncoverage (a valid LST value at the row)")
    for name, (cols, f) in sources.items():
        cov[name] = coverage(f, cols, y, ge)
        cv = cov[name]
        print(f"  {name:<7} rows {cv['rows']:.1%}   mixed-label units with any covered row "
              f"{cv['units_any']:.1%}, with a covered positive and negative {cv['units_both']:.1%} "
              f"({cv['auc_groups']} units scoreable on covered rows alone)")
        for j, cname in enumerate(cols):
            print(f"      {cname:<24} valid {np.isfinite(f[:, j]).mean():.1%}")

    tpi = hires[:, HC.index("tpi_2500")]
    z = X[:, 2].astype(float)
    print("\nwithin-unit lapse of LST with height (label-free; removed in the *_z columns)")
    for name in list(sources):
        cols, f = sources[name]
        adj, adj_cols, lapses = lapse_adjusted(f, cols, z, tpi, ge)
        sources[f"{name}_z"] = (adj_cols, adj)
        cov[f"{name}_z"] = cov[name]
        for p, g in lapses.items():
            print(f"  {p:<7} {g * 1000:+.2f} K/km")

    print("\nsingle columns, no model (micro within-hour AUC on rows where the column is "
          "valid; tpi_2500 and elevation on the same rows for reference — busy places "
          "are easier, and high ground is cooler)")
    for name, (cols, f) in sources.items():
        for j, cname in enumerate(cols):
            v = f[:, j]
            m = np.isfinite(v)
            if grouped_auc(np.zeros(int(m.sum())), y[m], ge[m])["groups"] == 0:
                print(f"  {cname:<24} no scoreable unit")
                continue
            a = grouped_auc(v[m], y[m], ge[m])
            b = grouped_auc(tpi[m], y[m], ge[m])
            e = grouped_auc(z[m], y[m], ge[m])
            print(f"  {cname:<24} {a['micro']:.3f}   tpi_2500 {b['micro']:.3f}   "
                  f"elevation {e['micro']:.3f}   ({a['groups']} units)")

    blocks = {"hires": hires, **{k: f for k, (_, f) in sources.items()}}
    raw = [k for k in sources if not k.endswith("_z")]
    variants = {"base": (), "hires": ("hires",)}
    for name in raw:
        variants[f"base+{name}"] = (name,)
        variants[f"hires+{name}"] = ("hires", name)
        variants[f"hires+{name}_z"] = ("hires", name, f"{name}_z")
    if len(raw) > 1:
        variants["hires+all"] = ("hires", *raw, *(f"{k}_z" for k in raw))
    Xi = np.column_stack([X, np.arange(len(X), dtype=float)])

    def tf(parts):
        return lambda Z: np.column_stack([Z[:, :-1]] + [blocks[p][Z[:, -1].astype(int)] for p in parts])

    for seed in args.seed:
        t0 = time.time()
        served = {name: oof_scores(Xi, y, gfold, tf(parts), k=5, seed=seed) * tm
                  for name, parts in variants.items()}
        print(f"\nseed {seed}  ({time.time() - t0:.0f}s)   delta vs reference, paired 95% CI "
              f"({args.boot} resamples)")
        def ci(a, b, m=None):
            m = np.ones(len(y), bool) if m is None else m
            d = paired_delta_ci(a[m], b[m], y[m], ge[m], n_boot=args.boot, seed=seed)
            return f"{d['point']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]", d["groups"]

        for name, s in served.items():
            line = f"  {name:<14} served {grouped_auc(s, y, ge)['micro']:.3f}"
            if name == "base":
                line += f"   vs hires {ci(served['hires'], s)[0]}"
            elif name != "hires":
                refs = ("base", "hires") if name.startswith("base+") else ("hires",)
                line += "".join(f"   vs {r} {ci(served[r], s)[0]}" for r in refs)
                src = name.split("+", 1)[1]
                if src in cov:
                    # Units with at least one covered row, every row of them kept:
                    # the ranking a map makes where the satellite saw something.
                    units = {g for g, cv in zip(ge, cov[src]["covered"]) if cv}
                    m = np.isin(ge, list(units))
                    txt, n_u = ci(served[refs[-1]], s, m)
                    line += f"   covered units vs {refs[-1]} {txt} ({n_u} units)"
            print(line, flush=True)
        # A column on its own against the hires model, both ranking the same
        # rows: the ones where the column has a value.
        for src, (cols, f) in sources.items():
            for j, cname in enumerate(cols):
                v = f[:, j]
                m = np.isfinite(v)
                if grouped_auc(np.zeros(int(m.sum())), y[m], ge[m])["groups"] == 0:
                    continue
                txt, n_u = ci(served["hires"], np.where(m, v, 0.0), m)
                print(f"  {cname + ' alone':<24} {grouped_auc(v[m], y[m], ge[m])['micro']:.3f}"
                      f"   hires on the same rows {grouped_auc(served['hires'][m], y[m], ge[m])['micro']:.3f}"
                      f"   alone - hires {txt} ({n_u} units)", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("need")
    n.add_argument("--buffer", required=True)
    n.add_argument("--out-dir", required=True)
    m = sub.add_parser("modis")
    m.add_argument("--buffer", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--cache", required=True)
    g = sub.add_parser("geo")
    g.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    g.add_argument("--buffer", required=True)
    g.add_argument("--files", required=True, help="folder the slot files were downloaded into")
    g.add_argument("--geoloc", action="append", required=True,
                   help="lat/lon file (MTG: one LATLON file; MSG: LAT then LON)")
    g.add_argument("--out", required=True)
    s = sub.add_parser("score")
    s.add_argument("--buffer", required=True)
    s.add_argument("--hires", required=True, help="drift_terrain cache; its 'under' set")
    s.add_argument("--lst", action="append", required=True)
    s.add_argument("--seed", type=int, action="append")
    s.add_argument("--boot", type=int, default=1500)
    args = ap.parse_args()
    if args.cmd == "score" and not args.seed:
        args.seed = [0]
    return {"need": cmd_need, "modis": cmd_modis, "geo": cmd_geo, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
