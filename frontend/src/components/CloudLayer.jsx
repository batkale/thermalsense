import { useEffect, useRef, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import L from 'leaflet';
import { useMap, useMapEvents } from 'react-leaflet';

import {
  CLOUD_WMS, CLOUD_MASK_LAYER, CLOUD_TOP_LAYER, CLOUD_CAPS, CLOUD_REFRESH_MS,
  CLOUD_MAX_PX, CLOUD_NATIVE_M, CLOUD_MARGIN, CLOUD_PUFF_PX,
  CLOUD_LOW_TOP_M, CLOUD_HIGH_TOP_M, CLOUD_TOP_RAMP,
} from '../config.js';
import { LAYER_BUILT } from './CompareLayer.jsx';

// -----------------------------------------------------------------------------
// Clouds now — where Meteosat sees cloud, drawn as an illustration
//
// No satellite picture reaches the screen. Two derived products are fetched as
// data (see config.js): a cloud mask, giving how cloudy each ~3 km pixel is,
// and a cloud-top height. From those the layer draws its own clouds on a
// lattice anchored to the map:
//
//   low tops   (< CLOUD_LOW_TOP_M)   puffs, lit on top, shaded underneath,
//                                    casting a soft shadow. Low is all the
//                                    products say: cumulus, stratocumulus and
//                                    fog look alike to them
//   mid tops                         a flat translucent layer
//   high tops  (>= CLOUD_HIGH_TOP_M) a thin veil with cirrus streaks, on top
//
// Drawn the way the map draws its own marks — flat light shapes with a thin
// dark rim, in the app's ice-white and navy — and, like them, not themed: the
// map is satellite imagery in both modes (see the head of index.css).
//
// Observed, never forecast: the frame is ~20-30 min old when it arrives and
// does not move with the forecast slider; the sidebar says so. It belongs to
// the built half of the comparison view — no model input comes from it.
// -----------------------------------------------------------------------------

const MAX_LAT = 85.0511;
const clampLat = (lat) => Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
const REQUEST_DEBOUNCE_MS = 250;
// Pixels drawn per frame are bounded: a 4K screen at 2x would otherwise be a
// 30-megapixel canvas for a layer of soft shapes.
const MAX_RENDER_PIXELS = 6_000_000;

// The app's ice-white (--text-title) for lit cloud, a blue-grey underneath,
// and its navy (--bg-app) for rims and ground shadow.
const COLOURS = {
  body:     '#f2f7fb',
  shade:    '#b4c6d6',
  rim:      'rgba(8, 18, 27, 0.55)',
  layer:    '#d6e2ec',
  layerRim: 'rgba(8, 18, 27, 0.35)',
  veil:     '#e9f1f8',
  streak:   '#ffffff',
  shadow:   '#08121b',
};

// ---- frame time -------------------------------------------------------------

async function newestTime(layer) {
  const r = await fetch(CLOUD_CAPS(layer));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const doc = new DOMParser().parseFromString(await r.text(), 'text/xml');
  const dim = [...doc.getElementsByTagName('Dimension')]
    .find(d => d.getAttribute('name') === 'time');
  const time = dim?.getAttribute('default');
  if (!time || Number.isNaN(Date.parse(time))) throw new Error('no time dimension');
  return time;
}

/**
 * The newest frame of each product, re-read every CLOUD_REFRESH_MS while
 * enabled. { time, topTime, failed }: `time` is the cloud mask's frame, the one
 * the sidebar reports; `topTime` may be null, and the clouds are then drawn
 * without heights (all as cumulus).
 */
export function useCloudFrame(enabled) {
  const [frame, setFrame] = useState({ time: null, topTime: null, failed: false });

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const load = async () => {
      const [mask, top] = await Promise.allSettled([
        newestTime(CLOUD_MASK_LAYER), newestTime(CLOUD_TOP_LAYER),
      ]);
      if (cancelled) return;
      // A failed refresh keeps the frame already on screen: one bad request
      // is no reason to clear the sky, and its age keeps counting up.
      setFrame(f => ({
        time:    mask.status === 'fulfilled' ? mask.value : f.time,
        topTime: top.status === 'fulfilled' ? top.value : f.topTime,
        failed:  mask.status !== 'fulfilled' && !f.time,
      }));
    };
    load();
    const id = setInterval(load, CLOUD_REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [enabled]);

  return frame;
}

// ---- decoding -----------------------------------------------------------------

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    // Without this the canvas is tainted and its pixels cannot be read.
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image failed'));
    img.src = url;
  });
}

