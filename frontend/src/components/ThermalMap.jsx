import { useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet';

import { MAP_CENTER, MAP_ZOOM, PREDICT_RADIUS, SOARING_AC_TYPES } from '../config.js';
import { displayPosition } from '../services/deadReckon.js';
import { nearLng, unwrapNear } from '../services/worldWrap.js';
import { RawDataCanvas, CompareControl, LAYER_BUILT } from './CompareLayer.jsx';
import { SatelliteCanvas } from './SatelliteLayer.jsx';

// Pre-computed 256-entry RGBA palette: density 0→255 maps blue→cyan→green→yellow→red
const _PALETTE = (() => {
  const p = new Uint8ClampedArray(1024);
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    if (v < 0.08) continue;
    let r = 0, g = 0, b = 0, a = 0;
    if      (v < 0.30) { const t=(v-0.08)/0.22; r=0;              g=Math.round(t*190);       b=255; a=Math.round(60+t*120); }
    else if (v < 0.50) { const t=(v-0.30)/0.20; r=0;              g=255;                      b=Math.round((1-t)*255); a=190; }
    else if (v < 0.70) { const t=(v-0.50)/0.20; r=Math.round(t*255); g=255;                  b=0;   a=200; }
    else               { const t=(v-0.70)/0.30; r=255;            g=Math.round((1-t)*255);   b=0;   a=215; }
    p[i*4]=r; p[i*4+1]=g; p[i*4+2]=b; p[i*4+3]=a;
  }
  return p;
})();

// -----------------------------------------------------------------------------
// Colour scale: transparent → blue-green → yellow → orange-red
// -----------------------------------------------------------------------------
// The floor and the opacity are set by what the model actually returns, not by
// the 0-1 range it could. Measured on the live site (15 Nov 2026, ten daytime
// forecast grids at four sites): the median cell sat between 3.5% and 25%, and
// no grid peaked above 56%. Against that the old 5% floor left
// holes — one grid drew 932 of its 40,401 cells — and 15% opacity over
// satellite imagery made the rest a tint nobody could see, so a completed scan
// looked like no scan. Only an exact zero is skipped now, which is what the
// night guard sends; a low probability is drawn, in the "low" colour.
function thermalColor(p) {
  if (p < 0.005) return null;
  const a = 0.34 + p * 0.40;
  if (p < 0.35) {
    const t = p / 0.35;
    return `rgba(20, ${Math.round(120 + t * 135)}, 255, ${a})`;
  }
  if (p < 0.65) {
    const t = (p - 0.35) / 0.30;
    return `rgba(${Math.round(20 + t * 235)}, 255, ${Math.round(255 - t * 255)}, ${a})`;
  }
  const t = (p - 0.65) / 0.35;
  return `rgba(255, ${Math.round(255 - t * 230)}, 0, ${a})`;
}

