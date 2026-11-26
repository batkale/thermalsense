// -----------------------------------------------------------------------------
// The 3D thermal view: scene builders
//
// One scan patch (the heatmap's own centre and radius) as a block of terrain,
// with the heatmap on its surface, a column rising from each of the forecast's
// strongest spots to the thermal top, and the live aircraft at their reported
// altitude. Pure three.js — Thermal3D.jsx owns the renderer and calls these.
//
// What is measured and what is drawn are kept apart on purpose, as on the 2D
// map: the ground colour is the heatmap, cell for cell; the ceiling is the
// thermal top /predict reports; aircraft and their trails are reported
// positions. The columns are an illustration of where the heatmap peaks — the
// model predicts a probability per cell, not a thermal's shape, width or lean —
// and the caption says so.
//
// Frame: metres, origin at the grid centre, x east, y up, z south (so north is
// -z). Heights are drawn VERTICAL_SCALE times their size, terrain and aircraft
// alike, so an aircraft never sinks into a hill the scale has raised.
// -----------------------------------------------------------------------------
import * as THREE from 'three';
import { Line2 } from 'three/examples/jsm/lines/Line2.js';
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js';
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js';

import { thermalRGB, thermalColor } from './thermalColor.js';
import { displayPosition } from './deadReckon.js';
import { nearLng } from './worldWrap.js';

const DEG = Math.PI / 180;

// İnönü's patch rises ~565 m across 11 km (818-1,383 m, Copernicus, 26 Nov
// 2026). At 1:1 that reads as a tablecloth from any distance worth viewing the
// whole patch from; 1.5 makes the ridges legible without turning hills into
// spires. Stated in the caption, and applied to everything with a height.
export const VERTICAL_SCALE = 1.5;
// The lean of a column is wind x time to climb: a thermal rising at CLIMB_MS
// drifts downwind at the wind's speed. 2 m/s is a typical climb, not a forecast
// one, and the wind is Open-Meteo's 10 m value (km/h), weaker than the wind
// aloft — so the lean is an illustration, and the caption says that too.
export const CLIMB_MS = 2;
// The block's sides drop this far below its lowest point.
const SKIRT_M = 250;

// --- Relief ------------------------------------------------------------------

function finishRelief(rel) {
  let min = Infinity, max = -Infinity, sum = 0;
  for (const v of rel.elev) { if (v < min) min = v; if (v > max) max = v; sum += v; }
  return { ...rel, min, max, mean: sum / rel.elev.length,
           key: reliefKey(rel.lat0, rel.lon0) };
}

/** One key per patch: the grid centre, which /predict and /terrain/relief share. */
export function reliefKey(lat, lon) {
  return `${lat.toFixed(5)},${lon.toFixed(5)}`;
}

/** /terrain/relief's answer, row-major from the south-west corner. */
export function reliefFromResponse(r) {
  return finishRelief({
    rows: r.rows, cols: r.cols, elev: Float32Array.from(r.elevation),
    lat0: r.grid_lat, lon0: r.grid_lon, radius: r.radius,
    source: r.source === 'copernicus' ? 'copernicus' : 'lattice',
  });
}

/**
 * The 121 samples /predict sent, bilinearly filled to n x n — the surface the
 * model's original 22 columns see. For a backend without /terrain/relief, or
 * when it cannot answer.
 */
export function reliefFromLattice(inputs, gridMeta, n = 51) {
  if (!inputs?.elevation || !gridMeta) return null;
  const { elevation: e, elevation_rows: R, elevation_cols: C } = inputs;
  if (R < 2 || C < 2 || e.length !== R * C) return null;
  const elev = new Float32Array(n * n);
  for (let i = 0; i < n; i++) {
    const fr = (i / (n - 1)) * (R - 1);
    const r0 = Math.min(R - 2, Math.floor(fr)), tr = fr - r0;
    for (let j = 0; j < n; j++) {
      const fc = (j / (n - 1)) * (C - 1);
      const c0 = Math.min(C - 2, Math.floor(fc)), tc = fc - c0;
      const a = e[r0 * C + c0], b = e[r0 * C + c0 + 1];
      const c = e[(r0 + 1) * C + c0], d = e[(r0 + 1) * C + c0 + 1];
      elev[i * n + j] = (a * (1 - tc) + b * tc) * (1 - tr) + (c * (1 - tc) + d * tc) * tr;
    }
  }
  return finishRelief({ rows: n, cols: n, elev, lat0: gridMeta.lat, lon0: gridMeta.lon,
                        radius: gridMeta.radius, source: 'lattice' });
}

/** No terrain at all: a level floor at h, said as such in the caption. */
export function flatRelief(gridMeta, h = 0) {
  return finishRelief({ rows: 2, cols: 2, elev: new Float32Array([h, h, h, h]),
                        lat0: gridMeta.lat, lon0: gridMeta.lon, radius: gridMeta.radius,
                        source: 'flat' });
}

/** Ground elevation under (lat, lon), bilinear, clamped to the patch's edge. */
export function groundAt(rel, lat, lon) {
  const { rows, cols, elev, lat0, lon0, radius } = rel;
  const fr = Math.min(rows - 1, Math.max(0, ((lat - (lat0 - radius)) / (2 * radius)) * (rows - 1)));
  const fc = Math.min(cols - 1, Math.max(0, ((lon - (lon0 - radius)) / (2 * radius)) * (cols - 1)));
  const r0 = Math.min(rows - 2, Math.floor(fr)), c0 = Math.min(cols - 2, Math.floor(fc));
  const tr = fr - r0, tc = fc - c0;
  const a = elev[r0 * cols + c0], b = elev[r0 * cols + c0 + 1];
  const c = elev[(r0 + 1) * cols + c0], d = elev[(r0 + 1) * cols + c0 + 1];
  return (a * (1 - tc) + b * tc) * (1 - tr) + (c * (1 - tc) + d * tc) * tr;
}

