import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { fetchPrediction, fetchGliderTrack } from '../services/api.js';
import { LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, GRID_RES, PREDICT_RADIUS, WS_URL, GLIDER_LAT_MIN, GLIDER_LAT_MAX, GLIDER_LON_MIN, GLIDER_LON_MAX } from '../config.js';

const GRID_COLS = Math.round((LON_MAX - LON_MIN) / GRID_RES);
const GRID_ROWS = Math.round((LAT_MAX - LAT_MIN) / GRID_RES);

/**
 * Convert a real-world lat/lon to grid x/y within the configured bounding box.
 * x grows east, y grows south (screen convention).
 */
function clusterCirclingGliders(gliders, radiusDeg = 0.012) {
  const circling = gliders.filter(g => g.circling && g.vario > 0.5);
  const unclustered = [...circling];
  const clusters = [];
  while (unclustered.length > 0) {
    const seed = unclustered.shift();
    const members = [seed];
    for (let i = unclustered.length - 1; i >= 0; i--) {
      const g = unclustered[i];
      if (Math.abs(g.lat - seed.lat) < radiusDeg && Math.abs(g.lon - seed.lon) < radiusDeg) {
        members.push(...unclustered.splice(i, 1));
      }
    }
    const n = members.length;
    clusters.push({
      lat:       members.reduce((s, g) => s + g.lat,   0) / n,
      lon:       members.reduce((s, g) => s + g.lon,   0) / n,
      avg_vario: members.reduce((s, g) => s + g.vario, 0) / n,
      count:     n,
      est_alt_m: Math.round(members.reduce((s, g) => s + g.alt, 0) / n),
    });
  }
  return clusters;
}

function latlonToGridXY(lat, lon) {
  const x = Math.round((lon - LON_MIN) / (LON_MAX - LON_MIN) * (GRID_COLS - 1));
  const y = Math.round((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * (GRID_ROWS - 1));
  return {
    x: Math.max(0, Math.min(GRID_COLS - 1, x)),
    y: Math.max(0, Math.min(GRID_ROWS - 1, y)),
  };
}

export function useBackend() {
  const [heatmap,     setHeatmap]     = useState(null);
  const [gridMeta,    setGridMeta]    = useState(null);
  const [gliders,     setGliders]     = useState([]);
  const [weather,     setWeather]     = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const wsRef             = useRef(null);
  const gliderPathsRef    = useRef({});   // { [id]: [{lat, lon, alt}] }
  const seededIdsRef      = useRef(new Set());

  const predict = useCallback(async (lat, lon, forecastH = 0, alt = null) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPrediction(lat, lon, forecastH, alt);

      setHeatmap(data.heatmap);
      setGridMeta({ lat, lon, rows: data.rows, cols: data.cols, radius: PREDICT_RADIUS });
      setWeather(data.weather);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Live glider WebSocket — auto-reconnects on close
  useEffect(() => {
    let cancelled = false;

    function connect() {
      if (cancelled) return;
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onmessage = (evt) => {
        try {
          const { gliders: raw, new_positions = {} } = JSON.parse(evt.data);
          const paths  = gliderPathsRef.current;
          const seeded = seededIdsRef.current;

          // Append every intermediate beacon the backend buffered since last frame.
          // This is what makes thermalling spirals visible — we get all positions,
          // not just the one snapshot that happened to be latest when the WS polled.
          for (const [id, positions] of Object.entries(new_positions)) {
            if (!paths[id]) paths[id] = [];
            for (const pos of positions) paths[id].push(pos);
            if (paths[id].length > 2000) paths[id] = paths[id].slice(-2000);
          }

          const european = raw.filter(g =>
            g.lat >= GLIDER_LAT_MIN && g.lat <= GLIDER_LAT_MAX &&
            g.lon >= GLIDER_LON_MIN && g.lon <= GLIDER_LON_MAX
          );

          // Seed historical track from SQLite the first time we see a glider
          for (const g of european) {
            if (!seeded.has(g.id)) {
              seeded.add(g.id);
              if (!paths[g.id]) paths[g.id] = [];
              fetchGliderTrack(g.id).then(track => {
                if (track.length === 0) return;
                const live = paths[g.id] || [];
                // Drop any live positions that duplicate the tail of the historical track
                // (beacon buffer and SQLite are written independently so the last few
                // positions can appear in both — a duplicate causes a visible kink)
                const lastHist = track[track.length - 1];
                const deduped = live.filter(
                  p => Math.abs(p.lat - lastHist.lat) > 1e-6 || Math.abs(p.lon - lastHist.lon) > 1e-6
                );
                paths[g.id] = [...track, ...deduped];
              });
            }
          }

          setGliders(european.map(g => ({ ...g, gridPos: latlonToGridXY(g.lat, g.lon) })));
        } catch { /* ignore malformed frames */ }
      };

      ws.onerror  = () => ws.close();
      ws.onclose  = () => { if (!cancelled) setTimeout(connect, 5000); };
    }

    connect();
    return () => {
      cancelled = true;
      wsRef.current?.close();
    };
  }, []);

  const activeThermals = useMemo(() => clusterCirclingGliders(gliders), [gliders]);

  return { heatmap, gridMeta, gliders, activeThermals, weather, loading, error, predict, gliderPathsRef };
}
