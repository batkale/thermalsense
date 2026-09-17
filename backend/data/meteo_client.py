import json
import logging
from pathlib import Path

import httpx
from datetime import datetime, timedelta, timezone
from config import OPEN_METEO_BASE, OPEN_METEO_HIST_BASE

log = logging.getLogger(__name__)

# A seed run issues thousands of requests.  Building a fresh AsyncClient per call
# opens a new TLS connection each time and leaves it in TIME_WAIT, which exhausts
# the Windows ephemeral port range and surfaces as bare httpx.ConnectError.
# Share one pooled client instead.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
        )
    return _client


async def aclose() -> None:
    """Release the pooled connections. Called from the app lifespan on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None

HOURLY_VARS = [
    "temperature_2m", "relativehumidity_2m", "windspeed_10m",
    "winddirection_10m", "cape", "shortwave_radiation",
    "convective_inhibition", "temperature_850hPa", "temperature_500hPa",
    "boundary_layer_height", "soil_temperature_0_to_7cm",
]

# Reasonable mid-summer Southern England defaults used when the API is offline
_FALLBACK: dict = {
    "temp_2m":      18.0,
    "humidity":      0.60,   # 0-1, as returned by this module
    "humidity_pct": 60.0,    # 0-100, as returned by the API (pre-division)
    "wind_speed":   12.0,
    "wind_dir":    270.0,
    "cape":        600.0,
    "cin":         -30.0,
    "solar_ghi":   500.0,
    "temp_850":      5.0,
    "temp_500":    -12.0,
    "lapse_rate":    4.9,
    "cape_base":    1200,
    "pbl_height":  1500.0,
    "soil_temp":     20.0,
}


def _hour_index(times: list[str], forecast_h: int) -> int:
    """
    Index of (now + forecast_h) within an hourly UTC time array.

    Open-Meteo's hourly arrays start at 00:00 of the first forecast day, so the
    offset must be measured from that origin — indexing by forecast_h alone
    reads midnight's values for every "now" request.
    """
    target = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0, tzinfo=None
    ) + timedelta(hours=forecast_h)
    stamp = target.strftime("%Y-%m-%dT%H:00")
    if stamp in times:
        return times.index(stamp)
    # Ragged/shifted array — fall back to arithmetic from the first timestamp
    start = datetime.fromisoformat(times[0])
    idx   = round((target - start).total_seconds() / 3600)
    return max(0, min(idx, len(times) - 1))


# --- live forecast: per-hour cache and a daily call counter -------------------
#
# Every candidate grid in a retrain cycle used to be its own request, night
# included, so the collection rate was pegged to Open-Meteo's 10,000/day long
# before anything else bound it.  Nothing about that was visible either — there
# was no counter.
#
# The key is a 0.1 degree cell and the forecast hour.  That is not a compromise:
# build_feature_matrix already spreads one point sample across the whole 11 km
# grid, so two requests 0.1 degree apart in the same hour were always going to
# produce the same sixteen columns.  Entries are dropped when the hour rolls
# over, which is also when the upstream values change.
_LIVE_SNAP = 0.1
_live_cache: dict[tuple, dict] = {}
_live_hour = ""
_live_usage = {"day": "", "requests": 0, "cache_hits": 0,
               "historical_requests": 0, "historical_cache_hits": 0}
# Open-Meteo's published free-tier ceiling.  Forecast and archive calls come out
# of the same budget, which is why both are counted here: the nightly seed and
# the live sampler are spending one allowance between them.
LIVE_DAILY_QUOTA = 10000


def _live_key(lat: float, lon: float, forecast_h: int) -> tuple:
    return (round(lat / _LIVE_SNAP) * _LIVE_SNAP,
            round(lon / _LIVE_SNAP) * _LIVE_SNAP, forecast_h)


def live_usage() -> dict:
    """
    Today's Open-Meteo calls against the published daily quota.

    `total` is what any budget must be checked against: the live sampler and the
    nightly seed draw on one allowance, so counting only one of them would let
    the pair overrun it together.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    base = ({"day": today, "requests": 0, "cache_hits": 0,
             "historical_requests": 0, "historical_cache_hits": 0}
            if _live_usage["day"] != today else dict(_live_usage))
    base["total"] = base["requests"] + base["historical_requests"]
    base["quota"] = LIVE_DAILY_QUOTA
    return base


