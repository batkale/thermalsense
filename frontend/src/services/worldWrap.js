// -----------------------------------------------------------------------------
// One world that wraps around
//
// Leaflet lays copies of the world side by side, and a point drawn at its own
// longitude lands on only one of them — so from a low zoom the map showed
// three worlds, two of them empty of aircraft. The map now never shows more
// than one world's width (see WorldFitController) and jumps back to the
// original copy when panned across the antimeridian (worldCopyJump). What is
// left is the stretch either side of 180°, where the view straddles two copies:
// every overlay is drawn at the copy of its longitude nearest the view centre,
// which is always the copy on screen.
// -----------------------------------------------------------------------------

/** `lon` shifted by whole turns to lie within 180° of `centerLng`. */
export function nearLng(lon, centerLng) {
  return lon + 360 * Math.round((centerLng - lon) / 360);
}

/**
 * A sequence of points as one continuous line, placed nearest `centerLng`.
 *
 * Each point is unwrapped against the one before it, so a track crossing the
 * date line stays one line rather than leaping across the screen, and the whole
 * line is then placed by its last point — the aircraft — so the icon and its
 * path are always drawn on the same copy.
 */
export function unwrapNear(points, centerLng) {
  if (!points.length) return [];
  const out = new Array(points.length);
  let prev = points[0].lon;
  for (let i = 0; i < points.length; i++) {
    prev = nearLng(points[i].lon, prev);
    out[i] = { lat: points[i].lat, lon: prev };
  }
  const shift = nearLng(prev, centerLng) - prev;
  if (shift !== 0) for (const p of out) p.lon += shift;
  return out;
}

/**
 * A viewport the server can filter on, wherever the view has been panned to.
 *
 * Shifts the box by whole turns so its centre lies inside ±180° — the server
 * also tests each beacon one turn either side, so a box straddling the date
 * line still selects both edges. A box a full turn wide or wider is clamped to
 * exactly one turn, which the server reads as "everything".
 */
export function normaliseBounds({ lat_min, lat_max, lon_min, lon_max }) {
  const lat = (v) => Math.max(-90, Math.min(90, v));
  if (lon_max - lon_min >= 360) {
    return { lat_min: lat(lat_min), lat_max: lat(lat_max), lon_min: -180, lon_max: 180 };
  }
  const mid   = (lon_min + lon_max) / 2;
  const shift = nearLng(mid, 0) - mid;
  return {
    lat_min: lat(lat_min), lat_max: lat(lat_max),
    lon_min: lon_min + shift, lon_max: lon_max + shift,
  };
}
