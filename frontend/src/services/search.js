const NOMINATIM = 'https://nominatim.openstreetmap.org/search';

// Europe + Great Britain bounding box for search hints
const EU_LON_MIN = -15.0;
const EU_LAT_MAX =  72.0;
const EU_LON_MAX =  45.0;
const EU_LAT_MIN =  35.0;

export async function geocodeQuery(query) {
  if (!query.trim()) return [];
  const params = new URLSearchParams({
    q:            query,
    format:       'json',
    limit:        '6',
    viewbox:      `${EU_LON_MIN},${EU_LAT_MAX},${EU_LON_MAX},${EU_LAT_MIN}`,
    bounded:      '0',
    addressdetails: '0',
  });
  const r = await fetch(`${NOMINATIM}?${params}`);
  if (!r.ok) throw new Error(`Nominatim ${r.status}`);
  const data = await r.json();
  return data.map(item => {
    const parts = item.display_name.split(',');
    return {
      type:     item.class === 'aeroway' ? 'airport' : 'place',
      label:    parts[0].trim(),
      sublabel: parts.slice(1, 3).join(',').trim(),
      lat:      parseFloat(item.lat),
      lon:      parseFloat(item.lon),
      // zoom based on place significance
      zoom: item.class === 'aeroway' ? 13
          : item.importance > 0.6    ? 9
          : item.importance > 0.3    ? 11
          : 13,
    };
  });
}
