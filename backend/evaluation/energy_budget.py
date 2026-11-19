"""
Heat the ground hands to the air, cell by cell, and the wind that carries it.

A thermal is the end of an energy chain: sunlight lands on the ground, part is
reflected, part goes into evaporation and into warming the soil, and what is
left heats the air touching the ground (the sensible heat flux, H).  Warm air
is lighter, collects, and rises.  Nothing in the 22 columns measures that last
step per cell — CLAUDE.md names it as the untried lever — and the pieces that
were tried one at a time (sun angle, sunshine integrated over the day, land
cover at ~1 km) each scored nothing.  This puts them back together as one
budget, at ~30 m:

    sunlight on the slope   direct beam x cos(incidence), unless a ridge within
                            4 km is between the cell and the sun, plus diffuse
                            sky and light reflected from the ground around it
    absorbed                x (1 - albedo)                      land cover
    minus longwave loss     constant LW_NET                     within an hour
                                                                 and area it
                                                                 barely varies
    minus ground storage    x (1 - g)                           land cover
    minus evaporation       x (1 - evaporative fraction)        land cover
    = H                     W/m^2 handed to the air

computed now and every half hour for the previous three hours, because ground
and air store heat: a slope that took the sun all morning can still be the warm
one after it has gone into shade.  The land-cover constants are typical midday
values from the surface-energy-balance literature, not measurements of these
sites; they are stated once in _SURFACE so they can be argued with.

Part two is the wind that carries the heat.  The buffer holds only 10 m wind,
weaker than and often turned from the wind at thermal height, which is the
likeliest reason drift correction failed (CLAUDE.md).  `aloft` fetches 925,
850 and 700 hPa wind for every row from Open-Meteo's historical forecast API,
plus direct and diffuse radiation, which the budget also needs.

    python -m evaluation.energy_budget aloft   --buffer X.npz --out aloft.npz --http-cache DIR
    python -m evaluation.energy_budget compute --buffer X.npz --aloft aloft.npz \\
        --out seb.npz --block-cache DIR
    python -m evaluation.energy_budget score   --buffer X.npz --aloft aloft.npz \\
        --seb seb.npz --hires drift.npz --seed 0 --seed 1 --seed 2
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

_COL_LAT, _COL_LON, _COL_ELEV = 0, 1, 2
_COL_U10, _COL_V10, _COL_AGL = 7, 8, 21

# Standard-atmosphere heights (m AMSL).  The real geopotential height moves a
# few hundred metres with the weather; for a layer mean over a climb that is
# second order, and leaving it out keeps a request under Open-Meteo's
# ten-variable weight.
LEVELS = (("925hPa", 760.0), ("850hPa", 1460.0), ("700hPa", 3010.0))
HOURLY = (["direct_radiation", "diffuse_radiation"]
          + [f"wind_{k}_{lv}" for lv, _ in LEVELS for k in ("speed", "direction")])
STEPS_H = np.arange(7) * 0.5          # now, then every half hour back to 3 h
_SNAP = 0.25                          # weather cell, degrees
_MAX_WINDOW_DAYS = 14                 # past this a request weighs more than one call


def _fingerprint(X: np.ndarray) -> str:
    return hashlib.sha1(np.ascontiguousarray(X[:, [0, 1, 7, 8, 21]]).tobytes()).hexdigest()


def _load_buffer(path: str):
    with np.load(path, allow_pickle=False) as d:
        return d["X"], d["y"].astype(int)


def _times(X: np.ndarray):
    from evaluation.evaluate import row_datetimes
    return row_datetimes(X)


def _snap(v: float) -> float:
    return round(round(v / _SNAP) * _SNAP, 4)


def _windows(dates) -> list[tuple]:
    """Fewest date windows of at most _MAX_WINDOW_DAYS covering every date."""
    out, start, prev = [], None, None
    for d in sorted(dates):
        if start is None:
            start = prev = d
        elif (d - start).days >= _MAX_WINDOW_DAYS:
            out.append((start, prev))
            start = prev = d
        else:
            prev = d
    if start is not None:
        out.append((start, prev))
    return out


# --- part two: wind aloft and radiation -------------------------------------

async def _fetch_all(jobs, cache_dir: Path, concurrency: int = 4, pace_s: float = 0.15):
    """jobs: {key: url}.  Returns {key: parsed JSON or None}, caching on disk."""
    import httpx
    cache_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    out, done, failed = {}, 0, 0
    t0 = time.time()

    async with httpx.AsyncClient(timeout=60) as client:
        async def one(key, url):
            nonlocal done, failed
            f = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
            if f.exists():
                out[key] = json.loads(f.read_text())
                return
            async with sem:
                for wait in (5, 15, 30, 60, None):
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            f.write_text(r.text)
                            out[key] = r.json()
                            break
                        if r.status_code not in (429, 500, 502, 503, 504) or wait is None:
                            print(f"  {r.status_code} for {key}: {r.text[:200]}", flush=True)
                            out[key] = None
                            failed += 1
                            break
                    except httpx.TransportError:
                        if wait is None:
                            out[key] = None
                            failed += 1
                            break
                    await asyncio.sleep(wait)
                await asyncio.sleep(pace_s)
            done += 1
            if done % 200 == 0:
                print(f"  {done} fetched  {failed} failed  {time.time() - t0:.0f}s", flush=True)

        await asyncio.gather(*(one(k, u) for k, u in jobs.items()))
    return out


def _uv(speed, direction_deg):
    """Meteorological (from) direction to the vector the air moves along."""
    rad = np.radians(direction_deg)
    return -speed * np.sin(rad), -speed * np.cos(rad)


def cmd_aloft(args) -> int:
    from config import OPEN_METEO_HIST_BASE
    X, _ = _load_buffer(args.buffer)
    ts = _times(X)
    cells = [(_snap(a), _snap(b)) for a, b in X[:, :2].astype(float)]

    need = defaultdict(set)
    for c, t in zip(cells, ts):
        # the three hours before, and the hour after for interpolation
        need[c].update({(t - timedelta(hours=3.5)).date(), (t + timedelta(hours=1)).date()})
    jobs = {}
    for (la, lo), dates in need.items():
        for s, e in _windows(dates):
            jobs[(la, lo, s.isoformat(), e.isoformat())] = (
                f"{OPEN_METEO_HIST_BASE}?latitude={la}&longitude={lo}"
                f"&start_date={s.isoformat()}&end_date={e.isoformat()}"
                f"&hourly={','.join(HOURLY)}&wind_speed_unit=ms&timeformat=unixtime")
    print(f"{len(X)} rows, {len(need)} weather cells, {len(jobs)} requests", flush=True)
    got = asyncio.run(_fetch_all(jobs, Path(args.http_cache)))

    # Merge windows per cell into one hourly series, winds as vectors.
    series = defaultdict(dict)
    for (la, lo, _, _), js in got.items():
        if not js:
            continue
        h = js["hourly"]
        for i, t in enumerate(h["time"]):
            rec = [h["direct_radiation"][i], h["diffuse_radiation"][i]]
            for lv, _ in LEVELS:
                s, d = h[f"wind_speed_{lv}"][i], h[f"wind_direction_{lv}"][i]
                rec += [np.nan, np.nan] if s is None or d is None else list(_uv(s, d))
            series[(la, lo)][t] = [np.nan if v is None else float(v) for v in rec]

    n = len(X)
    direct = np.full((n, len(STEPS_H)), np.nan)
    diffuse = np.full((n, len(STEPS_H)), np.nan)
    wind = np.full((n, len(LEVELS), 2), np.nan)
    arrays = {}
    for c, s in series.items():
        t = np.array(sorted(s))
        arrays[c] = (t, np.array([s[k] for k in t]))
    for i, (c, ts_i) in enumerate(zip(cells, ts)):
        if c not in arrays:
            continue
        t, v = arrays[c]
        now = ts_i.timestamp()
        for j in range(len(LEVELS)):
            wind[i, j, 0] = np.interp(now, t, v[:, 2 + 2 * j], left=np.nan, right=np.nan)
            wind[i, j, 1] = np.interp(now, t, v[:, 3 + 2 * j], left=np.nan, right=np.nan)
        # Open-Meteo radiation is the mean over the hour ENDING at its stamp,
        # so the value for an instant sits half an hour after that instant.
        at = now - STEPS_H * 3600 + 1800
        direct[i] = np.interp(at, t, v[:, 0], left=np.nan, right=np.nan)
        diffuse[i] = np.interp(at, t, v[:, 1], left=np.nan, right=np.nan)

    np.savez(args.out, fingerprint=np.array(_fingerprint(X)), wind=wind,
             direct=direct, diffuse=diffuse, levels=np.array([lv for lv, _ in LEVELS]))
    print(f"wrote {args.out}: wind missing {np.isnan(wind[:, 1, 0]).mean():.2%}, "
          f"radiation missing {np.isnan(direct[:, 0]).mean():.2%}")
    return 0


def load_aloft(path: str, X: np.ndarray) -> dict:
    with np.load(path, allow_pickle=False) as a:
        if str(a["fingerprint"]) != _fingerprint(X):
            raise SystemExit(f"{path} was fetched for a different buffer")
        return {k: a[k] for k in ("wind", "direct", "diffuse")}


def layer_wind(X: np.ndarray, wind: np.ndarray, n_sample: int = 24) -> np.ndarray:
    """
    Mean wind (u, v in m/s) over the column from the ground to the glider.

    A bubble rising from the ground passes through every layer on its way up,
    so its drift is the integral of the wind over height, not the wind at
    either end.  The profile runs through the 10 m wind from the buffer
    (km/h) and each pressure level above the ground, held constant above the
    top level.  Rows with no fetched profile fall back to the 10 m wind.
    """
    z0 = X[:, _COL_ELEV].astype(float) + 10.0
    top = z0 + np.maximum(X[:, _COL_AGL].astype(float) - 10.0, 0.0)
    u10, v10 = X[:, _COL_U10] / 3.6, X[:, _COL_V10] / 3.6
    out = np.column_stack([u10, v10]).astype(float)
    heights = np.array([z for _, z in LEVELS])
    for i in range(len(X)):
        if np.isnan(wind[i, :, 0]).any():
            continue
        above = heights > z0[i]
        zs = np.concatenate([[z0[i]], heights[above]])
        us = np.concatenate([[u10[i]], wind[i, above, 0]])
        vs = np.concatenate([[v10[i]], wind[i, above, 1]])
        z = np.linspace(z0[i], max(top[i], z0[i] + 1.0), n_sample)
        out[i] = np.interp(z, zs, us).mean(), np.interp(z, zs, vs).mean()
    return out


# --- part one: the surface energy budget -------------------------------------

LW_NET = -70.0                 # W/m^2, clear-sky daytime net longwave
STEP_M = 33.0                  # ~ one GLO-30 pixel, for the 3x3 slope patch
SHADOW_DIST_M = (66.0, 132.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0)
RINGS_M = (150.0, 400.0, 1000.0)
N_RING = 12
DECAY_H = 1.5                  # how fast stored heat stops mattering
_WC_TARGET_DEG = 0.0004        # WorldCover overview at ~40 m: one class per cell

# WorldCover class -> (albedo, ground storage share g, evaporative fraction EF)
# of net radiation.  Typical midday values from surface-energy-balance studies,
# not measurements of these sites.  What matters for ranking within an hour is
# their order: bare ground and towns hand most of their energy to the air,
# forest and crops spend much of it evaporating water, water stores it.
_SURFACE = {
    10:  (0.12, 0.05, 0.55),   # tree cover
    20:  (0.18, 0.10, 0.35),   # shrubland
    30:  (0.20, 0.10, 0.45),   # grassland
    40:  (0.20, 0.10, 0.45),   # cropland
    50:  (0.15, 0.30, 0.15),   # built-up
    60:  (0.25, 0.25, 0.15),   # bare / sparse vegetation
    70:  (0.70, 0.20, 0.60),   # snow and ice
    80:  (0.07, 0.60, 0.75),   # permanent water
    90:  (0.14, 0.10, 0.70),   # herbaceous wetland
    95:  (0.12, 0.05, 0.70),   # mangroves
    100: (0.18, 0.10, 0.40),   # moss and lichen
}
_DEFAULT = _SURFACE[30]        # class 0 (no data) inside a published tile
_SEA = _SURFACE[80]            # no tile is published over open sea
_NO_TILE, _FAILED = -1, -2

SEB_COLUMNS = ("sw_now", "shadow_now", "shade_3h", "h_now", "h_3h", "lc_factor",
               "h_ring400", "h_ring1000", "h_excess_400", "h_excess_1000")


def surface(codes: np.ndarray):
    """(albedo, g, EF) arrays for WorldCover codes; NaN where the read failed."""
    lut = np.array([_SURFACE.get(c, _DEFAULT) for c in range(256)], dtype=float)
    codes = np.asarray(codes)
    out = lut[np.clip(codes, 0, 255)]
    out = np.where((codes == _NO_TILE)[..., None], np.array(_SEA), out)
    out = np.where((codes == _FAILED)[..., None], np.nan, out)
    return out[..., 0], out[..., 1], out[..., 2]


def in_shadow(ray: np.ndarray, zc: np.ndarray, sun_elev_deg: np.ndarray) -> np.ndarray:
    """
    True where terrain toward the sun stands above the sun's line of sight.

    ray is (..., len(SHADOW_DIST_M)) elevations sampled toward the sun's
    azimuth; zc is the cell's own elevation.  A missing sample is treated as
    no obstacle rather than as a wall.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        rise = np.where(np.isfinite(ray), (ray - zc[..., None]) / np.array(SHADOW_DIST_M), -np.inf)
    horizon = rise.max(axis=-1)
    up = sun_elev_deg > 1.0
    return up & (horizon > np.tan(np.radians(np.clip(sun_elev_deg, 0.1, 89.9))))


