import { useLang } from '../i18n/LanguageContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';

export default function WeatherBar({ weather, forecastH = 0, onForecastChange, loading, error }) {
  const { t, lang, toggleLang, windDir } = useLang();

  return (
    <div className="top-bar">
      <span className="top-bar-title">ThermalSense</span>

      <div className="wx-section">
        <span className="wx-section-label">{t('weather')}</span>
        <div className="wx-chips">
          {weather ? (
            <>
              <WxChip label={t('temp')}     value={`${weather.temp_2m.toFixed(1)}°C`} />
              <WxChip label={t('humidity')} value={`${weather.humidity}%`} />
              <WxChip label={t('wind')}     value={<WindValue speed={weather.wind_speed} dir={weather.wind_dir} label={windDir(weather.wind_dir)} />} />
              <WxChip label={t('solar')}    value={`${Math.round(weather.solar_ghi)} W/m²`} />
              <WxChip label={t('lapse')}    value={`${weather.lapse_rate.toFixed(1)}°/km`} />
            </>
          ) : (
            <span className="wx-hint">
              {loading ? t('loading') : error ? `⚠ ${error}` : t('clickMapToLoad')}
            </span>
          )}
        </div>
      </div>

      <div className="wx-divider" />

      <div className="wx-section">
        <span className="wx-section-label">{t('thermals')}</span>
        <div className="wx-chips">
          {weather ? (
            <>
              <WxChip label="CAPE"       value={`${Math.round(weather.cape)} J/kg`} />
              <WxChip label="CIN"        value={`${Math.round(weather.cin)} J/kg`} />
              <WxChip label={t('tBase')} value={`${weather.thermal_base} m`} />
            </>
          ) : (
            <span className="wx-hint">—</span>
          )}
        </div>
      </div>

      <div className="wx-divider" />

      <div className="forecast-ctrl">
        <span className="forecast-ctrl-label">{t('forecast')}</span>
        <input
          type="range" min={0} max={23} step={1}
          value={forecastH}
          onChange={e => onForecastChange(Number(e.target.value))}
          className="forecast-slider"
        />
        <span className="forecast-ctrl-value">
          {!forecastH ? t('now') : t('plusHours', { n: forecastH })}
        </span>
      </div>

      <button
        className="lang-toggle"
        onClick={toggleLang}
        title={lang === 'tr' ? t('switchToEnglish') : t('switchToTurkish')}
        aria-label={lang === 'tr' ? t('switchToEnglish') : t('switchToTurkish')}
      >
        {LANG_LABEL[lang]}
      </button>
    </div>
  );
}

function WindValue({ speed, dir, label }) {
  return (
    <>
      {speed.toFixed(0)} km/h{' '}
      <span style={{ display: 'inline-block', transform: `rotate(${dir}deg)`, fontSize: '9px', lineHeight: 1 }}>▲</span>
      {' '}{label}
    </>
  );
}

function WxChip({ label, value }) {
  return (
    <div className="wx-chip">
      <span className="wx-chip-label">{label}</span>
      <span className="wx-chip-value">{value}</span>
    </div>
  );
}
