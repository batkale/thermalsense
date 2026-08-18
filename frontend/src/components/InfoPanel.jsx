import { useLang } from '../i18n/LanguageContext.jsx';

/**
 * One labelled figure in the prediction block.
 *
 * The whole row is the hover target rather than a marker inside it: every
 * metric here is a number whose meaning is not obvious from its label, so the
 * explanation should be where the reader is already looking instead of behind
 * a glyph they have to notice first and then aim at.
 */
function Metric({ label, tip, value, valueClass, sub }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className={valueClass ? `metric-value ${valueClass}` : 'metric-value'}>
        {value}
        {sub && <span className="metric-sub">{sub}</span>}
      </span>
      {tip && <span className="metric-bubble" role="tooltip">{tip}</span>}
    </div>
  );
}

export default function InfoPanel({
  thermalBase, cape, heatmapMax, predictAlt,
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

  // Signed on purpose: measured against the grid's mean terrain, a glider low in
  // a valley inside a hilly grid really is below it, and "-120 m" says so where
  // a bare number would read as height.
  const altAglLabel = predictAlt != null
    ? `${predictAlt.agl >= 0 ? '+' : ''}${predictAlt.agl.toLocaleString(locale)}`
    : null;

  const gliderLabel = filtered
    ? t('gliderCount', { n: `${gliders.length} / ${totalGliders}` })
    : t('gliderCount', { n: gliders.length });

  return (
    <div className="panel">

      {/* Prediction results */}
      {heatmapMax != null && (
        <div className="metrics">
          <Metric
            label={t('thermalPeak')}
            tip={t('thermalPeakTip')}
            value={`${heatmapPct}%`}
            valueClass={heatmapPct > 60 ? 'peak-high' : heatmapPct > 30 ? 'peak-mid' : 'peak-low'}
          />
          {/* Which altitude the heatmap answers for. Shown because the two ways
              of asking disagree: a glider click sends the aircraft's own
              altitude, a bare map click gets a default working height, and the
              resulting maps differ with nothing on screen to say why. */}
          {predictAlt != null && (
            <Metric
              label={t('predictionAlt')}
              tip={t('predictionAltTip')}
              value={`${predictAlt.amsl.toLocaleString(locale)} m`}
              sub={predictAlt.source === 'observer'
                ? t('altFromGlider',  { n: altAglLabel })
                : t('altFromDefault', { n: altAglLabel })}
            />
          )}
          {thermalBase != null && (
            <Metric
              label={t('thermalBase')}
              tip={t('thermalBaseTip')}
              value={`${thermalBase.toLocaleString(locale)} m`}
            />
          )}
          {cape != null && (
            <Metric
              label="CAPE"
              tip={t('capeTip')}
              value={`${Math.round(cape)} J/kg`}
            />
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
          <span className="tow-count">⊕ {t('towPlaneCount', { n: tow })}</span>
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