// --- Frame -------------------------------------------------------------------

/** Metres per degree at the patch, and the conversions both ways. */
export function makeFrame(rel) {
  const { lat0, lon0, radius } = rel;
  const p = lat0 * DEG;
  const mLat = 111132.954 - 559.822 * Math.cos(2 * p) + 1.175 * Math.cos(4 * p);
  const mLon = 111412.84 * Math.cos(p) - 93.5 * Math.cos(3 * p);
  const baseM = rel.min;
  return {
    lat0, lon0, radius, baseM, mLat, mLon,
    halfX: radius * mLon, halfZ: radius * mLat,
    x: lon => (lon - lon0) * mLon,
    z: lat => -(lat - lat0) * mLat,
    y: h => (h - baseM) * VERTICAL_SCALE,
    lat: z => lat0 - z / mLat,
    lon: x => lon0 + x / mLon,
  };
}

// --- Sun ---------------------------------------------------------------------

/**
 * Sun altitude and azimuth (radians; azimuth clockwise from north) at a moment
 * and place. The usual low-precision almanac, good to ~0.5 degrees — the light
 * in the scene, not an ephemeris. It is the same sun the model's cos_incidence
 * column reads, which is why the hillsides it lights are worth looking at.
 */
export function sunPosition(date, lat, lon) {
  const d = date.getTime() / 86400000 - 10957.5;            // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * DEG;
  const q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * DEG;
  const e = (23.439 - 0.00000036 * d) * DEG;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const gmst = ((18.697374558 + 24.06570982441908 * d) % 24 + 24) % 24;
  const ha = (gmst * 15 + lon) * DEG - ra;
  const phi = lat * DEG;
  const alt = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha));
  const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(phi) - Math.sin(phi) * Math.cos(ha));
  return { alt, az: (az + 2 * Math.PI) % (2 * Math.PI) };
}

/** Unit vector toward the sun in the scene frame. */
export function sunVector(sun) {
  return new THREE.Vector3(Math.sin(sun.az) * Math.cos(sun.alt), Math.sin(sun.alt),
                           -Math.cos(sun.az) * Math.cos(sun.alt));
}

/** A vertical gradient for the background: day blue, dusk, or night. */
export function skyTexture(sunAlt) {
  const c = document.createElement('canvas');
  c.width = 2; c.height = 256;
  const ctx = c.getContext('2d');
  const day   = [['#3f7fc4', '#a9cdea', '#dbe9f3'], ['#20344f', '#c58f6a', '#e8b98f'], ['#060d17', '#13243a', '#1f3550']];
  const a = sunAlt / DEG;
  const set = a > 8 ? day[0] : a > -4 ? day[1] : day[2];
  const grad = ctx.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, set[0]); grad.addColorStop(0.62, set[1]); grad.addColorStop(1, set[2]);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 2, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// --- Terrain -----------------------------------------------------------------

/** The relief as a mesh with uv over the patch (v = 0 on the southern edge). */
export function buildTerrain(rel, frame) {
  const { rows, cols, elev, lat0, lon0, radius } = rel;
  const latStep = (2 * radius) / (rows - 1), lonStep = (2 * radius) / (cols - 1);
  const pos = new Float32Array(rows * cols * 3);
  const uv  = new Float32Array(rows * cols * 2);
  for (let i = 0; i < rows; i++) {
    const z = frame.z(lat0 - radius + i * latStep);
    for (let j = 0; j < cols; j++) {
      const k = i * cols + j;
      pos[3 * k]     = frame.x(lon0 - radius + j * lonStep);
      pos[3 * k + 1] = frame.y(elev[k]);
      pos[3 * k + 2] = z;
      uv[2 * k]     = j / (cols - 1);
      uv[2 * k + 1] = i / (rows - 1);
    }
  }
  const idx = [];
  for (let i = 0; i < rows - 1; i++) {
    for (let j = 0; j < cols - 1; j++) {
      const a = i * cols + j, b = a + 1, c = a + cols, d = c + 1;
      idx.push(a, b, c, b, d, c);           // counter-clockwise seen from above
    }
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
  geom.setIndex(idx);
  geom.computeVertexNormals();
  const mesh = new THREE.Mesh(geom, new THREE.MeshLambertMaterial({ color: '#ffffff' }));

  // The block's sides: each edge vertex joined to the same point SKIRT_M below
  // the lowest ground, so the patch reads as a cut-out of the world rather
  // than a sheet floating in it.
  const yBase = frame.y(rel.min - SKIRT_M);
  const ring = [];
  for (let j = 0; j < cols; j++) ring.push(j);                                  // south
  for (let i = 1; i < rows; i++) ring.push(i * cols + cols - 1);                // east
  for (let j = cols - 2; j >= 0; j--) ring.push((rows - 1) * cols + j);         // north
  for (let i = rows - 2; i >= 0; i--) ring.push(i * cols);                      // west
  const sp = new Float32Array(ring.length * 2 * 3);
  const sc = new Float32Array(ring.length * 2 * 3);
  ring.forEach((k, n) => {
    sp.set([pos[3 * k], pos[3 * k + 1], pos[3 * k + 2]], 6 * n);
    sp.set([pos[3 * k], yBase, pos[3 * k + 2]], 6 * n + 3);
    sc.set([0.42, 0.36, 0.29], 6 * n);
    sc.set([0.16, 0.13, 0.11], 6 * n + 3);
  });
  const sidx = [];
  for (let n = 0; n < ring.length - 1; n++) {
    const a = 2 * n, b = 2 * n + 1, c = 2 * n + 2, d = 2 * n + 3;
    sidx.push(a, b, c, c, b, d);
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute('position', new THREE.BufferAttribute(sp, 3));
  sg.setAttribute('color', new THREE.BufferAttribute(sc, 3));
  sg.setIndex(sidx);
  const skirt = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide }));

  const group = new THREE.Group();
  group.add(mesh, skirt);
  return { group, mesh };
}

