import { GRID_RES } from '../config.js';
import { useLang } from '../i18n/LanguageContext.jsx';

/**
 * Converts the current heatmap into a GeoJSON FeatureCollection
 * (one Polygon per grid cell) and triggers a file download.
 * Compatible with XCSoar / SkyDemon custom overlay import.
 */
export default function ExportButton({ heatmap, gridMeta, thermalBase, cape }) {
  const { t } = useLang();
  const disabled = !heatmap || !gridMeta;

  function handleExport() {
    const { lat, lon, rows, cols, radius } = gridMeta;
    const latStep = (2 * radius) / rows;
    const lonStep = (2 * radius) / cols;

    const features = heatmap.map((prob, i) => {
      const row     = Math.floor(i / cols);
      const col     = i % cols;
      const latS    = lat - radius + row * latStep;
      const lonW    = lon - radius + col * lonStep;
      const latN    = latS + latStep;
      const lonE    = lonW + lonStep;

      return {
        type: 'Feature',
        geometry: {
          type: 'Polygon',
          // GeoJSON uses [lon, lat] order; ring must close
          coordinates: [[
            [lonW, latS], [lonE, latS], [lonE, latN], [lonW, latN], [lonW, latS],
          ]],
        },
        properties: {
          thermal_probability: Math.round(prob * 1000) / 1000,
          thermal_base_m:      thermalBase,
          cape_j_per_kg:       Math.round(cape),
        },
      };
    }).filter(f => f.properties.thermal_probability > 0.05); // drop empty cells

    const geojson = { type: 'FeatureCollection', features };
    const blob    = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/geo+json' });
    const url     = URL.createObjectURL(blob);
    const a       = document.createElement('a');
    a.href        = url;
    a.download    = `thermalsense_${lat.toFixed(2)}_${lon.toFixed(2)}.geojson`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <button className="btn-primary" onClick={handleExport} disabled={disabled}>
      ↓ {t('exportGeoJSON')}
    </button>
  );
}
