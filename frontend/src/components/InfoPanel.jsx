import { useEffect, useState } from 'react';
import { useLang } from '../i18n/LanguageContext.jsx';
import { GliderIcon } from './Icons.jsx';
import { CLOUD_LOW_TOP_M, CLOUD_HIGH_TOP_M } from '../config.js';
import { COLOURS } from './CloudLayer.jsx';

/**
 * What the cloud layer is showing: the frame's time and age, a key to the three
 * ways a cloud is drawn, and — when the slider is ahead — that the clouds are
 * not moving with it. Clouds next to a "+3 h" heatmap otherwise read as a
 * forecast of the clouds.
 */
function CloudCaption({ clouds, forecastH }) {
  const { t, locale } = useLang();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  if (clouds.failed || clouds.status === 'error') {
    return <span className="toggle-sub warn">{t('cloudsFailed')}</span>;
  }
  if (!clouds.time || clouds.status !== 'ok') {
    return <span className="toggle-sub">{t('cloudsLoading')}</span>;
  }
  const ageMin = Math.max(0, Math.round((now - Date.parse(clouds.time)) / 60_000));
  const km = (m) => (m / 1000).toLocaleString(locale);
  return (
    <>
      <span className="toggle-sub">
        {t('cloudsFrame', { time: clouds.time.slice(11, 16), n: ageMin })}
      </span>
      <span
        className="toggle-sub cloud-key"
        title={t('cloudsKeyTip', { low: km(CLOUD_LOW_TOP_M), high: km(CLOUD_HIGH_TOP_M) })}
      >
        <CloudGlyph kind="low" /><span>{t('cloudsKeyLow')}</span>
        <CloudGlyph kind="mid" /><span>{t('cloudsKeyMid')}</span>
        <CloudGlyph kind="high" /><span>{t('cloudsKeyHigh')}</span>
      </span>
      {forecastH > 0 && <span className="toggle-sub warn">{t('cloudsNotForecast')}</span>}
    </>
  );
}

/**
 * The three cloud marks in miniature, in the layer's own colours and light
 * (CloudLayer.jsx): lit from the upper left, grey on the far side. Like the
 * map's other legend swatches they key marks drawn over satellite imagery, so
 * they are not themed either. The map draws its clouds with no outline; here
 * a faint one stays, or a white heap vanishes on the light sidebar.
 */
const HEAP = [[6.5, 7.2, 3.6], [11.5, 5.6, 4.4], [16, 7.4, 3.4]];