// --- Ground texture ----------------------------------------------------------

const IMAGERY_URL = (z, x, y) =>
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
const IMAGERY_MAX_ZOOM  = 14;     // ~11 m a pixel at 40 N: sharper than a 3D view resolves
const IMAGERY_MAX_TILES = 48;     // 0.1 deg at 40 N is 42 tiles at zoom 14
const TEXTURE_MAX_PX    = 2048;

const lonPx = (lon, z) => ((lon + 180) / 360) * 256 * 2 ** z;
const latPx = (lat, z) => {
  const s = Math.sin(lat * DEG);
  return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * 256 * 2 ** z;
};

function loadImage(url, signal) {
  return new Promise(resolve => {
    const img = new Image();
    // The colours go into a WebGL texture, which a tainted canvas cannot feed.
    // Esri's tile server answers with Access-Control-Allow-Origin: *.
    img.crossOrigin = 'anonymous';
    img.onload  = () => resolve(img);
    img.onerror = () => resolve(null);
    signal?.addEventListener('abort', () => { img.src = ''; resolve(null); });
    img.src = url;
  });
}

/**
 * The same satellite imagery the 2D map uses, for one patch, as one canvas
 * spanning exactly [centre - radius, centre + radius] on both axes. The tiles
 * are Web Mercator and the patch is laid out linear in latitude; across 0.1
 * degrees the two differ by under a pixel. Null when no tile arrived.
 */
export async function loadImagery(lat0, lon0, radius, signal) {
  const s = lat0 - radius, n = lat0 + radius, w = lon0 - radius, e = lon0 + radius;
  let z = IMAGERY_MAX_ZOOM;
  for (; z > 10; z--) {
    const nx = Math.floor(lonPx(e, z) / 256) - Math.floor(lonPx(w, z) / 256) + 1;
    const ny = Math.floor(latPx(s, z) / 256) - Math.floor(latPx(n, z) / 256) + 1;
    if (nx * ny <= IMAGERY_MAX_TILES) break;
  }
  const x0 = lonPx(w, z), x1 = lonPx(e, z), y0 = latPx(n, z), y1 = latPx(s, z);
  const k = Math.min(1, TEXTURE_MAX_PX / Math.max(x1 - x0, y1 - y0));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round((x1 - x0) * k);
  canvas.height = Math.round((y1 - y0) * k);
  const ctx = canvas.getContext('2d');
  const span = 2 ** z;
  const jobs = [];
  for (let tx = Math.floor(x0 / 256); tx <= Math.floor(x1 / 256); tx++) {
    for (let ty = Math.floor(y0 / 256); ty <= Math.floor(y1 / 256); ty++) {
      const url = IMAGERY_URL(z, ((tx % span) + span) % span, ty);
      jobs.push(loadImage(url, signal).then(img => ({ img, tx, ty })));
    }
  }
  const tiles = await Promise.all(jobs);
  let loaded = 0;
  for (const { img, tx, ty } of tiles) {
    if (!img) continue;
    loaded++;
    // A whole pixel of overlap per side hides the seams resampling leaves.
    ctx.drawImage(img, (tx * 256 - x0) * k - 0.5, (ty * 256 - y0) * k - 0.5, 256 * k + 1, 256 * k + 1);
  }
  return loaded ? canvas : null;
}

/**
 * Shaded relief at the relief's own resolution, north up: the ground under any
 * tile that did not arrive, and the whole ground when none did. Hypsometric
 * tints, lit from the north-west — the cartographic convention, not the sun,
 * because this stands in for a photograph and should read like a map.
 */