function pixelsOf(img) {
  const c = document.createElement('canvas');
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0);
  return ctx.getImageData(0, 0, c.width, c.height).data;
}

/**
 * Cloud fraction 0-1 per pixel, NaN where the product has no data (off the
 * disc). Cloud is white, clear land green, clear sea blue, and the server
 * resamples, so a pixel's darkest channel is how much of it is cloud.
 */
export function decodeCover(rgba) {
  const out = new Float32Array(rgba.length / 4);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) {
    out[i] = rgba[p + 3] < 128
      ? NaN
      : Math.min(rgba[p], rgba[p + 1], rgba[p + 2]) / 255;
  }
  return out;
}

/** Cloud-top height in metres per pixel, NaN where there is no cloud top. */
export function decodeTop(rgba) {
  const out = new Float32Array(rgba.length / 4);
  const memo = new Map();
  for (let i = 0, p = 0; i < out.length; i++, p += 4) {
    if (rgba[p + 3] < 128) { out[i] = NaN; continue; }
    const r = rgba[p], g = rgba[p + 1], b = rgba[p + 2];
    const key = (r << 16) | (g << 8) | b;
    let h = memo.get(key);
    if (h === undefined) {
      // Nearest stop on the ramp. Resampled edge pixels are blends of two
      // colours, and the nearest stop to a blend is one of its two ends.
      let best = Infinity;
      for (const [m, sr, sg, sb] of CLOUD_TOP_RAMP) {
        const d = (r - sr) ** 2 + (g - sg) ** 2 + (b - sb) ** 2;
        if (d < best) { best = d; h = m; }
      }
      memo.set(key, h);
    }
    out[i] = h;
  }
  return out;
}

// ---- illustration -------------------------------------------------------------

// A stable pseudo-random number in [0, 1) per lattice node and channel, so a
// puff keeps its size and offset while the map pans.
function hash(i, j, k) {
  let h = Math.imul(i, 374761393) ^ Math.imul(j, 668265263) ^ Math.imul(k, 2147483647);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}

/** Bilinear cloud fraction at fractional data pixel (fx, fy); NaN if unknown. */
function sampleCover(data, fx, fy) {
  const { cover, w, h } = data;
  const x = fx - 0.5, y = fy - 0.5;
  const x0 = Math.floor(x), y0 = Math.floor(y);
  let sum = 0, wsum = 0;
  for (let dy = 0; dy <= 1; dy++) {
    for (let dx = 0; dx <= 1; dx++) {
      const xi = Math.min(w - 1, Math.max(0, x0 + dx));
      const yi = Math.min(h - 1, Math.max(0, y0 + dy));
      const v = cover[yi * w + xi];
      if (Number.isNaN(v)) continue;
      const wt = (dx ? x - x0 : 1 - (x - x0)) * (dy ? y - y0 : 1 - (y - y0));
      sum += v * wt;
      wsum += wt;
    }
  }
  return wsum > 1e-6 ? sum / wsum : NaN;
}

function sampleTop(data, fx, fy) {
  if (!data.top) return NaN;
  const xi = Math.min(data.w - 1, Math.max(0, Math.floor(fx)));
  const yi = Math.min(data.h - 1, Math.max(0, Math.floor(fy)));
  return data.top[yi * data.w + xi];
}

/**
 * Draw the clouds for one view into a fresh canvas.
 *
 * `origin` is the world pixel (at `zoom`) of the canvas's top-left corner;
 * the lattice is laid out in world pixels too, so the same node lands on the
 * same ground whatever the view, and the clouds do not shimmer while panning.
 */
