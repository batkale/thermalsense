export default function InfoPanel({
  thermalBase, cape, heatmapMax,
  gliders, totalGliders,
  showAirborneOnly, onAirborneToggle,
  showOgnHeatmap, onOgnHeatmapToggle,
  onRetrain,
}) {
  const circling = gliders.filter(g => g.circling).length;
  const tow      = gliders.filter(g => g.is_tow).length;
  const filtered = showAirborneOnly && totalGliders !== gliders.length;

  const heatmapPct = heatmapMax != null
    ? Math.round(heatmapMax * 100)
    : null;

  return (
    <div className="panel">

      {/* Prediction results */}
      {heatmapMax != null && (
        <div className="metrics">
          <div className="metric">
            <span className="metric-label">Thermal peak</span>
            <span className="metric-value"
              style={{ color: heatmapPct > 60 ? '#FC8181' : heatmapPct > 30 ? '#F6AD55' : '#9ae6b4' }}>
              {heatmapPct}%
            </span>
          </div>
          {thermalBase != null && (
            <div className="metric">
              <span className="metric-label">Thermal base</span>
              <span className="metric-value">{thermalBase.toLocaleString()} m</span>
            </div>
          )}
          {cape != null && (
            <div className="metric">
              <span className="metric-label">CAPE</span>
              <span className="metric-value">{Math.round(cape)} J/kg</span>
            </div>
          )}
        </div>
      )}

      {/* Glider counts */}
      <div className="glider-row">
        <span>
          ✈{' '}
          {filtered ? `${gliders.length} / ${totalGliders}` : gliders.length}
          {' '}gliders
        </span>
        {circling > 0 && <span className="circling">↻ {circling} circling</span>}
      </div>

      {tow > 0 && (
        <div className="glider-row">
          <span style={{ color: '#FFFFFF' }}>⊕ {tow} tow plane{tow > 1 ? 's' : ''}</span>
        </div>
      )}

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={showAirborneOnly}
          onChange={e => onAirborneToggle(e.target.checked)}
        />
        Airborne only <span className="toggle-hint">(AGL &gt; 10 m)</span>
      </label>

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={showOgnHeatmap}
          onChange={e => onOgnHeatmapToggle(e.target.checked)}
        />
        Live activity layer <span className="toggle-hint">(OGN density)</span>
      </label>

      <button className="btn-secondary" onClick={onRetrain}
        title="Trigger model retraining on latest OGN data">
        Retrain model
      </button>

    </div>
  );
}