function shadedRelief(rel, frame) {
  const { rows, cols, elev, min, max } = rel;
  const c = document.createElement('canvas');
  c.width = cols; c.height = rows;
  const ctx = c.getContext('2d');
  const img = ctx.createImageData(cols, rows);
  const dx = (2 * frame.halfX) / (cols - 1), dz = (2 * frame.halfZ) / (rows - 1);
  const L = [-0.5, 0.7071, 0.5];                       // east, up, north
  const span = max - min || 1;
  const at = (i, j) => elev[Math.max(0, Math.min(rows - 1, i)) * cols + Math.max(0, Math.min(cols - 1, j))];
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const ge = (at(i, j + 1) - at(i, j - 1)) / (2 * dx) * VERTICAL_SCALE;
      const gn = (at(i + 1, j) - at(i - 1, j)) / (2 * dz) * VERTICAL_SCALE;
      const len = Math.hypot(ge, 1, gn);
      const shade = Math.max(0, (-ge * L[0] + L[1] - gn * L[2]) / len);
      const t = (at(i, j) - min) / span;
      const r = 92 + t * 110, g = 118 + t * 72, b = 76 + t * 86;
      const f = 0.5 + 0.65 * shade;
      const o = ((rows - 1 - i) * cols + j) * 4;
      img.data[o] = r * f; img.data[o + 1] = g * f; img.data[o + 2] = b * f; img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/**
 * The ground: shaded relief, then the imagery, then the heatmap exactly as the
 * 2D map paints it (same colours, same opacity, each sample's square straddling
 * its point). North is the top of the canvas.
 */
export function composeGround(rel, frame, imagery, heatmap, gridMeta) {
  const canvas = document.createElement('canvas');
  if (imagery) {
    canvas.width = imagery.width; canvas.height = imagery.height;
  } else {
    canvas.width = 1024;
    canvas.height = Math.min(TEXTURE_MAX_PX, Math.round(1024 * frame.halfZ / frame.halfX));
  }
  const W = canvas.width, H = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(shadedRelief(rel, frame), 0, 0, W, H);
  if (imagery) ctx.drawImage(imagery, 0, 0, W, H);

  if (heatmap && gridMeta && reliefKey(gridMeta.lat, gridMeta.lon) === rel.key) {
    const { rows, cols } = gridMeta;
    const xs = new Array(cols + 1), ys = new Array(rows + 1);
    for (let c = 0; c <= cols; c++) xs[c] = Math.round(((c - 0.5) / (cols - 1)) * W);
    for (let r = 0; r <= rows; r++) ys[r] = Math.round(((rows - 1 - (r - 0.5)) / (rows - 1)) * H);
    for (let i = 0; i < heatmap.length; i++) {
      const color = thermalColor(heatmap[i]);
      if (!color) continue;
      const row = Math.floor(i / cols), col = i % cols;
      const x = Math.max(0, xs[col]), x2 = Math.min(W, xs[col + 1]);
      const y = Math.max(0, ys[row + 1]), y2 = Math.min(H, ys[row]);
      if (x2 <= x || y2 <= y) continue;
      ctx.fillStyle = color;
      ctx.fillRect(x, y, x2 - x, y2 - y);
    }
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

// --- Hotspots ----------------------------------------------------------------

const HOTSPOT_BLOCK = 8;       // cells per side, ~400 m: about a thermal's catchment
const MAX_COLUMNS   = 14;

/**
 * Where to stand the columns: blocks of the heatmap that are local maxima, in
 * the top 15% of the map's blocks and at least half as strong as the
 * strongest, kept apart by minSpacingM. Half the peak alone is not a bar on a
 * flat map: the İnönü grid of 26 Nov 2026 ran 11-28%, its median block was
 * above half the peak, and every bump became a column. The positions are
 * relative (the strongest spots of this map); the colour and opacity of each
 * column are its absolute probability, so a weak day draws faint blue columns
 * rather than confident red ones.
 */
export function findHotspots(heatmap, gridMeta, frame, minSpacingM) {
  if (!heatmap || !gridMeta) return [];
  const { rows, cols, lat, lon, radius } = gridMeta;
  const B = HOTSPOT_BLOCK;
  const nr = Math.ceil(rows / B), nc = Math.ceil(cols / B);
  const mean = new Float32Array(nr * nc), wLat = new Float32Array(nr * nc), wLon = new Float32Array(nr * nc);
  const latStep = (2 * radius) / (rows - 1), lonStep = (2 * radius) / (cols - 1);
  for (let br = 0; br < nr; br++) {
    for (let bc = 0; bc < nc; bc++) {
      let s = 0, n = 0, sl = 0, so = 0;
      for (let r = br * B; r < Math.min(rows, (br + 1) * B); r++) {
        for (let c = bc * B; c < Math.min(cols, (bc + 1) * B); c++) {
          const p = heatmap[r * cols + c];
          s += p; n++;
          sl += p * r; so += p * c;
        }
      }
      const k = br * nc + bc;
      mean[k] = n ? s / n : 0;
      wLat[k] = s > 0 ? sl / s : (br + 0.5) * B;
      wLon[k] = s > 0 ? so / s : (bc + 0.5) * B;
    }
  }
  const sorted = Float32Array.from(mean).sort();
  const top = sorted[sorted.length - 1];
  if (top < 0.02) return [];
  const floor = Math.max(0.02, top * 0.5, sorted[Math.floor(0.85 * (sorted.length - 1))]);
  const cand = [];
  for (let br = 0; br < nr; br++) {
    for (let bc = 0; bc < nc; bc++) {
      const v = mean[br * nc + bc];
      if (v < floor) continue;
      let peak = true;
      for (let dr = -1; dr <= 1 && peak; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          const r = br + dr, c = bc + dc;
          if ((dr || dc) && r >= 0 && c >= 0 && r < nr && c < nc && mean[r * nc + c] > v) { peak = false; break; }
        }
      }
      if (!peak) continue;
      const k = br * nc + bc;
      cand.push({ p: v, lat: lat - radius + wLat[k] * latStep, lon: lon - radius + wLon[k] * lonStep });
    }
  }
  cand.sort((a, b) => b.p - a.p);
  const keep = [];
  for (const c of cand) {
    if (keep.length >= MAX_COLUMNS) break;
    const x = frame.x(c.lon), z = frame.z(c.lat);
    if (keep.some(k => Math.hypot(frame.x(k.lon) - x, frame.z(k.lat) - z) < minSpacingM)) continue;
    keep.push({ ...c, s: top > floor ? (c.p - floor) / (top - floor) : 1 });
  }
  return keep;
}

// --- Thermal columns ---------------------------------------------------------

const COLUMN_VERT = /* glsl */ `
  attribute float aT;
  varying float vT;
  varying vec3 vN;
  varying vec3 vV;
  void main() {
    vT = aT;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vN = normalize(normalMatrix * normal);
    vV = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }`;

// Brightest at the silhouette, so the column reads as a volume of air rather
// than a tube; faint bands travel upward to say which way the air goes.
const COLUMN_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;
  varying float vT;
  varying vec3 vN;
  varying vec3 vV;
  void main() {
    float edge = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.4);
    float fade = smoothstep(0.0, 0.07, vT) * (1.0 - smoothstep(0.78, 1.0, vT));
    float bands = 0.72 + 0.28 * sin(vT * 24.0 - uTime * 2.4);
    gl_FragColor = vec4(uColor, uOpacity * fade * (0.1 + 0.9 * edge) * bands);
  }`;

const BUBBLE_VERT = /* glsl */ `
  attribute vec3 aSeed;          // phase, angle, radial fraction
  uniform vec3 uBase;
  uniform vec2 uDrift;           // horizontal metres per metre climbed (x, z)
  uniform float uHeight;         // metres from ground to top
  uniform float uScaleY;
  uniform float uR0;
  uniform float uR1;
  uniform float uTime;
  uniform float uSpeed;
  uniform float uWobble;
  uniform float uPx;             // viewport px per world metre at unit depth
  varying float vA;
  void main() {
    float t = fract(aSeed.x + uTime * uSpeed);
    float h = t * uHeight;
    float ang = aSeed.y + t * 5.0;
    float r = mix(uR0, uR1, pow(t, 0.7)) * aSeed.z;
    vec3 p = vec3(uBase.x + uDrift.x * h + sin(t * 6.2832 + uWobble) * 25.0 + cos(ang) * r,
                  uBase.y + h * uScaleY,
                  uBase.z + uDrift.y * h + cos(t * 6.2832 + uWobble) * 25.0 + sin(ang) * r);
    vA = smoothstep(0.0, 0.06, t) * (1.0 - smoothstep(0.8, 1.0, t));
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = clamp(38.0 * uPx / -mv.z, 1.5, 14.0);
    gl_Position = projectionMatrix * mv;
  }`;

const BUBBLE_FRAG = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying float vA;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    gl_FragColor = vec4(uColor, uOpacity * vA * smoothstep(0.5, 0.1, d));
  }`;

/**
 * A column per hotspot, ground to ceiling, leaning downwind; rising bubbles in
 * it; and under a cumulus forecast, a cloud on top of the stronger ones.
 * Returns the group and the uniform blocks the render loop animates.
 */
export function buildThermals({ spots, rel, frame, topM, wind, cumulus, pxPerMetre }) {
  const group = new THREE.Group();
  const animated = [];
  const u = Math.max(0, (wind?.speed ?? 0) / 3.6);                // km/h -> m/s
  const toward = (((wind?.dir ?? 0) + 180) % 360) * DEG;          // the wind blows from dir
  const drift = new THREE.Vector2(Math.sin(toward) * u / CLIMB_MS, -Math.cos(toward) * u / CLIMB_MS);
  const cloudMat = new THREE.MeshLambertMaterial({ color: '#f3f6f9', transparent: true, opacity: 0.93 });
  const puff = new THREE.IcosahedronGeometry(1, 3);

  spots.forEach((spot, n) => {
    const g0 = groundAt(rel, spot.lat, spot.lon);
    const heightM = Math.max(150, topM - g0);
    const base = new THREE.Vector3(frame.x(spot.lon), frame.y(g0), frame.z(spot.lat));
    const r0 = 70 + 150 * spot.s, r1 = r0 * 2;
    const wobble = n * 1.7;
    const centre = t => {
      const h = t * heightM;
      return [base.x + drift.x * h + Math.sin(t * 2 * Math.PI + wobble) * 25,
              base.y + h * VERTICAL_SCALE,
              base.z + drift.y * h + Math.cos(t * 2 * Math.PI + wobble) * 25];
    };
    const [cr, cg, cb] = thermalRGB(spot.p).map(v => v / 255);
    const opacity = 0.22 + 0.55 * Math.min(1, spot.p / 0.5);

    // Rings stacked level, not perpendicular to the lean: a drifting plume is a
    // column of air displaced sideways, not a tilted pipe.
    const NR = 28, NS = 28;
    const pos = new Float32Array((NR + 1) * (NS + 1) * 3), at = new Float32Array((NR + 1) * (NS + 1));
    for (let k = 0; k <= NR; k++) {
      const t = k / NR;
      const [x, y, z] = centre(t);
      const r = r0 + (r1 - r0) * t ** 0.7;
      for (let s = 0; s <= NS; s++) {
        const a = (s / NS) * 2 * Math.PI, o = k * (NS + 1) + s;
        pos.set([x + Math.cos(a) * r, y, z + Math.sin(a) * r], 3 * o);
        at[o] = t;
      }
    }
    const idx = [];
    for (let k = 0; k < NR; k++) {
      for (let s = 0; s < NS; s++) {
        const a = k * (NS + 1) + s, b = a + 1, c = a + NS + 1, d = c + 1;
        idx.push(a, c, b, b, c, d);
      }
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geom.setAttribute('aT', new THREE.BufferAttribute(at, 1));
    geom.setIndex(idx);
    geom.computeVertexNormals();
    const colUniforms = {
      uColor: { value: new THREE.Vector3(cr, cg, cb) },
      uOpacity: { value: opacity },
      uTime: { value: 0 },
    };
    const column = new THREE.Mesh(geom, new THREE.ShaderMaterial({
      uniforms: colUniforms, vertexShader: COLUMN_VERT, fragmentShader: COLUMN_FRAG,
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
    }));
    column.renderOrder = 2;
    group.add(column);
    animated.push(colUniforms);

    const NB = Math.round(40 + 70 * spot.s);
    const seeds = new Float32Array(NB * 3);
    for (let i = 0; i < NB; i++) {
      seeds.set([Math.random(), Math.random() * 2 * Math.PI, Math.sqrt(Math.random()) * 0.85], 3 * i);
    }
    const bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(NB * 3), 3));
    bg.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 3));
    const bubbleUniforms = {
      uBase: { value: base.clone() }, uDrift: { value: drift.clone() },
      uHeight: { value: heightM }, uScaleY: { value: VERTICAL_SCALE },
      uR0: { value: r0 }, uR1: { value: r1 }, uTime: { value: 0 },
      // A visual pace, not the forecast climb: at a real 2 m/s a bubble would
      // take ~12 minutes to cross the screen.
      uSpeed: { value: 0.055 + 0.05 * spot.s },
      uWobble: { value: wobble }, uPx: pxPerMetre,
      uColor: { value: new THREE.Vector3(0.55 + cr * 0.45, 0.55 + cg * 0.45, 0.55 + cb * 0.45) },
      uOpacity: { value: 0.55 + 0.4 * Math.min(1, spot.p / 0.5) },
    };
    const bubbles = new THREE.Points(bg, new THREE.ShaderMaterial({
      uniforms: bubbleUniforms, vertexShader: BUBBLE_VERT, fragmentShader: BUBBLE_FRAG,
      transparent: true, depthWrite: false,
    }));
    bubbles.frustumCulled = false;           // positions live in the shader
    bubbles.renderOrder = 3;
    group.add(bubbles);
    animated.push(bubbleUniforms);

    // Under cumulus the thermal top is the cloud base, so the stronger columns
    // get a cloud sitting on it. Blue thermals get none: that is the forecast.
    if (cumulus && spot.s >= 0.25) {
      const [tx, ty, tz] = centre(1);
      const size = 160 + 380 * spot.s;
      const cloud = new THREE.Group();
      const lumps = 6 + Math.round(4 * spot.s);
      for (let i = 0; i < lumps; i++) {
        const a = (i / lumps) * 2 * Math.PI + n;
        const d = i === 0 ? 0 : size * (0.45 + 0.35 * ((i * 37) % 10) / 10);
        const r = size * (i === 0 ? 0.75 : 0.42 + 0.25 * ((i * 53) % 10) / 10);
        const m = new THREE.Mesh(puff, cloudMat);
        m.scale.set(r, r * 0.72, r);
        m.position.set(tx + Math.cos(a) * d, ty + r * 0.45, tz + Math.sin(a) * d);
        cloud.add(m);
      }
      group.add(cloud);
    }
  });
  return { group, animated };
}

