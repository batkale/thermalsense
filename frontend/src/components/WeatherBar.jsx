import { useState, useRef } from 'react';
import { useLang } from '../i18n/LanguageContext.jsx';
import { useTheme } from '../theme/ThemeContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';

export default function WeatherBar({ weather, forecastH = 0, onForecastChange, loading, error }) {
  const { t, lang, toggleLang, windDir } = useLang();
  const { theme, toggleTheme } = useTheme();

  // The glyph shows the mode the click leads to, not the one in force —
  // a control labelled with its current state reads as a status light.
  const themeLabel = theme === 'dark' ? t('switchToLight') : t('switchToDark');

  return (
    <div className="top-bar">
      <span className="top-bar-title">ThermalSense</span>

      <div className="wx-section">
        <span className="wx-section-label">{t('weather')}</span>
        <div className="wx-chips">
          {weather ? (
            <>
              <WxChip label={t('temp')}     tip={t('tempTip')}     value={`${weather.temp_2m.toFixed(1)}°C`} />
              <WxChip label={t('humidity')} tip={t('humidityTip')} value={`${weather.humidity}%`} />
              <WxChip label={t('wind')}     tip={t('windTip')}     value={<WindValue speed={weather.wind_speed} dir={weather.wind_dir} label={windDir(weather.wind_dir)} />} />
              <WxChip label={t('solar')}    tip={t('solarTip')}    value={`${Math.round(weather.solar_ghi)} W/m²`} />
              <WxChip label={t('lapse')}    tip={t('lapseTip')}    value={`${weather.lapse_rate.toFixed(1)}°/km`} />
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
              <WxChip label="CAPE"       tip={t('capeTip')}        value={`${Math.round(weather.cape)} J/kg`} />
              <WxChip label="CIN"        tip={t('cinTip')}         value={`${Math.round(weather.cin)} J/kg`} />
              <WxChip label={t('tBase')} tip={t('thermalBaseTip')} value={`${weather.thermal_base} m`} />
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

      <button
        className="theme-toggle"
        onClick={toggleTheme}
        title={themeLabel}
        aria-label={themeLabel}
      >
        {theme === 'dark' ? '☀' : '☾'}
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

// Half the bubble's width, plus a little, so a chip near either screen edge
// nudges its bubble inward instead of letting it run off.
const WX_BUBBLE_HALF = 110;

function WxChip({ label, value, tip }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  // Fixed positioning, measured on hover, rather than an absolutely positioned
  // child like the sidebar metrics use. .top-bar sets overflow:hidden — it has
  // to, since the chips run past its right edge on a narrow window — and it is
  // only 50px tall, so an absolute bubble would be clipped away to nothing. A
  // fixed one is laid out against the viewport and escapes that clip.
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    setPos({
      top:  r.bottom + 6,
      left: Math.min(Math.max(r.left + r.width / 2, WX_BUBBLE_HALF),
                     window.innerWidth - WX_BUBBLE_HALF),
    });
  };

  return (
    <div
      className="wx-chip"
      ref={ref}
      onMouseEnter={tip ? show : undefined}
      onMouseLeave={tip ? () => setPos(null) : undefined}
    >
      <span className="wx-chip-label">{label}</span>
      <span className="wx-chip-value">{value}</span>
      {tip && pos && (
        <span className="wx-bubble" role="tooltip" style={{ top: pos.top, left: pos.left }}>
          {tip}
        </span>
      )}
    </div>
  );
}
