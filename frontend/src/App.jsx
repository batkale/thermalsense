import { useState, useCallback, useEffect, useRef } from 'react';
import ThermalMap        from './components/ThermalMap.jsx';
import InfoPanel         from './components/InfoPanel.jsx';
import WeatherBar        from './components/WeatherBar.jsx';
import ExportButton      from './components/ExportButton.jsx';
import SearchBar         from './components/SearchBar.jsx';
import PinnedGliderCard  from './components/PinnedGliderCard.jsx';
import TrainingPanel     from './components/TrainingPanel.jsx';
import { AlertIcon }     from './components/Icons.jsx';
import { useCloudFrame } from './components/CloudLayer.jsx';
import { useBackend }    from './hooks/useBackend.js';
import { triggerRetrain } from './services/api.js';
import { useLang }        from './i18n/LanguageContext.jsx';
import { ADMIN_TOKEN, HOME_SCAN, HOME_UTC_OFFSET_H, HOME_DAY_HOURS, FORECAST_STEPS } from './config.js';

// Retrain is an operator action: always available in dev, in production only
// when an admin token is configured for the bundle.
const CAN_RETRAIN = import.meta.env.DEV || Boolean(ADMIN_TOKEN);

/**
 * Hours ahead for the opening scan: 0 by day at HOME_SCAN, otherwise the
 * slider stop nearest the next local noon. A night scan is zeroed by the night
 * guard, which would open the demo on an empty map. The slider shows the
 * offset, so the map never claims to be "now" when it is not.
 */
function openingForecastH(now) {
  const local = (now.getUTCHours() + now.getUTCMinutes() / 60 + HOME_UTC_OFFSET_H) % 24;
  const [from, to] = HOME_DAY_HOURS;
  if (local >= from && local <= to) return 0;
  const ahead = (12 - local + 24) % 24;
  return FORECAST_STEPS.reduce((best, h) => (Math.abs(h - ahead) < Math.abs(best - ahead) ? h : best), 0);
}

