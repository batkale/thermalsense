import { API_BASE, ADMIN_TOKEN } from '../config.js';

export async function fetchPrediction(lat, lon, forecastH = 0) {
  const params = new URLSearchParams({ lat, lon, forecast_h: forecastH });
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

export async function triggerRetrain() {
  const res = await fetch(`${API_BASE}/train`, {
    method: 'POST',
    headers: ADMIN_TOKEN ? { 'X-Admin-Token': ADMIN_TOKEN } : {},
  });
  if (!res.ok) throw new Error(`Retrain failed: ${res.statusText}`);
  return res.json(); // { status: 'started' | 'already_running' }
}
