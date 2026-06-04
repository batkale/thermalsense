import { useState, useEffect } from 'react';
import { fetchElevation } from '../services/api.js';

export default function PinnedGliderCard({ glider, onUnpin }) {
  const [groundElev,  setGroundElev]  = useState(null);
  const [elevLoading, setElevLoading] = useState(false);

  // Re-fetch ground elevation only when a different glider is pinned
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
  const latDir    = glider.lat >= 0 ? 'N' : 'S';
  const lonDir    = glider.lon >= 0 ? 'E' : 'W';
  const agl       = groundElev != null ? glider.alt - groundElev : null;

  return (
    <div className="pin-card">
      <div className="pin-card-header">
        <span className="pin-callsign">{glider.id}</span>
        <button className="pin-close" onClick={onUnpin} title="Unpin">&times;</button>
      </div>

      <div className="pin-metrics">
        <div className="pin-metric">
          <span className="pin-metric-label">Alt MSL</span>
          <span className="pin-metric-value">{glider.alt} m</span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">Ground</span>
          <span className="pin-metric-value">
            {elevLoading ? '…' : groundElev != null ? `${groundElev} m` : '--'}
          </span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">AGL</span>
          <span className={`pin-metric-value ${agl != null && agl < 200 ? 'agl-low' : ''}`}>
            {elevLoading ? '…' : agl != null ? `${agl} m` : '--'}
          </span>
        </div>
      </div>

      <div className="pin-metrics">
        <div className="pin-metric">
          <span className="pin-metric-label">Vario</span>
          <span className={`pin-metric-value ${glider.vario > 0 ? 'vario-up' : glider.vario < 0 ? 'vario-down' : ''}`}>
            {varioSign}{glider.vario} m/s
          </span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">Hdg</span>
          <span className="pin-metric-value">
            {glider.heading != null ? `${glider.heading}°` : '--'}
          </span>
        </div>
      </div>

      <div className="pin-status">
        <span className={glider.is_tow ? 'pin-tow' : 'pin-glider'}>
          {glider.is_tow ? 'tow plane' : 'glider'}
        </span>
        {glider.circling
          ? <span className="pin-circling">circling</span>
          : <span className="pin-straight">straight</span>
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
