import { useState, useCallback, useEffect, useRef } from 'react';
import ThermalMap        from './components/ThermalMap.jsx';
import InfoPanel         from './components/InfoPanel.jsx';
import WeatherBar        from './components/WeatherBar.jsx';
import ExportButton      from './components/ExportButton.jsx';
import SearchBar         from './components/SearchBar.jsx';
import PinnedGliderCard  from './components/PinnedGliderCard.jsx';
import { useBackend }    from './hooks/useBackend.js';
import { triggerRetrain } from './services/api.js';
import { useLang }        from './i18n/LanguageContext.jsx';
import { ADMIN_TOKEN }    from './config.js';

// Retrain is an operator action: always available in dev, in production only
// when an admin token is configured for the bundle.
const CAN_RETRAIN = import.meta.env.DEV || Boolean(ADMIN_TOKEN);

export default function App() {
  const { t } = useLang();
  const [forecastH, setForecastH] = useState(0);
  // Stored as a translation key so the banner follows a language switch
  const [retrainMsgKey, setRetrainMsgKey] = useState('');
  const [showAirborneOnly, setShowAirborneOnly] = useState(false);
  const [showOgnHeatmap,   setShowOgnHeatmap]   = useState(true);
  const [flyTarget, setFlyTarget] = useState(null);
  const [pinnedId, setPinnedId] = useState(null);
  const lastPinnedRef = useRef(null);
  const locationRef = useRef(null);

  const {
    heatmap, gridMeta, gliders, activeThermals,
    weather, loading, error,
    predict, gliderPathsRef,
  } = useBackend();

  // A bare map click carries no altitude — the backend picks a working height
  // above the terrain. Clicking a glider sends that glider's own altitude, which
  // is what the model was trained on.
  const handleMapClick = useCallback((lat, lon) => {
    locationRef.current = { lat, lon, alt: null };
    predict(lat, lon, forecastH, null);
  }, [predict, forecastH]);


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

  const visibleGliders = showAirborneOnly
    ? gliders.filter(g => {
        if (g.agl != null)       return g.agl > 10;
        if (g.speed_kmh != null) return g.speed_kmh > 30;
        return true;
      })
    : gliders;

  const heatmapMax = heatmap ? heatmap.reduce((a, b) => Math.max(a, b), 0) : null;

  const livePinned = pinnedId ? gliders.find(g => g.id === pinnedId) ?? null : null;
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
            gliders={visibleGliders}
            activeThermals={activeThermals}
            showOgnHeatmap={showOgnHeatmap}
            onMapClick={handleMapClick}
            onGliderClick={g => {
              setPinnedId(g.id);
              locationRef.current = { lat: g.lat, lon: g.lon, alt: g.alt };
              predict(g.lat, g.lon, forecastH, g.alt);
            }}
            pinnedId={pinnedId}
            flyTarget={flyTarget}
            gliderPathsRef={gliderPathsRef}
          />
        </div>

        <aside className="sidebar">
          <SearchBar gliders={gliders} onSelect={setFlyTarget} />

          <InfoPanel
            gliders={visibleGliders}
            totalGliders={gliders.length}
            thermalBase={weather?.thermal_base}
            cape={weather?.cape}
            heatmapMax={heatmapMax}
            showAirborneOnly={showAirborneOnly}
            onAirborneToggle={setShowAirborneOnly}
            showOgnHeatmap={showOgnHeatmap}
            onOgnHeatmapToggle={setShowOgnHeatmap}
            onRetrain={CAN_RETRAIN ? handleRetrain : null}
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
          {error   && <div className="status error">⚠ {error}</div>}
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
    </div>
  );
}
