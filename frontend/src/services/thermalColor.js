// -----------------------------------------------------------------------------
// Colour scale: transparent → blue-green → yellow → orange-red
// -----------------------------------------------------------------------------
// Shared by the 2D heatmap (ThermalMap.jsx) and the 3D view (Thermal3D.jsx),
// so a cell and the column standing on it are the same colour.
//
// The floor and the opacity are set by what the model actually returns, not by
// the 0-1 range it could. Measured on the live site (15 Nov 2026, ten daytime
// forecast grids at four sites): the median cell sat between 3.5% and 25%, and
// no grid peaked above 56%. Against that the old 5% floor left
// holes — one grid drew 932 of its 40,401 cells — and 15% opacity over
// satellite imagery made the rest a tint nobody could see, so a completed scan
// looked like no scan. Only an exact zero is skipped now, which is what the
// night guard sends; a low probability is drawn, in the "low" colour.

/** [r, g, b], 0-255, for a probability. */
export function thermalRGB(p) {
  if (p < 0.35) {
    const t = p / 0.35;
    return [20, Math.round(120 + t * 135), 255];
  }
  if (p < 0.65) {
    const t = (p - 0.35) / 0.30;
    return [Math.round(20 + t * 235), 255, Math.round(255 - t * 255)];
  }
  const t = Math.min(1, (p - 0.65) / 0.35);
  return [255, Math.round(255 - t * 230), 0];
}

/** CSS colour for one heatmap cell, or null for a cell left unpainted. */
export function thermalColor(p) {
  if (p < 0.005) return null;
  const [r, g, b] = thermalRGB(p);
  return `rgba(${r}, ${g}, ${b}, ${0.34 + p * 0.40})`;
}
