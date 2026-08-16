"""
Solar geometry: sun position and per-cell terrain illumination.

The feature matrix already carries slope, aspect, hour and day-of-year, but
never combines them, so a south-facing slope at 14:00 in August and a
north-facing one look identical on the solar axis.  solar_ghi is a single
point value replicated across every cell, which means the model's only route
to spatial variation is the hand-tuned _terrain_multiplier in thermal_model.

cos(incidence) is the physically correct per-cell quantity: the cosine of the
angle between the sun vector and the terrain normal, i.e. the fraction of the
beam a tilted surface intercepts.  Flat ground at solar noon approaches 1; a
surface turned away from the sun goes to 0.  Multiplying it by solar_ghi gives
actual per-cell insolation, which is what drives a thermal.

No new dependency and no API call: everything comes from columns the matrix
already has.
"""

import numpy as np

# NOAA General Solar Position Calculations.  Accurate to well under a degree
# over the range that matters here, which is far finer than the ~1km scale the
# rest of the pipeline resolves.


def _fractional_year(doy: np.ndarray, hour_utc: np.ndarray) -> np.ndarray:
    return 2 * np.pi / 365.0 * (doy - 1 + (hour_utc - 12) / 24.0)


def _equation_of_time(gamma: np.ndarray) -> np.ndarray:
    """Minutes of offset between apparent and mean solar time."""
    return 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )


def _declination(gamma: np.ndarray) -> np.ndarray:
    """Solar declination in radians."""
    return (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.001480 * np.sin(3 * gamma)
    )


def sun_position(lat_deg, lon_deg, doy, hour_utc):
    """
    Solar elevation and azimuth in degrees, vectorised over array inputs.

    hour_utc is decimal hours UTC; lon_deg is positive east.  Azimuth is a
    compass bearing (0=N, 90=E), matching the aspect convention in
    feature_engineering._slope_aspect so the two can be differenced directly.
    Elevation is negative when the sun is below the horizon.
    """
    lat_deg = np.asarray(lat_deg, dtype=float)
    lon_deg = np.asarray(lon_deg, dtype=float)
    doy = np.asarray(doy, dtype=float)
    hour_utc = np.asarray(hour_utc, dtype=float)

    gamma = _fractional_year(doy, hour_utc)
    decl = _declination(gamma)

    # True solar time.  4 minutes per degree of longitude; no timezone term
    # because the pipeline is UTC throughout.
    tst = (hour_utc * 60 + _equation_of_time(gamma) + 4 * lon_deg) % 1440
    hour_angle = np.radians(tst / 4 - 180)

    lat = np.radians(lat_deg)

    # Sun direction in the local east-north-up frame.  Deriving azimuth from
    # arccos instead needs a separate mirror for the afternoon and is singular
    # near the zenith; atan2 over the horizontal components is unambiguous
    # everywhere and needs no correction.
    east = -np.cos(decl) * np.sin(hour_angle)
    north = (np.sin(decl) * np.cos(lat)
             - np.cos(decl) * np.sin(lat) * np.cos(hour_angle))
    up = np.clip(
        np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(hour_angle),
        -1.0, 1.0,
    )

    elevation = np.degrees(np.arcsin(up))
    azimuth = np.degrees(np.arctan2(east, north)) % 360.0
    return elevation, azimuth


def cos_incidence(slope_deg, aspect_deg, sun_elev_deg, sun_az_deg):
    """
    Cosine of the sun-to-surface incidence angle, clipped to [0, 1].

    aspect_deg follows feature_engineering: the compass bearing the surface
    faces (downhill direction), so the surface normal tilts toward it.

    Zero when the sun is below the horizon and zero where the surface is turned
    far enough away to be self-shadowed.  This is self-shadowing only — it does
    not account for a neighbouring ridge casting a shadow, which needs a
    horizon trace across the elevation grid rather than a per-cell calculation.
    """
    slope = np.radians(np.asarray(slope_deg, dtype=float))
    aspect = np.radians(np.asarray(aspect_deg, dtype=float))
    elev = np.radians(np.asarray(sun_elev_deg, dtype=float))
    azim = np.radians(np.asarray(sun_az_deg, dtype=float))

    ci = (np.cos(slope) * np.sin(elev)
          + np.sin(slope) * np.cos(elev) * np.cos(azim - aspect))
    # Night clamps to 0 rather than a small negative: no illumination is no
    # illumination, and a negative would read as "less than shadow".
    return np.clip(np.where(elev <= 0, 0.0, ci), 0.0, 1.0)
