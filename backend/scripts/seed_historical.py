"""
seed_historical.py — bulk-seed the training buffer with past IGC flights.

Sources (pick one):
  glidernet — live.glidernet.org REST API, no auth, last ~24 h of OGN tracks (default)
  ogn-log   — your local DB built by the running backend (grows every day)
  weglide   — requires a personal API token (--weglide-token)
  igc-dir   — drop any .igc files in a folder and use --igc-dir

Usage (run from the backend/ directory):
    uv run python scripts/seed_historical.py --source glidernet
    uv run python scripts/seed_historical.py --source ogn-log --days 30
    uv run python scripts/seed_historical.py --source weglide --weglide-user you@email.com --weglide-pass yourpass
    uv run python scripts/seed_historical.py --igc-dir C:\\path\\to\\igc\\files

Getting IGC files manually (always works):
    1. Go to https://www.weglide.org  — search your region, click a flight, Download IGC
    2. Or https://www.xcontest.org    — World / UK flights, Export IGC
    3. Drop all .igc files into one folder and run with --igc-dir
"""

import argparse
import asyncio
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np

# ---------------------------------------------------------------------------
# Allow running from the backend/ directory without installing the package
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (
    LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
    BUFFER_PATH, MODEL_PATH, GRID_RES, TERRAIN_RES,
)
from pipeline.feature_engineering import build_feature_matrix

