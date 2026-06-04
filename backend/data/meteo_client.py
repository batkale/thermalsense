import logging
import httpx
from datetime import datetime
from config import OPEN_METEO_BASE, OPEN_METEO_HIST_BASE

log = logging.getLogger(__name__)

HOURLY_VARS = [
    "temperature_2m", "relativehumidity_2m", "windspeed_10m",
    "winddirection_10m", "cape", "shortwave_radiation",
    "convective_inhibition", "temperature_850hPa", "temperature_500hPa",
    "boundary_layer_height", "soil_temperature_0_to_7cm",
]

# Reasonable mid-summer Southern England defaults used when the API is offline
_FALLBACK: dict = {
    "temp_2m":      18.0,
    "humidity":      0.60,
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


async def fetch_meteo_features(lat: float, lon: float, forecast_h: int) -> dict:
    """
    Open-Meteo: free, no API key needed.
    Returns fallback values if the network is unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(OPEN_METEO_BASE, params={
                "latitude": lat, "longitude": lon,
                "hourly": ",".join(HOURLY_VARS),
                "models": "best_match",
                "forecast_days": 1, "timezone": "auto",
            })
        r.raise_for_status()
        data   = r.json()
        hourly = data["hourly"]
        idx    = min(forecast_h, len(hourly["time"]) - 1)

        def _v(key, fb_key):
            val = hourly[key][idx]
            return val if val is not None else _FALLBACK[fb_key]

        t2m  = hourly["temperature_2m"][idx]
        t850 = hourly["temperature_850hPa"][idx]
        t500 = hourly["temperature_500hPa"][idx]
        return {
            "temp_2m":    t2m,
            "humidity":   hourly["relativehumidity_2m"][idx] / 100,
            "wind_speed": hourly["windspeed_10m"][idx],
            "wind_dir":   hourly["winddirection_10m"][idx],
            "cape":       hourly["cape"][idx],
            "cin":        _v("convective_inhibition", "cin"),
            "solar_ghi":  hourly["shortwave_radiation"][idx],
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


async def fetch_meteo_historical(lat: float, lon: float, dt: datetime) -> dict:
    """
    Fetch archived NWP meteo for a past hour using Open-Meteo's historical
    forecast API (model-archive, ~1-day lag).  Same variables as the live
    forecast endpoint so callers can use the result identically.
    Falls back to _FALLBACK on any error.
    """
    date_str  = dt.strftime("%Y-%m-%d")
    hour_idx  = dt.hour
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(OPEN_METEO_HIST_BASE, params={
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
            "humidity":   _v("relativehumidity_2m",        "humidity") / 100,
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
        log.warning(
            f"[meteo] historical fetch failed ({lat:.2f},{lon:.2f}) "
            f"{date_str}h{hour_idx:02d} ({exc}) — using fallback"
        )
        return _FALLBACK.copy()
