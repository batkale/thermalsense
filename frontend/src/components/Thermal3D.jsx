import { useEffect, useLayoutEffect, useRef, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { useLang } from '../i18n/LanguageContext.jsx';
import { fetchRelief } from '../services/api.js';
import {
  VERTICAL_SCALE, CLIMB_MS, reliefKey, reliefFromResponse, reliefFromLattice, flatRelief,
  makeFrame, groundAt, sunPosition, sunVector, skyTexture, buildTerrain, loadImagery,
  composeGround, findHotspots, buildThermals, buildCeiling, textSprite, sizeSprite,
  buildScalePost, thinTicks, GliderLayer, disposeObject,
} from '../services/thermal3d.js';

// -----------------------------------------------------------------------------
// The 3D view of one scan — loaded only when someone asks for it (App.jsx
// lazy-imports this file), so three.js never weighs on the 2D map's bundle.
//
// The Leaflet map stays mounted underneath: it keeps declaring the viewport the
// live socket streams, and switching back to 2D returns to exactly the view
// that was left. This view draws the scan patch only — the heatmap's own centre
// and radius — and a click on its terrain scans there, as a click on the map
// does. Everything it draws comes from props the 2D map already has, plus the
// patch's relief (/terrain/relief) and the same satellite imagery.
// -----------------------------------------------------------------------------

const FOV = 45;
const DEG = Math.PI / 180;

// Flipping between 2D and 3D unmounts this view, so the two things it fetches
// are kept: relief grids are ~40 KB each, and the imagery is one canvas
// (~7 MB), so only the last one.
const reliefCache = new Map();
const RELIEF_CACHE_MAX = 8;
let imageryCache = null;

function home(s) {
  const { camera, controls, frame, rel } = s;
  const target = new THREE.Vector3(0, frame.y(rel.mean) + 150, 0);
  // Far enough that a sphere round the whole patch fits the narrower of the
  // two fields of view: a tall, thin map (a phone, or the sidebar beside a
  // small window) is the one that otherwise cuts the patch off.
  const r = Math.hypot(frame.halfX, frame.halfZ) * 0.92;
  const vHalf = (FOV / 2) * DEG;
  const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
  const dist = r / Math.sin(Math.min(vHalf, hHalf));
  // From the south-south-west, a third of the way up: the sun's side for most
  // of a flying day in the northern hemisphere, so the lit slopes face us.
  const az = 200 * DEG, el = 33 * DEG;
  camera.position.set(target.x + Math.sin(az) * Math.cos(el) * dist,
                      target.y + Math.sin(el) * dist,
                      target.z - Math.cos(az) * Math.cos(el) * dist);
  controls.target.copy(target);
  controls.update();
}

function drop(s, name) {
  const part = s.parts[name];
  if (!part) return;
  s.world.remove(part);
  disposeObject(part);
  s.labels = s.labels.filter(l => !part.getObjectById(l.id) && l !== part);
  delete s.parts[name];
}

export default function Thermal3D({
  heatmap, gridMeta, rawInputs, weather, predictAlt, noInsolation,
  gliders, pinnedId, gliderPathsRef, onMapClick, onGliderClick,
}) {
  const { t, locale, windDir } = useLang();
  const glRef = useRef(null);
  const st = useRef(null);
  const [failed, setFailed] = useState(false);
  const [relief, setRelief] = useState(null);
  const [imagery, setImagery] = useState(null);      // { key, canvas | null }
  const [aircraft, setAircraft] = useState(0);
  // Open where the map has room for it beside the scene; folded to its title
  // on a short map (a phone), where seven lines would cover the patch it
  // describes. Measured on the map, not the window: on a phone the window is
  // tall and the map is a third of it.
  const boxRef = useRef(null);
  const [captionOpen, setCaptionOpen] = useState(false);
  useLayoutEffect(() => {
    const box = boxRef.current;
    setCaptionOpen(Boolean(box) && box.clientHeight >= 560 && box.clientWidth >= 760);
  }, []);
  // Read by the render loop and the click handler, which outlive any render.
  const live = useRef(null);
  live.current = { gliders, pinnedId, gliderPathsRef, onMapClick, onGliderClick };
  const latest = useRef(null);
  latest.current = { rawInputs, predictAlt };

  const key = gridMeta ? reliefKey(gridMeta.lat, gridMeta.lon) : null;

  // --- Renderer, camera, controls and the loop: once per mount -------------
  useEffect(() => {
    const host = glRef.current;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    } catch {
      setFailed(true);
      return undefined;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(FOV, 1, 20, 400000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = 1.45;              // ~83 degrees: never under the ground
    controls.minDistance = 600;
    controls.maxDistance = 90000;
    controls.screenSpacePanning = false;        // pan along the ground, not the screen
    controls.zoomToCursor = true;               // the wheel goes where the pointer is, as on the map

    // Imagery is already a photograph of lit ground, so the lights only add
    // relief: sky fill strong enough that a shaded slope stays readable, sun
    // strong enough that the slopes facing it — where the model's sun-angle
    // column says heating is — stand out.
    const hemi = new THREE.HemisphereLight('#eef5fc', '#5b5141', 1.9);
    const sun = new THREE.DirectionalLight('#fff6e8', 1.9);
    scene.add(hemi, sun, sun.target);
    const world = new THREE.Group();
    scene.add(world);
    const gliderLayer = new GliderLayer();
    scene.add(gliderLayer.group);
    const pxPerMetre = { value: 1 };

    const s = { renderer, scene, camera, controls, hemi, sun, world, gliderLayer, pxPerMetre,
                animated: [], parts: {}, labels: [], frame: null, rel: null,
                viewW: 1, viewH: 1, count: 0 };
    st.current = s;

    const resize = () => {
      const w = host.clientWidth || 1, h = host.clientHeight || 1;
      s.viewW = w; s.viewH = h;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      pxPerMetre.value = (h * renderer.getPixelRatio()) / (2 * Math.tan((FOV / 2) * DEG));
      gliderLayer.setResolution(w, h);
      gliderLayer.relabel(h, FOV);
      for (const l of s.labels) sizeSprite(l, h, FOV);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(host);
    resize();

    // A press that ends where it began is a click; anything else was the
    // controls turning the view, and must not scan wherever the drag stopped.
    let press = null;
    const onDown = e => { press = { x: e.clientX, y: e.clientY, at: performance.now(), b: e.button }; };
    const onUp = e => {
      const p = press;
      press = null;
      if (!p || p.b !== 0 || e.button !== 0) return;
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 5 || performance.now() - p.at > 700) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      const g = gliderLayer.pick(camera, x, y, rect.width, rect.height);
      if (g) { live.current.onGliderClick?.(g); return; }
      const mesh = s.parts.terrain?.userData.mesh;
      if (!mesh || !s.frame) return;
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2((x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1), camera);
      const hit = ray.intersectObject(mesh, false)[0];
      if (!hit) return;
      const lon = ((s.frame.lon(hit.point.x) + 540) % 360) - 180;
      live.current.onMapClick?.(s.frame.lat(hit.point.z), lon);
    };
    renderer.domElement.addEventListener('pointerdown', onDown);
    renderer.domElement.addEventListener('pointerup', onUp);

    const clock = new THREE.Clock();
    let raf = 0, lastAir = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const time = clock.getElapsedTime();
      for (const u of s.animated) u.uTime.value = time;
      controls.update();
      // Aircraft at the 2D icons' 10 fps; their size every frame, because it
      // follows the camera and the camera moves at 60.
      const now = performance.now();
      if (s.rel && now - lastAir > 100) {
        lastAir = now;
        const L = live.current;
        gliderLayer.update({ gliders: L.gliders, paths: L.gliderPathsRef?.current,
                             pinnedId: L.pinnedId, rel: s.rel, frame: s.frame, now: Date.now() });
        if (gliderLayer.count() !== s.count) { s.count = gliderLayer.count(); setAircraft(s.count); }
      }
      gliderLayer.fit(camera, s.viewH);
      thinTicks(s.parts.post, camera, s.viewH);
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onDown);
      renderer.domElement.removeEventListener('pointerup', onUp);
      controls.dispose();
      gliderLayer.dispose();
      disposeObject(world);
      scene.background?.dispose?.();
      renderer.dispose();
      // Without this every 2D -> 3D switch holds on to one more WebGL
      // context, and browsers stop handing them out at around sixteen.
      renderer.forceContextLoss();
      renderer.domElement.remove();
      st.current = null;
    };
  }, []);

  // --- The patch's relief ----------------------------------------------------
  useEffect(() => {
    if (!gridMeta) return undefined;
    const k = reliefKey(gridMeta.lat, gridMeta.lon);
    const hit = reliefCache.get(k);
    if (hit) { setRelief(hit); return undefined; }
    const ctl = new AbortController();
    const fallback = () => {
      const { rawInputs: inputs, predictAlt: alt } = latest.current;
      return reliefFromLattice(inputs, gridMeta)
        ?? flatRelief(gridMeta, alt ? alt.amsl - alt.agl : 0);
    };
    fetchRelief(gridMeta.lat, gridMeta.lon, ctl.signal)
      .then(r => {
        const rel = reliefFromResponse(r);
        // A relief for a different centre would put every column on the
        // wrong hillside; the lattice /predict sent is at least this patch.
        if (rel.key !== k) throw new Error('relief for another patch');
        reliefCache.set(k, rel);
        while (reliefCache.size > RELIEF_CACHE_MAX) reliefCache.delete(reliefCache.keys().next().value);
        return rel;
      })
      .catch(() => (ctl.signal.aborted ? null : fallback()))
      .then(rel => { if (rel && !ctl.signal.aborted) setRelief(rel); });
    return () => ctl.abort();
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps -- key is gridMeta's centre

  // --- Satellite imagery for the same patch ----------------------------------
  useEffect(() => {
    if (!gridMeta) return undefined;
    if (imageryCache?.key === key) { setImagery(imageryCache); return undefined; }
    const ctl = new AbortController();
    loadImagery(gridMeta.lat, gridMeta.lon, gridMeta.radius, ctl.signal)
      .catch(() => null)
      .then(canvas => {
        if (ctl.signal.aborted) return;
        const v = { key, canvas };
        if (canvas) imageryCache = v;
        setImagery(v);
      });
    return () => ctl.abort();
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps

  // --- Terrain: once per relief ------------------------------------------------
  useEffect(() => {
    const s = st.current;
    if (!s || !relief) return;
    drop(s, 'terrain');
    const frame = makeFrame(relief);
    const { group, mesh } = buildTerrain(relief, frame);
    group.userData.mesh = mesh;
    s.world.add(group);
    s.parts.terrain = group;
    const first = !s.rel;
    s.rel = relief;
    s.frame = frame;
    if (first) {
      home(s);
    } else {
      // A new scan centres the view on its patch and keeps the angle and
      // distance the user chose, so scanning from 3D does not throw the
      // camera back to its opening position.
      const offset = s.camera.position.clone().sub(s.controls.target);
      s.controls.target.set(0, frame.y(relief.mean) + 150, 0);
      s.camera.position.copy(s.controls.target).add(offset);
      s.controls.update();
    }
  }, [relief]);

  // --- The ground: imagery and the heatmap on it -----------------------------
  useEffect(() => {
    const s = st.current;
    const mesh = s?.parts.terrain?.userData.mesh;
    if (!mesh || !relief) return;
    // A new scan's heatmap arrives before its relief; until then the old
    // ground stays as it was rather than taking another patch's cells.
    if (gridMeta && reliefKey(gridMeta.lat, gridMeta.lon) !== relief.key) return;
    const tex = composeGround(relief, s.frame,
                              imagery?.key === relief.key ? imagery.canvas : null, heatmap, gridMeta);
    tex.anisotropy = s.renderer.capabilities.getMaxAnisotropy();
    mesh.material.map?.dispose();
    mesh.material.map = tex;
    mesh.material.needsUpdate = true;
  }, [relief, imagery, heatmap, gridMeta]);

  // --- Columns, ceiling, height post and north --------------------------------
  const terrainMean = predictAlt ? predictAlt.amsl - predictAlt.agl : relief?.mean ?? null;
  const topAgl = weather?.thermal_base ?? null;
  const topM = topAgl != null && terrainMean != null ? terrainMean + topAgl : null;
  const kind = weather?.cumulus?.kind ?? null;
  const drawThermals = Boolean(heatmap) && !noInsolation && topM != null;

  useEffect(() => {
    const s = st.current;
    if (!s || !relief || !s.frame) return;
    if (gridMeta && reliefKey(gridMeta.lat, gridMeta.lon) !== relief.key) return;
    for (const name of ['thermals', 'ceiling', 'post', 'north']) drop(s, name);
    s.animated = [];
    const { frame } = s;
    const labels = [];

    if (drawThermals) {
      // Thermals stand roughly a boundary layer's depth apart; never closer
      // than 900 m, or one hillside's peak draws as a bundle of columns.
      const spots = findHotspots(heatmap, gridMeta, frame, Math.max(900, 0.8 * topAgl));
      const { group, animated } = buildThermals({
        spots, rel: relief, frame, topM,
        wind: { speed: weather?.wind_speed, dir: weather?.wind_dir },
        cumulus: kind === 'cumulus', pxPerMetre: s.pxPerMetre,
      });
      s.world.add(group);
      s.parts.thermals = group;
      s.animated = animated;

      const ceiling = buildCeiling(frame, topM, kind);
      const tag = textSprite(
        t(kind === 'cumulus' ? 'view3dCeilingCu' : kind === 'blue' ? 'view3dCeilingBlue' : 'view3dCeiling',
          { n: Math.round(topM).toLocaleString(locale) }),
        { px: 12, center: [1, -0.4] });
      // North-east corner, reading inward: the post has the north-west one.
      tag.position.set(frame.halfX, frame.y(topM), -frame.halfZ);
      ceiling.add(tag);
      labels.push(tag);
      s.world.add(ceiling);
      s.parts.ceiling = ceiling;
    }

    const from = Math.floor(relief.min / 100) * 100;
    const to = Math.max(topM ?? 0, relief.max + 500);
    const post = buildScalePost(frame, from, to, h => t('metresMsl', { n: h.toLocaleString(locale) }));
    post.traverse(o => { if (o.userData?.label) labels.push(o); });
    s.world.add(post);
    s.parts.post = post;

    const north = new THREE.Group();
    const nl = textSprite(windDir(0), { px: 14, bold: true, bg: 'rgba(8,18,27,0.85)' });
    nl.position.set(0, frame.y(groundAt(relief, relief.lat0 + relief.radius, relief.lon0)) + 200,
                    -frame.halfZ - 400);
    north.add(nl);
    labels.push(nl);
    s.world.add(north);
    s.parts.north = north;

    for (const l of labels) sizeSprite(l, s.viewH, FOV);
    s.labels = labels;
  }, [relief, heatmap, gridMeta, weather, topM, kind, drawThermals, t, locale, windDir, topAgl]);

  // --- Light and sky from the sun at the forecast moment --------------------
  const when = weather?.target_at ? new Date(weather.target_at) : null;
  const sunNow = when && gridMeta ? sunPosition(when, gridMeta.lat, gridMeta.lon) : null;
  const whenMs = when?.getTime() ?? null;

  useEffect(() => {
    const s = st.current;
    if (!s || !relief) return;
    const sun = sunPosition(whenMs != null ? new Date(whenMs) : new Date(), relief.lat0, relief.lon0);
    const up = sun.alt > 0;
    // Below the horizon: a dim light from the north-west so the relief still
    // reads, and a night sky that says it is night.
    const dir = up ? sunVector(sun) : new THREE.Vector3(-0.5, 0.6, -0.6).normalize();
    s.sun.position.copy(dir.multiplyScalar(60000));
    s.sun.intensity = up ? 0.8 + 1.4 * Math.sin(sun.alt) ** 0.5 : 0.45;
    s.hemi.intensity = up ? 1.9 : 0.9;
    s.scene.background?.dispose?.();
    s.scene.background = skyTexture(sun.alt);
  }, [whenMs, relief]);

  const reset = useCallback(() => { if (st.current?.rel) home(st.current); }, []);

  // --- Caption ------------------------------------------------------------------
  const ready = relief && relief.key === key;
  let sunLine = null;
  if (sunNow) {
    if (sunNow.alt <= 0) {
      sunLine = t('view3dSunDown');
    } else {
      const off = weather?.utc_offset_s;
      const local = new Date(when.getTime() + (off ?? 0) * 1000);
      const hhmm = `${String(local.getUTCHours()).padStart(2, '0')}:${String(local.getUTCMinutes()).padStart(2, '0')}`;
      sunLine = t('view3dSun', { time: off == null ? `${hhmm} UTC` : hhmm });
    }
  }
  const v = VERTICAL_SCALE.toLocaleString(locale);
  const terrainLine = !relief ? null
    : relief.source === 'copernicus' ? t('view3dTerrainDem', { v })
    : relief.source === 'lattice'    ? t('view3dTerrainLattice', { v })
    : t('view3dTerrainFlat');
  const ceilingLine = topM == null ? t('view3dCeilingNone')
    : t(kind === 'cumulus' ? 'view3dCeilingCu' : kind === 'blue' ? 'view3dCeilingBlue' : 'view3dCeiling',
        { n: Math.round(topM).toLocaleString(locale) });

  return (
    <div className="view3d" ref={boxRef}>
      <div className="view3d-gl" ref={glRef} />

      {failed && <div className="view3d-empty">{t('view3dNoWebgl')}</div>}
      {!failed && !gridMeta && <div className="view3d-empty">{t('view3dNoScan')}</div>}
      {!failed && gridMeta && !ready && <div className="view3d-status" role="status">{t('view3dLoading')}</div>}

      {!failed && (
        <div className="view3d-tools">
          <button type="button" className="view3d-btn" onClick={reset} disabled={!relief}>
            {t('view3dReset')}
          </button>
          <span className="view3d-help">{t('view3dHelp')}</span>
        </div>
      )}

      <div className="view3d-foot">
      {!failed && relief && (
        <details className="view3d-caption" open={captionOpen}
                 onToggle={e => setCaptionOpen(e.currentTarget.open)}>
          <summary>{t('view3dTitle')}</summary>
          <ul>
            {predictAlt && <li>{t('view3dGround', { n: predictAlt.amsl.toLocaleString(locale) })}</li>}
            <li>{drawThermals ? t('view3dColumns') : t('view3dNoThermals')}</li>
            {!noInsolation && <li>{ceilingLine}</li>}
            {drawThermals && weather?.wind_speed != null && (
              <li>{t('view3dDrift', { speed: Math.round(weather.wind_speed), climb: CLIMB_MS })}</li>
            )}
            <li>{t('view3dAircraft', { n: aircraft })}</li>
            <li>{terrainLine}</li>
            {sunLine && <li>{sunLine}</li>}
            {imagery && imagery.key === relief.key && !imagery.canvas && (
              <li className="warn">{t('view3dImageryFailed')}</li>
            )}
          </ul>
        </details>
      )}

      {/* Legal credit for what this view draws, as on the 2D map's
          attribution control — not UI copy, so not translated. */}
      <div className="view3d-attrib">
        Imagery &copy; Esri, Maxar, Earthstar Geographics, USDA FSA, USGS, Aerogrid, IGN, IGP, and the GIS User Community
        {' | '}Terrain: Copernicus DEM &copy; DLR e.V. 2010&ndash;2014, &copy; Airbus Defence and Space GmbH 2014&ndash;2018, provided under COPERNICUS by the EU and ESA
      </div>
      </div>
    </div>
  );
}