def heat_flux(sw, albedo, g, ef):
    """Sensible heat (W/m^2) the surface hands to the air from absorbed sunlight."""
    return np.maximum((1 - albedo) * sw + LW_NET, 0.0) * (1 - g) * (1 - ef)


def _offset(lat, lon, north_m, east_m):
    mlon = 111_320.0 * np.maximum(np.cos(np.radians(lat)), 1e-3)
    return lat + north_m / 111_320.0, lon + east_m / mlon


def _keys(lat, lon) -> list[tuple[float, float]]:
    return list(zip(np.round(lat, 6).ravel().tolist(), np.round(lon, 6).ravel().tolist()))


class _WorldCover:
    """Class codes at points, from one WorldCover overview level, blocks cached."""

    def __init__(self, client):
        from collections import OrderedDict
        self.client = client
        self.tiles: dict[str, tuple | None] = {}
        self.blocks: "OrderedDict" = OrderedDict()

    async def _tile(self, name):
        from data import cog, landcover_client as lc
        if name not in self.tiles:
            r = await self.client.get(lc.tile_url(name),
                                      headers={"Range": f"bytes=0-{lc._HEADER_BYTES - 1}"})
            if r.status_code in (403, 404):
                self.tiles[name] = None
            else:
                r.raise_for_status()
                levels = cog.parse_ifds(r.content)
                level = lc._pick_level(levels, _WC_TARGET_DEG)
                self.tiles[name] = (levels, level, lc._geo(levels, level))
        return self.tiles[name]

    async def _block(self, name, lv, idx):
        import zlib
        from data import landcover_client as lc
        key = (name, idx)
        if key in self.blocks:
            self.blocks.move_to_end(key)
            return self.blocks[key]
        start, length = lv["offsets"][idx], lv["counts"][idx]
        if length == 0:
            arr = None
        else:
            r = await self.client.get(lc.tile_url(name),
                                      headers={"Range": f"bytes={start}-{start + length - 1}"})
            r.raise_for_status()
            arr = np.frombuffer(zlib.decompress(r.content), dtype=np.uint8).reshape(
                lv["tile_h"][0], lv["tile_w"][0])
        self.blocks[key] = arr
        while len(self.blocks) > 256:
            self.blocks.popitem(last=False)
        return arr

    async def classes(self, points) -> dict:
        from data import landcover_client as lc
        by_tile = defaultdict(list)
        for p in points:
            by_tile[lc.tile_name(*p)].append(p)
        out = {}
        for name, pts in by_tile.items():
            try:
                t = await self._tile(name)
            except Exception:
                out.update({p: _FAILED for p in pts})
                continue
            if t is None:
                out.update({p: _NO_TILE for p in pts})
                continue
            levels, level, (lon0, lat0, dpp) = t
            lv = levels[level]
            tw, th = lv["tile_w"][0], lv["tile_h"][0]
            across = -(-lv["width"][0] // tw)
            by_block = defaultdict(list)
            for p in pts:
                px = min(max(int((p[1] - lon0) / dpp), 0), lv["width"][0] - 1)
                py = min(max(int((lat0 - p[0]) / dpp), 0), lv["height"][0] - 1)
                by_block[(py // th) * across + px // tw].append((p, py % th, px % tw))
            for idx, items in by_block.items():
                try:
                    block = await self._block(name, lv, idx)
                except Exception:
                    out.update({p: _FAILED for p, _, _ in items})
                    continue
                for p, r_, c_ in items:
                    out[p] = 0 if block is None else int(block[r_, c_])
        return out


async def _seb(X, times, aloft, block_cache: str, batch: int = 300) -> np.ndarray:
    import httpx
    from data import dem_client
    from evaluation.drift_terrain import _DiskCachedClient
    from pipeline.solar import cos_incidence, sun_position

    root = Path(block_cache)
    dem_client._BLOCK_CACHE_SIZE = max(dem_client._BLOCK_CACHE_SIZE, 96)
    dem_wrapped = _DiskCachedClient(dem_client._get_client(), root)
    dem_client._get_client = lambda: dem_wrapped
    wc = _WorldCover(_DiskCachedClient(
        httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=8)), root / "worldcover"))

    n = len(X)
    hour0 = np.array([t.hour + t.minute / 60 + t.second / 3600 for t in times])
    doy0 = np.array([t.timetuple().tm_yday for t in times], dtype=float)
    hour = hour0[:, None] - STEPS_H[None, :]
    doy = doy0[:, None] - (hour < 0)
    hour = hour % 24
    w_t = np.exp(-STEPS_H / DECAY_H)

    pn = np.repeat([-1.0, 0.0, 1.0], 3) * STEP_M          # rows run south -> north
    pe = np.tile([-1.0, 0.0, 1.0], 3) * STEP_M
    D = np.array(SHADOW_DIST_M)
    ang = np.linspace(0, 2 * np.pi, N_RING, endpoint=False)
    rn = np.concatenate([r * np.cos(ang) for r in RINGS_M])
    re = np.concatenate([r * np.sin(ang) for r in RINGS_M])

    out = np.full((n, len(SEB_COLUMNS)), np.nan)
    order = np.lexsort((np.floor(X[:, _COL_LON] * 4), np.floor(X[:, _COL_LAT] * 4)))
    t0 = time.time()
    try:
        for s in range(0, n, batch):
            idx = order[s:s + batch]
            b = len(idx)
            lat = X[idx, _COL_LAT].astype(float)
            lon = X[idx, _COL_LON].astype(float)
            el, az = sun_position(lat[:, None], lon[:, None], doy[idx], hour[idx])   # (b, 7)

            plat, plon = _offset(lat[:, None], lon[:, None], pn[None], pe[None])        # (b, 9)
            azr = np.radians(az)[:, :, None]
            slat, slon = _offset(lat[:, None, None], lon[:, None, None],
                                 np.cos(azr) * D, np.sin(azr) * D)                       # (b, 7, 7)
            dem_keys = _keys(plat, plon) + _keys(slat, slon)
            got = await dem_client.fetch_elevations(list(dict.fromkeys(dem_keys)))
            z = np.array([got.get(k) for k in dem_keys], dtype=float)
            patch = z[:b * 9].reshape(b, 3, 3)
            ray = z[b * 9:].reshape(b, len(STEPS_H), len(D))

            clat, clon = _offset(lat[:, None], lon[:, None],
                                 np.concatenate([[0.0], rn])[None], np.concatenate([[0.0], re])[None])
            wc_keys = _keys(clat, clon)
            cls = await wc.classes(list(dict.fromkeys(wc_keys)))
            codes = np.array([cls[k] for k in wc_keys]).reshape(b, 1 + len(rn))
            alb, g, ef = surface(codes)                                                  # (b, 37)

            # 3x3 Horn gradient, same convention as hires_terrain.features_from
            dzdx = ((patch[:, 0, 2] + 2 * patch[:, 1, 2] + patch[:, 2, 2])
                    - (patch[:, 0, 0] + 2 * patch[:, 1, 0] + patch[:, 2, 0])) / (8 * STEP_M)
            dzdy = ((patch[:, 2, 0] + 2 * patch[:, 2, 1] + patch[:, 2, 2])
                    - (patch[:, 0, 0] + 2 * patch[:, 0, 1] + patch[:, 0, 2])) / (8 * STEP_M)
            slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
            aspect = np.degrees(np.arctan2(-dzdx, -dzdy)) % 360.0
            zc = patch[:, 1, 1]

            sun_up = el > 1.0
            shadow = in_shadow(ray, np.repeat(zc[:, None], len(STEPS_H), axis=1), el)

            direct = aloft["direct"][idx]
            diffuse = aloft["diffuse"][idx]
            ghi = direct + diffuse
            dni = np.where(sun_up, np.minimum(direct / np.maximum(np.sin(np.radians(el)), 0.087), 1100.0), 0.0)
            cosi = cos_incidence(slope[:, None], aspect[:, None], el, az)
            svf = ((1 + np.cos(np.radians(slope))) / 2)[:, None]
            alb_around = np.nanmean(alb[:, 1 + N_RING:1 + 2 * N_RING], axis=1)[:, None]
            sw = (~shadow) * dni * cosi + diffuse * svf + alb_around * ghi * (1 - svf)

            a0, g0, e0 = alb[:, :1], g[:, :1], ef[:, :1]
            h = heat_flux(sw, a0, g0, e0)                                                # (b, 7)
            h3 = (h * w_t).sum(1) / w_t.sum()

            # The same budget on flat ground at every ring point: the heat the
            # surroundings supply.  A cell hotter than its surroundings is where
            # warm air converges and leaves; the difference is what ranks.
            h_ring = heat_flux(ghi[:, None, :], alb[:, 1:, None], g[:, 1:, None], ef[:, 1:, None])  # (b, 36, 7)
            h_ring = (h_ring * w_t).sum(2) / w_t.sum()
            r400 = np.nanmean(h_ring[:, N_RING:2 * N_RING], axis=1)
            r1000 = np.nanmean(h_ring[:, 2 * N_RING:], axis=1)
            up = sun_up.sum(1)
            out[idx] = np.column_stack([
                sw[:, 0], shadow[:, 0].astype(float),
                np.where(up > 0, (shadow & sun_up).sum(1) / np.maximum(up, 1), np.nan),
                h[:, 0], h3, ((1 - a0) * (1 - g0) * (1 - e0))[:, 0],
                r400, r1000, h3 - r400, h3 - r1000,
            ])
            if (s // batch) % 20 == 0:
                u = dem_client.usage()
                print(f"{s + b}/{n}  {time.time() - t0:.0f}s  dem blocks {u['blocks']} "
                      f"errors {u['errors']}  worldcover blocks {len(wc.blocks)}", flush=True)
    finally:
        await dem_client.aclose()
        await wc.client.aclose()
    return out


def cmd_compute(args) -> int:
    X, _ = _load_buffer(args.buffer)
    aloft = load_aloft(args.aloft, X)
    feats = asyncio.run(_seb(X, _times(X), aloft, args.block_cache))
    np.savez(args.out, fingerprint=np.array(_fingerprint(X)), columns=np.array(SEB_COLUMNS), feats=feats)
    for j, c in enumerate(SEB_COLUMNS):
        v = feats[:, j]
        print(f"  {c:<14} missing {np.isnan(v).mean():6.2%}  median {np.nanmedian(v):9.2f}  "
              f"p90 {np.nanpercentile(v, 90):9.2f}")
    print(f"wrote {args.out}")
    return 0


# --- scoring ----------------------------------------------------------------

def aloft_columns(X: np.ndarray, aloft: dict, hires: np.ndarray) -> np.ndarray:
    """Layer-mean and 850 hPa wind, and which way each 30 m slope faces the layer wind.

    windward is +1 for a slope facing straight into the wind and -1 for the lee
    side, weighted by steepness — the 10 m version of this scored nothing.
    """
    from evaluation.hires_terrain import COLUMNS as HC
    lw = layer_wind(X, aloft["wind"])
    speed = np.hypot(lw[:, 0], lw[:, 1])
    wind_from = np.arctan2(-lw[:, 0], -lw[:, 1])
    north, east = hires[:, HC.index("northness_hr")], hires[:, HC.index("eastness_hr")]
    windward = north * np.cos(wind_from) + east * np.sin(wind_from)
    return np.column_stack([lw, aloft["wind"][:, 1, :], windward, windward * speed])


def cmd_score(args) -> int:
    from data.meteo_client import is_fallback_row
    from evaluation.evaluate import _terrain_multiplier, oof_scores
    from evaluation.holdout import decision_ids, group_ids
    from evaluation.metrics import grouped_auc, paired_delta_ci
    from models.thermal_model import _MIN_SAMPLE_AGL

    X, y = _load_buffer(args.buffer)
    aloft = load_aloft(args.aloft, X)
    with np.load(args.seb, allow_pickle=False) as c:
        if str(c["fingerprint"]) != _fingerprint(X):
            raise SystemExit(f"{args.seb} was computed for a different buffer")
        seb = c["feats"]
    with np.load(args.hires, allow_pickle=False) as c:
        hires = c["f_under"]
        if np.isnan(hires[:, 0]).mean() > 0.02:
            raise SystemExit(f"{args.hires}: more than 2% of rows have no terrain")
    if np.isnan(seb[:, SEB_COLUMNS.index("h_3h")]).mean() > 0.02:
        raise SystemExit(f"{args.seb}: more than 2% of rows have no budget")
    wind = aloft_columns(X, aloft, hires)

    keep = ~is_fallback_row(X) & (X[:, _COL_AGL] >= _MIN_SAMPLE_AGL)
    X, y, seb, hires, wind = X[keep], y[keep], seb[keep], hires[keep], wind[keep]
    gfold, ge = group_ids(X), decision_ids(X)
    tm = _terrain_multiplier(X)
    print(f"rows {len(y)}, positives {int(y.sum())}, mixed-label units "
          f"{grouped_auc(np.zeros(len(y)), y, ge)['groups']}")

    print("\nsingle columns, no model (micro within-hour AUC; below 0.5 means the "
          "column ranks the wrong way round)")
    singles = {c: seb[:, j] for j, c in enumerate(SEB_COLUMNS)}
    singles.update({"windward_layer": wind[:, 4], "windward_x_speed": wind[:, 5]})
    for name, v in singles.items():
        m = np.isfinite(v)
        print(f"  {name:<17} {grouped_auc(v[m], y[m], ge[m])['micro']:.3f}")

    blocks = {"hires": hires, "seb": seb, "aloft": wind}
    variants = {"base": (), "hires": ("hires",), "hires+seb": ("hires", "seb"),
                "hires+aloft": ("hires", "aloft"), "hires+seb+aloft": ("hires", "seb", "aloft")}
    Xi = np.column_stack([X, np.arange(len(X), dtype=float)])

    def tf(parts):
        return lambda Z: np.column_stack([Z[:, :-1]] + [blocks[p][Z[:, -1].astype(int)] for p in parts])

    for seed in args.seed:
        t0 = time.time()
        served = {name: oof_scores(Xi, y, gfold, tf(parts), k=5, seed=seed) * tm
                  for name, parts in variants.items()}
        print(f"\nseed {seed}  ({time.time() - t0:.0f}s)")
        for name, s in served.items():
            line = f"  {name:<16} served {grouped_auc(s, y, ge)['micro']:.3f}"
            if name != "hires":
                d = paired_delta_ci(served["hires"], s, y, ge, n_boot=args.boot, seed=seed)
                line += f"   vs hires {d['point']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            print(line, flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("aloft")
    a.add_argument("--buffer", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--http-cache", required=True)
    c = sub.add_parser("compute")
    c.add_argument("--buffer", required=True)
    c.add_argument("--aloft", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--block-cache", required=True)
    s = sub.add_parser("score")
    s.add_argument("--buffer", required=True)
    s.add_argument("--aloft", required=True)
    s.add_argument("--seb", required=True)
    s.add_argument("--hires", required=True, help="drift_terrain cache; its 'under' set")
    s.add_argument("--seed", type=int, action="append")
    s.add_argument("--boot", type=int, default=1000)
    args = ap.parse_args()
    if args.cmd == "score" and not args.seed:
        args.seed = [0]
    return {"aloft": cmd_aloft, "compute": cmd_compute, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