// --- Ceiling, labels, scale --------------------------------------------------

/** The thermal top as a faint sheet over the patch, with a 1 km grid. */
export function buildCeiling(frame, topM, kind) {
  const y = frame.y(topM);
  const colour = kind === 'blue' ? '#8fd0ff' : '#ffffff';
  const group = new THREE.Group();
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(2 * frame.halfX, 2 * frame.halfZ),
    new THREE.MeshBasicMaterial({ color: colour, transparent: true, opacity: 0.07,
                                  depthWrite: false, side: THREE.DoubleSide }),
  );
  plane.rotation.x = -Math.PI / 2;
  plane.position.y = y;
  plane.renderOrder = 1;
  group.add(plane);
  const pts = [];
  for (let x = -Math.floor(frame.halfX / 1000) * 1000; x <= frame.halfX; x += 1000) {
    pts.push(x, y, -frame.halfZ, x, y, frame.halfZ);
  }
  for (let z = -Math.floor(frame.halfZ / 1000) * 1000; z <= frame.halfZ; z += 1000) {
    pts.push(-frame.halfX, y, z, frame.halfX, y, z);
  }
  const grid = new THREE.LineSegments(
    new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)),
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.14, depthWrite: false }),
  );
  const hx = frame.halfX, hz = frame.halfZ;
  const rim = new THREE.LineLoop(
    new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(
      [-hx, y, -hz, hx, y, -hz, hx, y, hz, -hx, y, hz], 3)),
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.6, depthWrite: false }),
  );
  group.add(grid, rim);
  return group;
}