// -----------------------------------------------------------------------------
// Glider icon — top-down silhouette drawn with canvas 2D primitives
// Thermalling: gold + dashed ring + 15° bank tilt
// Straight gliders: sky blue, pointing north
// Tow planes: white.  Gliders under tow: slate, and never styled as thermalling —
// they are climbing on a rope, so the gold "found lift" treatment would be a lie.
// -----------------------------------------------------------------------------
function drawGlider(ctx, x, y, circling, vario, pinned = false, heading = null,
                    isTow = false, underTow = false) {
  const thermalling = circling && !isTow && !underTow;
  const color      = isTow ? '#FFFFFF' : underTow ? '#B0BEC5'
                     : thermalling ? '#FFD700' : '#87CEEB';
  const bank       = thermalling ? Math.PI / 12 : 0;      // 15° tilt for banked flight
  const headingRad = heading != null ? heading * Math.PI / 180 : 0; // null → point north

  // pinned highlight ring (drawn first so it appears behind the icon)
  if (pinned) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, 19, 0, Math.PI * 2);
    ctx.strokeStyle = '#FC8181';
    ctx.lineWidth   = 2.5;
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(headingRad + bank); // true heading first, then bank angle

  // dashed orbit ring — thermalling only, not a tug or a glider on the rope
  if (thermalling) {
    ctx.beginPath();
    ctx.arc(0, 0, 14, 0, Math.PI * 2);
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = '#FFD700';
    ctx.lineWidth   = 1.2;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.fillStyle   = color;
  ctx.strokeStyle = 'rgba(0,0,0,0.6)';
  ctx.lineWidth   = 0.8;

  // fuselage (narrow vertical ellipse)
  ctx.beginPath();
  ctx.ellipse(0, 0, 2, 8, 0, 0, Math.PI * 2);
  ctx.fill(); ctx.stroke();

  // main wings (swept back, span ~22 px)
  ctx.beginPath();
  ctx.moveTo(0, -1);
  ctx.lineTo(-11, 5);
  ctx.lineTo(-11, 7);
  ctx.lineTo(0,   3);
  ctx.lineTo(11,  7);
  ctx.lineTo(11,  5);
  ctx.closePath();
  ctx.fill(); ctx.stroke();

  // horizontal tail (span ~8 px, near the back)
  ctx.beginPath();
  ctx.moveTo(0,  6);
  ctx.lineTo(-4, 9);
  ctx.lineTo(-4, 10);
  ctx.lineTo(0,  8);
  ctx.lineTo(4,  10);
  ctx.lineTo(4,  9);
  ctx.closePath();
  ctx.fill(); ctx.stroke();

  ctx.restore();

  // vario label below the icon
  if (vario != null) {
    const sign  = vario >= 0 ? '+' : '';
    const label = `${sign}${vario.toFixed(1)}`;
    ctx.save();
    ctx.font         = '9px sans-serif';
    ctx.textAlign    = 'center';
    ctx.fillStyle    = '#fff';
    ctx.strokeStyle  = 'rgba(0,0,0,0.7)';
    ctx.lineWidth    = 2.5;
    ctx.strokeText(label, x, y + 22);
    ctx.fillText(label, x, y + 22);
    ctx.restore();
  }
}

// -----------------------------------------------------------------------------
// Flight-path trail — red fading polyline (glideandseek style) for the clicked
// glider, drawn behind its icon.  White for tow planes.
// -----------------------------------------------------------------------------
function drawGliderPath(ctx, map, points, isTow) {
  if (points.length < 2) return;
  const rgb = isTow ? '255,255,255' : '255,60,60';

  // Project to screen coords; cap at 800 points for render budget
  const src = points.length > 800 ? points.slice(-800) : points;
  const pts = unwrapNear(src, map.getCenter().lng)
    .map(p => map.latLngToContainerPoint([p.lat, p.lon]));

  ctx.save();
  ctx.lineWidth  = 2.5;
  ctx.lineCap    = 'round';
  ctx.lineJoin   = 'round';

  for (let i = 0; i < pts.length - 1; i++) {
    const alpha = 0.25 + ((i + 1) / pts.length) * 0.70;
    // Catmull-Rom neighbours (clamped at ends)
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    // Convert to cubic bezier control points
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    ctx.strokeStyle = `rgba(${rgb},${alpha.toFixed(2)})`;
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
    ctx.stroke();
  }

  ctx.restore();
}

// -----------------------------------------------------------------------------
// Thermal bubble helpers — active thermal overlays derived from circling gliders
// -----------------------------------------------------------------------------
function thermalBubbleColor(vario) {
  if (vario < 1.5) return [255, 220, 50];   // yellow  – weak
  if (vario < 3.0) return [255, 140, 0];    // orange  – moderate
  return [255, 50, 0];                       // red     – strong
}

