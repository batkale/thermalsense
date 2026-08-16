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

export const MAP_CENTER = [39.8167, 30.1167]; // İnönü havaalanı (THK yelken kanat merkezi)
export const MAP_ZOOM   = 11;

// Must match backend fetch_elevation_grid default radius (config.py GRID_RADIUS).
// The grid spans centre ± this on both axes, inclusive of both edges, so it is
// 201 × 201 sample points at exactly GRID_RES apart — use rows-1/cols-1 when
// converting an index back to a coordinate.
export const PREDICT_RADIUS = 0.05; // degrees → ~11 × 7 km area at ~50 m per cell
