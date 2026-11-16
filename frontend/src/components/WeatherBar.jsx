import { useState, useRef } from 'react';
import { useLang } from '../i18n/LanguageContext.jsx';
import { useTheme } from '../theme/ThemeContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';
import { FORECAST_STEPS } from '../config.js';
import { BrandMark, SunIcon, MoonIcon, AlertIcon } from './Icons.jsx';

export default function WeatherBar({ weather, forecastH = 0, onForecastChange, loading, error }) {
  const { t, lang, toggleLang, windDir } = useLang();
  const { theme, toggleTheme } = useTheme();

  // The glyph shows the mode the click leads to, not the one in force —
  // a control labelled with its current state reads as a status light.
  const themeLabel = theme === 'dark' ? t('switchToLight') : t('switchToDark');
  const forecastLabel = formatForecast(t, forecastH);

  return (
    <div className="top-bar">
      <span className="top-bar-title">
        <BrandMark className="brand-mark" size={20} />
        <span className="brand-word">ThermalSense</span>
        {/* A real navigation, not a panel: the landing page is a second page of
            the same build (frontend/about.html). It sits on the left because
            .top-bar clips whatever runs past its right edge on a narrow window. */}
        <a className="top-bar-about" href="/about" title={t('aboutTitle')}>{t('about')}</a>
      </span>

      {/* One strip for both readouts, so a phone can scroll them sideways as a
          row instead of clipping everything past the first two. */}
      <div className="wx-strip">
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
              {loading ? t('loading') : error ? <><AlertIcon /> {t(error)}</> : t('clickMapToLoad')}
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

      </div>

      <div className="forecast-ctrl">
        <span className="forecast-ctrl-label">{t('forecast')}</span>
        {/* The range runs over indices into FORECAST_STEPS, not over hours: the
            stops are 15 minutes apart for three hours and an hour apart after,
            and a range input can only step evenly. */}
        <input
          type="range" min={0} max={FORECAST_STEPS.length - 1} step={1}
          value={Math.max(0, FORECAST_STEPS.indexOf(forecastH))}
          onChange={e => onForecastChange(FORECAST_STEPS[Number(e.target.value)])}
          aria-label={t('forecast')}
          aria-valuetext={forecastLabel}
          className="forecast-slider"
        />
        <span className="forecast-ctrl-value">{forecastLabel}</span>
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
        {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
      </button>
    </div>
  );
}

// "+45 dk", "+1 sa 15 dk", "+6 sa". Minutes are spelled out rather than
// abbreviated to "m", which on a map full of altitudes reads as metres.
function formatForecast(t, hours) {
  if (!hours) return t('now');
  const total = Math.round(hours * 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (!m) return t('plusHours', { n: h });
  if (!h) return t('plusMinutes', { n: m });
  return t('plusHoursMinutes', { h, m });
}

function WindValue({ speed, dir, label }) {
  return (
    <>
      {speed.toFixed(0)} km/h{' '}
      <svg className="wind-arrow" viewBox="0 0 10 10" width="9" height="9" aria-hidden="true"
           style={{ transform: `rotate(${dir}deg)` }}>
        <path d="M5 0.5 9 9.5 5 7.4 1 9.5Z" fill="currentColor" />
      </svg>
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