log = logging.getLogger("seed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ARCHIVE_URL      = "https://archive-api.open-meteo.com/v1/archive"
_SRTM_URL         = "https://api.opentopodata.org/v1/srtm30m"
_SKYLINES_URL     = "https://skylines.aero/api"
_WEGLIDE_URL      = "https://api.weglide.org/v1"
_GLIDERNET_REC    = "http://live.glidernet.org/rec.php"

# Circling detection — matches ogn_client.py threshold
_CIRCLING_DEG_S = 8.0
# Minimum climb rate to label a thermal (matches thermal_model.py)
_THERMAL_VARIO  = 1.5
# Sample every N seconds along a circling segment to avoid over-representing long thermals
_SAMPLE_INTERVAL_S = 30
# Max elevation API batch size (opentopodata limit)
_ELEV_BATCH = 100


# ---------------------------------------------------------------------------
# IGC parser
# ---------------------------------------------------------------------------
def parse_igc(text: str) -> list[dict]:
    """
    Parse an IGC file and return a list of fix dicts:
      {time: datetime, lat: float, lon: float, alt: float,
       vario: float, turn_rate: float, circling: bool}
    """
    fixes = []
    date = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("HFDTE") or line.startswith("HPDTE"):
            # HFDTEDDMMYY or HFDTE DATE:DDMMYY
            digits = "".join(c for c in line[5:] if c.isdigit())
            if len(digits) >= 6:
                dd, mm, yy = int(digits[0:2]), int(digits[2:4]), int(digits[4:6])
                year = 2000 + yy if yy < 70 else 1900 + yy
                date = datetime(year, mm, dd, tzinfo=timezone.utc)

        if line.startswith("B") and len(line) >= 35 and date:
            try:
                hh, mi, ss = int(line[1:3]), int(line[3:5]), int(line[5:7])
                lat_d = int(line[7:9])
                lat_m = float(line[9:14]) / 1000
                lat   = lat_d + lat_m / 60
                if line[14] == "S":
                    lat = -lat
                lon_d = int(line[15:18])
                lon_m = float(line[18:23]) / 1000
                lon   = lon_d + lon_m / 60
                if line[23] == "W":
                    lon = -lon
                alt = float(line[30:35])   # GPS altitude (metres)
                fixes.append({
                    "time": date.replace(hour=hh, minute=mi, second=ss),
                    "lat": lat, "lon": lon, "alt": alt,
                })
            except (ValueError, IndexError):
                continue

    if len(fixes) < 2:
        return []

    # Compute vario and turn_rate between consecutive fixes
    result = []
    for i, f in enumerate(fixes):
        if i == 0:
            f["vario"] = 0.0
            f["turn_rate"] = 0.0
            f["circling"] = False
            result.append(f)
            continue

        prev = fixes[i - 1]
        dt   = (f["time"] - prev["time"]).total_seconds()
        if dt <= 0:
            continue

        f["vario"] = (f["alt"] - prev["alt"]) / dt

        # Bearing from prev → current fix
        dlat = f["lat"] - prev["lat"]
        dlon = (f["lon"] - prev["lon"]) * math.cos(math.radians(f["lat"]))
        bearing = math.degrees(math.atan2(dlon, dlat)) % 360

        if i >= 2:
            p2 = fixes[i - 2]
            dt2 = (prev["time"] - p2["time"]).total_seconds()
            if dt2 > 0:
                dlat2 = prev["lat"] - p2["lat"]
                dlon2 = (prev["lon"] - p2["lon"]) * math.cos(math.radians(prev["lat"]))
                prev_bearing = math.degrees(math.atan2(dlon2, dlat2)) % 360
                raw_turn = bearing - prev_bearing
                # Normalise to [-180, 180]
                raw_turn = (raw_turn + 180) % 360 - 180
                f["turn_rate"] = raw_turn / dt
            else:
                f["turn_rate"] = 0.0
        else:
            f["turn_rate"] = 0.0

        f["circling"] = abs(f["turn_rate"]) > _CIRCLING_DEG_S
        result.append(f)

    return result


def extract_samples(fixes: list[dict], lat_min: float, lat_max: float,
                    lon_min: float, lon_max: float) -> list[dict]:
    """
    Return one sample per _SAMPLE_INTERVAL_S seconds from circling segments
    that are inside the configured bounding box.
    """
    samples = []
    last_sampled = None

    for f in fixes:
        if not (lat_min <= f["lat"] <= lat_max and lon_min <= f["lon"] <= lon_max):
            continue
        if last_sampled and (f["time"] - last_sampled).total_seconds() < _SAMPLE_INTERVAL_S:
            continue
        samples.append(f)
        last_sampled = f["time"]

    return samples


# ---------------------------------------------------------------------------
# Historical weather (Open-Meteo archive — same variables as meteo_client.py)
# ---------------------------------------------------------------------------
async def fetch_historical_meteo(client: httpx.AsyncClient,
                                  lat: float, lon: float,
                                  dt: datetime) -> dict | None:
    date_str = dt.strftime("%Y-%m-%d")
    try:
        r = await client.get(_ARCHIVE_URL, params={
            "latitude": lat, "longitude": lon,
            "start_date": date_str, "end_date": date_str,
            "hourly": ",".join([
                "temperature_2m", "relativehumidity_2m",
                "windspeed_10m", "winddirection_10m",
                "cape", "shortwave_radiation",
                "convective_inhibition",
                "temperature_850hPa", "temperature_500hPa",
            ]),
            "timezone": "UTC",
        }, timeout=20)
        r.raise_for_status()
        data   = r.json()
        hourly = data["hourly"]
        idx    = min(dt.hour, len(hourly["time"]) - 1)
        t2m  = hourly["temperature_2m"][idx]
        t850 = hourly["temperature_850hPa"][idx]
        t500 = hourly["temperature_500hPa"][idx]
        return {
            "temp_2m":    t2m,
            "humidity":   (hourly["relativehumidity_2m"][idx] or 60) / 100,
            "wind_speed": hourly["windspeed_10m"][idx] or 0,
            "wind_dir":   hourly["winddirection_10m"][idx] or 270,
            "cape":       hourly["cape"][idx] or 0,
            "cin":        hourly["convective_inhibition"][idx] or 0,
            "solar_ghi":  hourly["shortwave_radiation"][idx] or 0,
            "temp_850":   t850,
            "temp_500":   t500,
            "lapse_rate": (t850 - t500) / 3.5 if (t850 and t500) else 5.0,
            "cape_base":  max(0, int(((t2m - 8) / max(1, (t2m - t850) / 1.5)) * 1000)) if t850 else 1200,
        }
    except Exception as exc:
        log.debug(f"[meteo] {date_str} {lat:.2f}/{lon:.2f} failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Terrain: batch elevation fetch then upscale to GRID_RES (mirrors terrain_client)
# ---------------------------------------------------------------------------
async def fetch_elevation_grid_historical(client: httpx.AsyncClient,
                                           lat: float, lon: float,
                                           radius: float = 0.05) -> np.ndarray | None:
    lats = np.arange(lat - radius, lat + radius, TERRAIN_RES)
    lons = np.arange(lon - radius, lon + radius, TERRAIN_RES)
    lat_g, lon_g = np.meshgrid(lats, lons, indexing="ij")
    points = list(zip(lat_g.ravel().tolist(), lon_g.ravel().tolist()))

    elevs = []
    for i in range(0, len(points), _ELEV_BATCH):
        batch  = points[i:i + _ELEV_BATCH]
        loc_str = "|".join(f"{la},{lo}" for la, lo in batch)
        try:
            r = await client.get(_SRTM_URL, params={"locations": loc_str}, timeout=30)
            r.raise_for_status()
            elevs.extend(res.get("elevation") or 0 for res in r.json()["results"])
            await asyncio.sleep(1.1)   # 1 req/s hard limit
        except Exception as exc:
            log.debug(f"[terrain] batch failed: {exc}")
            elevs.extend([0] * len(batch))

    coarse = np.array(elevs, dtype=float).reshape(lat_g.shape)

    # Upsample to GRID_RES with bilinear interpolation
    fine_lats = np.arange(lat - radius, lat + radius, GRID_RES)
    fine_lons = np.arange(lon - radius, lon + radius, GRID_RES)
    fine_lat_g, fine_lon_g = np.meshgrid(fine_lats, fine_lons, indexing="ij")

    r_idx = np.clip(
        (fine_lat_g - lats[0]) / TERRAIN_RES, 0, coarse.shape[0] - 1
    )
    c_idx = np.clip(
        (fine_lon_g - lons[0]) / TERRAIN_RES, 0, coarse.shape[1] - 1
    )
    r0, c0 = r_idx.astype(int), c_idx.astype(int)
    r1 = np.clip(r0 + 1, 0, coarse.shape[0] - 1)
    c1 = np.clip(c0 + 1, 0, coarse.shape[1] - 1)
    dr, dc = r_idx - r0, c_idx - c0
    fine = (
        coarse[r0, c0] * (1 - dr) * (1 - dc)
        + coarse[r1, c0] * dr * (1 - dc)
        + coarse[r0, c1] * (1 - dr) * dc
        + coarse[r1, c1] * dr * dc
    )
    return fine


# ---------------------------------------------------------------------------
# Skylines downloader — open API, no token required
# ---------------------------------------------------------------------------
async def fetch_skylines_igc_files(days_back: int,
                                    lat_min: float, lat_max: float,
                                    lon_min: float, lon_max: float) -> list[str]:
    """
    Download IGC text for public Skylines flights within the bounding box.
    Skylines is fully open-source and requires no authentication.
    """
    end   = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)

    igc_texts = []
    page      = 1
    per_page  = 50

    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                  headers={"Accept": "application/json"}) as client:
        while True:
            try:
                r = await client.get(f"{_SKYLINES_URL}/flights", params={
                    "date_from": start.isoformat(),
                    "date_to":   end.isoformat(),
                    "bbox":      f"{lon_min},{lat_min},{lon_max},{lat_max}",
                    "page":      page,
                    "per_page":  per_page,
                })
                r.raise_for_status()
                payload = r.json()
            except Exception as exc:
                log.warning(f"[skylines] flight list failed (page {page}): {exc}")
                break

            flights = payload.get("flights") or (payload if isinstance(payload, list) else [])
            if not flights:
                break

            for fl in flights:
                try:
                    fid   = fl.get("id") or fl.get("flight_id")
                    igc_r = await client.get(f"{_SKYLINES_URL}/flights/{fid}/igc")
                    igc_r.raise_for_status()
                    igc_texts.append(igc_r.text)
                    log.info(f"[skylines] downloaded flight {fid} ({len(igc_texts)} total)")
                    await asyncio.sleep(0.3)
                except Exception as exc:
                    log.debug(f"[skylines] skipping flight: {exc}")

            if len(flights) < per_page:
                break
            page += 1

    return igc_texts


