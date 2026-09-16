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
    tempTip:            'Yerden 2 m yükseklikteki hava sıcaklığı.',
    humidityTip:        'Bağıl nem. Kuru hava daha yüksek bir bulut tabanı '
                      + 'demektir.',
    windTip:            'Yer rüzgârının hızı ve geldiği yön. Güçlü rüzgâr '
                      + 'termalleri eğer ve dağıtır.',
    solarTip:           'Yere ulaşan güneş ışınımı — termalleri besleyen ısı '
                      + 'kaynağı. 50 W/m² altında yüzey ısınması olmaz.',
    lapseTip:           'Sıcaklığın yükseklikle düşme hızı. Dik gradyan '
                      + 'kararsız hava ve güçlü termal demektir.',
    cinTip:             'Konvektif engelleme — termallerin aşması gereken '
                      + 'kapak. Yüksek değer termalleri bastırır.',
    forecast:           'Tahmin',
    now:                'Şimdi',
    plusHours:          '+{n} sa',
    loading:            'Yükleniyor…',
    clickMapToLoad:     'Yüklemek için haritaya tıklayın',
    switchToEnglish:    "İngilizce'ye geç",
    switchToTurkish:    "Türkçe'ye geç",
    switchToLight:      'Aydınlık moda geç',
    switchToDark:       'Karanlık moda geç',

    // info panel
    thermalPeak:        'Termal zirve',
    thermalBase:        'Termal taban',
    thermalPeakTip:     'Haritadaki en yüksek termal olasılığı — modelin bu '
                      + 'alandaki en iyi hücreye verdiği güven.',
    noInsolation:       'Güneş ışınımı yok — termal oluşamaz',
    noInsolationTip:    'Yüzey ısınması termal oluşumu için gereken eşiğin '
                      + 'altında. Model bu koşulda hiç eğitilmedi, o yüzden '
                      + 'tahmin üretmek yerine üretmediğini söylüyor.',
    thermalBaseTip:     'Termallerin tepe yaptığı tahmini yükseklik. Bunun '
                      + 'üzerinde tırmanış beklemeyin.',
    capeTip:            'Konvektif potansiyel enerji. Yüksek değer güçlü '
                      + 'yükseliş, çok yüksek değer fırtına riski demektir.',
    predictionAlt:      'Tahmin yüksekliği',
    predictionAltTip:   'Isı haritası bu yükseklik için geçerlidir. Planöre '
                      + 'tıklamak onun yüksekliğini, boş haritaya tıklamak '
                      + 'arazi ortalaması üzerinde varsayılan bir yüksekliği '
                      + 'kullanır.',
    altFromGlider:      'planörden · arazi {n} m',
    altFromDefault:     'varsayılan · arazi {n} m',
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

    // training status panel
    openTrainingStatus: 'Eğitim durumu',
    trainingStatus:     'Eğitim durumu',
    close:              'Kapat',
    loadingStatus:      'Durum yükleniyor…',
    trainingStatusFailed: 'Durum alınamadı. Bağlantı açık mı?',
    sectionUnavailable: 'Bu bölüm okunamadı: {e}',
    gateWaiting:        'Kalite kapısı bekliyor: benchmark {g} / {need} karışık grup. '
                      + 'Eşik aşılana kadar model değişmez.',
    gateReady:          'Kalite kapısı açık: benchmark {g} / {need} karışık grup. '
                      + 'Yeni modeller doğrulanarak devreye alınabilir.',
    turkiyeNoData:      'Türkiye için henüz eğitim örneği yok. OGN akışında Türkiye '
                      + 'trafiği çok az; bölge ayrıca ölçülemiyor.',
    sectionFeed:        'Veri akışı',
    feedConnection:     'OGN bağlantısı',
    connected:          'Bağlı',
    disconnected:       'Kopuk',
    feedArea:           'Toplama alanı',
    worldwide:          'Dünya çapı',
    glidersInView:      'Alandaki uçak',
    beaconsLastHour:    'Son 1 saatte beacon',
    sectionModel:       'Model',
    lastFit:            'Son eğitim',
    noFitYet:           'Henüz eğitim döngüsü çalışmadı',
    fit_too_few_samples:    'Atlandı — örnek yetersiz',
    fit_too_few_positives:  'Atlandı — pozitif örnek yetersiz',
    fit_benchmark_too_small:'Doğrulanamadı — benchmark küçük, eski model korundu',
    fit_accepted_unverified:'Doğrulanmadan kabul edildi (serviste model yoktu)',
    fit_score_not_finite:   'Atıldı — skor hesaplanamadı',
    fit_rejected:           'Reddedildi — mevcut modelden kötü',
    fit_rebaselined:        'Kabul edildi — üst üste retlerden sonra yeni referans',
    fit_accepted:           'Kabul edildi — yeni model serviste',
    pairedDelta:        'Fark (yeni − mevcut)',
    benchmarkScore:     'Benchmark skoru',
    notComparable:      'Karşılaştırılabilir değil',
    sectionBuffer:      'Eğitim verisi',
    samples:            'Örnek',
    samplesValue:       '{n} ({p} pozitif)',
    distinctDays:       'Farklı gün',
    region:             'Bölge',
    positivesShort:     'Pozitif',
    regionTurkiye:      'Türkiye',
    regionAlps:         'Alpler',
    regionGermany:      'Almanya',
    regionIberia:       'İberya',
    regionOther:        'Diğer',
    sectionStorage:     'Depolama',
    archiveDays:        'Arşiv',
    archiveValue:       '{d} gün, {mb} MB',
    archiveEmpty:       'Henüz arşivlenmiş gün yok',
    climatologyBuckets: 'Klimatoloji kovası',
    diskFree:           'Boş disk',
    sectionTerrainApi:  'Arazi API kotası (bugün)',
    requestsToday:      'İstek',
    rateLimited:        '{n} istek hız sınırına takıldı',
    agoSeconds:         '{n} sn önce',
    agoMinutes:         '{n} dk önce',
    agoHours:           '{n} sa önce',
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
    tempTip:            'Air temperature 2 m above the ground.',
    humidityTip:        'Relative humidity. Drier air means a higher cloud '
                      + 'base.',
    windTip:            'Surface wind speed and the direction it comes from. '
                      + 'Strong wind tilts and breaks up thermals.',
    solarTip:           'Solar radiation reaching the ground — the heat that '
                      + 'drives thermals. Below 50 W/m² there is no surface '
                      + 'heating.',
    lapseTip:           'How fast temperature falls with height. A steep lapse '
                      + 'rate means unstable air and strong thermals.',
    cinTip:             'Convective inhibition — the cap thermals must break '
                      + 'through. High values suppress them.',
    forecast:           'Forecast',
    now:                'Now',
    plusHours:          '+{n}h',
    loading:            'Loading…',
    clickMapToLoad:     'Click the map to load',
    switchToEnglish:    'Switch to English',
    switchToTurkish:    'Switch to Turkish',
    switchToLight:      'Switch to light mode',
    switchToDark:       'Switch to dark mode',

    thermalPeak:        'Thermal peak',
    thermalBase:        'Thermal base',
    thermalPeakTip:     'The highest thermal probability on the map — the model '
                      + 'confidence for the best cell in this area.',
    noInsolation:       'No insolation — thermals cannot form',
    noInsolationTip:    'Surface heating is below the level any thermal needs. '
                      + 'The model was never trained in this regime, so it says '
                      + 'it has no forecast rather than inventing one.',
    thermalBaseTip:     'Estimated height where thermals top out. Expect no '
                      + 'climb above it.',
    capeTip:            'Convective available potential energy. Higher means '
                      + 'stronger lift; very high means storm risk.',
    predictionAlt:      'Prediction altitude',
    predictionAltTip:   'The heatmap answers for this altitude. Clicking a '
                      + 'glider uses the altitude of that aircraft; clicking '
                      + 'bare map uses a default height above mean terrain.',
    altFromGlider:      'from glider · terrain {n} m',
    altFromDefault:     'default · terrain {n} m',
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

    // training status panel
    openTrainingStatus: 'Training status',
    trainingStatus:     'Training status',
    close:              'Close',
    loadingStatus:      'Loading status…',
    trainingStatusFailed: 'Could not load status. Is the tunnel open?',
    sectionUnavailable: 'This section could not be read: {e}',
    gateWaiting:        'Quality gate waiting: benchmark {g} / {need} mixed-label groups. '
                      + 'The model will not change until this is met.',
    gateReady:          'Quality gate open: benchmark {g} / {need} mixed-label groups. '
                      + 'New models can be verified and promoted.',
    turkiyeNoData:      'No training samples from Türkiye yet. Türkiye traffic on the '
                      + 'OGN feed is very sparse, so the region cannot be scored on its own.',
    sectionFeed:        'Data feed',
    feedConnection:     'OGN connection',
    connected:          'Connected',
    disconnected:       'Disconnected',
    feedArea:           'Collection area',
    worldwide:          'Worldwide',
    glidersInView:      'Aircraft in area',
    beaconsLastHour:    'Beacons, last hour',
    sectionModel:       'Model',
    lastFit:            'Last training',
    noFitYet:           'No training cycle has run yet',
    fit_too_few_samples:    'Skipped — not enough samples',
    fit_too_few_positives:  'Skipped — not enough positives',
    fit_benchmark_too_small:'Not verifiable — benchmark too small, kept current model',
    fit_accepted_unverified:'Accepted unverified (no model was serving)',
    fit_score_not_finite:   'Discarded — score could not be computed',
    fit_rejected:           'Rejected — worse than the current model',
    fit_rebaselined:        'Accepted — new baseline after repeated rejections',
    fit_accepted:           'Accepted — new model in service',
    pairedDelta:        'Delta (new − current)',
    benchmarkScore:     'Benchmark score',
    notComparable:      'Not comparable',
    sectionBuffer:      'Training data',
    samples:            'Samples',
    samplesValue:       '{n} ({p} positive)',
    distinctDays:       'Distinct days',
    region:             'Region',
    positivesShort:     'Positive',
    regionTurkiye:      'Türkiye',
    regionAlps:         'Alps',
    regionGermany:      'Germany',
    regionIberia:       'Iberia',
    regionOther:        'Other',
    sectionStorage:     'Storage',
    archiveDays:        'Archive',
    archiveValue:       '{d} days, {mb} MB',
    archiveEmpty:       'No archived days yet',
    climatologyBuckets: 'Climatology buckets',
    diskFree:           'Free disk',
    sectionTerrainApi:  'Terrain API quota (today)',
    requestsToday:      'Requests',
    rateLimited:        '{n} requests hit the rate limit',
    agoSeconds:         '{n} s ago',
    agoMinutes:         '{n} min ago',
    agoHours:           '{n} h ago',
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
