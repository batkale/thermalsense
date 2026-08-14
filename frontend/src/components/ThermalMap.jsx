import { useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet';

import { MAP_CENTER, MAP_ZOOM } from '../config.js';

// OGN aircraft types that gain height on lift alone: glider, hang glider, paraglider.
// Mirrors SOARING_AC_TYPES in backend/data/ogn_client.py.
const SOARING_AC_TYPES = new Set([0x1, 0x6, 0x7]);

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
function thermalColor(p) {
  if (p < 0.05)  return null;  // skip near-zero cells entirely
  const a = 0.15 + p * 0.55;
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
  const pts = src.map(p => map.latLngToContainerPoint([p.lat, p.lon]));

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
      for (const t of thermalsRef.current) {
        const pt = map.latLngToContainerPoint([t.lat, t.lon]);
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
function OgnHeatmapCanvas({ gliders }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  const glRef     = useRef(gliders);
  glRef.current   = gliders;

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

    for (const g of thermals) {
      const { x, y } = map.latLngToContainerPoint([g.lat, g.lon]);
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
  useEffect(() => { draw(); }, [draw, gliders]);

  return createPortal(
    <canvas
      ref={canvasRef}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 398 }}
    />,
    map.getContainer()
  );
}

// -----------------------------------------------------------------------------
// Canvas layer — portalled into the Leaflet container so it overlays the tiles
// -----------------------------------------------------------------------------
function HeatmapCanvas({ heatmap, gridMeta, gliders, pinnedId, gliderPathsRef }) {
  const map       = useMap();
  const canvasRef = useRef(null);

  // Always-current data refs — lets the stable draw callback read latest values
  // without being recreated every render, eliminating stale-closure wipes from
  // Leaflet internal events (resize, pan-snap) fired after predict() returns.
  const dataRef = useRef({ heatmap, gridMeta, gliders, pinnedId });
  dataRef.current = { heatmap, gridMeta, gliders, pinnedId };

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const { heatmap, gridMeta, gliders, pinnedId } = dataRef.current;

    const { width, height } = map.getContainer().getBoundingClientRect();
    canvas.width  = width;
    canvas.height = height;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);

    // ---- heatmap grid ----
    if (heatmap && gridMeta) {
      const { lat, lon, rows, cols, radius } = gridMeta;
      const latStep = (2 * radius) / rows;
      const lonStep = (2 * radius) / cols;

      heatmap.forEach((prob, i) => {
        const color = thermalColor(prob);
        if (!color) return;

        const row     = Math.floor(i / cols);
        const col     = i % cols;
        const cellLat = lat - radius + row * latStep;
        const cellLon = lon - radius + col * lonStep;

        const sw = map.latLngToContainerPoint([cellLat,            cellLon]);
        const ne = map.latLngToContainerPoint([cellLat + latStep,  cellLon + lonStep]);

        const x = Math.min(sw.x, ne.x);
        const y = Math.min(sw.y, ne.y);
        const w = Math.abs(sw.x - ne.x) + 1;
        const h = Math.abs(sw.y - ne.y) + 1;

        ctx.fillStyle = color;
        ctx.fillRect(x, y, w, h);
      });
    }

    // ---- clicked glider path (drawn before icon so icon sits on top) ----
    if (pinnedId && gliderPathsRef) {
      const points = gliderPathsRef.current[pinnedId];
      const pinnedGlider = gliders.find(g => g.id === pinnedId);
      if (points && points.length >= 2) {
        drawGliderPath(ctx, map, points, pinnedGlider?.is_tow ?? false);
      }
    }

    // ---- live gliders ----
    gliders.forEach(g => {
      const pt = map.latLngToContainerPoint([g.lat, g.lon]);
      drawGlider(ctx, pt.x, pt.y, g.circling, g.vario, g.id === pinnedId, g.heading,
                 g.is_tow, g.under_tow);
    });
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
  useEffect(() => { draw(); }, [draw, heatmap, gridMeta, gliders, pinnedId]);

  return createPortal(
    <canvas
      ref={canvasRef}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 450 }}
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
      const cp = e.containerPoint;
      let best = null, bestDist = Infinity;
      for (const g of gliders) {
        const pt = map.latLngToContainerPoint([g.lat, g.lon]);
        const d  = Math.hypot(cp.x - pt.x, cp.y - pt.y);
        if (d < GLIDER_HIT_PX && d < bestDist) { best = g; bestDist = d; }
      }
      if (best) onGliderClick(best);
      else onMapClick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

// -----------------------------------------------------------------------------
// Fly-to controller — responds to search selections
// -----------------------------------------------------------------------------
function FlyToController({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.flyTo([target.lat, target.lon], target.zoom ?? 12, { duration: 1.2 });
  }, [target, map]);
  return null;
}

// -----------------------------------------------------------------------------
// Public component
// -----------------------------------------------------------------------------
export default function ThermalMap({ heatmap, gridMeta, gliders, activeThermals = [], showOgnHeatmap = true, onMapClick, onGliderClick, pinnedId, flyTarget, gliderPathsRef }) {
  return (
    <MapContainer
      center={MAP_CENTER}
      zoom={MAP_ZOOM}
      style={{ width: '100%', height: '100%' }}
      zoomControl={true}
    >
      {/* Free OSM satellite-style tiles (ESRI) */}
      <TileLayer
        url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        attribution="Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, USDA FSA, USGS, Aerogrid, IGN, IGP, and the GIS User Community"
        maxZoom={18}
      />
      {/* Road labels on top — CartoDB, free, no API key */}
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://carto.com">CARTO</a>'
        opacity={0.7}
        subdomains="abcd"
      />

      {showOgnHeatmap && <OgnHeatmapCanvas gliders={gliders} />}
      <HeatmapCanvas heatmap={heatmap} gridMeta={gridMeta} gliders={gliders} pinnedId={pinnedId} gliderPathsRef={gliderPathsRef} />
      <ThermalBubbleCanvas activeThermals={activeThermals} />
      <MapClickHandler onMapClick={onMapClick} gliders={gliders} onGliderClick={onGliderClick} />
      <FlyToController target={flyTarget} />
    </MapContainer>
  );
}
