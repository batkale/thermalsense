import { useState, useCallback, useEffect, useRef } from 'react';
import ThermalMap        from './components/ThermalMap.jsx';
import InfoPanel         from './components/InfoPanel.jsx';
import WeatherBar        from './components/WeatherBar.jsx';
import ExportButton      from './components/ExportButton.jsx';
import SearchBar         from './components/SearchBar.jsx';
import PinnedGliderCard  from './components/PinnedGliderCard.jsx';
import { useBackend }    from './hooks/useBackend.js';
import { triggerRetrain } from './services/api.js';

export default function App() {
  const [forecastH, setForecastH] = useState(0);
  const [retrainMsg, setRetrainMsg] = useState('');
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

  const handleMapClick = useCallback((lat, lon) => {
    locationRef.current = { lat, lon };
    predict(lat, lon, forecastH);
  }, [predict, forecastH]);


  const handleRetrain = useCallback(async () => {
    setRetrainMsg('');
    try {
      const { status } = await triggerRetrain();
      setRetrainMsg(status === 'already_running' ? 'Already retraining…' : 'Retraining started');
    } catch {
      setRetrainMsg('Retrain request failed');
    }
    setTimeout(() => setRetrainMsg(''), 4000);
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
              locationRef.current = { lat: g.lat, lon: g.lon };
              predict(g.lat, g.lon, forecastH);
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
            onRetrain={handleRetrain}
          />

          <PinnedGliderCard glider={pinnedGlider} onUnpin={() => { setPinnedId(null); lastPinnedRef.current = null; }} />

          {retrainMsg && <div className="status retrain">{retrainMsg}</div>}

          <ExportButton
            heatmap={heatmap}
            gridMeta={gridMeta}
            thermalBase={weather?.thermal_base}
            cape={weather?.cape}
          />

          {loading && <div className="status loading">Fetching prediction…</div>}
          {error   && <div className="status error">⚠ {error}</div>}
          {!loading && !error && !heatmap && <div className="status hint">Click the map to predict thermals</div>}

          <div className="legend">
            <div className="legend-title">Thermal probability</div>
            <div className="legend-bar" />
            <div className="legend-labels">
              <span>Low</span><span>High</span>
            </div>
            <div className="legend-glyphs">
              <span><span className="dot yellow"  /> circling</span>
              <span><span className="dot blue"    /> glider</span>
              <span><span className="dot orange"  /> tow plane</span>
              <span><span className="dot thermal" /> active thermal</span>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
