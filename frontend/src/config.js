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

// --- Satellite clouds -----------------------------------------------------------
// EUMETSAT's public WMS (EUMETView): no key, no fee, CORS open. Requested straight
// from the browser, so the backend never holds an image. GeoColour is true colour
// by day (cumulus show as white puffs over the ground) and infrared cloud over a
// static night-lights background after dark. Meteosat MTG-I, every 10 minutes,
// ~1 km at nadir and coarser over Türkiye, which it sees ~48° off the
// sub-satellite point. The newest frame was ~30 minutes old when measured.
//
// Every image request names its frame time. Without one the server answers
// "latest" with Cache-Control: max-age=604800, and on 19 Nov 2026 such a request
// came back a sunlit scene at 07:20 local in November — someone else's cached
// render — where the 04:20 UTC frame named explicitly was the night it was.
export const SATELLITE_WMS   = 'https://view.eumetsat.int/geoserver/wms';
export const SATELLITE_LAYER = 'mtg_fd:rgb_geocolour';
// The per-layer capabilities document (~6 KB, not the 280 KB global one): its
// time dimension's default is the newest frame.
export const SATELLITE_CAPS  = 'https://view.eumetsat.int/geoserver/mtg_fd/rgb_geocolour/ows'
                             + '?service=WMS&request=GetCapabilities&version=1.3.0';
export const SATELLITE_REFRESH_MS = 5 * 60 * 1000;
// Opaque imagery: enough of the base map shows through to keep roads and ridges
// legible, while white cloud still reads as cloud.
export const SATELLITE_OPACITY = 0.8;
// One GetMap image per view, at most this many pixels on a side.
export const SATELLITE_MAX_PX = 2048;
// Nor finer than this many metres of ground per pixel. The data is ~1 km at
// nadir, and the server upsamples nearest-neighbour (its `interpolations`
// parameter changed nothing, 19 Nov 2026), so zoomed in to a scan a full-size
// request drew 2 km squares. Asking for native pixels and letting the canvas
// smooth them shows the same information without inventing hard edges.
export const SATELLITE_NATIVE_M = 1000;

// Must match backend fetch_elevation_grid default radius (config.py GRID_RADIUS).
// The grid spans centre ± this on both axes, inclusive of both edges, so it is
// 201 × 201 sample points at exactly GRID_RES apart — use rows-1/cols-1 when
// converting an index back to a coordinate.
export const PREDICT_RADIUS = 0.05; // degrees → ~11 × 7 km area at ~50 m per cell
