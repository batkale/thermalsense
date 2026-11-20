// Primary operating area — İnönü / Eskişehir, the main Turkish soaring region.
// This is the *prediction grid*, not the glider feed: GRID_COLS/GRID_ROWS in
// useBackend.js are derived from it at GRID_RES (~50 m), so widening it explodes
// the grid (a worldwide box at this resolution is 720,000 × 360,000 cells).
// Live gliders are bounded separately by GLIDER_* below.
export const LAT_MIN  = 39.0;
export const LAT_MAX  = 40.5;
export const LON_MIN  = 29.5;
export const LON_MAX  = 31.5;

// Bounding box for live glider display — worldwide (must match backend config.py).
// Kept as an explicit box rather than deleted so a regional deploy can narrow it
// in one place; at these values the check in useBackend.js admits everything.
export const GLIDER_LAT_MIN = -90.0;
export const GLIDER_LAT_MAX = 90.0;
export const GLIDER_LON_MIN = -180.0;
export const GLIDER_LON_MAX = 180.0;
export const GRID_RES = 0.0005;   // degrees per cell (~50 m)

// OGN aircraft types that gain height on lift alone: glider, hang glider,
// paraglider. Mirrors SOARING_AC_TYPES in backend/data/ogn_client.py. Anything
// that turns "circling and climbing" into lift on screen must test against this
// *and* against under_tow — see isThermalEvidence in useBackend.js.
export const SOARING_AC_TYPES = new Set([0x1, 0x6, 0x7]);

// --- Backend origin -----------------------------------------------------------
// Single-container deploy: the API shares the page's origin, so API_BASE is ''
// and requests go to relative paths. Split deploy or local dev: point
// VITE_API_BASE at the backend (see frontend/.env.example).
function resolveApiBase() {
  const explicit = import.meta.env.VITE_API_BASE;
  if (explicit) return explicit.replace(/\/+$/, '');
  return import.meta.env.PROD ? '' : 'http://localhost:8000';
}

export const API_BASE = resolveApiBase();

// Derive the socket URL from whatever origin the API lives on. The http→ws
// rewrite also maps https→wss, which matters because a page served over HTTPS
// cannot open a plain ws:// socket — browsers block it as mixed content.
function resolveWsUrl() {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  const origin = API_BASE || window.location.origin;
  return `${origin.replace(/^http/, 'ws')}/ws/live`;
}

export const WS_URL = resolveWsUrl();

// Optional admin token for the retrain button. NOTE: anything in a VITE_ var is
// compiled into the public JS bundle — it deters drive-by abuse, it is not a
// secret. Leave it unset in production (the button hides itself) and trigger
// retraining with curl instead.
export const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN ?? '';

// Opening view: Türkiye. The map used to open on the European soaring belt,
// where the worldwide feed's traffic is — but a demo that opens on foreign
// gliders over an empty Anatolia answers the wrong question for a Turkish
// audience (TUSAŞ HANGAR mentor review, Nov 2026). It now opens on Türkiye and
// scans HOME_SCAN straight away (App.jsx), so the first thing on screen is a
// filled heatmap over İnönü rather than a blank map waiting for a click.
// Zoom 6 shows the whole country on a laptop-width window; ScanFocusController
// then flies in to the scan, because at zoom 6 the 11 km grid is a few pixels.
export const MAP_CENTER = [39.0, 35.0];
export const MAP_ZOOM   = 6;

// İnönü havaalanı (THK yelken kanat merkezi), the primary operating area.
export const HOME_SCAN = { lat: 39.8167, lon: 30.1167 };
// Türkiye keeps UTC+3 all year (no DST since 2016), so the opening scan can pick
// a daylight hour without a zone lookup. Between these local hours it scans now;
// outside them it asks for the next local noon, because a night scan is zeroed
// by the night guard and would open the demo on an empty map.
export const HOME_UTC_OFFSET_H = 3;
export const HOME_DAY_HOURS    = [9, 16];

// Forecast slider stops, in hours ahead. Every 15 minutes across the first three
// hours — the window a pilot is choosing a launch time in, where a whole hour is
// too coarse — then hourly out to +23. The slider moves by index through this
// list, so the near hours get proportionally more of its travel. Open-Meteo is
// hourly; the backend interpolates the quarter hours between its fields and
// accepts any multiple of 0.25 up to 24.
export const FORECAST_STEPS = [
  ...Array.from({ length: 12 }, (_, i) => i * 0.25),   // 0, 0.25 … 2.75
  ...Array.from({ length: 21 }, (_, i) => 3 + i),      // 3, 4 … 23
];