/**
 * Text that keeps its size on screen however far the camera is: a sprite with
 * size attenuation off, whose scale sizeSprite sets from the viewport height.
 */
export function textSprite(text, { px = 13, color = '#f2f7fb', bg = 'rgba(8,18,27,0.72)',
                                   bold = false, center = [0.5, 0.5] } = {}) {
  const k = 3;                                            // drawn at 3x for sharp edges
  const font = `${bold ? 650 : 500} ${px * k}px "Archivo Variable", system-ui, sans-serif`;
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  ctx.font = font;
  const pad = 5 * k;
  c.width = Math.ceil(ctx.measureText(text).width + 2 * pad);
  c.height = Math.ceil(px * k * 1.5);
  ctx.font = font;
  if (bg) {
    ctx.fillStyle = bg;
    const r = 4 * k;
    ctx.beginPath();
    ctx.roundRect ? ctx.roundRect(0, 0, c.width, c.height, r) : ctx.rect(0, 0, c.width, c.height);
    ctx.fill();
  }
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, pad, c.height / 2 + k);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, sizeAttenuation: false, depthTest: false, transparent: true,
  }));
  sprite.center.set(center[0], center[1]);
  sprite.renderOrder = 10;
  sprite.userData = { px: c.height / k, aspect: c.width / c.height, label: true };
  return sprite;
}

/** Scale one textSprite for a viewport of height viewH (CSS px) and vertical fov. */
export function sizeSprite(sprite, viewH, fov) {
  const s = (sprite.userData.px * 2 * Math.tan((fov / 2) * DEG)) / Math.max(1, viewH);
  sprite.scale.set(s * sprite.userData.aspect, s, 1);
}

function niceStep(range) {
  for (const s of [100, 200, 250, 500, 1000, 2000]) if (range / s <= 6) return s;
  return 5000;
}

/**
 * A height post at the north-west corner, ticked in metres MSL: behind the
 * patch from the opening view, so it is never the nearest, largest thing on
 * screen, and clear of the caption in the lower left.
 */
export function buildScalePost(frame, fromM, toM, label) {
  const group = new THREE.Group();
  const x = -frame.halfX - 120, z = -frame.halfZ - 120;
  const mat = new THREE.LineBasicMaterial({ color: '#e9f1f8', transparent: true, opacity: 0.85 });
  const pts = [x, frame.y(fromM), z, x, frame.y(toM), z];
  const step = niceStep(toM - fromM);
  const ticks = [];
  for (let h = Math.ceil(fromM / step) * step; h <= toM; h += step) {
    pts.push(x, frame.y(h), z, x - 160, frame.y(h), z);
    // Labels point inward, over the patch: outward they ran off the screen's
    // edge whenever the corner was near it, which on a phone is always.
    const s = textSprite(label(h), { px: 11, center: [-0.08, 0.5] });
    s.position.set(x + 30, frame.y(h), z);
    group.add(s);
    ticks.push(s);
  }
  group.add(new THREE.LineSegments(
    new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(pts, 3)), mat));
  group.userData.ticks = ticks;
  return group;
}

