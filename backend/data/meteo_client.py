import json
import logging
import math
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

# Asked for by the live forecast request only, and shown to the user, never fed
# to the model: build_feature_matrix reads the meteo dict by name, so these ride
# along without touching FEATURE_COUNT.  They come in the request that is made
# anyway — Open-Meteo's 10k/day is shared with the training sampler, so a second
# call per click for a weather icon would be the worst way to spend it.
DISPLAY_VARS = ["weathercode", "is_day"]

# WMO weather interpretation codes (the table Open-Meteo documents) to the plain
# condition the map shows.  The values are translation keys on the frontend
# (weather_cond_<key>), so they are part of the wire contract — rename one and
# both dictionaries change with it.
_CONDITION_BY_CODE: dict[int, str] = {
    0: "clear",
    1: "mostly_clear",
    2: "partly_cloudy",
    3: "cloudy",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    80: "rain", 81: "rain", 82: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}


def weather_condition(code) -> str | None:
    """Condition key for a WMO weather code, or None when there is no code.

    An unknown code is None rather than a guess: a "sunny" icon over a code this
    table has never seen would be inventing weather, which is the one thing the
    fallback history of this module says not to do.
    """
    if code is None:
        return None
    try:
        return _CONDITION_BY_CODE.get(int(round(float(code))))
    except (TypeError, ValueError):
        return None


def local_utc_offset_s(tz_name: str | None, fallback_s, at: datetime) -> int | None:
    """Seconds east of UTC at the place the forecast is for, at the moment `at`.

    Open-Meteo's utc_offset_seconds is the offset *now*; with the slider up to
    +24 h ahead that is an hour wrong across a DST change.  The IANA name it
    returns beside it answers for any moment, so that is preferred, and the
    plain offset is the stand-in when the name is missing or unknown here.
    """
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            off = at.astimezone(ZoneInfo(tz_name)).utcoffset()
            if off is not None:
                return int(off.total_seconds())
        except Exception:
            pass
    if fallback_s is None:
        return None
    try:
        return int(fallback_s)
    except (TypeError, ValueError):
        return None


def _utc_stamps(times: list) -> list[str]:
    """Hourly stamps as UTC 'YYYY-MM-DDTHH:00', whatever form they arrived in.

    The live request asks for timezone=auto, which is what makes Open-Meteo say
    where the point's clock is, and timeformat=unixtime, so the time axis stays
    absolute and every slot lookup below reads exactly the hour it read when the
    request said timezone=UTC.  ISO strings pass through untouched.
    """
    if not times or not isinstance(times[0], (int, float)):
        return times
    return [datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:00")
            for t in times]

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


def _hour_slots(times: list[str], forecast_h: float) -> tuple[int, int, float]:
    """
    The two hourly slots either side of (current hour + forecast_h), and how far
    from the first to the second the offset falls (0 <= w < 1).

    The forecast slider steps in quarter hours over the first three hours, and
    Open-Meteo answers hourly.  Reading the slot the offset lands in would give
    four identical weather inputs per hour and one jump at each boundary, so a
    fractional offset is interpolated between the two hours instead — the same
    thing Open-Meteo's own 15-minute product does outside the few regions that
    have a native 15-minute model.

    Anchored on the current hour exactly as _hour_index is, so a whole offset is
    w = 0 and reads the slot it always read.  forecast_h = 0 in particular is
    still the current hour's values: that is what the live sampler pairs its
    labels with, and served features must stay the ones training saw.  It also
    keeps the result constant within a clock hour, which is what the per-hour
    live cache assumes.
    """
    whole = math.floor(forecast_h)
    w     = forecast_h - whole
    i0    = _hour_index(times, whole)
    if w <= 0 or i0 >= len(times) - 1:
        return i0, i0, 0.0
    return i0, i0 + 1, w


def _interp(a, b, w: float, fallback, circular: bool = False):
    """
    a + (b - a) * w over two hourly values, either of which may be None.

    w = 0 returns a untouched, None included, so a whole-hour offset reads
    exactly what it read before interpolation existed.  With one side missing
    the side that exists is nearer the truth than _FALLBACK.  circular is for
    wind direction: 350 deg to 10 deg passes through north, not through south.
    """
    if w == 0 or b is None:
        return a if a is not None else fallback
    if a is None:
        return b
    if circular:
        return (a + ((b - a + 180) % 360 - 180) * w) % 360
    return a + (b - a) * w