function illustrate(map, data, { width, height, origin, zoom, dpr }) {
  const S = CLOUD_PUFF_PX;
  const rowH = S * 0.866;                       // hexagonal rows: no visible grid
  const nw = map.project(L.latLng(data.nw), zoom);
  const se = map.project(L.latLng(data.se), zoom);
  const sx = data.w / (se.x - nw.x), sy = data.h / (se.y - nw.y);

  const low = new Path2D(), mid = new Path2D(), high = new Path2D(), streaks = new Path2D();
  const j0 = Math.floor((origin.y - 2 * S) / rowH), j1 = Math.ceil((origin.y + height + 2 * S) / rowH);
  const i0 = Math.floor((origin.x - 2 * S) / S), i1 = Math.ceil((origin.x + width + 2 * S) / S);
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const a = hash(i, j, 1), b = hash(i, j, 2), c = hash(i, j, 3);
      const wx = i * S + (j & 1 ? S / 2 : 0) + (a - 0.5) * 0.5 * S;
      const wy = j * rowH + (b - 0.5) * 0.5 * S;
      const fx = (wx - nw.x) * sx, fy = (wy - nw.y) * sy;
      if (fx < 0 || fy < 0 || fx >= data.w || fy >= data.h) continue;
      const cover = sampleCover(data, fx, fy);
      // Jittered threshold: a hard 0.5 would trace the pixel grid as a contour.
      if (!(cover > 0.3 + 0.25 * c)) continue;
      const x = wx - origin.x, y = wy - origin.y;
      const r = S * (0.45 + 0.4 * cover) * (0.85 + 0.3 * a);
      const top = sampleTop(data, fx, fy);
      if (Number.isNaN(top) || top < CLOUD_LOW_TOP_M) {
        // A dome and two lower lobes, so a lone puff is still a cloud and not
        // a ball; side by side, the lobes merge into a scalloped edge.
        const flip = c > 0.5 ? 1 : -1;
        for (const [dx, dy, k] of [[0, 0, 1], [-0.78 * flip, 0.3, 0.68], [0.82 * flip, 0.34, 0.6]]) {
          const px = x + dx * r, py = y + dy * r, pr = r * k;
          low.moveTo(px + pr, py);
          low.arc(px, py, pr, 0, 2 * Math.PI);
        }
      } else if (top < CLOUD_HIGH_TOP_M) {
        mid.moveTo(x + r * 1.4, y);
        mid.ellipse(x, y, r * 1.4, r * 0.75, 0, 0, 2 * Math.PI);
      } else {
        high.moveTo(x + r * 1.5 * Math.cos(-0.35), y + r * 1.5 * Math.sin(-0.35));
        high.ellipse(x, y, r * 1.5, r * 0.7, -0.35, 0, 2 * Math.PI);
        if (b > 0.35) {
          // A cirrus streak: a shallow arc along the veil's grain.
          const len = S * (1.2 + 0.8 * cover);
          const ang = -0.35 + (c - 0.5) * 0.5;
          const ux = Math.cos(ang) * len / 2, uy = Math.sin(ang) * len / 2;
          const bow = (a - 0.5) * 0.5 * len;
          streaks.moveTo(x - ux, y - uy);
          streaks.quadraticCurveTo(x - uy * bow / len * 2, y + ux * bow / len * 2, x + ux, y + uy);
        }
      }
    }
  }

  const make = () => {
    const cv = document.createElement('canvas');
    cv.width = Math.round(width * dpr);
    cv.height = Math.round(height * dpr);
    const ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr);
    return ctx;
  };
  const o = make(), t = make(), bd = make();
  const put = (ctx, alpha) => {
    o.globalAlpha = alpha;
    o.drawImage(ctx.canvas, 0, 0, ctx.canvas.width, ctx.canvas.height, 0, 0, width, height);
    o.globalAlpha = 1;
  };

  // Shadows on the ground, down and to the right of their cloud: soft, so
  // they read as shade rather than as a second, darker cloud.
  t.fillStyle = COLOURS.shadow;
  t.fill(low);
  t.fill(mid);
  o.save();
  o.filter = `blur(${Math.round(S * 0.3)}px)`;
  o.globalAlpha = 0.3;
  o.drawImage(t.canvas, 0, 0, t.canvas.width, t.canvas.height, S * 0.35, S * 0.5, width, height);
  o.restore();

  // Cumulus. The body is the union of the puffs in the shade colour, then the
  // same puffs nudged up and to the left in the lit colour, kept only where
  // they cover the first: a lit top and a shaded rim underneath. Under it, the
  // puffs stroked twice as wide as the rim should be — the body covers the
  // inner half and every arc inside the union, leaving one outline round the
  // outside, like the dark edge on the map's other marks.
  bd.fillStyle = COLOURS.shade;
  bd.fill(low);
  bd.save();
  bd.globalCompositeOperation = 'source-atop';
  bd.translate(-S * 0.14, -S * 0.2);
  bd.fillStyle = COLOURS.body;
  bd.fill(low);
  bd.restore();
  t.clearRect(0, 0, width, height);
  t.lineWidth = 2.4;
  t.strokeStyle = COLOURS.rim;
  t.stroke(low);
  t.drawImage(bd.canvas, 0, 0, bd.canvas.width, bd.canvas.height, 0, 0, width, height);
  put(t, 0.95);

  // Layer cloud, above the cumulus: flat and translucent, with a fainter rim.
  t.clearRect(0, 0, width, height);
  t.lineWidth = 2;
  t.strokeStyle = COLOURS.layerRim;
  t.stroke(mid);
  t.fillStyle = COLOURS.layer;
  t.fill(mid);
  put(t, 0.62);

  // High cloud last, over everything below it: a thin veil and its streaks.
  o.globalAlpha = 0.32;
  o.fillStyle = COLOURS.veil;
  o.fill(high);
  o.globalAlpha = 0.75;
  o.strokeStyle = COLOURS.streak;
  o.lineWidth = 1.6;
  o.lineCap = 'round';
  o.stroke(streaks);
  o.globalAlpha = 1;
  return o.canvas;
}