/**
 * Show every k-th tick label of a scale post, k chosen so neighbours sit at
 * least a label's height apart on screen — from far off, or on a short map,
 * six labels on a few hundred pixels of post otherwise print over each other.
 */
export function thinTicks(post, camera, viewH) {
  const ticks = post?.userData.ticks;
  if (!ticks || ticks.length < 2) return;
  const a = ticks[0].position.clone().project(camera);
  const b = ticks[1].position.clone().project(camera);
  const gap = (Math.abs(a.y - b.y) / 2) * viewH;
  const k = Math.max(1, Math.ceil((ticks[0].userData.px + 3) / Math.max(gap, 0.01)));
  ticks.forEach((t, i) => { t.visible = i % k === 0; });
}

// --- Aircraft ----------------------------------------------------------------

const GLIDER_PX = 26;            // wingspan on screen, like the 2D icon
const DOT_PX = 9;                // the camera-facing dot under it
const TRAIL_POINTS = 150;
const PINNED_TRAIL_POINTS = 800; // the 2D map's cap for the pinned path
const PICK_PX = 18;              // the 2D map's GLIDER_HIT_PX

// Half the 2D icon's outline, nose up, wingspan 1: mirrored for the other side.
const HALF_OUTLINE = [[0, 0.45], [0.035, 0.12], [0.5, 0.03], [0.5, -0.03], [0.035, -0.06],
                      [0.025, -0.33], [0.16, -0.37], [0.16, -0.42], [0, -0.41]];

function gliderGeometries() {
  const outline = [...HALF_OUTLINE, ...HALF_OUTLINE.slice(1, -1).reverse().map(([x, y]) => [-x, y])];
  const shape = new THREE.Shape(outline.map(([x, y]) => new THREE.Vector2(x, y)));
  const fill = new THREE.ShapeGeometry(shape);
  fill.rotateX(-Math.PI / 2);                   // lie flat, nose toward north (-z)
  const line = new THREE.BufferGeometry().setFromPoints(outline.map(([x, y]) => new THREE.Vector3(x, 0, -y)));
  return { fill, line };
}

/** The 2D map's colours: tug white, on tow slate, thermalling gold, else sky blue. */
/** A white disc with a dark rim, tinted per aircraft by its sprite's colour. */
function dotTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const ctx = c.getContext('2d');
  ctx.beginPath();
  ctx.arc(32, 32, 26, 0, 2 * Math.PI);
  ctx.fillStyle = '#ffffff';
  ctx.fill();
  ctx.lineWidth = 8;
  ctx.strokeStyle = 'rgba(0,0,0,0.75)';
  ctx.stroke();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function gliderColour(g) {
  if (g.is_tow) return '#FFFFFF';
  if (g.under_tow) return '#B0BEC5';
  return g.circling ? '#FFD700' : '#87CEEB';
}

function linearRGB(hex, f = 1) {
  const c = new THREE.Color(hex);              // stored linear
  return [c.r * f, c.g * f, c.b * f];
}

/**
 * Live aircraft inside the patch, at their reported altitude, each with a line
 * to the ground beneath it and its recent reported positions as a trail — the
 * pinned one's whole flight, red, as on the 2D map. The icon may be dead
 * reckoned between beacons exactly as the 2D icon is; the trail and the line
 * never are.
 */
export class GliderLayer {
  constructor() {
    this.group = new THREE.Group();
    this.items = new Map();
    this.geo = gliderGeometries();
    // The silhouette lies flat, which is what shows heading from above and
    // what vanishes edge-on from the side; the dot is there from any angle.
    this.dotTex = dotTexture();
    this.resolution = new THREE.Vector2(1, 1);
  }

  setResolution(w, h) {
    this.resolution.set(w, h);
    for (const it of this.items.values()) it.trail?.material.resolution.set(w, h);
  }

  update({ gliders, paths, pinnedId, rel, frame, now }) {
    const seen = new Set();
    for (const g of gliders) {
      if (g.alt == null) continue;
      const lon = nearLng(g.lon, rel.lon0);
      if (Math.abs(g.lat - rel.lat0) > rel.radius || Math.abs(lon - rel.lon0) > rel.radius) continue;
      seen.add(g.id);
      const it = this.items.get(g.id) ?? this.add(g.id);
      it.g = g;
      const pinned = g.id === pinnedId;
      const colour = gliderColour(g);
      const look = `${colour}|${pinned}`;
      if (it.look !== look) {
        it.look = look;
        it.fill.material.color.set(colour);
        it.dot.material.color.set(pinned ? '#FC8181' : colour);
        it.edge.material.color.set(pinned ? '#FC8181' : '#000000');
        it.edge.material.opacity = pinned ? 1 : 0.6;
        it.drop.material.color.set(colour);
        it.trailN = -1;                                   // recolour the trail
      }

      const p = displayPosition(g, now);
      const plon = nearLng(p.lon, rel.lon0);
      const ground = groundAt(rel, p.lat, plon);
      const alt = Math.max(g.alt, ground + 5);
      it.icon.position.set(frame.x(plon), frame.y(alt), frame.z(p.lat));
      it.icon.rotation.y = -((g.heading ?? 0) * DEG);
      it.dot.position.copy(it.icon.position);
      const d = it.drop.geometry.attributes.position;
      d.setXYZ(0, it.icon.position.x, it.icon.position.y, it.icon.position.z);
      d.setXYZ(1, it.icon.position.x, frame.y(ground), it.icon.position.z);
      d.needsUpdate = true;
      it.drop.geometry.computeBoundingSphere();

      const text = g.vario != null ? `${g.vario >= 0 ? '+' : ''}${g.vario.toFixed(1)}` : '';
      if (text !== it.text) {
        it.text = text;
        if (it.label) { this.group.remove(it.label); disposeObject(it.label); it.label = null; }
        if (text) {
          it.label = textSprite(text, { px: 10, center: [0.5, 1.9], bg: 'rgba(0,0,0,0.5)' });
          sizeSprite(it.label, this.viewH ?? 800, this.fov ?? 45);
          this.group.add(it.label);
        }
      }
      it.label?.position.copy(it.icon.position);

      // Reported positions only, never the projected icon position.
      const path = paths?.[g.id];
      const n = path?.length ?? 0;
      if (n !== it.trailN || pinned !== it.trailPinned) {
        it.trailN = n;
        it.trailPinned = pinned;
        this.setTrail(it, path, pinned, g, rel, frame);
      }
    }
    for (const [id, it] of this.items) {
      if (seen.has(id)) continue;
      this.group.remove(it.root);
      disposeObject(it.root, this.shared());
      if (it.label) { this.group.remove(it.label); disposeObject(it.label); }
      this.items.delete(id);
    }
  }

