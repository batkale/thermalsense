import { API_BASE, ADMIN_TOKEN } from '../config.js';

/**
 * alt is the observer's altitude in metres AMSL — the clicked glider's own
 * altitude. Omitting it makes the backend assume a typical working height above
 * the terrain; sending a wrong one is worse than sending none, because the model
 * is trained on real glider altitudes and answers "is there lift *here*".
 */
export async function fetchPrediction(lat, lon, forecastH = 0, alt = null) {
  const params = new URLSearchParams({ lat, lon, forecast_h: forecastH });
  if (alt != null) params.set('alt', Math.round(alt));
  const res = await fetch(`${API_BASE}/predict?${params}`);
  if (!res.ok) throw new Error(`Predict failed: ${res.statusText}`);
  return res.json(); // { heatmap: float[], thermal_base: int, cape: float }
}

/**
 * The scan patch's real relief for the 3D view: ~100 m Copernicus samples over
 * the same snapped centre and radius as the heatmap, south-west first.
 * { grid_lat, grid_lon, radius, rows, cols, elevation: int[], source, filled }.
 * Throws on 503 (no terrain) and on a backend too old to have the endpoint;
 * the caller then draws the 121 samples /predict sent.
 */
export async function fetchRelief(lat, lon, signal) {
  const params = new URLSearchParams({ lat, lon });
  const res = await fetch(`${API_BASE}/terrain/relief?${params}`, { signal });
  if (!res.ok) throw new Error(`Relief failed: ${res.statusText}`);
  return res.json();
}

export async function fetchElevation(lat, lon) {
  const params = new URLSearchParams({ lat, lon });
  const res = await fetch(`${API_BASE}/elevation?${params}`);
  if (!res.ok) throw new Error(`Elevation failed: ${res.statusText}`);
  return res.json(); // { elevation: int | null }
}

/**
 * The sky and the local clock at one point, without running the model — for
 * the pinned glider card, which follows an aircraft rather than the last scan.
 * { condition, is_day, utc_offset_s, timezone, target_at, temp_2m, meteo_fallback }
 */
export async function fetchWeather(lat, lon, forecastH = 0) {
  const params = new URLSearchParams({ lat, lon, forecast_h: forecastH });
  const res = await fetch(`${API_BASE}/weather?${params}`);
  if (!res.ok) throw new Error(`Weather failed: ${res.statusText}`);
  return res.json();
}

export async function fetchGliderTrack(gliderId) {
  const res = await fetch(`${API_BASE}/ogn/track/${encodeURIComponent(gliderId)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.track ?? [];
}

/**
 * Glider id search across the whole live feed. Runs server-side because the
 * live socket only carries gliders inside the current viewport, so the client
 * no longer has a full list to filter.
 */
export async function searchGliders(query, limit = 3) {
  const params = new URLSearchParams({ q: query, limit });
  const res = await fetch(`${API_BASE}/ogn/search?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.gliders ?? [];
}

/**
 * One aircraft's current state by exact id, independent of the viewport the
 * live socket is scoped to. Resolves to null when the feed no longer carries
 * it — landed, or aged out — which is a normal outcome, not an error.
 */
export async function fetchGlider(gliderId) {
  const res = await fetch(`${API_BASE}/ogn/glider/${encodeURIComponent(gliderId)}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.glider ?? null;
}

export async function triggerRetrain() {
  const res = await fetch(`${API_BASE}/train`, {
    method: 'POST',
    headers: ADMIN_TOKEN ? { 'X-Admin-Token': ADMIN_TOKEN } : {},
  });
  if (!res.ok) throw new Error(`Retrain failed: ${res.statusText}`);
  return res.json(); // { status: 'started' | 'already_running' }
}

/**
 * Collection and training health for the operator panel. Read-only; each
 * section carries its own `error` field instead of failing the whole call.
 */
export async function fetchTrainingStatus() {
  const res = await fetch(`${API_BASE}/training/status`);
  if (!res.ok) throw new Error(`Training status failed: ${res.statusText}`);
  return res.json();
}

// One record per finished day: the model as it stood before that day, scored
// on it beside older copies. Changes once a day, so it is fetched once per open.
export async function fetchTrainingHistory(days = 30) {
  const res = await fetch(`${API_BASE}/training/history?days=${days}`);
  if (!res.ok) throw new Error(`Training history failed: ${res.statusText}`);
  return res.json();
}
