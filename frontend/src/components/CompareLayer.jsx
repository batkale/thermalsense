import { useEffect, useLayoutEffect, useRef, useCallback, useState } from 'react';
import { createPortal } from 'react-dom';
import L from 'leaflet';
import { useMap, useMapEvents } from 'react-leaflet';

import { useLang } from '../i18n/LanguageContext.jsx';
import { nearLng, unwrapNear } from '../services/worldWrap.js';

// -----------------------------------------------------------------------------
// Raw data / product comparison — a before/after divider across the map
//
// Left of the divider is what arrives: position reports from aircraft, the
// elevation samples a prediction fetched, one weather sample. Right of it is
// the map as it is normally drawn — everything ThermalSense works out from
// those inputs.
//
// The split itself is CSS, not drawing. Every canvas still spans the whole map;
// the layers carry one of the two class names below and index.css clips each to
// its side of --cmp-split on the Leaflet container. Dragging the divider
// therefore repaints no canvas at all, which matters when one of them is a
// 40,401-cell grid.
// -----------------------------------------------------------------------------
export const LAYER_BUILT = 'cmp-layer-built';
export const LAYER_RAW   = 'cmp-layer-raw';

// Reports drawn behind each aircraft, and behind the pinned one — whose whole
// flight is on hand because pinning backfills it. 800 matches the cap the
// smoothed path on the other side of the divider uses, so the two halves show
// the same stretch of the same flight.
const TRAIL_POINTS        = 40;
const PINNED_TRAIL_POINTS = 800;
// Below this the trail of a whole minute is a few pixels long: a smudge under
// the dot rather than separate reports, for one fillRect each.
const TRAIL_MIN_ZOOM = 9;
// Elevation figures are written out only once neighbouring samples are far
// enough apart on screen for four digits to fit between them.
const LABEL_MIN_GAP_PX = 28;

// Low ground deep violet, high ground near white. Violet on purpose: it is in
// neither the heatmap's blue-to-red ramp nor the satellite imagery underneath.
// An earth-toned ramp was tried first and vanished into the hillsides it was
// drawn on, and anything from the heatmap's palette would make these squares —
// measurements of height — read as a second, coarser forecast of lift.
function elevationColor(t) {
  const r = Math.round(76 + t * 177);
  const g = Math.round(29 + t * 213);
  const b = Math.round(149 + t * 106);
  return `rgb(${r},${g},${b})`;
}

function drawElevationSamples(ctx, map, gridMeta, rawInputs) {
  const { lat, radius } = gridMeta;
  const lon = nearLng(gridMeta.lon, map.getCenter().lng);
  const { elevation, elevation_rows: rows, elevation_cols: cols } = rawInputs;
  if (!elevation || rows < 2 || cols < 2 || elevation.length !== rows * cols) return;

  const sw = map.latLngToContainerPoint([lat - radius, lon - radius]);
  const ne = map.latLngToContainerPoint([lat + radius, lon + radius]);

  // The patch every input below describes — and the area the one weather
  // sample is spread across, which has no mark of its own to draw.
  ctx.save();
  ctx.setLineDash([4, 4]);
  ctx.lineWidth   = 1;
  ctx.strokeStyle = 'rgba(255,255,255,0.6)';
  ctx.strokeRect(Math.round(sw.x) + 0.5, Math.round(ne.y) + 0.5,
                 Math.round(ne.x - sw.x), Math.round(sw.y - ne.y));
  ctx.restore();

  let lo = Infinity, hi = -Infinity;
  for (const v of elevation) { if (v < lo) lo = v; if (v > hi) hi = v; }
  const span = hi - lo;

  // Same index arithmetic as the heatmap: row 0 is the southern edge, column 0
  // the western one, and the last of each lands exactly on the far edge.
  const latStep  = (2 * radius) / (rows - 1);
  const lonStep  = (2 * radius) / (cols - 1);
  const gapPx    = (ne.x - sw.x) / (cols - 1);
  const half     = Math.max(1.5, Math.min(5, gapPx * 0.22));
  const labelled = gapPx >= LABEL_MIN_GAP_PX;

  ctx.save();
  ctx.font         = '9px sans-serif';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'top';
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const v  = elevation[r * cols + c];
      const pt = map.latLngToContainerPoint([lat - radius + r * latStep,
                                             lon - radius + c * lonStep]);
      // A flat patch has no range to place a value in; mid-ramp says "no
      // relief" where either end would claim a floor or a summit.
      ctx.fillStyle   = elevationColor(span > 0 ? (v - lo) / span : 0.5);
      ctx.strokeStyle = 'rgba(255,255,255,0.9)';
      ctx.lineWidth   = 1;
      ctx.fillRect(pt.x - half, pt.y - half, half * 2, half * 2);
      ctx.strokeRect(pt.x - half, pt.y - half, half * 2, half * 2);
      if (labelled) {
        ctx.lineWidth   = 2.5;
        ctx.strokeStyle = 'rgba(0,0,0,0.75)';
        ctx.fillStyle   = '#fff';
        ctx.strokeText(String(v), pt.x, pt.y + half + 2);
        ctx.fillText(String(v),   pt.x, pt.y + half + 2);
      }
    }
  }
  ctx.restore();
}