  /** The icon geometries every aircraft shares, never freed with one of them. */
  shared() { return new Set([this.geo.fill, this.geo.line, this.dotTex]); }

  add(id) {
    const root = new THREE.Group();
    const icon = new THREE.Group();
    const fill = new THREE.Mesh(this.geo.fill, new THREE.MeshBasicMaterial({ color: '#87CEEB', side: THREE.DoubleSide }));
    const edge = new THREE.LineLoop(this.geo.line, new THREE.LineBasicMaterial({ color: '#000000', transparent: true, opacity: 0.6 }));
    icon.add(fill, edge);
    const dot = new THREE.Sprite(new THREE.SpriteMaterial({
      map: this.dotTex, color: '#87CEEB', sizeAttenuation: false, transparent: true,
    }));
    dot.renderOrder = 5;
    const drop = new THREE.Line(
      new THREE.BufferGeometry().setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3)),
      new THREE.LineBasicMaterial({ color: '#87CEEB', transparent: true, opacity: 0.6 }),
    );
    root.add(icon, dot, drop);
    this.group.add(root);
    const it = { id, root, icon, fill, edge, dot, drop, trail: null, trailN: -1, label: null, text: null };
    this.items.set(id, it);
    return it;
  }

  setTrail(it, path, pinned, g, rel, frame) {
    if (it.trail) { it.root.remove(it.trail); it.trail.geometry.dispose(); it.trail.material.dispose(); it.trail = null; }
    if (!path || path.length < 2) return;
    const src = path.slice(-(pinned ? PINNED_TRAIL_POINTS : TRAIL_POINTS));
    const pos = [], col = [];
    const hex = pinned ? (g.is_tow ? '#ffffff' : '#ff3c3c') : gliderColour(g);
    src.forEach((q, i) => {
      if (q.alt == null) return;
      const lon = nearLng(q.lon, rel.lon0);
      const alt = Math.max(q.alt, groundAt(rel, q.lat, lon) + 5);
      pos.push(frame.x(lon), frame.y(alt), frame.z(q.lat));
      // Oldest faint, newest full — the 2D path's fade, done in brightness
      // because a fat line carries no per-vertex alpha.
      col.push(...linearRGB(hex, 0.3 + 0.7 * ((i + 1) / src.length)));
    });
    if (pos.length < 6) return;
    const geom = new LineGeometry();
    geom.setPositions(pos);
    geom.setColors(col);
    const mat = new LineMaterial({ linewidth: pinned ? 2.6 : 1.6, vertexColors: true,
                                   transparent: true, opacity: pinned ? 0.95 : 0.8 });
    mat.resolution.copy(this.resolution);
    it.trail = new Line2(geom, mat);
    it.root.add(it.trail);
  }

  /** Keep icons GLIDER_PX wide on screen and labels their set size. */
  fit(camera, viewH) {
    this.viewH = viewH;
    this.fov = camera.fov;
    const k = (2 * Math.tan((camera.fov / 2) * DEG)) / Math.max(1, viewH);
    for (const it of this.items.values()) {
      it.icon.scale.setScalar(GLIDER_PX * k * camera.position.distanceTo(it.icon.position));
      it.dot.scale.setScalar(DOT_PX * (it.look?.endsWith('true') ? 1.4 : 1) * k);
    }
  }

  relabel(viewH, fov) {
    this.viewH = viewH; this.fov = fov;
    for (const it of this.items.values()) if (it.label) sizeSprite(it.label, viewH, fov);
  }

  /** The aircraft drawn nearest (x, y) on a canvas w x h, within PICK_PX. */
  pick(camera, x, y, w, h) {
    let best = null, bestD = PICK_PX;
    const v = new THREE.Vector3();
    for (const it of this.items.values()) {
      v.copy(it.icon.position).project(camera);
      if (v.z > 1) continue;
      const d = Math.hypot((v.x + 1) / 2 * w - x, (1 - v.y) / 2 * h - y);
      if (d < bestD) { best = it.g; bestD = d; }
    }
    return best;
  }

  count() { return this.items.size; }

  dispose() {
    for (const it of this.items.values()) { disposeObject(it.root, this.shared()); if (it.label) disposeObject(it.label); }
    this.items.clear();
    this.geo.fill.dispose();
    this.geo.line.dispose();
    this.dotTex.dispose();
  }
}

/** Free everything under an object: geometries, materials and their textures. */
export function disposeObject(obj, keep = new Set()) {
  obj.traverse(o => {
    if (o.geometry && !keep.has(o.geometry)) o.geometry.dispose();
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) {
      if (keep.has(m)) continue;
      for (const v of Object.values(m)) if (v?.isTexture && !keep.has(v)) v.dispose();
      m.dispose();
    }
  });
}