function drawThermalBubble(ctx, x, y, avgVario, count, phase) {
  const [r, g, b] = thermalBubbleColor(avgVario);
  const baseR  = 22 + Math.min(avgVario * 5, 18);   // 22–40 px, scales with strength
  const outerR = baseR + Math.sin(phase) * 5;        // ±5 px pulse
  const innerR = baseR * 0.55;

  // Radial gradient glow
  const grad = ctx.createRadialGradient(x, y, innerR * 0.4, x, y, outerR);
  grad.addColorStop(0,   `rgba(${r},${g},${b},0.28)`);
  grad.addColorStop(0.5, `rgba(${r},${g},${b},0.12)`);
  grad.addColorStop(1,   `rgba(${r},${g},${b},0.00)`);
  ctx.beginPath();
  ctx.arc(x, y, outerR, 0, Math.PI * 2);
  ctx.fillStyle = grad;
  ctx.fill();

  // Inner solid fill
  ctx.beginPath();
  ctx.arc(x, y, innerR, 0, Math.PI * 2);
  ctx.fillStyle = `rgba(${r},${g},${b},0.38)`;
  ctx.fill();

  // Core dot
  ctx.beginPath();
  ctx.arc(x, y, 5, 0, Math.PI * 2);
  ctx.fillStyle = `rgba(${r},${g},${b},0.92)`;
  ctx.fill();

  // Vario label below bubble
  const label = `↑${avgVario.toFixed(1)}m/s`;
  ctx.save();
  ctx.font        = 'bold 10px sans-serif';
  ctx.textAlign   = 'center';
  ctx.lineWidth   = 2.5;
  ctx.strokeStyle = 'rgba(0,0,0,0.75)';
  ctx.fillStyle   = '#fff';
  ctx.strokeText(label, x, y + outerR + 13);
  ctx.fillText(label,   x, y + outerR + 13);
  ctx.restore();

  // Count badge when multiple gliders share the thermal
  if (count > 1) {
    const bx = x + innerR - 2;
    const by = y - innerR + 2;
    ctx.save();
    ctx.beginPath();
    ctx.arc(bx, by, 8, 0, Math.PI * 2);
    ctx.fillStyle = `rgb(${r},${g},${b})`;
    ctx.fill();
    ctx.font         = 'bold 9px sans-serif';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle    = '#fff';
    ctx.fillText(String(count), bx, by);
    ctx.restore();
  }
}

// -----------------------------------------------------------------------------
// Animated thermal bubble canvas — RAF loop, sits above the heatmap layer
// -----------------------------------------------------------------------------
function ThermalBubbleCanvas({ activeThermals }) {
  const map         = useMap();
  const canvasRef   = useRef(null);
  const phaseRef    = useRef(0);
  const rafRef      = useRef(null);
  const thermalsRef = useRef(activeThermals);
  const zoomingRef  = useRef(false);
  thermalsRef.current = activeThermals;   // always up-to-date without restarting RAF

  const loop = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const { width, height } = map.getContainer().getBoundingClientRect();
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width  = width;
      canvas.height = height;
    }

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!zoomingRef.current) {
      phaseRef.current += 0.04;   // ~2.4 rad/s → full cycle every ~2.6 s
      const cLng = map.getCenter().lng;
      for (const t of thermalsRef.current) {
        const pt = map.latLngToContainerPoint([t.lat, nearLng(t.lon, cLng)]);
        drawThermalBubble(ctx, pt.x, pt.y, t.avg_vario, t.count, phaseRef.current);
      }
    }

    rafRef.current = requestAnimationFrame(loop);
  }, [map]);  // stable — only changes if the Leaflet map instance changes

  useEffect(() => {
    if (activeThermals.length === 0) {
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      const canvas = canvasRef.current;
      if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    rafRef.current = requestAnimationFrame(loop);
    return () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; } };
  }, [loop, activeThermals.length]);

  useMapEvents({
    resize() {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const { width, height } = map.getContainer().getBoundingClientRect();
      canvas.width  = width;
      canvas.height = height;
    },
    zoomstart() { zoomingRef.current = true; },
    zoomend()   { zoomingRef.current = false; },
  });

  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_BUILT}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 401 }}
    />,
    map.getContainer()
  );
}

// -----------------------------------------------------------------------------
// OGN live-activity heatmap — two-pass KDE from all circling gliders
// Pass 1: white Gaussians on black offscreen canvas (additive)
// Pass 2: palette lookup per pixel → blue/cyan/green/yellow/red
// -----------------------------------------------------------------------------
// Pass 2 walks every pixel on the canvas — ~1.6M of them at 1080p — so this
// layer is the most expensive thing on the map. Circling clusters drift far too
// slowly to be worth that on every stream frame, so data-driven repaints stay
// on the 2 s cadence they had before WS_FRAME_INTERVAL was halved. Map
// interaction still repaints immediately: panning moves every blob at once.
const KDE_MIN_MS = 2000;