function drawBeacons(ctx, map, gliders, paths, pinnedId, width, height) {
  const withTrail = map.getZoom() >= TRAIL_MIN_ZOOM;

  const cLng = map.getCenter().lng;
  ctx.save();
  ctx.fillStyle = '#fff';
  for (const g of gliders) {
    // The reported position, never displayPosition(): projecting an aircraft
    // forward between beacons is one of the things built on this data, so it
    // belongs to the other side of the divider.
    const pt = map.latLngToContainerPoint([g.lat, nearLng(g.lon, cLng)]);
    if (pt.x < -40 || pt.y < -40 || pt.x > width + 40 || pt.y > height + 40) continue;

    const reports = withTrail ? paths[g.id] : null;
    if (reports && reports.length) {
      const keep  = g.id === pinnedId ? PINNED_TRAIL_POINTS : TRAIL_POINTS;
      // Same copy as the dot: the trail is placed by its newest report.
      const trail = unwrapNear(reports.slice(-keep), cLng);
      const n     = trail.length;
      for (let i = 0; i < n; i++) {
        const p = map.latLngToContainerPoint([trail[i].lat, trail[i].lon]);
        ctx.globalAlpha = 0.25 + 0.6 * ((i + 1) / n);
        ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
      }
    }

    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = 'rgba(0,0,0,0.7)';
    ctx.lineWidth   = 1;
    ctx.stroke();
  }
  ctx.restore();
}

/**
 * The "before" half: inputs exactly as they arrive, with nothing inferred.
 *
 * Every aircraft is the same white dot — no gold for circling, no tow colours,
 * no heading — because which aircraft is thermalling is a conclusion, and
 * conclusions are what the other half is for. Mounted only while the
 * comparison is on, so it costs nothing the rest of the time.
 */
export function RawDataCanvas({ gliders, gridMeta, rawInputs, pinnedId, gliderPathsRef }) {
  const map       = useMap();
  const canvasRef = useRef(null);
  const dataRef   = useRef({ gliders, gridMeta, rawInputs, pinnedId });
  dataRef.current = { gliders, gridMeta, rawInputs, pinnedId };

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { gliders, gridMeta, rawInputs, pinnedId } = dataRef.current;

    const { width, height } = map.getContainer().getBoundingClientRect();
    canvas.width  = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);

    if (rawInputs && gridMeta) drawElevationSamples(ctx, map, gridMeta, rawInputs);
    drawBeacons(ctx, map, gliders, gliderPathsRef.current, pinnedId, width, height);
  }, [map, gliderPathsRef]);

  useMapEvents({
    move:    draw,
    zoomend: draw,
    resize:  draw,
    zoomstart() {
      const canvas = canvasRef.current;
      if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    },
  });

  // `gliders` gets a new identity with every stream frame, which is the right
  // cadence here: a frame is when new reports can have arrived.
  useEffect(() => { draw(); }, [draw, gliders, gridMeta, rawInputs, pinnedId]);

  return createPortal(
    <canvas
      ref={canvasRef}
      className={LAYER_RAW}
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', zIndex: 449 }}
    />,
    map.getContainer()
  );
}

const clampPct = (v) => Math.max(0, Math.min(100, v));

/**
 * The divider, and the two cards that say what each side is showing.
 *
 * The cards hang off the divider rather than the map's corners so each stays on
 * the half it describes wherever the divider is dragged.
 */