def _count_live(hit: bool, historical: bool = False) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if _live_usage["day"] != today:
        _live_usage.update(day=today, requests=0, cache_hits=0,
                           historical_requests=0, historical_cache_hits=0)
    prefix = "historical_" if historical else ""
    _live_usage[f"{prefix}cache_hits" if hit else f"{prefix}requests"] += 1


async def fetch_meteo_features(lat: float, lon: float, forecast_h: int) -> dict:
    """
    Open-Meteo: free, no API key needed.
    Returns fallback values if the network is unavailable.

    Cached per 0.1 degree cell for the current hour — see the note above.
    """
    global _live_hour
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if hour != _live_hour:
        _live_cache.clear()
        _live_hour = hour
    key = _live_key(lat, lon, forecast_h)
    hit = _live_cache.get(key)
    if hit is not None:
        _count_live(hit=True)
        return hit
    try:
        r = await _get_client().get(OPEN_METEO_BASE, params={
            "latitude": lat, "longitude": lon,
            "hourly": ",".join(HOURLY_VARS),
            "models": "best_match",
            # 2 days so a forecast_h late in the day stays in range
            "forecast_days": 2, "timezone": "UTC",
        })
        r.raise_for_status()
        data   = r.json()
        hourly = data["hourly"]
        idx    = _hour_index(hourly["time"], forecast_h)

        def _v(key, fb_key):
            val = hourly[key][idx]
            return val if val is not None else _FALLBACK[fb_key]

        t2m  = _v("temperature_2m",     "temp_2m")
        t850 = _v("temperature_850hPa", "temp_850")
        t500 = _v("temperature_500hPa", "temp_500")
        _count_live(hit=False)
        out = {
            "temp_2m":    t2m,
            "humidity":   _v("relativehumidity_2m", "humidity_pct") / 100,
            "wind_speed": _v("windspeed_10m",       "wind_speed"),
            "wind_dir":   _v("winddirection_10m",   "wind_dir"),
            "cape":       _v("cape",                "cape"),
            "cin":        _v("convective_inhibition", "cin"),
            "solar_ghi":  _v("shortwave_radiation", "solar_ghi"),
            "temp_850":   t850,
            "temp_500":   t500,
            "lapse_rate": (t850 - t500) / 3.5,
            "cape_base":  max(0, int(((t2m - 8) / max(1, (t2m - t850) / 1.5)) * 1000)),
            "pbl_height": _v("boundary_layer_height",     "pbl_height"),
            "soil_temp":  _v("soil_temperature_0_to_7cm", "soil_temp"),
        }
        # Only a real answer is cached: the fallback is synthetic, and pinning it
        # for an hour would hand the same invented weather to every grid in the
        # cell — including the ones being written into the training buffer.
        _live_cache[key] = out
        return out
    except Exception as exc:
        log.warning(f"[meteo] fetch failed ({exc}) — using fallback values")
        return _FALLBACK.copy()


# --- historical response cache -------------------------------------------
#
# Keyed on the (0.5deg, hour) bucket /seed already groups on, so every beacon in
# a bucket shares one entry — the same de-duplication the seed does, made to
# survive the process.  Written as JSON Lines and appended: an append cannot
# corrupt what is already on disk if the box dies mid-write, and a partial last
# line is simply skipped on load.
_HIST_SNAP = 0.5
_hist_cache: dict[str, dict] = {}
_hist_loaded = False
_hist_pending: list[str] = []
_HIST_FLUSH_EVERY = 50


def _hist_path():
    from config import DATA_DIR
    return Path(DATA_DIR) / "cache" / "meteo_historical.jsonl"


def _hist_key(lat: float, lon: float, dt: datetime) -> str:
    la = round(round(lat / _HIST_SNAP) * _HIST_SNAP, 2)
    lo = round(round(lon / _HIST_SNAP) * _HIST_SNAP, 2)
    return f"{la}|{lo}|{dt.strftime('%Y-%m-%dT%H')}"


def _hist_cache_load() -> None:
    global _hist_loaded
    if _hist_loaded:
        return
    _hist_loaded = True
    p = _hist_path()
    if not p.exists():
        return
    bad = 0
    try:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    _hist_cache[rec["k"]] = rec["v"]
                except Exception:
                    bad += 1
        log.info(f"[meteo] historical cache: {len(_hist_cache):,} buckets"
                 + (f", {bad} unreadable line(s) skipped" if bad else ""))
    except Exception as exc:
        log.warning(f"[meteo] historical cache unreadable ({exc}) — starting empty")