// ---- layer --------------------------------------------------------------------

/**
 * The clouds themselves. onStatus('loading' | 'ok' | 'error') reports each
 * view's request so the sidebar can say what is on screen.
 */
export function CloudCanvas({ time, topTime, onStatus }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  const dataRef   = useRef(null);   // decoded products for the last view fetched
  const shownRef  = useRef(null);   // the last drawing and where it belongs
  const seqRef    = useRef(0);
  const timerRef  = useRef(null);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;

  const topLeft = useCallback(
    () => map.project(map.containerPointToLatLng(L.point(0, 0)), map.getZoom()),
    [map],
  );

  // Copy the last drawing onto the visible canvas at the current pan.
  const blit = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width, height } = map.getContainer().getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const shown = shownRef.current;
    if (!shown || shown.zoom !== map.getZoom()) return;
    const tl = topLeft();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.drawImage(shown.canvas, shown.origin.x - tl.x, shown.origin.y - tl.y,
                  shown.width, shown.height);
  }, [map, topLeft]);

  // Draw the current view from whatever data is in hand.
  const render = useCallback(() => {
    const data = dataRef.current;
    if (!data) { shownRef.current = null; blit(); return; }
    const { width, height } = map.getContainer().getBoundingClientRect();
    if (width < 2 || height < 2) return;
    const dpr = Math.min(window.devicePixelRatio || 1,
                         Math.sqrt(MAX_RENDER_PIXELS / (width * height)));
    const zoom = map.getZoom();
    const origin = topLeft();
    shownRef.current = {
      canvas: illustrate(map, data, { width, height, origin, zoom, dpr }),
      origin, zoom, width, height,
    };
    blit();
  }, [map, blit, topLeft]);

  const request = useCallback(async () => {
    const size = map.getSize();
    if (!time || size.x < 2 || size.y < 2) return;
    const b = map.getBounds();
    const padLat = (b.getNorth() - b.getSouth()) * CLOUD_MARGIN;
    const padLng = (b.getEast() - b.getWest()) * CLOUD_MARGIN;
    const north = clampLat(b.getNorth() + padLat);
    const south = clampLat(b.getSouth() - padLat);
    const west = b.getWest() - padLng, east = b.getEast() + padLng;
    // Leaflet repeats the world sideways and reports longitudes on the copy in
    // view (390 for 30 E one copy over). The server only knows ±180, so ask
    // for the view shifted back onto it, and draw at the copy's own longitudes.
    const shift = 360 * Math.round(map.getCenter().lng / 360);
    const crs = map.options.crs;
    const sw = crs.project(L.latLng(south, west - shift));
    const ne = crs.project(L.latLng(north, east - shift));
    const spanPx = size.x * (1 + 2 * CLOUD_MARGIN);
    const groundPerPx = ((ne.x - sw.x) / spanPx) * Math.cos(map.getCenter().lat * Math.PI / 180);
    const aspect = (ne.y - sw.y) / (ne.x - sw.x);
    const longSide = Math.max(spanPx, spanPx * aspect);
    const scale = Math.min(1, CLOUD_MAX_PX / longSide, groundPerPx / CLOUD_NATIVE_M);
    const width = Math.max(2, Math.round(spanPx * scale));
    const height = Math.max(2, Math.round(spanPx * aspect * scale));
    const url = (layer, t) => `${CLOUD_WMS}?${new URLSearchParams({
      service: 'WMS', version: '1.3.0', request: 'GetMap',
      layers: layer, styles: '',
      // PNG, never JPEG: the colours are the data.
      format: 'image/png', transparent: 'true',
      crs: 'EPSG:3857',
      bbox: [sw.x, sw.y, ne.x, ne.y].map(v => v.toFixed(0)).join(','),
      width: String(width), height: String(height),
      time: t,
    })}`;

    const seq = ++seqRef.current;
    statusRef.current?.('loading');
    const [mask, top] = await Promise.allSettled([
      loadImage(url(CLOUD_MASK_LAYER, time)),
      topTime ? loadImage(url(CLOUD_TOP_LAYER, topTime)) : Promise.reject(new Error('no frame')),
    ]);
    // A slower answer for a view the map has already left is dropped.
    if (seq !== seqRef.current) return;
    if (mask.status !== 'fulfilled') { statusRef.current?.('error'); return; }
    try {
      dataRef.current = {
        w: width, h: height,
        nw: [north, west], se: [south, east],
        cover: decodeCover(pixelsOf(mask.value)),
        // Without heights every cloud is drawn as cumulus — still where the
        // clouds are, only without the low / high distinction.
        top: top.status === 'fulfilled' ? decodeTop(pixelsOf(top.value)) : null,
      };
    } catch {
      statusRef.current?.('error');
      return;
    }
    render();
    statusRef.current?.('ok');
  }, [map, time, topTime, render]);

  const schedule = useCallback(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(request, REQUEST_DEBOUNCE_MS);
  }, [request]);

  // A new frame, or the first mount: fetch at once.
  useEffect(() => {
    request();
    return () => { clearTimeout(timerRef.current); };
  }, [request]);

  // Unmounting abandons whatever is in flight.
  useEffect(() => () => { seqRef.current += 1; }, []);

  // Credit EUMETSAT while its products are on screen, as its data policy asks.
  // Legal credit, not UI copy, so it is not translated — like the tile credits.
  useEffect(() => {
    const ctl = map.attributionControl;
    if (!ctl) return undefined;
    const year = time ? new Date(time).getUTCFullYear() : new Date().getUTCFullYear();
    const text = `Clouds drawn from Meteosat Cloud Mask &amp; Cloud Top Height &copy; EUMETSAT ${year}`;
    ctl.addAttribution(text);
    return () => { ctl.removeAttribution(text); };
  }, [map, time]);

  useMapEvents({
    move: blit,
    // Redraw at once from the data in hand (the request margin covers a pan),
    // then fetch the new view.
    moveend: () => { render(); schedule(); },
    zoomend: () => { render(); schedule(); },
    resize: () => { render(); schedule(); },
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
