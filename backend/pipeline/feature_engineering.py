import numpy as np
from datetime import datetime, timezone
from config import GRID_RES

_CELL_SIZE_M = GRID_RES * 111_320  # metres per degree (latitude direction, ~55 m at GRID_RES=0.0005°)

# Width of one feature row. Buffers written under a different width describe a
# different feature space and cannot be mixed, so ThermalModel.load() checks
# against this rather than a literal that would silently drift.
FEATURE_COUNT = 22

# Land-use heat/albedo lookup by CORINE-style class (placeholder values)
_LAND_USE = {
    "urban":      (1.0, 0.15),
    "bare_rock":  (0.9, 0.20),
    "farmland":   (0.5, 0.25),
    "forest":     (0.2, 0.12),
    "water":      (0.0, 0.06),
    "default":    (0.4, 0.20),
}

def _slope_aspect(elev_grid: np.ndarray, cell_size_m: float = _CELL_SIZE_M,
                  lat_deg: float | None = None):
    """
    Return (slope_deg, aspect_deg) arrays from an elevation grid.

    Grid convention: row index increases NORTH, column index increases EAST.
    Aspect is the compass bearing the slope faces — i.e. the downhill direction —
    with 0=N, 90=E, 180=S, 270=W.

    cell_size_m is the north-south cell size.  A degree of longitude shrinks as
    cos(latitude), so the east-west size is scaled by cos(lat_deg); pass
    lat_deg=None to skip the correction (square cells).
    """
    ns_m = cell_size_m
    ew_m = cell_size_m if lat_deg is None else max(
        cell_size_m * float(np.cos(np.radians(lat_deg))), 1e-6   # guard near the poles
    )
    dy, dx = np.gradient(elev_grid, ns_m, ew_m)
    slope = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))
    # Downhill vector is -grad, so the bearing is atan2(east, north) = atan2(-dx, -dy).
    # Negating only dx mirrors the result north-south and reports shaded northern
    # slopes as sun-facing.
    aspect = np.degrees(np.arctan2(-dx, -dy)) % 360
    return slope, aspect

def _wind_components(speed: float, direction_deg: float):
    rad = np.radians(direction_deg)
    u = -speed * np.sin(rad)
    v = -speed * np.cos(rad)
    return u, v

def _cyclic(value: float, period: float):
    angle = 2 * np.pi * value / period
    return np.sin(angle), np.cos(angle)

def build_feature_matrix(meteo: dict, elev_grid: np.ndarray,
                         land_use: str = "default",
                         dt: datetime | None = None,
                         lat_bounds: tuple[float, float] | None = None,
                         lon_bounds: tuple[float, float] | None = None,
                         alt_amsl: float | None = None) -> np.ndarray:
    """
    Build a flat (N, FEATURE_COUNT) feature matrix — one row per grid cell.

    Features (columns):
        0  lat
        1  lon
        2  elevation
        3  slope
        4  aspect
        5  temp_2m
        6  humidity
        7  wind_u
        8  wind_v
        9  cape
        10 cin
        11 solar_ghi
        12 lapse_rate
        13 land_use_heat
        14 land_use_albedo
        15 hour_sin
        16 hour_cos
        17 day_of_year_sin
        18 day_of_year_cos
        19 pbl_height
        20 soil_temp
        21 alt_agl

    Pass lat_bounds=(south, north) and lon_bounds=(west, east) to embed real
    geographic coordinates; otherwise row/column indices are used as placeholders.

    alt_amsl is the observer's altitude above sea level — the glider's altitude
    when training, the pilot's when predicting.  It is converted to height above
    ground per cell, because lift depends on how far you are above the terrain
    that generated it, not on your altimeter reading.  That also makes the column
    vary across the grid rather than being one constant.  Omit it for a
    ground-level (0 m AGL) matrix.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    rows, cols = elev_grid.shape

    lats = np.linspace(lat_bounds[0], lat_bounds[1], rows) if lat_bounds else np.linspace(0, rows - 1, rows)
    lons = np.linspace(lon_bounds[0], lon_bounds[1], cols) if lon_bounds else np.linspace(0, cols - 1, cols)
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")

    # Longitude cells narrow with latitude — scale east-west spacing accordingly
    slope, aspect = _slope_aspect(
        elev_grid, lat_deg=(sum(lat_bounds) / 2 if lat_bounds else None)
    )
    wind_u, wind_v = _wind_components(meteo["wind_speed"], meteo["wind_dir"])
    heat, albedo = _LAND_USE.get(land_use, _LAND_USE["default"])

    hour_sin, hour_cos = _cyclic(dt.hour + dt.minute / 60, 24)
    doy_sin, doy_cos   = _cyclic(dt.timetuple().tm_yday, 365)

    # Height above the ground under each cell. Clipped at 0: a reported altitude
    # below the SRTM surface is a sensor/terrain-resolution artefact, not a glider
    # underground, and a negative height would be a nonsense feature value.
    alt_agl = (
        np.zeros_like(elev_grid) if alt_amsl is None
        else np.clip(float(alt_amsl) - elev_grid, 0.0, None)
    )

    n = rows * cols
    mat = np.column_stack([
        lat_grid.ravel(),                       # 0  lat
        lon_grid.ravel(),                       # 1  lon
        elev_grid.ravel(),                      # 2  elevation
        slope.ravel(),                          # 3  slope
        aspect.ravel(),                         # 4  aspect
        np.full(n, meteo["temp_2m"]),           # 5  temp_2m
        np.full(n, meteo["humidity"]),          # 6  humidity
        np.full(n, wind_u),                     # 7  wind_u
        np.full(n, wind_v),                     # 8  wind_v
        np.full(n, meteo["cape"]),              # 9  cape
        np.full(n, meteo["cin"]),               # 10 cin
        np.full(n, meteo["solar_ghi"]),         # 11 solar_ghi
        np.full(n, meteo["lapse_rate"]),        # 12 lapse_rate
        np.full(n, heat),                       # 13 land_use_heat
        np.full(n, albedo),                     # 14 land_use_albedo
        np.full(n, hour_sin),                   # 15 hour_sin
        np.full(n, hour_cos),                   # 16 hour_cos
        np.full(n, doy_sin),                    # 17 day_of_year_sin
        np.full(n, doy_cos),                    # 18 day_of_year_cos
        np.full(n, meteo["pbl_height"]),        # 19 pbl_height
        np.full(n, meteo["soil_temp"]),         # 20 soil_temp
        alt_agl.ravel(),                        # 21 alt_agl
    ])
    return mat
