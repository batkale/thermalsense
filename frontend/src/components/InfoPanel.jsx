import { useLang } from '../i18n/LanguageContext.jsx';

export default function InfoPanel({
  thermalBase, cape, heatmapMax,
  gliders, totalGliders,
  showAirborneOnly, onAirborneToggle,
  showOgnHeatmap, onOgnHeatmapToggle,
  onRetrain,
}) {
  const { t, locale } = useLang();

  const circling = gliders.filter(g => g.circling).length;
  const tow      = gliders.filter(g => g.is_tow).length;
  const filtered = showAirborneOnly && totalGliders !== gliders.length;

  const heatmapPct = heatmapMax != null
    ? Math.round(heatmapMax * 100)
    : null;

  const gliderLabel = filtered
    ? t('gliderCount', { n: `${gliders.length} / ${totalGliders}` })
    : t('gliderCount', { n: gliders.length });

  return (
    <div className="panel">

      {/* Prediction results */}
      {heatmapMax != null && (
        <div className="metrics">
          <div className="metric">
            <span className="metric-label">{t('thermalPeak')}</span>
            <span className="metric-value"
              style={{ color: heatmapPct > 60 ? '#FC8181' : heatmapPct > 30 ? '#F6AD55' : '#9ae6b4' }}>
              {heatmapPct}%
            </span>
          </div>
          {thermalBase != null && (
            <div className="metric">
              <span className="metric-label">{t('thermalBase')}</span>
              <span className="metric-value">{thermalBase.toLocaleString(locale)} m</span>
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
        <span>✈ {gliderLabel}</span>
        {circling > 0 && (
          <span className="circling">↻ {t('circlingCount', { n: circling })}</span>
        )}
      </div>

      {tow > 0 && (
        <div className="glider-row">
          <span style={{ color: '#FFFFFF' }}>⊕ {t('towPlaneCount', { n: tow })}</span>
        </div>
      )}

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={showAirborneOnly}
          onChange={e => onAirborneToggle(e.target.checked)}
        />
        {t('airborneOnly')} <span className="toggle-hint">{t('airborneHint')}</span>
      </label>

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={showOgnHeatmap}
          onChange={e => onOgnHeatmapToggle(e.target.checked)}
        />
        {t('liveActivityLayer')} <span className="toggle-hint">{t('ognDensityHint')}</span>
      </label>

      {/* Hidden in production unless an admin token is configured — the endpoint
          rejects unauthenticated calls, so an always-visible button would just fail. */}
      {onRetrain && (
        <button className="btn-secondary" onClick={onRetrain} title={t('retrainTitle')}>
          {t('retrainModel')}
        </button>
      )}

    </div>
  );
}