export default function App() {
  const { t } = useLang();
  const [forecastH, setForecastH] = useState(0);
  // Stored as a translation key so the banner follows a language switch
  const [retrainMsgKey, setRetrainMsgKey] = useState('');
  const [showAirborneOnly, setShowAirborneOnly] = useState(false);
  const [showOgnHeatmap,   setShowOgnHeatmap]   = useState(true);
  // Before/after divider across the map: raw inputs on one side, what is built
  // from them on the other. ?compare=1 opens straight into it, for the same
  // reason ?panel=training exists — it is a view people link to.
  const [compare, setCompare] = useState(
    () => new URLSearchParams(window.location.search).get('compare') === '1'
  );
  // Where the clouds are now, drawn from Meteosat's cloud mask and cloud-top
  // height under the heatmap. Off by default, and ?clouds=1 opens with it on,
  // like ?compare=1.
  const [showClouds, setShowClouds] = useState(
    () => new URLSearchParams(window.location.search).get('clouds') === '1'
  );
  const cloudFrame = useCloudFrame(showClouds);
  const [cloudStatus, setCloudStatus] = useState(null);
  // A pan asks for a new view, but the clouds already drawn stay on screen until
  // it arrives, so "loading" after an image is shown would only make the caption
  // flicker. An error is reported either way: part of the view may be stale.
  const handleCloudStatus = useCallback(
    s => setCloudStatus(prev => (s === 'loading' && prev === 'ok' ? prev : s)), []
  );
  const toggleClouds = useCallback(on => {
    setShowClouds(on);
    setCloudStatus(null);
  }, []);
  const [flyTarget, setFlyTarget] = useState(null);
  const [pinnedId, setPinnedId] = useState(null);
  // Opens straight into the panel with ?panel=training, so a training node can
  // be bookmarked to the view it is actually watched through.
  const [showTraining, setShowTraining] = useState(
    () => new URLSearchParams(window.location.search).get('panel') === 'training'
  );
  const closeTraining = useCallback(() => setShowTraining(false), []);
  const lastPinnedRef = useRef(null);
  const locationRef = useRef(null);

  // The last bare-map click, as a fresh object each time so two clicks on the
  // same spot both count. The map flies in to it when the scan would otherwise
  // be too small to see — see ScanFocusController.
  const [scanFocus, setScanFocus] = useState(null);

  const {
    heatmap, gridMeta, gliders, activeThermals,
    weather, predictAlt, noInsolation, meteoFallback, rawInputs, loading, error, pinnedGlider: livePinned,
    pendingScan, predict, setViewport, seedTrack, gliderPathsRef,
  } = useBackend(pinnedId);

  // A bare map click carries no altitude — the backend picks a working height
  // above the terrain. Clicking a glider sends that glider's own altitude, which
  // is what the model was trained on.
  const handleMapClick = useCallback((lat, lon) => {
    locationRef.current = { lat, lon, alt: null };
    setScanFocus({ lat, lon });
    predict(lat, lon, forecastH, null);
  }, [predict, forecastH]);

  // The opening scan: İnönü, at once, so the map opens on a filled Turkish
  // heatmap instead of an empty one waiting for a click. Declared before the
  // slider effect below on purpose — it seeds locationRef, and that effect then
  // issues the request (once, debounced), whether or not the hour changes.
  useEffect(() => {
    locationRef.current = { lat: HOME_SCAN.lat, lon: HOME_SCAN.lon, alt: null };
    setScanFocus({ lat: HOME_SCAN.lat, lon: HOME_SCAN.lon });
    setForecastH(openingForecastH(new Date()));
  }, []);

  // Moving the forecast slider re-runs the scan already on the map. It used to
  // change only what the *next* click would ask for: the label read "+6 h" over
  // a heatmap still answering for now, and locationRef — kept for exactly this
  // — was written on every click and never read. Debounced, because dragging
  // across the range would otherwise queue one prediction per step behind
  // PREDICT_CONCURRENCY=1.
  useEffect(() => {
    const loc = locationRef.current;
    if (!loc) return undefined;
    const id = setTimeout(() => predict(loc.lat, loc.lon, forecastH, loc.alt), 350);
    return () => clearTimeout(id);
  }, [forecastH, predict]);


  const handleRetrain = useCallback(async () => {
    setRetrainMsgKey('');
    try {
      const { status } = await triggerRetrain();
      setRetrainMsgKey(status === 'already_running' ? 'retrainAlready' : 'retrainStarted');
    } catch {
      setRetrainMsgKey('retrainFailed');
    }
    setTimeout(() => setRetrainMsgKey(''), 4000);
  }, []);

  // Height above ground is the only reliable ground test, so it is tried first.
  // agl is null while the backend's elevation cache is still cold for this patch
  // of ground — a frame or two after a viewport moves somewhere new. Ground speed
  // is the fallback for that window only: it cannot tell a cruising glider from a
  // winch launch or a takeoff roll, both of which comfortably clear 30 km/h.
  const visibleGliders = showAirborneOnly
    ? gliders.filter(g => {
        if (g.agl != null)       return g.agl > 10;
        if (g.speed_kmh != null) return g.speed_kmh > 30;
        return true;
      })
    : gliders;

  const heatmapMax = heatmap ? heatmap.reduce((a, b) => Math.max(a, b), 0) : null;

  // One line over the map itself, where the click happened. The sidebar already
  // carries these states, but a scan that draws nothing — no sun, or a failed
  // request — left the map unchanged and the explanation 800 px to the right.
  const mapNoteKey = loading ? 'fetchingPrediction'
    : error                  ? error
    : (heatmap && noInsolation) ? 'noInsolationMap'
    : (heatmap && meteoFallback) ? 'meteoFallbackMap'
    : null;

  // livePinned comes from the socket when it carries the aircraft and from the
  // 5 s by-id poll when it does not, so it stays current wherever the map is
  // pointing. The ref only bridges the frame or two before the first of those
  // answers — and is dropped the moment the pin changes, or it would bridge
  // that gap with the *previous* aircraft's numbers under the new one's id.
  if (lastPinnedRef.current?.id !== pinnedId) lastPinnedRef.current = null;
  if (livePinned) lastPinnedRef.current = livePinned;
  const pinnedGlider = pinnedId ? (livePinned ?? lastPinnedRef.current) : null;

  return (
    <div className="app">
      <WeatherBar
        weather={weather}
        forecastH={forecastH}
        onForecastChange={setForecastH}
        loading={loading}
        error={error}
      />

      <div className="main-row">
        <div className="map-wrap">
          <ThermalMap
            heatmap={heatmap}
            gridMeta={gridMeta}
            pendingScan={pendingScan}
            scanFocus={scanFocus}
            gliders={visibleGliders}
            activeThermals={activeThermals}
            showOgnHeatmap={showOgnHeatmap}
            onMapClick={handleMapClick}
            onGliderClick={g => {
              setPinnedId(g.id);
              // The flight so far is only drawn for the pinned glider, so it is
              // only fetched here — see seedTrack.
              seedTrack(g.id);
              locationRef.current = { lat: g.lat, lon: g.lon, alt: g.alt };
              predict(g.lat, g.lon, forecastH, g.alt);
            }}
            onViewportChange={setViewport}
            pinnedId={pinnedId}
            flyTarget={flyTarget}
            gliderPathsRef={gliderPathsRef}
            compare={compare}
            rawInputs={rawInputs}
            noInsolation={noInsolation}
            cloudTime={showClouds ? cloudFrame.time : null}
            cloudTopTime={cloudFrame.topTime}
            onCloudStatus={handleCloudStatus}
          />
          {/* With the comparison on, its two caption cards hang from the top of
              the divider — which starts at the centre, exactly where this note
              sits — so the note drops to the bottom edge instead of covering
              the labels that say which half is which. */}
          {mapNoteKey && (
            <div
              className={`map-note${error && !loading ? ' error' : ''}${compare ? ' low' : ''}`}
              role="status"
            >
              {t(mapNoteKey)}
            </div>
          )}
        </div>

        <aside className="sidebar">
          <SearchBar onSelect={setFlyTarget} />

          <InfoPanel
            gliders={visibleGliders}
            totalGliders={gliders.length}
            thermalBase={weather?.thermal_base}
            cape={weather?.cape}
            heatmapMax={heatmapMax}
            predictAlt={predictAlt}
            noInsolation={noInsolation}
            showAirborneOnly={showAirborneOnly}
            onAirborneToggle={setShowAirborneOnly}
            showOgnHeatmap={showOgnHeatmap}
            onOgnHeatmapToggle={setShowOgnHeatmap}
            compare={compare}
            onCompareToggle={setCompare}
            showClouds={showClouds}
            onCloudsToggle={toggleClouds}
            clouds={{ time: cloudFrame.time, failed: cloudFrame.failed, status: cloudStatus }}
            forecastH={forecastH}
            onRetrain={CAN_RETRAIN ? handleRetrain : null}
            onOpenTraining={() => setShowTraining(true)}
          />

          <PinnedGliderCard glider={pinnedGlider} onUnpin={() => { setPinnedId(null); lastPinnedRef.current = null; }} />

          {retrainMsgKey && <div className="status retrain">{t(retrainMsgKey)}</div>}

          <ExportButton
            heatmap={heatmap}
            gridMeta={gridMeta}
            thermalBase={weather?.thermal_base}
            cape={weather?.cape}
          />

          {loading && <div className="status loading">{t('fetchingPrediction')}</div>}
          {error   && <div className="status error"><AlertIcon /> {t(error)}</div>}
          {!loading && !error && !heatmap && <div className="status hint">{t('clickMapToPredict')}</div>}

          <div className="legend">
            <div className="legend-title">{t('legendTitle')}</div>
            <div className="legend-bar" />
            <div className="legend-labels">
              <span>{t('low')}</span><span>{t('high')}</span>
            </div>
            <div className="legend-glyphs">
              <span><span className="dot yellow"  /> {t('circling')}</span>
              <span><span className="dot blue"    /> {t('glider')}</span>
              <span><span className="dot orange"  /> {t('towPlane')}</span>
              <span><span className="dot thermal" /> {t('activeThermal')}</span>
            </div>
          </div>
        </aside>
      </div>

      {showTraining && <TrainingPanel onClose={closeTraining} />}
    </div>
  );
}
