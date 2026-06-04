// Must match backend/config.py
export const LAT_MIN  = 50.5;
export const LAT_MAX  = 52.5;
export const LON_MIN  = -1.5;
export const LON_MAX  =  2.0;

// Europe-wide bounding box for live glider display (must match backend config.py)
export const GLIDER_LAT_MIN = 35.0;
export const GLIDER_LAT_MAX = 72.0;
export const GLIDER_LON_MIN = -15.0;
export const GLIDER_LON_MAX = 45.0;
export const GRID_RES = 0.0005;   // degrees per cell (~50 m)

export const API_BASE = 'http://localhost:8000';
export const WS_URL   = 'ws://localhost:8000/ws/live';

export const MAP_CENTER = [(LAT_MIN + LAT_MAX) / 2, (LON_MIN + LON_MAX) / 2]; // [51.5, 0.25]
export const MAP_ZOOM   = 11;

// Must match backend fetch_elevation_grid default radius
export const PREDICT_RADIUS = 0.05; // degrees → ~11 × 7 km area, 200 × 200 cells at ~50 m each