function OgnHeatmapCanvas({ gliders }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  const glRef     = useRef(gliders);
  glRef.current   = gliders;
  const lastDataDrawRef = useRef(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { width: W, height: H } = map.getContainer().getBoundingClientRect();
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);

    // Soaring types only (glider / hang glider / paraglider), and not on aerotow.
    // This heatmap turns "circling and climbing" straight into painted lift, so it
    // must not trust the feed on its own — an airliner in a hold satisfies both
    // conditions, and so does a glider being dragged up on a rope.
    const thermals = glRef.current.filter(
      g => SOARING_AC_TYPES.has(g.ac_type) && !g.under_tow
           && g.circling && g.vario > 0.3
    );
    if (!thermals.length) return;

    // pass 1 — accumulate white Gaussians on black canvas (lighter = additive)
    const off = typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(W, H)
      : Object.assign(document.createElement('canvas'), { width: W, height: H });
    const oct = off.getContext('2d');
    oct.fillStyle = '#000';
    oct.fillRect(0, 0, W, H);
    oct.globalCompositeOperation = 'lighter';

    const zoom  = map.getZoom();
    const blobR = Math.min(Math.max(12, 22 * Math.pow(1.5, zoom - 9)), 130);

    const cLng = map.getCenter().lng;
    for (const g of thermals) {
      const { x, y } = map.latLngToContainerPoint([g.lat, nearLng(g.lon, cLng)]);
      const alpha = Math.min(0.10 + (g.vario / 6.0) * 0.38, 0.52);
      const grad  = oct.createRadialGradient(x, y, 0, x, y, blobR);
      grad.addColorStop(0,   `rgba(255,255,255,${alpha.toFixed(3)})`);
      grad.addColorStop(0.5, `rgba(255,255,255,${(alpha * 0.4).toFixed(3)})`);
      grad.addColorStop(1,   'rgba(0,0,0,0)');
      oct.fillStyle = grad;
      oct.beginPath();
      oct.arc(x, y, blobR, 0, Math.PI * 2);
      oct.fill();
    }

    // pass 2 — map R-channel density (0-255) through colour palette
    const src = oct.getImageData(0, 0, W, H).data;
    const out  = new ImageData(W, H);
    const dst  = out.data;
    for (let i = 0; i < src.length; i += 4) {
      const v = src[i];   // R = G = B since blobs are white
      if (v === 0) continue;
      const pi = v * 4;
      dst[i] = _PALETTE[pi]; dst[i+1] = _PALETTE[pi+1];
      dst[i+2] = _PALETTE[pi+2]; dst[i+3] = _PALETTE[pi+3];
    }
    ctx.putImageData(out, 0, 0);
  }, [map]);

  useMapEvents({
    move:    draw,
    zoomend: draw,
    resize:  draw,
    zoomstart() {
      const c = canvasRef.current;
      if (c) c.getContext('2d').clearRect(0, 0, c.width, c.height);
    },
  });
  // Trailing edge, not leading-only: the timer guarantees the newest frame is
  // eventually painted, so a burst of updates cannot leave stale lift on screen.
  useEffect(() => {
    const since = Date.now() - lastDataDrawRef.current;
    if (since >= KDE_MIN_MS) {
      lastDataDrawRef.current = Date.now();
      draw();
      return;
    }
    const id = setTimeout(() => {
      lastDataDrawRef.current = Date.now();
      draw();
    }, KDE_MIN_MS - since);
    return () => clearTimeout(id);
  }, [draw, gliders]);

  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_BUILT}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 398 }}
    />,
    map.getContainer()
  );
}

// -----------------------------------------------------------------------------
// Scan footprint — the square a prediction covers, drawn whatever it contains
//
// The cells alone cannot say "a scan happened here": at night every one is zero
// and none is painted, and from the opening Europe-wide view the whole 11 km
// grid is under three pixels across. The outline is what makes a click land
// somewhere visible; dashed while the request is in flight, solid once the
// cells under it are real. Too small to read as a square, it becomes a ring.
// -----------------------------------------------------------------------------
const SCAN_RING_BELOW_PX = 16;

function drawScanFootprint(ctx, map, lat, lon, half, pending) {
  lon = nearLng(lon, map.getCenter().lng);
  const sw = map.latLngToContainerPoint([lat - half, lon - half]);
  const ne = map.latLngToContainerPoint([lat + half, lon + half]);
  const x = Math.min(sw.x, ne.x);
  const y = Math.min(sw.y, ne.y);
  const w = Math.abs(ne.x - sw.x);
  const h = Math.abs(ne.y - sw.y);

  ctx.save();
  ctx.setLineDash(pending ? [7, 5] : []);
  // Dark under light, so the line holds on snow, sea and forest alike.
  for (const [width, style] of [[3.5, 'rgba(0,0,0,0.55)'], [1.5, 'rgba(255,255,255,0.92)']]) {
    ctx.lineWidth   = width;
    ctx.strokeStyle = style;
    ctx.beginPath();
    if (Math.max(w, h) < SCAN_RING_BELOW_PX) {
      ctx.arc(x + w / 2, y + h / 2, 11, 0, Math.PI * 2);
    } else {
      ctx.rect(x, y, w, h);
    }
    ctx.stroke();
  }
  ctx.restore();
}