export function CompareControl({ gliders, activeThermals, gridMeta, rawInputs, noInsolation }) {
  const map = useMap();
  const { t, locale } = useLang();
  const rootRef = useRef(null);
  const grabRef = useRef(null);
  const [split, setSplit] = useState(50);

  // Layout effects, not effects: the class is what clips the layers, and a
  // frame painted before it lands shows both halves on top of each other.
  useLayoutEffect(() => {
    const container = map.getContainer();
    container.classList.add('cmp-on');
    return () => {
      container.classList.remove('cmp-on');
      container.style.removeProperty('--cmp-split');
    };
  }, [map]);

  useLayoutEffect(() => {
    map.getContainer().style.setProperty('--cmp-split', `${split}%`);
  }, [map, split]);

  // Native listeners, not React props. The divider sits inside Leaflet's
  // container, and Leaflet's own handlers run on the way up to React's root —
  // so by the time a synthetic handler could stop a press, the map has already
  // started panning under it, and the release has already been read as a click
  // asking for a prediction.
  useEffect(() => {
    const root = rootRef.current;
    const grab = grabRef.current;
    if (!root || !grab) return;
    const container = map.getContainer();

    // Covers the cards too: a click on a caption is not a click on the map.
    L.DomEvent.disableClickPropagation(root);

    let dragging = false;
    const toPct = (clientX) => {
      const r = container.getBoundingClientRect();
      return r.width > 0 ? clampPct(((clientX - r.left) / r.width) * 100) : 50;
    };
    const down = (e) => {
      if (e.button != null && e.button !== 0) return;
      dragging = true;
      grab.setPointerCapture(e.pointerId);
      e.preventDefault();   // no text selection, no compatibility mousedown
    };
    const move = (e) => { if (dragging) setSplit(toPct(e.clientX)); };
    const up   = (e) => {
      dragging = false;
      if (grab.hasPointerCapture(e.pointerId)) grab.releasePointerCapture(e.pointerId);
    };
    const reset = () => setSplit(50);

    grab.addEventListener('pointerdown',   down);
    grab.addEventListener('pointermove',   move);
    grab.addEventListener('pointerup',     up);
    grab.addEventListener('pointercancel', up);
    grab.addEventListener('dblclick',      reset);
    return () => {
      grab.removeEventListener('pointerdown',   down);
      grab.removeEventListener('pointermove',   move);
      grab.removeEventListener('pointerup',     up);
      grab.removeEventListener('pointercancel', up);
      grab.removeEventListener('dblclick',      reset);
    };
  }, [map]);

  const onKeyDown = (e) => {
    const step = e.shiftKey ? 10 : 2;
    if      (e.key === 'ArrowLeft')  setSplit(s => clampPct(s - step));
    else if (e.key === 'ArrowRight') setSplit(s => clampPct(s + step));
    else if (e.key === 'Home')       setSplit(0);
    else if (e.key === 'End')        setSplit(100);
    else return;
    e.preventDefault();
  };

  const fmt = (n) => n.toLocaleString(locale);

  const cells    = gridMeta ? gridMeta.rows * gridMeta.cols : null;
  const samples  = rawInputs?.elevation?.length ?? null;
  // Spacing of the fetched samples, worked out from the grid they came with
  // rather than quoted: 111.32 km is one degree of latitude.
  const sampleKm = gridMeta && rawInputs?.elevation_rows > 1
    ? ((2 * gridMeta.radius) / (rawInputs.elevation_rows - 1)) * 111.32
    : null;
  // Same definition as the sidebar's count, so the two never disagree on screen.
  const circling = gliders.filter(g => g.circling).length;

  return createPortal(
    <div ref={rootRef} className="cmp-divider">
      <div
        ref={grabRef}
        className="cmp-grab"
        role="slider"
        tabIndex={0}
        aria-orientation="horizontal"
        aria-label={t('compareHandle')}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(split)}
        onKeyDown={onKeyDown}
      >
        <span className="cmp-knob" aria-hidden="true">
          <svg width="18" height="10" viewBox="0 0 18 10">
            <path d="M6 0 L0 5 L6 10 Z M12 0 L18 5 L12 10 Z" fill="currentColor" />
          </svg>
        </span>
      </div>

      <section className="cmp-card raw">
        <div className="cmp-card-head">
          <span className="cmp-tag">{t('compareBefore')}</span>
          <span className="cmp-title">{t('compareRawTitle')}</span>
        </div>
        <ul className="cmp-rows">
          <li><span className="cmp-swatch beacon" />{t('rawBeacons', { n: fmt(gliders.length) })}</li>
          {samples != null && sampleKm != null && (
            <li>
              <span className="cmp-swatch elev" />
              {t('rawElevation', {
                n:  fmt(samples),
                km: sampleKm.toLocaleString(locale, { maximumFractionDigits: 1 }),
              })}
            </li>
          )}
          {gridMeta
            ? <li><span className="cmp-swatch area" />{t('rawWeather')}</li>
            : <li className="cmp-hint">{t('rawNoGrid')}</li>}
        </ul>
      </section>

      <section className="cmp-card built">
        <div className="cmp-card-head">
          <span className="cmp-tag">{t('compareAfter')}</span>
          <span className="cmp-title">{t('compareBuiltTitle')}</span>
        </div>
        <ul className="cmp-rows">
          {/* At night the grid is zeroed rather than predicted, so claiming
              40,401 probabilities here would describe a map that is not there. */}
          {cells == null ? (
            <li className="cmp-hint">{t('clickMapToPredict')}</li>
          ) : noInsolation ? (
            <li className="cmp-hint">{t('noInsolation')}</li>
          ) : (
            <li>
              <span className="cmp-swatch heat" />
              {rawInputs?.feature_count != null
                ? t('builtCells', { n: fmt(cells), f: rawInputs.feature_count })
                : t('builtCellsOnly', { n: fmt(cells) })}
            </li>
          )}
          <li><span className="cmp-swatch circling" />{t('builtCircling', { n: circling })}</li>
          <li><span className="cmp-swatch thermal" />{t('builtThermals', { n: activeThermals.length })}</li>
        </ul>
      </section>
    </div>,
    map.getContainer()
  );
}
