const NOMINATIM = 'https://nominatim.openstreetmap.org/search';

// Türkiye bounding box — biases results locally; bounded=0 still allows
// the rest of Europe through, just ranked lower.
const TR_LON_MIN =  25.6;
const TR_LAT_MAX =  42.2;
const TR_LON_MAX =  45.0;
const TR_LAT_MIN =  35.8;

export async function geocodeQuery(query, lang = 'tr') {
  if (!query.trim()) return [];
  const params = new URLSearchParams({
    q:            query,
    format:       'json',
    limit:        '6',
    viewbox:      `${TR_LON_MIN},${TR_LAT_MAX},${TR_LON_MAX},${TR_LAT_MIN}`,
    bounded:      '0',
    addressdetails: '0',
    'accept-language': lang,
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
