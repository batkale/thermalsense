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
               "historical_requests": 0, "historical_cache_hits": 0}
# Open-Meteo's published free-tier ceiling.  Forecast and archive calls come out
# of the same budget, which is why both are counted here: the nightly seed and
# the live sampler are spending one allowance between them.
LIVE_DAILY_QUOTA = 10000


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

        # The lapse rate below is computed from the interpolated temperatures,
        # not interpolated itself, so it stays consistent with the numbers it
        # is built from.  The thermal top is too, by thermal_top_agl at use.
        t2m  = _v("temperature_2m",     "temp_2m")
        t850 = _v("temperature_850hPa", "temp_850")
        t500 = _v("temperature_500hPa", "temp_500")
        _count_live(hit=False)
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


# --- thermal top ---------------------------------------------------------------
#
# How high thermals reach, in metres above the ground.  Dry thermals stop at the
# top of the convective boundary layer; where the layer clears the condensation
# level they stop at cloud base instead, because that is where cumulus forms and
# where a glider has to leave the climb.  So the top is the lower of the two.
#
# Cloud base is the lifting condensation level of a surface parcel: about 125 m
# per degree C of dew-point spread (Lawrence 2005, BAMS 86:225 -- the pilot's
# "400 ft per degree").  The layer height is Open-Meteo's boundary_layer_height,
# already in every request.
#
# It replaced `cape_base`, (t2m - 8) / max(1, (t2m - t850) / 1.5) * 1000, on
# 19 Nov 2026.  That formula read no humidity and no layer height: 0 m (filter
# off) on every hour under 8 degC, tens of kilometres wherever 2 m and 850 hPa
# were within 1.5 degC of each other, and 2,273 m at Inonu that noon against a
# 900 m cloud base.  Measured against how high thermalling aircraft actually
# climbed in 978 (0.5 deg, hour) buckets of the archive, Spearman 0.73 and a
# median miss of 229 m here against 0.55 and 715 m there -- see CLAUDE.md.
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


def lcl_agl(temp_c: float, rh_frac: float) -> float | None:
    """Condensation level of a surface parcel, metres above ground — the cumulus base."""
    td = dew_point_c(temp_c, rh_frac)
    if td is None:
        return None
    return max(0.0, (temp_c - td) * _LCL_M_PER_C)


def thermal_top_agl(meteo: dict) -> float | None:
    """How high thermals reach, metres above ground: min(boundary layer, cloud base).

    Reads only temp_2m, humidity and pbl_height, which every meteo dict carries
    -- including historical-cache entries written before this function existed,
    so nothing on disk needs rewriting.  None when one of the three is missing:
    that is "no estimate", and callers must not filter on it.  0 is an estimate
    (saturated air at 2 m: fog or stratus on the ground), not a missing value.
    """
    lcl = lcl_agl(meteo.get("temp_2m"), meteo.get("humidity"))
    pbl = meteo.get("pbl_height")
    if lcl is None or pbl is None:
        return None
    return min(max(float(pbl), 0.0), lcl)


def thermal_top_columns(X):
    """thermal_top_agl over feature rows (temp_2m, humidity, pbl_height columns).

    The three inputs are columns 5, 6 and 19 of build_feature_matrix, so a
    buffer row carries everything needed to recompute its own thermal top.
    NaN where humidity is not positive (no dew point).
    """
    import numpy as np
    X = np.asarray(X, dtype=float)
    t, rh, pbl = X[:, 5], X[:, 6], X[:, 19]
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.log(np.clip(rh, None, 1.0)) + _MAGNUS_A * t / (_MAGNUS_B + t)
        td = _MAGNUS_B * g / (_MAGNUS_A - g)
    lcl = np.maximum((t - td) * _LCL_M_PER_C, 0.0)
    top = np.minimum(np.maximum(pbl, 0.0), lcl)
    return np.where(rh > 0, top, np.nan)


def _with_legacy_base(meteo: dict) -> dict:
    """Write the thermal top under the old `cape_base` key too, for a rollback.

    Nothing in this code reads `cape_base` any more -- it recomputes the top
    from the fields, so cache entries written under the old formula are read
    correctly.  But the historical cache is permanent and an older image reads
    `meteo["cape_base"]` from every entry: without the key its /seed would fail
    each beacon of every bucket cached after the switch.  It gets the new value,
    which is the better one for it as well.
    """
    top = thermal_top_agl(meteo)
    meteo["cape_base"] = 0 if top is None else int(round(top))
    return meteo


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


def _hist_cell(lat: float, lon: float) -> tuple[float, float]:
    return (round(round(lat / _HIST_SNAP) * _HIST_SNAP, 2),
            round(round(lon / _HIST_SNAP) * _HIST_SNAP, 2))


def _hist_key(lat: float, lon: float, dt: datetime) -> str:
    la, lo = _hist_cell(lat, lon)
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


def historical_cached(lat: float, lon: float, dt: datetime) -> bool:
    """Whether fetch_meteo_historical would answer this hour without a call."""
    return _hist_cache_get(_hist_key(lat, lon, dt)) is not None