def _hist_cache_get(key: str):
    _hist_cache_load()
    return _hist_cache.get(key)


def flush_historical_cache(force: bool = False) -> None:
    """Append queued entries. Called on a batch boundary and at shutdown."""
    if not _hist_pending or (not force and len(_hist_pending) < _HIST_FLUSH_EVERY):
        return
    p = _hist_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for k in _hist_pending:
                fh.write(json.dumps({"k": k, "v": _hist_cache[k]}) + "\n")
        _hist_pending.clear()
    except Exception as exc:
        log.warning(f"[meteo] historical cache write failed: {exc}")


def _hist_cache_put(key: str, value: dict) -> dict:
    _hist_cache_load()
    _hist_cache[key] = value
    _hist_pending.append(key)
    flush_historical_cache()
    return value


async def fetch_meteo_historical(
    lat: float, lon: float, dt: datetime, strict: bool = False
) -> dict | None:
    """
    Fetch archived NWP meteo for a past hour using Open-Meteo's historical
    forecast API (model-archive, ~1-day lag).  Same variables as the live
    forecast endpoint so callers can use the result identically.

    Results are cached to disk permanently, keyed by the same (0.5deg, hour)
    bucket `/seed` groups on.  The cold beacon archive makes the training buffer
    rebuildable in principle, but only at 10,000 Open-Meteo calls a day: a year
    of feed is on the order of a million buckets, so an uncached rebuild is
    months of fetching rather than an afternoon.  The response is a dozen floats
    and the past does not change, so this is the cheapest possible way to make
    the archive usable at the scale it is being collected at.

    A cached row also pins the weather a sample was trained against, which the
    live API does not: Open-Meteo revises its archive as reanalysis lands, so an
    uncached rebuild would quietly produce a different buffer from the same
    beacons.

    On error this returns _FALLBACK — fixed synthetic values.  That is fine for
    a one-off display request but poisonous for training: it pairs real thermal
    labels with invented weather.  Pass strict=True to get None instead so the
    caller can skip the sample rather than learn from a fabrication.
    """
    date_str  = dt.strftime("%Y-%m-%d")
    hour_idx  = dt.hour
    ckey = _hist_key(lat, lon, dt)
    hit = _hist_cache_get(ckey)
    if hit is not None:
        _count_live(hit=True, historical=True)
        return hit
    try:
        r = await _get_client().get(OPEN_METEO_HIST_BASE, params={
            "latitude":   lat,
            "longitude":  lon,
            "hourly":     ",".join(HOURLY_VARS),
            "start_date": date_str,
            "end_date":   date_str,
            "timezone":   "UTC",
        })
        r.raise_for_status()
        hourly = r.json()["hourly"]

        def _v(key, fb_key):
            val = hourly[key][hour_idx]
            return val if val is not None else _FALLBACK[fb_key]

        t2m  = _v("temperature_2m",    "temp_2m")
        t850 = _v("temperature_850hPa", "temp_850")
        t500 = _v("temperature_500hPa", "temp_500")
        _count_live(hit=False, historical=True)
        return _hist_cache_put(ckey, {
            "temp_2m":    t2m,
            "humidity":   _v("relativehumidity_2m",        "humidity_pct") / 100,
            "wind_speed": _v("windspeed_10m",              "wind_speed"),
            "wind_dir":   _v("winddirection_10m",          "wind_dir"),
            "cape":       _v("cape",                        "cape"),
            "cin":        _v("convective_inhibition",       "cin"),
            "solar_ghi":  _v("shortwave_radiation",         "solar_ghi"),
            "temp_850":   t850,
            "temp_500":   t500,
            "lapse_rate": (t850 - t500) / 3.5,
            "cape_base":  max(0, int(((t2m - 8) / max(1, (t2m - t850) / 1.5)) * 1000)),
            "pbl_height": _v("boundary_layer_height",      "pbl_height"),
            "soil_temp":  _v("soil_temperature_0_to_7cm",  "soil_temp"),
        })
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.warning(
            f"[meteo] historical fetch failed ({lat:.2f},{lon:.2f}) "
            f"{date_str}h{hour_idx:02d} ({detail}) — "
            f"{'skipping' if strict else 'using fallback'}"
        )
        return None if strict else _FALLBACK.copy()