// -----------------------------------------------------------------------------
// Canvas layer — portalled into the Leaflet container so it overlays the tiles
// -----------------------------------------------------------------------------
// Deliberately does not take the glider array. Repainting the grid means up to
// 40,401 fillRects, and this layer would otherwise redraw all of them on every
// stream frame purely because a glider moved somewhere off in the corner. The
// icons live on GliderCanvas above; all this needs of them is whether the
// pinned one is a tow plane, which decides the path colour.
function HeatmapCanvas({ heatmap, gridMeta, pendingScan, pinnedIsTow, pinnedId, gliderPathsRef }) {
  const map       = useMap();
  const canvasRef = useRef(null);

  // Always-current data refs — lets the stable draw callback read latest values
  // without being recreated every render, eliminating stale-closure wipes from
  // Leaflet internal events (resize, pan-snap) fired after predict() returns.
  const dataRef = useRef({ heatmap, gridMeta, pendingScan, pinnedIsTow, pinnedId });
  dataRef.current = { heatmap, gridMeta, pendingScan, pinnedIsTow, pinnedId };

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const { heatmap, gridMeta, pendingScan, pinnedIsTow, pinnedId } = dataRef.current;

    const { width, height } = map.getContainer().getBoundingClientRect();
    canvas.width  = width;
    canvas.height = height;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);

    // ---- heatmap grid ----
    if (heatmap && gridMeta) {
      const { lat, rows, cols, radius } = gridMeta;
      const lon = nearLng(gridMeta.lon, map.getCenter().lng);
      // The grid is a lattice of sample points spanning [centre-radius,
      // centre+radius] inclusive, so there are rows-1 gaps between rows — not
      // rows. Dividing by rows stretches the drawing ~0.5% past the data.
      const latStep = (2 * radius) / (rows - 1);
      const lonStep = (2 * radius) / (cols - 1);

      // Cell edges, projected once per axis and snapped to whole pixels. Web
      // Mercator is separable — x depends only on longitude, y only on
      // latitude — so cols+1 and rows+1 projections place all 40,401 cells,
      // where projecting two corners per cell cost 80,802 on every map move.
      // Snapping is what makes neighbours share an edge exactly: the old
      // "+1 px" overlap painted every seam twice, and at an opacity high
      // enough to see the grid at all that drew a lattice of darker lines
      // across it. Each sample sits at the centre of the cell it colours, hence
      // the half-step: the square straddles it rather than hanging north-east.
      const xs = new Array(cols + 1);
      const ys = new Array(rows + 1);
      for (let c = 0; c <= cols; c++) {
        xs[c] = Math.round(map.latLngToContainerPoint(
          [lat, lon - radius + (c - 0.5) * lonStep]).x);
      }
      for (let r = 0; r <= rows; r++) {
        ys[r] = Math.round(map.latLngToContainerPoint(
          [lat - radius + (r - 0.5) * latStep, lon]).y);
      }

      for (let i = 0; i < heatmap.length; i++) {
        const row = Math.floor(i / cols);
        const col = i % cols;
        const x = xs[col];
        const w = xs[col + 1] - x;
        const y = ys[row + 1];            // row index grows north, screen y south
        const h = ys[row] - y;
        // Zoomed far out most cells are narrower than a pixel and collapse to
        // nothing; the one whose edge crosses the pixel boundary paints it.
        if (w <= 0 || h <= 0) continue;
        if (x >= width || y >= height || x + w <= 0 || y + h <= 0) continue;

        const color = thermalColor(heatmap[i]);
        if (!color) continue;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, w, h);
      }

      // Half a cell wider than the lattice, because each sample's square
      // straddles it — the line then sits on the painted edge, not inside it.
      drawScanFootprint(ctx, map, lat, lon, radius + latStep / 2, false);
    }

    // ---- scan in flight ----
    // Centred on the clicked point; the answer comes back snapped onto the
    // terrain lattice up to ~550 m away, which at this size is a line width.
    if (pendingScan) {
      drawScanFootprint(ctx, map, pendingScan.lat, pendingScan.lon, PREDICT_RADIUS, true);
    }

    // ---- clicked glider path (icons are drawn on the layer above this one) ----
    // Only ever reported positions: the path is the flight that happened, so
    // no dead-reckoned point is allowed to join it.
    if (pinnedId && gliderPathsRef) {
      const points = gliderPathsRef.current[pinnedId];
      if (points && points.length >= 2) {
        drawGliderPath(ctx, map, points, pinnedIsTow);
      }
    }
  }, [map, gliderPathsRef]); // stable — reads live data from dataRef, not closure

  useMapEvents({
    move:      draw,
    zoomend:   draw,
    resize:    draw,
    zoomstart() {
      const canvas = canvasRef.current;
      if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    },
  });

  // Redraw whenever visible data changes
  useEffect(() => { draw(); }, [draw, heatmap, gridMeta, pendingScan, pinnedIsTow, pinnedId]);

  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_BUILT}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 450 }}
    />,
    map.getContainer()
  );
}

