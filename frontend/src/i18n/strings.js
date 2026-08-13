// UI string dictionary — Turkish is the default, English is the fallback.
// Values may contain {placeholders}, or be functions for count-dependent forms.

export const LANGS        = ['tr', 'en'];
export const DEFAULT_LANG = 'tr';

export const LANG_LABEL = { tr: 'TR', en: 'EN' };

// Compass points, 8-way, N-first — must stay index-aligned across languages
export const WIND_DIRS = {
  tr: ['K', 'KD', 'D', 'GD', 'G', 'GB', 'B', 'KB'],
  en: ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'],
};

const STRINGS = {
  tr: {
    // top bar
    weather:            'Hava Durumu',
    thermals:           'Termaller',
    temp:               'Sıcaklık',
    humidity:           'Nem',
    wind:               'Rüzgâr',
    solar:              'Güneş',
    lapse:              'Gradyan',
    tBase:              'T-taban',
    forecast:           'Tahmin',
    now:                'Şimdi',
    plusHours:          '+{n} sa',
    loading:            'Yükleniyor…',
    clickMapToLoad:     'Yüklemek için haritaya tıklayın',
    switchToEnglish:    "İngilizce'ye geç",
    switchToTurkish:    "Türkçe'ye geç",

    // info panel
    thermalPeak:        'Termal zirve',
    thermalBase:        'Termal taban',
    gliderCount:        '{n} planör',
    circlingCount:      '{n} daire çiziyor',
    towPlaneCount:      '{n} çekici uçak',
    airborneOnly:       'Sadece havada',
    airborneHint:       '(AGL > 10 m)',
    liveActivityLayer:  'Canlı aktivite katmanı',
    ognDensityHint:     '(OGN yoğunluğu)',
    retrainModel:       'Modeli yeniden eğit',
    retrainTitle:       'Modeli en güncel OGN verisiyle yeniden eğit',

    // statuses
    retrainAlready:     'Zaten eğitiliyor…',
    retrainStarted:     'Yeniden eğitim başladı',
    retrainFailed:      'Yeniden eğitim isteği başarısız',
    fetchingPrediction: 'Tahmin alınıyor…',
    clickMapToPredict:  'Termalleri tahmin etmek için haritaya tıklayın',

    // legend
    legendTitle:        'Termal olasılığı',
    low:                'Düşük',
    high:               'Yüksek',
    circling:           'daire çiziyor',
    glider:             'planör',
    towPlane:           'çekici uçak',
    activeThermal:      'aktif termal',

    // search
    searchPlaceholder:  'Planör, havaalanı, yer ara…',
    clearSearch:        'Aramayı temizle',
    tagGlider:          'planör',
    tagAirport:         'havaalanı',
    tagPlace:           'yer',
    searchAltVario:     'irtifa {alt} m  ·  {vario} m/s',

    // pinned glider card
    unpin:              'Sabitlemeyi kaldır',
    altMsl:             'İrtifa MSL',
    ground:             'Zemin',
    agl:                'AGL',
    vario:              'Variyo',
    heading:            'Rota',
    straight:           'düz uçuş',
    latN:               'K',
    latS:               'G',
    lonE:               'D',
    lonW:               'B',

    // export
    exportGeoJSON:      'GeoJSON dışa aktar',
  },

  en: {
    weather:            'Weather',
    thermals:           'Thermals',
    temp:               'Temp',
    humidity:           'Humidity',
    wind:               'Wind',
    solar:              'Solar',
    lapse:              'Lapse',
    tBase:              'T-base',
    forecast:           'Forecast',
    now:                'Now',
    plusHours:          '+{n}h',
    loading:            'Loading…',
    clickMapToLoad:     'Click the map to load',
    switchToEnglish:    'Switch to English',
    switchToTurkish:    'Switch to Turkish',

    thermalPeak:        'Thermal peak',
    thermalBase:        'Thermal base',
    gliderCount:        ({ n }) => `${n} glider${n === 1 ? '' : 's'}`,
    circlingCount:      '{n} circling',
    towPlaneCount:      ({ n }) => `${n} tow plane${n === 1 ? '' : 's'}`,
    airborneOnly:       'Airborne only',
    airborneHint:       '(AGL > 10 m)',
    liveActivityLayer:  'Live activity layer',
    ognDensityHint:     '(OGN density)',
    retrainModel:       'Retrain model',
    retrainTitle:       'Trigger model retraining on latest OGN data',

    retrainAlready:     'Already retraining…',
    retrainStarted:     'Retraining started',
    retrainFailed:      'Retrain request failed',
    fetchingPrediction: 'Fetching prediction…',
    clickMapToPredict:  'Click the map to predict thermals',

    legendTitle:        'Thermal probability',
    low:                'Low',
    high:               'High',
    circling:           'circling',
    glider:             'glider',
    towPlane:           'tow plane',
    activeThermal:      'active thermal',

    searchPlaceholder:  'Search gliders, airports, places…',
    clearSearch:        'Clear search',
    tagGlider:          'glider',
    tagAirport:         'airport',
    tagPlace:           'place',
    searchAltVario:     'alt {alt} m  ·  {vario} m/s',

    unpin:              'Unpin',
    altMsl:             'Alt MSL',
    ground:             'Ground',
    agl:                'AGL',
    vario:              'Vario',
    heading:            'Hdg',
    straight:           'straight',
    latN:               'N',
    latS:               'S',
    lonE:               'E',
    lonW:               'W',

    exportGeoJSON:      'Export GeoJSON',
  },
};

/** Look up `key` in `lang`, falling back to Turkish, then to the key itself. */
export function translate(lang, key, vars) {
  const entry = STRINGS[lang]?.[key] ?? STRINGS[DEFAULT_LANG][key] ?? key;
  if (typeof entry === 'function') return entry(vars ?? {});
  if (!vars) return entry;
  return entry.replace(/\{(\w+)\}/g, (m, name) => (vars[name] ?? m));
}

/** Number locale matching the active UI language. */
export function localeOf(lang) {
  return lang === 'tr' ? 'tr-TR' : 'en-GB';
}

export function windDirLabel(lang, deg) {
  return (WIND_DIRS[lang] ?? WIND_DIRS[DEFAULT_LANG])[Math.round(deg / 45) % 8];
}