# ---------------------------------------------------------------------------
# WeGlide downloader — requires a personal API token
# Get yours free at: https://www.weglide.org → Settings → API token
# ---------------------------------------------------------------------------
# WeGlide — OAuth2 login + thermal replay + flight tracks
# Register free at weglide.org, then pass --weglide-user / --weglide-pass
# ---------------------------------------------------------------------------
async def _weglide_login(client: httpx.AsyncClient, user: str, password: str) -> str:
    """Exchange username + password for a Bearer token via OAuth2 password grant."""
    r = await client.post(f"{_WEGLIDE_URL}/auth/token", data={
        "grant_type": "password",
        "username":   user,
        "password":   password,
    })
    r.raise_for_status()
    return r.json()["access_token"]


async def fetch_weglide_samples(days_back: int,
                                 lat_min: float, lat_max: float,
                                 lon_min: float, lon_max: float,
                                 user: str, password: str) -> list[dict]:
    """
    Two-phase WeGlide fetch:
      1. GET /v1/thermal  — WeGlide's pre-processed thermal database (best signal)
      2. GET /v1/flight   — flight list, then GET /v1/flightdata/{id} for GPS tracks
    Returns a flat list of fix-dicts ready for feature building.
    """
    end   = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    samples: list[dict] = []

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Authenticate
        try:
            token = await _weglide_login(client, user, password)
            client.headers["Authorization"] = f"Bearer {token}"
            log.info("[weglide] authenticated OK")
        except Exception as exc:
            log.error(f"[weglide] login failed: {exc}")
            return []

        # ── Phase 1: thermal replay database ────────────────────────────────
        # /v1/thermal returns confirmed thermal locations extracted from all flights.
        # Each entry has lat, lon, date, avg climb rate — perfect training labels.
        try:
            r = await client.get(f"{_WEGLIDE_URL}/thermal", params={
                "start_date": start.isoformat(),
                "end_date":   end.isoformat(),
                "min_lat": lat_min, "max_lat": lat_max,
                "min_lon": lon_min, "max_lon": lon_max,
            })
            r.raise_for_status()
            thermals = r.json() if isinstance(r.json(), list) else r.json().get("thermals", [])
            for t in thermals:
                try:
                    lat   = float(t.get("latitude")  or t.get("lat"))
                    lon   = float(t.get("longitude") or t.get("lon"))
                    vario = float(t.get("avg_climb") or t.get("vario") or 2.0)
                    dt_raw = t.get("date") or t.get("datetime") or end.isoformat()
                    dt    = datetime.fromisoformat(dt_raw) if "T" in str(dt_raw) \
                            else datetime(*(int(x) for x in str(dt_raw).split("-")), tzinfo=timezone.utc)
                    if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                        continue
                    samples.append({
                        "time": dt, "lat": lat, "lon": lon,
                        "alt": float(t.get("altitude") or 800),
                        "vario": vario, "turn_rate": 15.0, "circling": True,
                    })
                except Exception:
                    continue
            log.info(f"[weglide] {len(samples)} thermals from /v1/thermal")
        except Exception as exc:
            log.warning(f"[weglide] thermal endpoint failed: {exc}")

        # ── Phase 2: flight GPS tracks ───────────────────────────────────────
        page     = 0
        per_page = 50
        igc_texts: list[str] = []

        while True:
            try:
                r = await client.get(f"{_WEGLIDE_URL}/flight", params={
                    "start_date": start.isoformat(),
                    "end_date":   end.isoformat(),
                    "page": page, "per_page": per_page,
                })
                r.raise_for_status()
                flights = r.json() if isinstance(r.json(), list) else r.json().get("flights", [])
            except Exception as exc:
                log.warning(f"[weglide] flight list page {page} failed: {exc}")
                break

            if not flights:
                break

            for fl in flights:
                try:
                    # Filter to bounding box by takeoff coordinates
                    to_lat = fl.get("takeoff_latitude") or fl.get("latitude")
                    to_lon = fl.get("takeoff_longitude") or fl.get("longitude")
                    if not (to_lat and to_lon):
                        continue
                    if not (lat_min <= float(to_lat) <= lat_max and
                            lon_min <= float(to_lon) <= lon_max):
                        continue
                    fid   = fl["id"]
                    igc_r = await client.get(f"{_WEGLIDE_URL}/igcfile/{fid}")
                    igc_r.raise_for_status()
                    igc_texts.append(igc_r.text)
                    await asyncio.sleep(0.4)
                except Exception as exc:
                    log.debug(f"[weglide] skipping flight: {exc}")

            if len(flights) < per_page:
                break
            page += 1

        log.info(f"[weglide] {len(igc_texts)} IGC tracks downloaded")
        for text in igc_texts:
            fixes = parse_igc(text)
            samples.extend(extract_samples(fixes, lat_min, lat_max, lon_min, lon_max))

    return samples


