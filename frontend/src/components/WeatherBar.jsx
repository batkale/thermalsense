const DIRS = ['N','NE','E','SE','S','SW','W','NW'];
function windDirLabel(deg) {
  return DIRS[Math.round(deg / 45) % 8];
}

export default function WeatherBar({ weather, forecastH = 0, onForecastChange, loading, error }) {
  return (
    <div className="top-bar">
      <span className="top-bar-title">ThermalSense</span>

      <div className="wx-section">
        <span className="wx-section-label">Weather</span>
        <div className="wx-chips">
          {weather ? (
            <>
              <WxChip label="Temp"     value={`${weather.temp_2m.toFixed(1)}°C`} />
              <WxChip label="Humidity" value={`${weather.humidity}%`} />
              <WxChip label="Wind"     value={<WindValue speed={weather.wind_speed} dir={weather.wind_dir} />} />
              <WxChip label="Solar"    value={`${Math.round(weather.solar_ghi)} W/m²`} />
              <WxChip label="Lapse"    value={`${weather.lapse_rate.toFixed(1)}°/km`} />
            </>
          ) : (
            <span className="wx-hint">
              {loading ? 'Loading…' : error ? `⚠ ${error}` : 'Click the map to load'}
            </span>
          )}
        </div>
      </div>

      <div className="wx-divider" />

      <div className="wx-section">
        <span className="wx-section-label">Thermals</span>
        <div className="wx-chips">
          {weather ? (
            <>
              <WxChip label="CAPE"   value={`${Math.round(weather.cape)} J/kg`} />
              <WxChip label="CIN"    value={`${Math.round(weather.cin)} J/kg`} />
              <WxChip label="T-base" value={`${weather.thermal_base} m`} />
            </>
          ) : (
            <span className="wx-hint">—</span>
          )}
        </div>
      </div>

      <div className="wx-divider" />

      <div className="forecast-ctrl">
        <span className="forecast-ctrl-label">Forecast</span>
        <input
          type="range" min={0} max={23} step={1}
          value={forecastH}
          onChange={e => onForecastChange(Number(e.target.value))}
          className="forecast-slider"
        />
        <span className="forecast-ctrl-value">{!forecastH ? 'Now' : `+${forecastH}h`}</span>
      </div>
    </div>
  );
}

function WindValue({ speed, dir }) {
  return (
    <>
      {speed.toFixed(0)} km/h{' '}
      <span style={{ display: 'inline-block', transform: `rotate(${dir}deg)`, fontSize: '9px', lineHeight: 1 }}>▲</span>
      {' '}{windDirLabel(dir)}
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