// -----------------------------------------------------------------------------
// Glider icons — own canvas, own RAF loop, above the heatmap
//
// Split out from HeatmapCanvas so the icons can animate between beacons without
// dragging a full grid repaint along at the same rate. Positions come from
// displayPosition(), which projects along heading and ground speed while a
// beacon is outstanding, so an aircraft reporting every 40 s still moves.
// -----------------------------------------------------------------------------
const ICON_FPS = 10;   // 100 ms — smooth enough for aircraft, far below RAF's 60

function GliderCanvas({ gliders, pinnedId }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  const rafRef    = useRef(null);
  const lastRef   = useRef(0);
  const dataRef   = useRef({ gliders, pinnedId });
  dataRef.current = { gliders, pinnedId };   // current without restarting the loop

  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { gliders, pinnedId } = dataRef.current;

    const { width, height } = map.getContainer().getBoundingClientRect();
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width  = width;
      canvas.height = height;
    }
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);

    const now  = Date.now();
    const cLng = map.getCenter().lng;
    for (const g of gliders) {
      const { lat, lon } = displayPosition(g, now);
      const pt = map.latLngToContainerPoint([lat, nearLng(lon, cLng)]);
      // Cull off-screen work: the backend pads the streamed viewport by 25 %,
      // so a full frame carries icons that land outside the canvas.
      if (pt.x < -40 || pt.y < -40 || pt.x > width + 40 || pt.y > height + 40) continue;
      drawGlider(ctx, pt.x, pt.y, g.circling, g.vario, g.id === pinnedId, g.heading,
                 g.is_tow, g.under_tow);
    }
  }, [map]);

  const loop = useCallback((ts) => {
    rafRef.current = requestAnimationFrame(loop);
    if (ts - lastRef.current < 1000 / ICON_FPS) return;
    lastRef.current = ts;
    paint();
  }, [paint]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(loop);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [loop]);

  // Panning and zooming move every icon even when no aircraft has reported, and
  // those events outpace ICON_FPS, so they repaint directly instead of waiting.
  useMapEvents({
    move:    paint,
    zoomend: paint,
    resize:  paint,
    zoomstart() {
      const canvas = canvasRef.current;
      if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    },
  });

  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_BUILT}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 451 }}
    />,
    map.getContainer()
  );
}

// -----------------------------------------------------------------------------
// Click handler — glider proximity check, then fall through to map prediction
// -----------------------------------------------------------------------------
const GLIDER_HIT_PX = 18;  // click radius in screen pixels

function MapClickHandler({ onMapClick, gliders, onGliderClick }) {
  const map = useMapEvents({
    click(e) {
      const cp  = e.containerPoint;
      const now  = Date.now();
      const cLng = map.getCenter().lng;
      let best = null, bestDist = Infinity;
      for (const g of gliders) {
        // Hit-test where the icon is drawn, not where it last reported —
        // otherwise a projected marker has its clickable area left behind at
        // the stale position and clicking the aircraft misses it entirely.
        // And at the copy it is drawn on, which is the one nearest the centre.
        const { lat, lon } = displayPosition(g, now);
        const pt = map.latLngToContainerPoint([lat, nearLng(lon, cLng)]);
        const d  = Math.hypot(cp.x - pt.x, cp.y - pt.y);
        if (d < GLIDER_HIT_PX && d < bestDist) { best = g; bestDist = d; }
      }
      if (best) { onGliderClick(best); return; }
      // wrap(): Leaflet repeats the world sideways, and a click on a repeat
      // reports the longitude it was drawn at — 390 for 30 E one copy over.
      // Sent as is, the weather request fails and the terrain has no tile, so
      // the scan came back built from fallback weather over flat ground.
      const ll = e.latlng.wrap();
      onMapClick(ll.lat, ll.lng);
    },
  });
  return null;
}

