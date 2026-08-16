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

export async function fetchElevation(lat, lon) {
  const params = new URLSearchParams({ lat, lon });
  const res = await fetch(`${API_BASE}/elevation?${params}`);
  if (!res.ok) throw new Error(`Elevation failed: ${res.statusText}`);
  return res.json(); // { elevation: int | null }
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

export async function triggerRetrain() {
  const res = await fetch(`${API_BASE}/train`, {
    method: 'POST',
    headers: ADMIN_TOKEN ? { 'X-Admin-Token': ADMIN_TOKEN } : {},
  });
  if (!res.ok) throw new Error(`Retrain failed: ${res.statusText}`);
  return res.json(); // { status: 'started' | 'already_running' }
}