# ---------------------------------------------------------------------------
# live.glidernet.org rec.php — returns recent OGN tracks as JSON, no auth
# Covers roughly the last 24 h of flights in the bounding box
# ---------------------------------------------------------------------------
async def fetch_glidernet_samples(lat_min: float, lat_max: float,
                                   lon_min: float, lon_max: float) -> list[dict]:
    """
    Returns a flat list of fix-dicts (same shape as parse_igc output) directly
    from the OGN REST API — no IGC parsing needed.
    """
    samples = []
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(_GLIDERNET_REC, params={
                "a": lat_min, "b": lat_max,
                "c": lon_min, "d": lon_max,
                "s": 10,   # min speed filter (km/h) — excludes parked gliders
            })
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning(f"[glidernet] fetch failed: {exc}")
            return []

    # Response: {"a": [ {"0":callsign,"1":lat,"2":lon,"3":alt,"4":vario,...}, ... ]}
    for ac in data.get("a", []):
        try:
            lat   = float(ac.get("1", 0))
            lon   = float(ac.get("2", 0))
            alt   = float(ac.get("3", 0))
            vario = float(ac.get("4", 0))
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
            samples.append({
                "time":     datetime.now(timezone.utc),
                "lat":      lat,
                "lon":      lon,
                "alt":      alt,
                "vario":    vario,
                "turn_rate": 0.0,
                "circling": vario > _THERMAL_VARIO,  # infer from climb rate
            })
        except (KeyError, ValueError):
            continue

    log.info(f"[glidernet] {len(samples)} fixes fetched")
    return samples