// -----------------------------------------------------------------------------
// Viewport reporter — tells the backend which gliders are worth streaming
// -----------------------------------------------------------------------------
function ViewportReporter({ onChange }) {
  const map = useMap();

  const report = useCallback(() => {
    if (!onChange) return;
    // pad(0.25) keeps a ring of just-off-screen traffic in the stream, so panning
    // reveals gliders that are already there instead of popping them in a frame
    // or two later once the new bounds have made the round trip.
    const b = map.getBounds().pad(0.25);
    onChange({
      lat_min: b.getSouth(), lat_max: b.getNorth(),
      lon_min: b.getWest(),  lon_max: b.getEast(),
    });
  }, [map, onChange]);

  useEffect(() => { report(); }, [report]);   // declare the initial view
  useMapEvents({ moveend: report, zoomend: report });
  return null;
}

// -----------------------------------------------------------------------------
// Scan focus — bring the map to a scan that would otherwise be too small to see
//
// The map opens on the whole of Türkiye (zoom 6), where the 0.1-degree grid a
// prediction returns is a few pixels — and the opening scan of İnönü relies on
// this controller to fly in to it. When the opening view was the European
// soaring belt (zoom 5), clicking bare map from it produced
// a correct heatmap that occupied less space than the cursor. So a click that
// would draw smaller than MIN_SCAN_PX flies in to fit the square; one made from
// close enough to read it leaves the view exactly where the user put it.
//
// Driven by the click, not by the response: the flight and the request overlap,
// so the dashed footprint is on screen while the answer is still on its way.
// -----------------------------------------------------------------------------
const MIN_SCAN_PX = 160;

function ScanFocusController({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return undefined;
    // The opening scan (App.jsx) sets a target on the very first render, before
    // the container has been laid out. flyToBounds on a zero-sized map divides
    // by its size and throws "Invalid LatLng (NaN, NaN)", taking the whole app
    // down with it. So wait, a frame at a time, until the map has real pixels.
    let raf = 0;
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
      map.invalidateSize({ pan: false });
      const size = map.getSize();
      if (size.x < 2 * 48 + 1 || size.y < 2 * 48 + 1) { raf = requestAnimationFrame(run); return; }
      const r    = PREDICT_RADIUS;
      // The click arrives wrapped into ±180; near the date line the copy on
      // screen may be the other one, and flying to the wrapped one would cross
      // the whole world to get there.
      const lon  = nearLng(target.lon, map.getCenter().lng);
      const west = map.latLngToContainerPoint([target.lat, lon - r]);
      const east = map.latLngToContainerPoint([target.lat, lon + r]);
      if (Math.abs(east.x - west.x) >= MIN_SCAN_PX) return;
      map.flyToBounds(
        [[target.lat - r, lon - r], [target.lat + r, lon + r]],
        { padding: [48, 48], maxZoom: 13, duration: 0.9 },
      );
    };
    map.whenReady(run);
    return () => { cancelled = true; cancelAnimationFrame(raf); };
  }, [target, map]);
  return null;
}

// -----------------------------------------------------------------------------
// World fit — never zoom out far enough to see the world twice
//
// Leaflet repeats the world sideways, and at zoom 2 on a wide screen three
// copies sat side by side with the aircraft drawn on only one. The floor is
// the lowest zoom at which one world (256 px at zoom 0, doubling per level) is
// at least as wide as the map, so the user sees one world that wraps around —
// worldCopyJump on the map brings a pan across the date line back to the same
// copy — rather than a row of them. Re-derived on resize: a phone can go
// lower than a desktop, and rotating it changes the answer.
// -----------------------------------------------------------------------------
function WorldFitController() {
  const map = useMap();
  const fit = useCallback(() => {
    const width = map.getSize().x;
    if (width > 0) map.setMinZoom(Math.max(0, Math.ceil(Math.log2(width / 256))));
  }, [map]);
  useEffect(() => { fit(); }, [fit]);
  useMapEvents({ resize: fit });
  return null;
}