function CloudGlyph({ kind }) {
  const rim = 'rgba(8,18,27,0.3)';
  const id = `cloud-glyph-${kind}`;
  return (
    <svg className="cloud-glyph" viewBox="0 0 22 12" width="22" height="12" aria-hidden="true">
      {kind === 'low' && (
        <g>
          <defs>
            <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="4" y1="2" x2="17" y2="12">
              <stop offset="0" stopColor={COLOURS.lit} />
              <stop offset="0.45" stopColor={COLOURS.body} />
              <stop offset="0.8" stopColor={COLOURS.shade} />
              <stop offset="1" stopColor={COLOURS.deep} />
            </linearGradient>
          </defs>
          {/* Stroked under the fills, so only the outer edge keeps a line. */}
          {HEAP.map(([cx, cy, r]) => (
            <circle key={`r${cx}`} cx={cx} cy={cy} r={r} fill="none" stroke={rim} strokeWidth="1" />
          ))}
          {HEAP.map(([cx, cy, r]) => (
            <circle key={`f${cx}`} cx={cx} cy={cy} r={r} fill={`url(#${id})`} />
          ))}
        </g>
      )}
      {kind === 'mid' && (
        <g>
          <defs>
            <filter id={id} x="-20%" y="-40%" width="140%" height="180%">
              <feGaussianBlur stdDeviation="0.5" />
            </filter>
          </defs>
          <ellipse cx="11" cy="6.5" rx="9.5" ry="3.6" fill={COLOURS.sheet} fillOpacity="0.9"
                   stroke={rim} strokeWidth="1" filter={`url(#${id})`} />
        </g>
      )}
      {kind === 'high' && (
        <g strokeLinecap="round" fill="none">
          <path d="M2 8 q 6 -4 12 -5" stroke={rim} strokeWidth="2.4" />
          <path d="M8 10.5 q 6 -4 12 -5" stroke={rim} strokeWidth="2.4" />
          <path d="M2 8 q 6 -4 12 -5" stroke={COLOURS.fibre} strokeWidth="1.2" />
          <path d="M8 10.5 q 6 -4 12 -5" stroke={COLOURS.fibre} strokeWidth="1.2" />
        </g>
      )}
    </svg>
  );
}

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
  thermalBase, cape, heatmapMax, predictAlt, noInsolation,
  gliders, totalGliders,
  showAirborneOnly, onAirborneToggle,
  showOgnHeatmap, onOgnHeatmapToggle,
  compare, onCompareToggle,
  showClouds, onCloudsToggle, clouds, forecastH = 0,
  onRetrain,
  onOpenTraining,
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
          {/* Without surface heating no thermal can form, and the backend zeroes
              the grid rather than extrapolating the model outside everything it
              was trained on. Showing "0%" would read as a forecast of no lift;
              this says there is no forecast to make. */}
          {noInsolation ? (
            <Metric
              label={t('thermalPeak')}
              tip={t('noInsolationTip')}
              value="—"
              valueClass="peak-low"
              sub={t('noInsolation')}
            />
          ) : (
            <Metric
              label={t('thermalPeak')}
              tip={t('thermalPeakTip')}
              value={`${heatmapPct}%`}
              valueClass={heatmapPct > 60 ? 'peak-high' : heatmapPct > 30 ? 'peak-mid' : 'peak-low'}
            />
          )}
          {/* Which altitude the heatmap answers for. Shown because the two ways
              of asking disagree: a glider click sends the aircraft's own
              altitude, a bare map click gets a default working height, and the
              resulting maps differ with nothing on screen to say why. */}
          {predictAlt != null && (
            <Metric
              label={t('predictionAlt')}
              tip={t('predictionAltTip')}
              value={t('metresMsl', { n: predictAlt.amsl.toLocaleString(locale) })}
              sub={predictAlt.source === 'observer'
                ? t('altFromGlider',  { n: altAglLabel })
                : t('altFromDefault', { n: altAglLabel })}
            />
          )}
          {/* The thermal base is above ground; the prediction altitude above it
              is above sea level. Both are labelled, and the base is also given
              in MSL over the grid's mean terrain (amsl − agl of the prediction
              altitude), so the two can be compared directly. */}
          {thermalBase != null && (
            <Metric
              label={t('thermalBase')}
              tip={t('thermalBaseTip')}
              value={t('metresAgl', { n: thermalBase.toLocaleString(locale) })}
              sub={predictAlt != null
                ? t('thermalBaseMsl', {
                    n: Math.round(thermalBase + predictAlt.amsl - predictAlt.agl).toLocaleString(locale),
                  })
                : undefined}
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
        <span className="glider-total"><GliderIcon /> {gliderLabel}</span>
        {circling > 0 && (
          <span className="circling"><span className="dot yellow" aria-hidden="true" />{t('circlingCount', { n: circling })}</span>
        )}
      </div>

      {tow > 0 && (
        <div className="glider-row">
          <span className="tow-count"><span className="dot orange" aria-hidden="true" />{t('towPlaneCount', { n: tow })}</span>
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

      {/* Splits the map with a draggable divider: the inputs as they arrive on
          one side, everything worked out from them on the other. */}
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={compare}
          onChange={e => onCompareToggle(e.target.checked)}
        />
        {t('compareToggle')} <span className="toggle-hint">{t('compareHint')}</span>
      </label>

      {onCloudsToggle && (
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={showClouds}
            onChange={e => onCloudsToggle(e.target.checked)}
          />
          {t('cloudsToggle')} <span className="toggle-hint">{t('cloudsHint')}</span>
          {showClouds && clouds && <CloudCaption clouds={clouds} forecastH={forecastH} />}
        </label>
      )}

      {/* Hidden in production unless an admin token is configured — the endpoint
          rejects unauthenticated calls, so an always-visible button would just fail. */}
      {onRetrain && (
        <button className="btn-secondary" onClick={onRetrain} title={t('retrainTitle')}>
          {t('retrainModel')}
        </button>
      )}

      {onOpenTraining && (
        <button className="btn-secondary" onClick={onOpenTraining}>
          {t('openTrainingStatus')}
        </button>
      )}

    </div>
  );
}