# ---------------------------------------------------------------------------
# Local OGN history DB — reads beacons logged by ogn_client.py
# ---------------------------------------------------------------------------
def load_ogn_db_samples(days_back: int,
                         lat_min: float, lat_max: float,
                         lon_min: float, lon_max: float) -> list[dict]:
    """
    Read beacons from data/ogn_history.db written by the live APRS stream.
    Returns fix-dicts ready for feature building (no IGC parsing needed).
    """
    db_path = Path(__file__).resolve().parents[1] / "data" / "ogn_history.db"
    if not db_path.exists():
        log.warning(f"[ogn-log] no history DB found at {db_path} — "
                    "start the backend and let it run to accumulate data first")
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    samples = []

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """SELECT ts, lat, lon, alt, vario, circling
               FROM beacons
               WHERE ts >= ?
                 AND lat BETWEEN ? AND ?
                 AND lon BETWEEN ? AND ?
               ORDER BY ts""",
            (cutoff, lat_min, lat_max, lon_min, lon_max),
        ).fetchall()

    for ts, lat, lon, alt, vario, circling in rows:
        samples.append({
            "time":     datetime.fromisoformat(ts),
            "lat":      lat,
            "lon":      lon,
            "alt":      alt,
            "vario":    vario,
            "turn_rate": 0.0,
            "circling": bool(circling),
        })

    log.info(f"[ogn-log] {len(samples)} beacons loaded from local DB "
             f"(last {days_back} days)")
    return samples