# --- live forecast: per-hour cache and a daily call counter -------------------
#
# Every candidate grid in a retrain cycle used to be its own request, night
# included, so the collection rate was pegged to Open-Meteo's 10,000/day long
# before anything else bound it.  Nothing about that was visible either — there
# was no counter.
#
# The key is a 0.1 degree cell and the forecast offset.  That is not a compromise:
# build_feature_matrix already spreads one point sample across the whole 11 km
# grid, so two requests 0.1 degree apart in the same hour were always going to
# produce the same sixteen columns.  Entries are dropped when the hour rolls
# over, which is also when the upstream values change.  /predict rounds the
# offset to a quarter hour before it gets here, so a client cannot mint a fresh
# key — and a fresh upstream call — per float.
_LIVE_SNAP = 0.1
_live_cache: dict[tuple, dict] = {}
_live_hour = ""
_live_usage = {"day": "", "requests": 0, "cache_hits": 0,
               "historical_requests": 0, "historical_cache_hits": 0,
               "calls": 0.0, "historical_calls": 0.0}
# Open-Meteo's published free-tier ceiling.  Forecast and archive calls come out
# of the same budget, which is why both are counted here: the nightly seed and
# the live sampler are spending one allowance between them.
LIVE_DAILY_QUOTA = 10000


def _call_weight(n_vars: int, days: float = 1) -> float:
    """What Open-Meteo bills one request as, in calls.

    Its pricing page counts a request for more than 10 weather variables, or
    more than two weeks of one location, as a fraction more: "a request for 2
    weeks of data with 15 weather variables will be calculated as 1.5 API
    calls", and four weeks of the same as 3.0 — so the two factors multiply.
    The quota is in these calls, not in requests.  Until 19 Nov 2026 the counter
    added 1 per request while the live one carried 13 variables (HOURLY_VARS +
    DISPLAY_VARS): every forecast was 1.3 calls, every archive hour 1.1, and the
    budgets checked against `total` were up to 30% short of what was spent.
    """
    return max(1.0, n_vars / 10) * max(1.0, days / 14)


# The live request asks for 2 forecast days, the archive request for one.
_LIVE_CALL_WEIGHT = _call_weight(len(HOURLY_VARS) + len(DISPLAY_VARS), 2)
_HIST_CALL_WEIGHT = _call_weight(len(HOURLY_VARS), 1)


def _live_key(lat: float, lon: float, forecast_h: float) -> tuple:
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
    # `requests` counts what was sent; `total` is what Open-Meteo bills for it
    # (see _call_weight), and is the figure the quota is checked against.
    base["calls"] = round(base.get("calls", 0.0), 1)
    base["historical_calls"] = round(base.get("historical_calls", 0.0), 1)
    base["total"] = round(base["calls"] + base["historical_calls"], 1)
    base["quota"] = LIVE_DAILY_QUOTA
    return base


def _count_live(hit: bool, historical: bool = False, weight: float = 1.0) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if _live_usage["day"] != today:
        _live_usage.update(day=today, requests=0, cache_hits=0,
                           historical_requests=0, historical_cache_hits=0,
                           calls=0.0, historical_calls=0.0)
    prefix = "historical_" if historical else ""
    if hit:
        _live_usage[f"{prefix}cache_hits"] += 1
        return
    _live_usage[f"{prefix}requests"] += 1
    _live_usage[f"{prefix}calls"] = _live_usage.get(f"{prefix}calls", 0.0) + weight