// -----------------------------------------------------------------------------
// Fly-to controller — responds to search selections
// -----------------------------------------------------------------------------
function FlyToController({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.flyTo([target.lat, nearLng(target.lon, map.getCenter().lng)],
              target.zoom ?? 12, { duration: 1.2 });
  }, [target, map]);
  return null;
}

// -----------------------------------------------------------------------------
// Public component
// -----------------------------------------------------------------------------
export default function ThermalMap({ heatmap, gridMeta, pendingScan, scanFocus, gliders, activeThermals = [], showOgnHeatmap = true, onMapClick, onGliderClick, onViewportChange, pinnedId, flyTarget, gliderPathsRef, compare = false, rawInputs = null, noInsolation = false, satelliteTime = null, onSatelliteStatus }) {
  // Passed to HeatmapCanvas as a plain boolean rather than the glider object:
  // that layer repaints the whole grid when its props change, and an object
  // would arrive with a fresh identity on every stream frame.
  const pinnedIsTow = pinnedId
    ? gliders.find(g => g.id === pinnedId)?.is_tow ?? false
    : false;

  return (
    <MapContainer
      center={MAP_CENTER}
      zoom={MAP_ZOOM}
      style={{ width: '100%', height: '100%' }}
      zoomControl={true}
      // Panning across the antimeridian jumps back to the same world, so the
      // aircraft on it are not left behind on the copy you came from.
      worldCopyJump={true}
    >
      {/* Free OSM satellite-style tiles (ESRI) */}
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        attribution="Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, USDA FSA, USGS, Aerogrid, IGN, IGP, and the GIS User Community"
        maxZoom={18}
      />
      {/* Place names and borders on top — Esri's reference layer, the overlay
          published for World Imagery, from the same host and without a key.
          It replaced CARTO's voyager_only_labels on 15 Nov 2026: requested
          without an API key those tiles now come back with no labels at all
          and "API KEY REQUIRED" printed across each one, so the watermark was
          tiled over every view of the map. Note the {z}/{y}/{x} order — the
          ArcGIS tile path puts the row first, the reverse of CARTO's. */}
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
        // The first half is the service's own copyrightText, as published in
        // its metadata. Terrain credit rides on this layer because the
        // elevation behind every heatmap is Copernicus GLO-30, whose licence
        // requires attribution wherever derived products are shown. Legal
        // credit, not UI copy, so it is not translated — the tile credits
        // beside it are not either.
        attribution='Labels: Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community | Terrain: Copernicus DEM &copy; DLR e.V. 2010&ndash;2014, &copy; Airbus Defence and Space GmbH 2014&ndash;2018, provided under COPERNICUS by the EU and ESA'
        // Same strength the CARTO labels were drawn at. This layer also
        // carries administrative boundaries, which at full strength lay a
        // white mesh of municipal borders over the terrain at zoom 9-11.
        opacity={0.7}
        maxZoom={18}
      />

      {/* Observed clouds, directly on the base imagery and under everything
          ThermalSense draws. Mounted only while the toggle is on and a frame
          time is known — see SatelliteLayer.jsx. */}
      {satelliteTime && <SatelliteCanvas time={satelliteTime} onStatus={onSatelliteStatus} />}
      {showOgnHeatmap && <OgnHeatmapCanvas gliders={gliders} />}
      <HeatmapCanvas heatmap={heatmap} gridMeta={gridMeta} pendingScan={pendingScan} pinnedIsTow={pinnedIsTow} pinnedId={pinnedId} gliderPathsRef={gliderPathsRef} />
      <ThermalBubbleCanvas activeThermals={activeThermals} />
      <GliderCanvas gliders={gliders} pinnedId={pinnedId} />
      {/* Before/after comparison. Everything above is the "after" half and is
          only clipped, never altered; the raw canvas and the divider exist
          only while the comparison is on. */}
      {compare && (
        <>
          <RawDataCanvas gliders={gliders} gridMeta={gridMeta} rawInputs={rawInputs} pinnedId={pinnedId} gliderPathsRef={gliderPathsRef} />
          <CompareControl gliders={gliders} activeThermals={activeThermals} gridMeta={gridMeta} rawInputs={rawInputs} noInsolation={noInsolation} />
        </>
      )}
      <MapClickHandler onMapClick={onMapClick} gliders={gliders} onGliderClick={onGliderClick} />
      <ViewportReporter onChange={onViewportChange} />
      <WorldFitController />
      <ScanFocusController target={scanFocus} />
      <FlyToController target={flyTarget} />
    </MapContainer>
  );
}
