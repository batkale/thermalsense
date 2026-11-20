import { useEffect, useRef, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import L from 'leaflet';
import { useMap, useMapEvents } from 'react-leaflet';

import {
  SATELLITE_WMS, SATELLITE_LAYER, SATELLITE_CAPS, SATELLITE_REFRESH_MS,
  SATELLITE_OPACITY, SATELLITE_MAX_PX, SATELLITE_NATIVE_M,
} from '../config.js';
import { LAYER_BUILT } from './CompareLayer.jsx';

// -----------------------------------------------------------------------------
// Satellite clouds — the sky as Meteosat last saw it, under everything we draw
//
// Observed, never forecast: the frame is ~30 minutes old when it arrives and
// does not move with the forecast slider, and the sidebar says so. The backend
// is not involved — the browser asks EUMETSAT directly, so the image costs the
// 896 MB box nothing and Open-Meteo's quota nothing.
//
// One GetMap image per view rather than tiles. The comparison view clips each
// layer with clip-path on a box spanning the map, and a Leaflet tile layer's
// container is a zero-sized div inside the panning map pane, which that rule
// cannot clip. A canvas the size of the map can, like every other layer here.
// It belongs to the built half: no model input comes from it, so the raw half —
// the inputs as they arrive — has nothing of it to show.
// -----------------------------------------------------------------------------

// Web Mercator's limit. Leaflet's bounds run past it zoomed out; EPSG:3857
// projection clamps there, so the corners the image is drawn at must as well.
const MAX_LAT = 85.0511;
const clampLat = (lat) => Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
// Wait for the map to settle before asking for a new view.
const REQUEST_DEBOUNCE_MS = 250;

/**
 * The newest frame time, re-read every SATELLITE_REFRESH_MS while enabled.
 * { time: ISO string | null, failed: bool }.
 */
export function useSatelliteFrame(enabled) {
  const [frame, setFrame] = useState({ time: null, failed: false });

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const r = await fetch(SATELLITE_CAPS);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const doc = new DOMParser().parseFromString(await r.text(), 'text/xml');
        const dim = [...doc.getElementsByTagName('Dimension')]
          .find(d => d.getAttribute('name') === 'time');
        const time = dim?.getAttribute('default');
        if (!time || Number.isNaN(Date.parse(time))) throw new Error('no time dimension');
        if (!cancelled) setFrame({ time, failed: false });
      } catch {
        // Keep the frame already on screen: one failed refresh is not a reason
        // to blank the sky, and its age in the sidebar keeps counting up.
        if (!cancelled) setFrame(f => ({ time: f.time, failed: !f.time }));
      }
    };
    load();
    const id = setInterval(load, SATELLITE_REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [enabled]);

  return frame;
}

/**
 * The image itself. onStatus('loading' | 'ok' | 'error') reports each view's
 * request so the sidebar can say what is on screen.
 */
export function SatelliteCanvas({ time, onStatus }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  // The image on screen and the corners it was requested for. Kept while the
  // next one loads, so a pan or zoom shows the old view in place, not a gap.
  const shownRef  = useRef(null);
  const seqRef    = useRef(0);
  const timerRef  = useRef(null);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width, height } = map.getContainer().getBoundingClientRect();
    canvas.width  = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    const shown = shownRef.current;
    if (!shown) return;
    // Both the image and the map are Web Mercator, so placing two corners
    // places every pixel between them.
    const nw = map.latLngToContainerPoint(shown.nw);
    const se = map.latLngToContainerPoint(shown.se);
    ctx.globalAlpha = SATELLITE_OPACITY;
    // Zoomed in, the image holds fewer pixels than the screen (see request);
    // smoothing is what turns ~1 km samples back into cloud.
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(shown.img, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
  }, [map]);

  const request = useCallback(() => {
    const size = map.getSize();
    if (!time || size.x < 2 || size.y < 2) return;
    const b = map.getBounds();
    const north = clampLat(b.getNorth());
    const south = clampLat(b.getSouth());
    // Leaflet repeats the world sideways and reports longitudes on the copy in
    // view (390 for 30 E one copy over). The server only knows ±180, so ask
    // for the view shifted back onto it, and draw at the copy's own longitudes.
    const shift = 360 * Math.round(map.getCenter().lng / 360);
    const crs = map.options.crs;
    const sw = crs.project(L.latLng(south, b.getWest() - shift));
    const ne = crs.project(L.latLng(north, b.getEast() - shift));
    // Mercator metres shrink to cos(latitude) on the ground.
    const groundPerPx = ((ne.x - sw.x) / size.x) * Math.cos(map.getCenter().lat * Math.PI / 180);
    const scale = Math.min(1, SATELLITE_MAX_PX / Math.max(size.x, size.y),
                           groundPerPx / SATELLITE_NATIVE_M);
    const width  = Math.max(2, Math.round(size.x * scale));
    const height = Math.max(2, Math.round(size.y * scale));
    const params = new URLSearchParams({
      service: 'WMS', version: '1.3.0', request: 'GetMap',
      layers: SATELLITE_LAYER, styles: '',
      // JPEG blocks would be most of a tiny image; PNG costs little there.
      format: width * height <= 256 * 256 ? 'image/png' : 'image/jpeg',
      crs: 'EPSG:3857',
      bbox: [sw.x, sw.y, ne.x, ne.y].map(v => v.toFixed(0)).join(','),
      width: String(width),
      height: String(height),
      time,
    });
    const seq = ++seqRef.current;
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => {
      // A slower answer for a view the map has already left is dropped.
      if (seq !== seqRef.current) return;
      shownRef.current = { img, nw: [north, b.getWest()], se: [south, b.getEast()] };
      draw();
      statusRef.current?.('ok');
    };
    img.onerror = () => {
      if (seq !== seqRef.current) return;
      statusRef.current?.('error');
    };
    statusRef.current?.('loading');
    img.src = `${SATELLITE_WMS}?${params}`;
  }, [map, time, draw]);

  const schedule = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(request, REQUEST_DEBOUNCE_MS);
  }, [request]);

  // A new frame time, or the first mount: fetch at once.
  useEffect(() => {
    request();
    return () => { clearTimeout(timerRef.current); };
  }, [request]);

  // Unmounting abandons whatever is in flight.
  useEffect(() => () => { seqRef.current += 1; }, []);

  // Credit EUMETSAT while the imagery is on screen, as its data policy asks.
  // Legal credit, not UI copy, so it is not translated — like the tile credits.
  useEffect(() => {
    const ctl = map.attributionControl;
    if (!ctl) return undefined;
    const year = time ? new Date(time).getUTCFullYear() : new Date().getUTCFullYear();
    const text = `Clouds: Meteosat MTG GeoColour &copy; EUMETSAT ${year}`;
    ctl.addAttribution(text);
    return () => { ctl.removeAttribution(text); };
  }, [map, time]);

  useMapEvents({
    move:    draw,
    zoomend: () => { draw(); schedule(); },
    moveend: schedule,
    resize:  () => { draw(); schedule(); },
    zoomstart() {
      const canvas = canvasRef.current;
      if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    },
  });

  // z-index 400 is the map pane's own. Equal z-index paints in DOM order, and
  // this canvas is appended after the pane, so it lands directly on the base
  // imagery and under every overlay above it (thermal bubbles at 401 and up).
  // Anything below 400 is hidden under the opaque base tiles altogether.
  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_BUILT}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 400 }}
    />,
    map.getContainer()
  );
}