async def fetch_meteo_features(lat: float, lon: float, forecast_h: float,
                               strict: bool = False) -> dict | None:
    """
    Open-Meteo: free, no API key needed.
    Returns fallback values if the network is unavailable.

    forecast_h is hours ahead of the current hour.  A fractional offset is
    interpolated between the two hourly values either side — see _hour_slots.

    Cached per 0.1 degree cell for the current hour — see the note above.

    Pass strict=True from anything that *trains* on the result, and get None
    instead of _FALLBACK when the fetch fails — the same contract
    fetch_meteo_historical and fetch_elevation_grid already have.  The live
    sampler did not use one, so every timeout or refused request became a
    training row: a real label attached to 18 degC, 500 W/m2 and 600 J/kg of
    CAPE, whatever the hour and wherever the aircraft was.  Measured on the
    buffer of 6 Nov 2026: 862 of 48,281 rows (1.8%), 70 of them positives,
    spread over 16 days and every continent the feed covers.  At night the
    invented 500 W/m2 also walked those rows straight past _MIN_SOLAR_GHI.
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
            "hourly": ",".join(HOURLY_VARS + DISPLAY_VARS),
            "models": "best_match",
            # 2 days so a forecast_h late in the day stays in range.  The days
            # are local ones under timezone=auto, which changes nothing: the
            # point's own "now" always lies inside its own first day, so now +
            # 24 h still lies inside the two — the same argument as for UTC.
            "forecast_days": 2,
            "timezone": "auto", "timeformat": "unixtime",
        })
        r.raise_for_status()
        data   = r.json()
        hourly = data["hourly"]
        times  = _utc_stamps(hourly["time"])
        i0, i1, w = _hour_slots(times, forecast_h)
        # A weather code is a category: halfway between "cloudy" and "rain" is
        # not a code.  Take the nearer of the two hours instead.
        i_near = i1 if w >= 0.5 else i0
        target = datetime.fromisoformat(times[i0]).replace(tzinfo=timezone.utc)             + timedelta(hours=w)

        def _v(key, fb_key, circular=False):
            return _interp(hourly[key][i0], hourly[key][i1], w,
                           _FALLBACK[fb_key], circular)

        # The derived values below (lapse rate, cloud base) are computed from
        # the interpolated temperatures, not interpolated themselves, so they
        # stay consistent with the three numbers they are built from.
        t2m  = _v("temperature_2m",     "temp_2m")
        t850 = _v("temperature_850hPa", "temp_850")
        t500 = _v("temperature_500hPa", "temp_500")
        _count_live(hit=False, weight=_LIVE_CALL_WEIGHT)
        out = {
            "temp_2m":    t2m,
            "humidity":   _v("relativehumidity_2m", "humidity_pct") / 100,
            "wind_speed": _v("windspeed_10m",       "wind_speed"),
            "wind_dir":   _v("winddirection_10m",   "wind_dir", circular=True),
            "cape":       _v("cape",                "cape"),
            "cin":        _v("convective_inhibition", "cin"),
            "solar_ghi":  _v("shortwave_radiation", "solar_ghi"),
            "temp_850":   t850,
            "temp_500":   t500,
            "lapse_rate": (t850 - t500) / 3.5,
            "cape_base":  max(0, int(((t2m - 8) / max(1, (t2m - t850) / 1.5)) * 1000)),
            "pbl_height": _v("boundary_layer_height",     "pbl_height"),
            "soil_temp":  _v("soil_temperature_0_to_7cm", "soil_temp"),
            # Display only — see DISPLAY_VARS.  .get: a slot or a whole
            # variable can be missing, and then the map shows no condition.
            "weather_code": _slot(hourly, "weathercode", i_near),
            "is_day":       _slot(hourly, "is_day", i_near),
            "timezone":     data.get("timezone"),
            "utc_offset_s": local_utc_offset_s(data.get("timezone"),
                                               data.get("utc_offset_seconds"),
                                               target),
        }
        # Only a real answer is cached: the fallback is synthetic, and pinning it
        # for an hour would hand the same invented weather to every grid in the
        # cell — including the ones being written into the training buffer.
        _live_cache[key] = out
        return out
    except Exception as exc:
        log.warning(f"[meteo] fetch failed ({exc}) — "
                    f"{'skipping' if strict else 'using fallback values'}")
        return None if strict else _FALLBACK.copy()


def _slot(hourly: dict, key: str, i: int):
    vals = hourly.get(key)
    if not vals or i >= len(vals) or vals[i] is None:
        return None
    return int(round(float(vals[i])))


def is_synthetic(meteo: dict) -> bool:
    """True when a dict from fetch_meteo_features is _FALLBACK, not a forecast.

    The fallback is returned as a plain copy, and a real answer never carries
    the humidity_pct key _FALLBACK has, so equality cannot mistake one for the
    other.  A marker key would be tidier, but callers and tests compare the
    fallback to _FALLBACK directly and it would break every one of them.
    """
    return meteo == _FALLBACK


# --- cumulus outlook (display only) -------------------------------------------
#
# Where thermals mark themselves with cumulus, and how high the cloud base is —
# worked out from three numbers the live request already carries, so it costs no
# variable and no call.  Never a feature: nothing here reaches the meteo dict
# build_feature_matrix reads.
#
# Cloud base is the lifting condensation level of a surface parcel: about 125 m
# per degree C of dew-point spread (Lawrence 2005, BAMS 86:225 — the pilot's
# "400 ft per degree").  Cumulus forms where the thermals reach it, i.e. where
# the model's boundary layer tops out above it; where the layer stops short,
# the thermals are "blue" — dry, unmarked, topping out at the layer height.
# That is the cumulus-potential test soaring forecasts use.  It is a point
# estimate for one 0.1 degree cell from 2 m values, and says nothing about where
# inside the cell the clouds stand; the satellite layer on the map is what shows
# that.
_LCL_M_PER_C = 125.0
# Magnus coefficients over water (Alduchov & Eskridge 1996).
_MAGNUS_A, _MAGNUS_B = 17.625, 243.04


def dew_point_c(temp_c: float, rh_frac: float) -> float | None:
    """Dew point in degrees C from air temperature and relative humidity (0-1)."""
    if temp_c is None or rh_frac is None or rh_frac <= 0:
        return None
    rh = min(float(rh_frac), 1.0)
    g = math.log(rh) + _MAGNUS_A * temp_c / (_MAGNUS_B + temp_c)
    return _MAGNUS_B * g / (_MAGNUS_A - g)


def cumulus_outlook(meteo: dict, min_solar_ghi: float) -> dict | None:
    """Cumulus or blue thermals at the forecast moment, and the cloud base.

    kind is a translation key on the frontend (cumulus_<kind>), so it is part of
    the wire contract:
      cumulus — thermals reach the condensation level; base_agl is the cloud base
      blue    — the boundary layer stops below it; thermals stay unmarked
      none    — too little sun for thermals at all (the night guard's floor)
    None for the synthetic fallback: an invented 60% humidity would draw an
    invented cloud base.
    """
    if is_synthetic(meteo):
        return None
    t2m, rh, pbl = meteo.get("temp_2m"), meteo.get("humidity"), meteo.get("pbl_height")
    td = dew_point_c(t2m, rh)
    if td is None or pbl is None:
        return None
    lcl = max(0.0, (t2m - td) * _LCL_M_PER_C)
    if meteo.get("solar_ghi", 0) < min_solar_ghi:
        kind = "none"
    else:
        kind = "cumulus" if pbl >= lcl else "blue"
    return {
        "kind":      kind,
        # 50 m steps: the 125 m/degC rule is good to a few hundred metres, and
        # "1,637 m" would claim more than that.
        "base_agl":  int(round(lcl / 50) * 50),
        "dew_point": round(td, 1),
        # How far the layer top clears the base (+) or falls short of it (-).
        "margin_m":  int(round((pbl - lcl) / 50) * 50),
    }


# The feature columns _FALLBACK lands in, with the value each one takes.  A real
# hour matching all seven at once does not happen: across the 47,419 genuine rows
# of that buffer the best single column matched 2.8% of the time and no row
# matched more than two.
_FALLBACK_SIGNATURE = (
    (5,  _FALLBACK["temp_2m"]),
    (6,  _FALLBACK["humidity"]),
    (9,  _FALLBACK["cape"]),
    (10, _FALLBACK["cin"]),
    (11, _FALLBACK["solar_ghi"]),
    (19, _FALLBACK["pbl_height"]),
    (20, _FALLBACK["soil_temp"]),
)


def is_fallback_row(X):
    """Boolean mask over feature rows built from _FALLBACK rather than a forecast.

    For cleaning buffers collected before the sampler went strict.  Column
    numbers follow build_feature_matrix; a test pins the two together.
    """
    import numpy as np
    X = np.asarray(X)
    mask = np.ones(len(X), dtype=bool)
    for col, value in _FALLBACK_SIGNATURE:
        mask &= np.isclose(X[:, col], value)
    return mask


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
        _count_live(hit=False, historical=True, weight=_HIST_CALL_WEIGHT)
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