def _hist_record(hourly: dict, i: int) -> dict:
    """
    The cache entry for slot i of an archive response.

    Both fetches build it here — the single hour and the range — so the two can
    never cache different weather for the same hour.
    """
    def _v(key, fb_key):
        val = hourly[key][i]
        return val if val is not None else _FALLBACK[fb_key]

    t850 = _v("temperature_850hPa", "temp_850")
    t500 = _v("temperature_500hPa", "temp_500")
    return _with_legacy_base({
        "temp_2m":    _v("temperature_2m",             "temp_2m"),
        "humidity":   _v("relativehumidity_2m",        "humidity_pct") / 100,
        "wind_speed": _v("windspeed_10m",              "wind_speed"),
        "wind_dir":   _v("winddirection_10m",          "wind_dir"),
        "cape":       _v("cape",                        "cape"),
        "cin":        _v("convective_inhibition",       "cin"),
        "solar_ghi":  _v("shortwave_radiation",         "solar_ghi"),
        "temp_850":   t850,
        "temp_500":   t500,
        "lapse_rate": (t850 - t500) / 3.5,
        "pbl_height": _v("boundary_layer_height",      "pbl_height"),
        "soil_temp":  _v("soil_temperature_0_to_7cm",  "soil_temp"),
    })


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
        record = _hist_record(r.json()["hourly"], hour_idx)
        _count_live(hit=False, historical=True)
        return _hist_cache_put(ckey, record)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.warning(
            f"[meteo] historical fetch failed ({lat:.2f},{lon:.2f}) "
            f"{date_str}h{hour_idx:02d} ({detail}) — "
            f"{'skipping' if strict else 'using fallback'}"
        )
        return None if strict else _FALLBACK.copy()


# --- many archived hours in one request ------------------------------------
#
# Open-Meteo bills a request by its span as well as its width: anything up to
# two weeks costs the same as a single day (1.1 calls for the 11 HOURLY_VARS),
# and every further fortnight costs that again.  fetch_meteo_historical asks for
# a whole day and keeps one hour of it, so a cell flown for nine hours of one
# day cost nine calls for one day's data.  Asking once per cell per fortnight
# is the same data for one call.  Measured rebuilding 28 Sep-6 Nov 2026 from
# the archive: 1,741 calls where hour-by-hour would have been ~17,000, and 477
# of 479 hours checked came back identical to the single-day answers — the two
# that differed were one hour Open-Meteo had revised since it was cached.
_HIST_RANGE_DAYS = 14


def plan_historical_ranges(points) -> list[tuple[float, float, list[datetime]]]:
    """
    Group archived hours into the fewest requests that bill as one.

    points: (lat, lon, dt) for every hour wanted.  Hours already cached are
    dropped; the rest are grouped by the 0.5 deg cell they are cached under and
    cut into spans of at most _HIST_RANGE_DAYS days.  Returns one
    (lat, lon, hours) per request, at the cell's snapped centre — where /seed
    already asks — with the requests that cover the most hours first, so a
    budget that runs out partway has bought as much weather as it could.
    """
    cells: dict[tuple[float, float], dict[str, datetime]] = {}
    for lat, lon, dt in points:
        key = _hist_key(lat, lon, dt)
        if _hist_cache_get(key) is None:
            cells.setdefault(_hist_cell(lat, lon), {})[key] = dt

    plan = []
    for (la, lo), hours in sorted(cells.items()):
        span: list[datetime] = []
        for dt in sorted(hours.values()):
            if span and (dt.date() - span[0].date()).days >= _HIST_RANGE_DAYS:
                plan.append((la, lo, span))
                span = []
            span.append(dt)
        plan.append((la, lo, span))
    plan.sort(key=lambda p: -len(p[2]))      # stable: ties stay in cell order
    return plan


async def fetch_meteo_historical_range(lat: float, lon: float, dts: list[datetime]) -> int:
    """
    Cache several archived hours of one 0.5 deg cell with a single request.

    Each hour lands under exactly the key, as exactly the record,
    fetch_meteo_historical would have written for it, which then answers it
    from the cache.  Only the hours asked for are kept: the response carries
    every hour of every day in the span, and the cache lives in memory whole.

    Returns how many hours were cached.  A failure caches nothing and raises
    nothing — those hours are simply still missing, and fetch_meteo_historical
    will try each one itself, strict as ever.
    """
    if not dts:
        return 0
    first, last = min(dts).date(), max(dts).date()
    if (last - first).days >= _HIST_RANGE_DAYS:
        raise ValueError(f"{first}..{last} is longer than {_HIST_RANGE_DAYS} days "
                         f"and would bill as more than one call")
    la, lo = _hist_cell(lat, lon)
    try:
        r = await _get_client().get(OPEN_METEO_HIST_BASE, params={
            "latitude":   la,
            "longitude":  lo,
            "hourly":     ",".join(HOURLY_VARS),
            "start_date": first.isoformat(),
            "end_date":   last.isoformat(),
            "timezone":   "UTC",
        })
        r.raise_for_status()
        _count_live(hit=False, historical=True)
        hourly = r.json()["hourly"]
        slot = {str(t)[:13]: i for i, t in enumerate(hourly["time"])}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        log.warning(f"[meteo] historical range failed ({la:.2f},{lo:.2f}) "
                    f"{first}..{last} ({detail}) — {len(dts)} hour(s) left to fetch singly")
        return 0

    cached = 0
    for dt in dts:
        key = _hist_key(la, lo, dt)
        i = slot.get(dt.strftime("%Y-%m-%dT%H"))
        if i is None or _hist_cache_get(key) is not None:
            continue
        try:
            record = _hist_record(hourly, i)
        except Exception:
            continue                 # left for the single-hour fetch to judge
        _hist_cache_put(key, record)
        cached += 1
    return cached
