import logging
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


async def fetch_meteo_features(lat: float, lon: float, forecast_h: int) -> dict:
    """
    Open-Meteo: free, no API key needed.
    Returns fallback values if the network is unavailable.
    """
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
        return {
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
    except Exception as exc:
        log.warning(f"[meteo] fetch failed ({exc}) — using fallback values")
        return _FALLBACK.copy()


async def fetch_meteo_historical(
    lat: float, lon: float, dt: datetime, strict: bool = False
) -> dict | None:
    """
    Fetch archived NWP meteo for a past hour using Open-Meteo's historical
    forecast API (model-archive, ~1-day lag).  Same variables as the live
    forecast endpoint so callers can use the result identically.

    On error this returns _FALLBACK — fixed synthetic values.  That is fine for
    a one-off display request but poisonous for training: it pairs real thermal
    labels with invented weather.  Pass strict=True to get None instead so the
    caller can skip the sample rather than learn from a fabrication.
    """
    date_str  = dt.strftime("%Y-%m-%d")
    hour_idx  = dt.hour
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
        return {
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
        }
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.warning(
            f"[meteo] historical fetch failed ({lat:.2f},{lon:.2f}) "
            f"{date_str}h{hour_idx:02d} ({detail}) — "
            f"{'skipping' if strict else 'using fallback'}"
        )
        return None if strict else _FALLBACK.copy()
