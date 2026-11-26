import { GRID_RES } from '../config.js';
import { useLang } from '../i18n/LanguageContext.jsx';
import { DownloadIcon } from './Icons.jsx';

/**
 * Converts the current heatmap into a GeoJSON FeatureCollection
 * (one Polygon per grid cell) and triggers a file download.
 * Compatible with XCSoar / SkyDemon custom overlay import.
 */
export default function ExportButton({ heatmap, gridMeta, thermalBase, predictAlt, cape }) {
  const { t } = useLang();
  const disabled = !heatmap || !gridMeta;

  function handleExport() {
    const { lat, lon, rows, cols, radius } = gridMeta;
    // The thermal base is a height above the ground (thermal_top_agl). Every
    // altitude on screen names its reference, so the export does too, and
    // carries the MSL figure the info panel shows beside it: the same base over
    // the grid's mean terrain (amsl - agl of the prediction altitude). Null
    // means no estimate, never 0 m, which is a real one (saturated air).
    const baseAgl = thermalBase ?? null;
    const baseMsl = baseAgl != null && predictAlt != null
      ? Math.round(baseAgl + predictAlt.amsl - predictAlt.agl)
      : null;
    // rows-1 gaps between rows of sample points, not rows — see config.js.
    const latStep = (2 * radius) / (rows - 1);
    const lonStep = (2 * radius) / (cols - 1);

    const features = heatmap.map((prob, i) => {
      const row     = Math.floor(i / cols);
      const col     = i % cols;
      // The sample sits at the centre of the polygon it describes, so the cell
      // straddles it — exported footprints otherwise sit half a cell north-east
      // of the ground they were predicted for.
      const latS    = lat - radius + row * latStep - latStep / 2;
      const lonW    = lon - radius + col * lonStep - lonStep / 2;
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
          thermal_base_agl_m:  baseAgl,
          thermal_base_msl_m:  baseMsl,
          cape_j_per_kg:       cape != null ? Math.round(cape) : null,
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
      <DownloadIcon /> {t('exportGeoJSON')}
    </button>
  );
}
