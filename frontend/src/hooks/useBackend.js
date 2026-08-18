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
  const [predictAlt,  setPredictAlt]  = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const wsRef             = useRef(null);
  const gliderPathsRef    = useRef({});   // { [id]: [{lat, lon, alt}] }
  const seededIdsRef      = useRef(new Set());
  const boundsRef         = useRef(null); // latest map viewport, resent on reconnect

  /**
   * Tell the backend which patch of the world this client is looking at, so it
   * streams only the gliders on screen instead of every one on the planet.
   * Held in a ref as well as sent, because a reconnect needs to re-declare the
   * viewport — the server starts each socket unfiltered.
   */
  const setViewport = useCallback((bounds) => {
    boundsRef.current = bounds;
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ bounds }));
    }
  }, []);

  /**
   * Backfill one glider's flight so far from SQLite, once.
   *
   * Called when a glider is pinned, not when it appears. Only the pinned
   * glider's path is ever drawn, and seeding on sight meant a viewer opening
   * the map fired one /ogn/track request per glider in view — measured at ~480
   * at once against a beacon table holding a week of the worldwide feed. That
   * buried the event loop for a minute at a time, which starved /ws/live of the
   * very frames the tracks were being fetched to decorate.
   */
  const seedTrack = useCallback((id) => {
    const seeded = seededIdsRef.current;
    if (!id || seeded.has(id)) return;
    seeded.add(id);
    const paths = gliderPathsRef.current;
    if (!paths[id]) paths[id] = [];
    fetchGliderTrack(id)
      .then(track => {
        if (track.length === 0) return;
        const live = paths[id] || [];
        // Drop any live positions that duplicate the tail of the historical track
        // (beacon buffer and SQLite are written independently so the last few
        // positions can appear in both — a duplicate causes a visible kink)
        const lastHist = track[track.length - 1];
        const deduped = live.filter(
          p => Math.abs(p.lat - lastHist.lat) > 1e-6 || Math.abs(p.lon - lastHist.lon) > 1e-6
        );
        paths[id] = [...track, ...deduped];
      })
      // Re-arm on failure: the live tail still draws, and pinning again retries.
      .catch(() => { seeded.delete(id); });
  }, []);

  const predict = useCallback(async (lat, lon, forecastH = 0, alt = null) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPrediction(lat, lon, forecastH, alt);

      setHeatmap(data.heatmap);
      // Draw at the grid's own centre, not the point we asked about. The backend
      // snaps the request onto the terrain lattice (up to ~550 m), and drawing
      // the cells anywhere else puts every prediction on the wrong ground.
      // Fall back to the request point only for a backend too old to report it.
      setGridMeta({
        lat:    data.grid_lat ?? lat,
        lon:    data.grid_lon ?? lon,
        rows:   data.rows,
        cols:   data.cols,
        radius: PREDICT_RADIUS,
      });
      // The height the heatmap answers for. A map click leaves it to the backend
      // (mean terrain plus a working height), a glider click sends that glider's
      // own altitude — so the same spot legitimately returns two different maps.
      // Only the backend knows the derived value, so it is read, never computed.
      // Null for a backend too old to report it, which just hides the row.
      setPredictAlt(
        data.alt_amsl != null
          ? { amsl: data.alt_amsl, agl: data.alt_agl, source: data.alt_source }
          : null
      );
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

      ws.onopen = () => {
        if (boundsRef.current) ws.send(JSON.stringify({ bounds: boundsRef.current }));
      };

      ws.onmessage = (evt) => {
        try {
          const { gliders: raw, new_positions = {} } = JSON.parse(evt.data);
          const paths = gliderPathsRef.current;

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

  return { heatmap, gridMeta, gliders, activeThermals, weather, predictAlt,
           loading, error,
           predict, setViewport, seedTrack, gliderPathsRef };
}
