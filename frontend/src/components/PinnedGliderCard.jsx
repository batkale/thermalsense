import { useState, useEffect } from 'react';
import { fetchElevation } from '../services/api.js';
import { useLang } from '../i18n/LanguageContext.jsx';

export default function PinnedGliderCard({ glider, onUnpin }) {
  const { t } = useLang();
  const [groundElev,  setGroundElev]  = useState(null);
  const [elevLoading, setElevLoading] = useState(false);

  // Re-fetch ground elevation only when a different glider is pinned.
  // This is the exact point, not the lattice cell the wire's agl is sampled on,
  // and it stays that way deliberately: the card is the one place a number is
  // read rather than thresholded, so it should show the ground actually under
  // the aircraft. See _attach_cached_agl in main.py for how the wire earns the
  // same accuracy near the ground, which is where the two used to disagree.
  useEffect(() => {
    if (!glider) { setGroundElev(null); return; }
    let cancelled = false;
    setElevLoading(true);
    fetchElevation(glider.lat, glider.lon)
      .then(data => { if (!cancelled) setGroundElev(data.elevation); })
      .catch(()  => { if (!cancelled) setGroundElev(null); })
      .finally(() => { if (!cancelled) setElevLoading(false); });
    return () => { cancelled = true; };
  }, [glider?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!glider) return null;

  const varioSign = glider.vario >= 0 ? '+' : '';
  const latDir    = glider.lat >= 0 ? t('latN') : t('latS');
  const lonDir    = glider.lon >= 0 ? t('lonE') : t('lonW');
  const agl       = groundElev != null ? glider.alt - groundElev : null;

  return (
    <div className="pin-card">
      <div className="pin-card-header">
        <span className="pin-callsign">{glider.id}</span>
        <button className="pin-close" onClick={onUnpin} title={t('unpin')}>&times;</button>
      </div>

      <div className="pin-metrics">
        <div className="pin-metric">
          <span className="pin-metric-label">{t('altMsl')}</span>
          <span className="pin-metric-value">{glider.alt} m</span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">{t('ground')}</span>
          <span className="pin-metric-value">
            {elevLoading ? '…' : groundElev != null ? `${groundElev} m` : '--'}
          </span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">{t('agl')}</span>
          <span className={`pin-metric-value ${agl != null && agl < 200 ? 'agl-low' : ''}`}>
            {elevLoading ? '…' : agl != null ? `${agl} m` : '--'}
          </span>
        </div>
      </div>

      <div className="pin-metrics">
        <div className="pin-metric">
          <span className="pin-metric-label">{t('vario')}</span>
          <span className={`pin-metric-value ${glider.vario > 0 ? 'vario-up' : glider.vario < 0 ? 'vario-down' : ''}`}>
            {varioSign}{glider.vario} m/s
          </span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">{t('heading')}</span>
          <span className="pin-metric-value">
            {glider.heading != null ? `${glider.heading}°` : '--'}
          </span>
        </div>
      </div>

      <div className="pin-status">
        <span className={glider.is_tow ? 'pin-tow' : 'pin-glider'}>
          {glider.is_tow ? t('towPlane') : t('glider')}
        </span>
        {glider.circling
          ? <span className="pin-circling">{t('circling')}</span>
          : <span className="pin-straight">{t('straight')}</span>
        }
      </div>

      <div className="pin-coords">
        {Math.abs(glider.lat).toFixed(4)}&deg;&nbsp;{latDir}
        &nbsp;&nbsp;
        {Math.abs(glider.lon).toFixed(4)}&deg;&nbsp;{lonDir}
      </div>
    </div>
  );
}
