import { useState, useEffect } from 'react';
import { fetchElevation, fetchWeather } from '../services/api.js';
import { useLang } from '../i18n/LanguageContext.jsx';
import { ConditionIcon, conditionKey, useLocalClock, formatLocalTime, formatOffset } from './SkyClock.jsx';

// How far the aircraft may travel before the ground under it is looked up
// again: ~500 m of latitude. Coarse enough that a glider at 100 km/h asks about
// once every 18 s, fine enough that the reading keeps up with a ridge.
const GROUND_STEP_DEG = 0.0045;

// The backend caches weather per 0.1 degree cell and hour, so asking again
// inside the same cell costs nothing upstream but tells us nothing either.
// Re-asked on a new cell, and on a timer so an hour boundary is picked up.
const WEATHER_STEP_DEG = 0.1;
const WEATHER_REFRESH_MS = 10 * 60 * 1000;

export default function PinnedGliderCard({ glider, onUnpin }) {
  const { t, locale } = useLang();
  const [groundElev,  setGroundElev]  = useState(null);
  const [elevLoading, setElevLoading] = useState(false);
  const [sky,         setSky]         = useState(null);

  // Which ~500 m patch the aircraft is over. The effect below keys on it, so
  // the lookup follows the flight instead of being taken once.
  const patchLat = glider ? Math.round(glider.lat / GROUND_STEP_DEG) : null;
  const patchLon = glider ? Math.round(glider.lon / GROUND_STEP_DEG) : null;

  const wxLat = glider ? Math.round(glider.lat / WEATHER_STEP_DEG) : null;
  const wxLon = glider ? Math.round(glider.lon / WEATHER_STEP_DEG) : null;

  // A different aircraft: the previous one's ground means nothing here.
  useEffect(() => {
    setGroundElev(null);
    setSky(null);
    setElevLoading(Boolean(glider));
  }, [glider?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Ground elevation at the exact point, not the lattice cell the wire's agl is
  // sampled on, and it stays that way deliberately: the card is the one place a
  // number is read rather than thresholded, so it should show the ground
  // actually under the aircraft. See _attach_cached_agl in main.py for how the
  // wire earns the same accuracy near the ground, which is where the two used
  // to disagree.
  //
  // It used to be fetched once per pin. The altitude beside it kept updating,
  // so AGL became "current altitude minus the ground where it was clicked": a
  // glider pinned over a valley floor at 500 m and followed across a 2,000 m
  // ridge read 1,500 m too high the whole way over. The last value stays on
  // screen while the next one loads, so the row does not blink every 500 m.
  useEffect(() => {
    if (!glider) return undefined;
    let cancelled = false;
    fetchElevation(glider.lat, glider.lon)
      .then(data => { if (!cancelled && data.elevation != null) setGroundElev(data.elevation); })
      .catch(()  => { /* keep the last reading; the next patch retries */ })
      .finally(() => { if (!cancelled) setElevLoading(false); });
    return () => { cancelled = true; };
  }, [glider?.id, patchLat, patchLon]);  // eslint-disable-line react-hooks/exhaustive-deps

  // The sky and the local clock where the aircraft is, now — not where the map
  // was last scanned, and not at the slider's moment: the card describes a
  // live aircraft. The last answer stays while the next cell's loads.
  useEffect(() => {
    if (!glider) return undefined;
    let cancelled = false;
    const load = () => fetchWeather(glider.lat, glider.lon, 0)
      .then(data => { if (!cancelled) setSky(data); })
      .catch(() => { /* keep the last reading */ });
    load();
    const id = setInterval(load, WEATHER_REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [glider?.id, wxLat, wxLon]);  // eslint-disable-line react-hooks/exhaustive-deps

  const shifted = useLocalClock(sky?.utc_offset_s, sky?.target_at, true);

  if (!glider) return null;

  const skyKey = sky ? conditionKey(sky.condition, sky.is_day) : null;

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
            {groundElev != null ? `${groundElev} m` : elevLoading ? '…' : '--'}
          </span>
        </div>
        <div className="pin-metric">
          <span className="pin-metric-label">{t('agl')}</span>
          <span className={`pin-metric-value ${agl != null && agl < 200 ? 'agl-low' : ''}`}>
            {agl != null ? `${agl} m` : elevLoading ? '…' : '--'}
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

      {(skyKey || shifted != null) && (
        <div className="pin-sky">
          {skyKey && (
            <span className="pin-sky-cond" title={t('skyTip')}>
              <ConditionIcon condition={sky.condition} isDay={sky.is_day} size={16} />
              {t(skyKey)}
            </span>
          )}
          {shifted != null && (
            <span className="pin-sky-time"
                  title={t('localTimeTip', { offset: formatOffset(t, sky.utc_offset_s) })}>
              {t('localTime')} {formatLocalTime(shifted, locale)}
              <span className="pin-sky-offset">{formatOffset(t, sky.utc_offset_s)}</span>
            </span>
          )}
        </div>
      )}

      <div className="pin-coords">
        {Math.abs(glider.lat).toFixed(4)}&deg;&nbsp;{latDir}
        &nbsp;&nbsp;
        {Math.abs(glider.lon).toFixed(4)}&deg;&nbsp;{lonDir}
      </div>
    </div>
  );
}