// --- Clouds now ---------------------------------------------------------------------
// Where the clouds are, drawn by us rather than shown as a photograph. Two of
// EUMETSAT's derived Meteosat products are read as *data*, straight from the
// browser (public WMS, no key, CORS open — the backend never holds them and
// Open-Meteo's quota is untouched):
//   Cloud Mask (msg_fes:clm)       — cloud / clear land / clear sea per pixel.
//                                    Served as white / green / blue, resampled,
//                                    so the darkest channel of a pixel is its
//                                    cloud fraction.
//   Cloud Top Height (msg_fes:cth) — a colour ramp from 320 m to 16 km; decoded
//                                    back to metres with CLOUD_TOP_RAMP.
// MSG at 0 degrees, every 15 min, ~3 km at nadir (~4-5 km over Europe and
// Türkiye), infrared-based, so it works at night too. Its disc ends near
// ±75° longitude: the Americas and the far east of Asia get no clouds drawn,
// which is "unknown", never "clear".
//
// Every request names its frame time. Without one the server answers "latest"
// with Cache-Control: max-age=604800, and on 19 Nov 2026 a GeoColour request
// came back someone else's cached render of a different hour.
export const CLOUD_WMS        = 'https://view.eumetsat.int/geoserver/wms';
export const CLOUD_MASK_LAYER = 'msg_fes:clm';
export const CLOUD_TOP_LAYER  = 'msg_fes:cth';
// Per-layer capabilities (a few KB, not the 280 KB global one): the time
// dimension's default is the newest frame. The two products are not always on
// the same frame — on 20 Nov 2026 the height was 15 min ahead of the mask.
export const CLOUD_CAPS = (layer) => {
  const [ws, name] = layer.split(':');
  return `https://view.eumetsat.int/geoserver/${ws}/${name}/ows`
       + '?service=WMS&request=GetCapabilities&version=1.3.0';
};
export const CLOUD_REFRESH_MS = 5 * 60 * 1000;
// One GetMap per product per view, at most this many pixels on a side. The
// clouds are drawn on a lattice of CLOUD_PUFF_PX, so more data than that is
// never looked at.
export const CLOUD_MAX_PX = 256;
// Nor finer than this many metres of ground per pixel: the data's own size.
export const CLOUD_NATIVE_M = 3000;
// The data is requested this much wider than the view on every side, so a pan
// redraws from data already in hand instead of leaving a blank edge.
export const CLOUD_MARGIN = 0.3;
// Spacing of the illustration's lattice, in screen pixels. Each node that is
// cloudy becomes one puff; overlapping puffs make the scalloped outline.
export const CLOUD_PUFF_PX = 18;
// Cloud-top bands, metres above sea level: below LOW the cloud is drawn as
// cumulus puffs, from HIGH up as a cirrus veil with streaks, between as a
// flat layer. A thick high top (a storm) is drawn as a veil too — the two
// products cannot tell it from cirrus.
export const CLOUD_LOW_TOP_M  = 3500;
export const CLOUD_HIGH_TOP_M = 7000;
// msg_fes:cth's colour ramp, read off its GetLegendGraphic on 20 Nov 2026:
// [metres, r, g, b], 41 stops, linear between 320 m and 16,000 m.
export const CLOUD_TOP_RAMP = [
  [320, 0, 0, 0], [710, 53, 23, 0], [1100, 83, 37, 0], [1490, 122, 36, 42],
  [1880, 146, 24, 94], [2280, 174, 9, 154], [2670, 167, 0, 201], [3060, 117, 0, 216],
  [3450, 47, 0, 239], [3840, 4, 1, 253], [4230, 0, 45, 255], [4620, 0, 76, 255],
  [5010, 0, 121, 255], [5410, 0, 163, 255], [5800, 0, 194, 255], [6190, 0, 244, 255],
  [6580, 0, 248, 226], [6970, 0, 224, 128], [7360, 0, 207, 67], [7750, 10, 194, 0],
  [8140, 52, 204, 0], [8530, 83, 212, 0], [8930, 134, 225, 0], [9320, 159, 231, 0],
  [9710, 208, 243, 0], [10100, 237, 250, 0], [10490, 254, 226, 0], [10880, 254, 184, 0],
  [11270, 254, 152, 0], [11660, 254, 102, 0], [12060, 254, 77, 0], [12450, 254, 28, 0],
  [12840, 250, 11, 8], [13230, 219, 57, 57], [13620, 191, 108, 108], [14010, 166, 150, 150],
  [14400, 181, 181, 181], [14790, 193, 193, 193], [15190, 218, 218, 218],
  [15580, 230, 230, 230], [15970, 255, 255, 255],
];

// Must match backend fetch_elevation_grid default radius (config.py GRID_RADIUS).
// The grid spans centre ± this on both axes, inclusive of both edges, so it is
// 201 × 201 sample points at exactly GRID_RES apart — use rows-1/cols-1 when
// converting an index back to a coordinate.
export const PREDICT_RADIUS = 0.05; // degrees → ~11 × 7 km area at ~50 m per cell