# ---------------------------------------------------------------------------
# Buffer I/O
# ---------------------------------------------------------------------------
def load_buffer(path: str) -> tuple[list, list]:
    p = Path(path)
    if p.exists():
        data = np.load(str(p), allow_pickle=False)
        log.info(f"[buffer] loaded {len(data['X'])} existing samples from {path}")
        return list(data["X"]), list(data["y"].astype(int))
    return [], []


def save_buffer(path: str, X: list, y: list) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, X=np.array(X), y=np.array(y))
    log.info(f"[buffer] saved {len(X)} samples to {path}")


# ---------------------------------------------------------------------------
# Main seeding loops
# ---------------------------------------------------------------------------
async def seed_from_fixes(samples: list[dict], buffer_X: list, buffer_y: list) -> tuple[list, list]:
    """Build features directly from fix-dicts (glidernet / ogn-log sources)."""
    radius = 0.05
    added  = 0

    async with httpx.AsyncClient(timeout=30) as client:
        # Sample every _SAMPLE_INTERVAL_S seconds to avoid redundant data
        last_ts: dict[str, datetime] = {}
        for s in samples:
            key = f"{s['lat']:.2f},{s['lon']:.2f}"
            if key in last_ts:
                if (s["time"] - last_ts[key]).total_seconds() < _SAMPLE_INTERVAL_S:
                    continue
            last_ts[key] = s["time"]

            meteo = await fetch_historical_meteo(client, s["lat"], s["lon"], s["time"])
            if meteo is None:
                continue
            elev = await fetch_elevation_grid_historical(client, s["lat"], s["lon"], radius)
            if elev is None:
                continue

            feat  = build_feature_matrix(
                meteo, elev,
                lat_bounds=(s["lat"] - radius, s["lat"] + radius),
                lon_bounds=(s["lon"] - radius, s["lon"] + radius),
                dt=s["time"],
            )
            label = int(s["circling"] and s["vario"] > _THERMAL_VARIO)
            buffer_X.append(feat[feat.shape[0] // 2])
            buffer_y.append(label)
            added += 1

            if added % 50 == 0:
                log.info(f"[seed] {added} samples added "
                         f"({sum(buffer_y)} thermals / {len(buffer_y) - sum(buffer_y)} non-thermals)")

    log.info(f"[seed] done — added {added} samples total")
    return buffer_X, buffer_y


async def seed(igc_texts: list[str], buffer_X: list, buffer_y: list,
               lat_min: float, lat_max: float,
               lon_min: float, lon_max: float) -> tuple[list, list]:

    radius = 0.05
    added  = 0

    async with httpx.AsyncClient(timeout=30) as client:
        for i, text in enumerate(igc_texts):
            fixes   = parse_igc(text)
            samples = extract_samples(fixes, lat_min, lat_max, lon_min, lon_max)
            log.info(f"[seed] flight {i+1}/{len(igc_texts)}: "
                     f"{len(fixes)} fixes → {len(samples)} candidate samples")

            for s in samples:
                meteo = await fetch_historical_meteo(client, s["lat"], s["lon"], s["time"])
                if meteo is None:
                    continue

                elev = await fetch_elevation_grid_historical(client, s["lat"], s["lon"], radius)
                if elev is None:
                    continue

                feat   = build_feature_matrix(
                    meteo, elev,
                    lat_bounds=(s["lat"] - radius, s["lat"] + radius),
                    lon_bounds=(s["lon"] - radius, s["lon"] + radius),
                    dt=s["time"],
                )
                center = feat.shape[0] // 2
                label  = int(s["circling"] and s["vario"] > _THERMAL_VARIO)
                buffer_X.append(feat[center])
                buffer_y.append(label)
                added += 1

                if added % 50 == 0:
                    log.info(f"[seed] {added} samples added so far "
                             f"({sum(buffer_y)} thermals / {len(buffer_y) - sum(buffer_y)} non-thermals)")

    log.info(f"[seed] done — added {added} samples total")
    return buffer_X, buffer_y


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed ThermalSense training buffer")
    parser.add_argument("--days",          type=int,  default=30,
                        help="Days back to fetch (default 30)")
    parser.add_argument("--source",        type=str,  default="glidernet",
                        choices=["glidernet", "ogn-log", "weglide"],
                        help="Data source (default: glidernet). "
                             "ogn-log reads your local DB built by the running backend.")
    parser.add_argument("--weglide-user",  type=str,  default="",
                        help="WeGlide email (required for --source weglide)")
    parser.add_argument("--weglide-pass",  type=str,  default="",
                        help="WeGlide password (required for --source weglide)")
    parser.add_argument("--igc-dir",       type=str,  default=None,
                        help="Local folder of .igc files — skips all API calls")
    args = parser.parse_args()

    buffer_X, buffer_y = load_buffer(BUFFER_PATH)

    if args.igc_dir:
        folder    = Path(args.igc_dir)
        igc_files = list(folder.glob("**/*.igc")) + list(folder.glob("**/*.IGC"))
        log.info(f"[seed] reading {len(igc_files)} local IGC files from {folder}")
        igc_texts = [f.read_text(errors="replace") for f in igc_files]
    elif args.source == "weglide":
        if not args.weglide_user or not args.weglide_pass:
            log.error("WeGlide requires --weglide-user and --weglide-pass. "
                      "Register free at https://www.weglide.org")
            return
        log.info(f"[seed] fetching WeGlide thermals + flights for the last {args.days} days …")
        fixes = await fetch_weglide_samples(
            args.days, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
            user=args.weglide_user, password=args.weglide_pass,
        )
        if not fixes:
            log.warning("[seed] WeGlide returned no data")
            return
        buffer_X, buffer_y = await seed_from_fixes(fixes, buffer_X, buffer_y)

    # --- sources that return fix-dicts directly (no IGC) ---
    if args.source == "glidernet" and not args.igc_dir:
        log.info("[seed] fetching recent OGN tracks from live.glidernet.org …")
        fixes = await fetch_glidernet_samples(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
        if not fixes:
            log.warning("[seed] no fixes returned — nothing to do")
            return
        buffer_X, buffer_y = await seed_from_fixes(fixes, buffer_X, buffer_y)

    elif args.source == "ogn-log" and not args.igc_dir:
        log.info(f"[seed] reading local OGN history DB (last {args.days} days) …")
        fixes = load_ogn_db_samples(args.days, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX)
        if not fixes:
            return
        buffer_X, buffer_y = await seed_from_fixes(fixes, buffer_X, buffer_y)

    else:
        # --- IGC-based sources ---
        if not igc_texts:
            log.warning("[seed] no IGC files found — nothing to do")
            return
        buffer_X, buffer_y = await seed(
            igc_texts, buffer_X, buffer_y,
            LAT_MIN, LAT_MAX, LON_MIN, LON_MAX,
        )

    save_buffer(BUFFER_PATH, buffer_X, buffer_y)
    pos = sum(buffer_y)
    log.info(f"[seed] buffer total: {len(buffer_X)} samples "
             f"({pos} thermal / {len(buffer_y) - pos} non-thermal)")


if __name__ == "__main__":
    asyncio.run(main())
