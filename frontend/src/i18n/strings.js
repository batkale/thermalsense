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
    plusMinutes:        '+{n} dk',
    plusHoursMinutes:   '+{h} sa {m} dk',
    loading:            'Yükleniyor…',
    clickMapToLoad:     'Yüklemek için haritaya tıklayın',
    sky:                'Gökyüzü',
    skyTip:             'Bu nokta ve saat için hava durumu (Open-Meteo). '
                      + 'Model girdisi değil, yalnızca bilgi içindir.',
    localTime:          'Yerel saat',
    localTimeTip:       'Tıklanan noktadaki saat ({offset}) — tarayıcınızın '
                      + 'saati değil. Tahmin seçiliyse o anın saatidir.',
    utcOffset:          'UTC{sign}{h}{m}',
    weather_cond_sunny:         'Güneşli',
    weather_cond_clear:         'Açık',
    weather_cond_mostly_clear:  'Az bulutlu',
    weather_cond_partly_cloudy: 'Parçalı bulutlu',
    weather_cond_cloudy:        'Bulutlu',
    weather_cond_fog:           'Sisli',
    weather_cond_drizzle:       'Çiseleyen',
    weather_cond_rain:          'Yağmurlu',
    weather_cond_snow:          'Karlı',
    weather_cond_thunderstorm:  'Gök gürültülü',
    switchToEnglish:    "İngilizce'ye geç",
    switchToTurkish:    "Türkçe'ye geç",
    switchToLight:      'Aydınlık moda geç',
    switchToDark:       'Karanlık moda geç',
    about:              'Hakkında',
    aboutTitle:         'ThermalSense nedir, nasıl öğrenir, ne kadar iyi',

    // info panel
    thermalPeak:        'Termal zirve',
    thermalBase:        'Termal taban',
    thermalPeakTip:     'Haritadaki en yüksek termal olasılığı — modelin bu '
                      + 'alandaki en iyi hücreye verdiği güven.',
    noInsolation:       'Güneş ışınımı yok — termal oluşamaz',
    noInsolationTip:    'Yüzey ısınması termal oluşumu için gereken eşiğin '
                      + 'altında. Model bu koşulda hiç eğitilmedi, o yüzden '
                      + 'tahmin üretmek yerine üretmediğini söylüyor.',
    thermalBaseTip:     'Termallerin tepe yaptığı tahmini yükseklik, yerden '
                      + '(AGL) ölçülür. Bunun üzerinde tırmanış beklemeyin.',
    capeTip:            'Konvektif potansiyel enerji. Yüksek değer güçlü '
                      + 'yükseliş, çok yüksek değer fırtına riski demektir.',
    predictionAlt:      'Tahmin yüksekliği',
    predictionAltTip:   'Isı haritası bu yükseklik için geçerlidir. Büyük '
                      + 'rakam deniz seviyesinden (MSL), alttaki satır alanın '
                      + 'ortalama arazisinden (AGL) ölçülür. Bir hava '
                      + 'aracına tıklamak onun yüksekliğini, boş haritaya tıklamak '
                      + 'arazi ortalaması üzerinde varsayılan bir yüksekliği '
                      + 'kullanır.',
    altFromGlider:      'hava aracından · yerden {n} m AGL',
    altFromDefault:     'varsayılan · yerden {n} m AGL',
    // Every altitude on screen names its reference: a bare "m" next to another
    // bare "m" invited comparing a sea-level height with an above-ground one
    // (TUSAŞ HANGAR mentor review, Nov 2026).
    metresMsl:          '{n} m MSL',
    metresAgl:          '{n} m AGL',
    thermalBaseMsl:     '≈ {n} m MSL (alanın arazisi üzerine)',
    gliderCount:        '{n} süzülen hava aracı',
    circlingCount:      '{n} daire çiziyor',
    towPlaneCount:      '{n} çekici uçak',
    airborneOnly:       'Sadece havada',
    airborneHint:       '(AGL > 10 m)',
    liveActivityLayer:  'Canlı aktivite katmanı',
    ognDensityHint:     '(termal telemetrisi)',
    retrainModel:       'Modeli yeniden eğit',
    retrainTitle:       'Modeli en güncel OGN verisiyle yeniden eğit',

    // raw data / product comparison (before/after divider on the map)
    compareToggle:      'Ham veri ↔ ThermalSense AI',
    compareHint:        '(AI kapalı / açık)',
    compareHandle:      'Ham veri ile ThermalSense AI çıktısı arasındaki ayırıcı — '
                      + 'karşılaştırmak için sürükleyin',
    compareBefore:      'AI kapalı',
    compareAfter:       'AI açık',
    compareRawTitle:    'Sistem kapalı · ham veri',
    compareBuiltTitle:  'ThermalSense AI · işlenmiş çıktı',
    rawBeacons:         '{n} uçak: bildirilen konum, irtifa ve tırmanma hızı',
    rawElevation:       '{n} yükseklik ölçümü, ~{km} km arayla',
    rawWeather:         'Standart hava servisi: tüm alan için tek, düşük çözünürlüklü örnek',
    rawNoGrid:          'Arazi ve hava girdilerini görmek için haritaya tıklayın',
    builtCells:         '{n} hücre, her biri {f} özellik → termal olasılığı',
    builtCellsOnly:     '{n} hücre → termal olasılığı',
    builtCircling:      '{n} uçak daire çiziyor (dönüş hızından)',
    builtThermals:      '{n} aktif termal (kümelenen tırmanışlardan)',

    // statuses
    retrainAlready:     'Zaten eğitiliyor…',
    retrainStarted:     'Yeniden eğitim başladı',
    retrainFailed:      'Yeniden eğitim isteği başarısız',
    fetchingPrediction: 'Tahmin alınıyor…',
    clickMapToPredict:  'Termalleri tahmin etmek için haritaya tıklayın',
    predictFailed:      'Tahmin alınamadı. Bağlantıyı kontrol edip tekrar tıklayın.',
    noInsolationMap:    'Bu saatte güneş ışınımı yok, termal oluşamaz. İleri '
                      + 'saatler için Tahmin sürgüsünü kaydırın.',
    meteoFallbackMap:   'Hava tahmini şu an alınamadı; bu harita varsayılan '
                      + 'hava değerleriyle çizildi. Yalnızca arazi sıralamasına '
                      + 'güvenin, yüzdelere değil.',

    // legend
    legendTitle:        'Termal olasılığı',
    low:                'Düşük',
    high:               'Yüksek',
    circling:           'daire çiziyor',
    glider:             'süzülen hava aracı',
    towPlane:           'çekici uçak',
    activeThermal:      'aktif termal',

    // search
    searchPlaceholder:  'Hava aracı, havaalanı, yer ara…',
    clearSearch:        'Aramayı temizle',
    tagGlider:          'hava aracı',
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
    nightlySeed:        'Gecelik seed',
    seedAdded:          '{n} satır eklendi',
    noSeedYet:          'Henüz çalışmadı',
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
    sectionMeteoApi:    'Hava verisi kotası (bugün)',
    cacheHits:          'Önbellekten karşılanan',
    sectionTerrain:     'Arazi verisi (bugün)',
    terrainSource:      'Kaynak',
    sourceCopernicus:   'Copernicus GLO-30 (kotasız)',
    sourceOpenTopoData: 'OpenTopoData (günde 1.000 istek)',
    copernicusToday:    'Copernicus',
    copernicusValue:    '{p} nokta · {b} blok · {mb} MB',
    apiFallbackToday:   'Yedek API isteği',
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
    plusMinutes:        '+{n} min',
    plusHoursMinutes:   '+{h}h {m}min',
    loading:            'Loading…',
    clickMapToLoad:     'Click the map to load',
    sky:                'Sky',
    skyTip:             'Weather at this point and time (Open-Meteo). '
                      + 'Shown for context; it is not a model input.',
    localTime:          'Local time',
    localTimeTip:       'The clock at the clicked point ({offset}), not your '
                      + "browser's. With a forecast selected, it is that moment.",
    utcOffset:          'UTC{sign}{h}{m}',
    weather_cond_sunny:         'Sunny',
    weather_cond_clear:         'Clear',
    weather_cond_mostly_clear:  'Mostly clear',
    weather_cond_partly_cloudy: 'Partly cloudy',
    weather_cond_cloudy:        'Cloudy',
    weather_cond_fog:           'Fog',
    weather_cond_drizzle:       'Drizzle',
    weather_cond_rain:          'Rain',
    weather_cond_snow:          'Snow',
    weather_cond_thunderstorm:  'Thunderstorm',
    switchToEnglish:    'Switch to English',
    switchToTurkish:    'Switch to Turkish',
    switchToLight:      'Switch to light mode',
    switchToDark:       'Switch to dark mode',
    about:              'About',
    aboutTitle:         'What ThermalSense is, how it learns and how good it is',

    thermalPeak:        'Thermal peak',
    thermalBase:        'Thermal base',
    thermalPeakTip:     'The highest thermal probability on the map — the model '
                      + 'confidence for the best cell in this area.',
    noInsolation:       'No insolation — thermals cannot form',
    noInsolationTip:    'Surface heating is below the level any thermal needs. '
                      + 'The model was never trained in this regime, so it says '
                      + 'it has no forecast rather than inventing one.',
    thermalBaseTip:     'Estimated height where thermals top out, measured '
                      + 'above ground (AGL). Expect no climb above it.',
    capeTip:            'Convective available potential energy. Higher means '
                      + 'stronger lift; very high means storm risk.',
    predictionAlt:      'Prediction altitude',
    predictionAltTip:   'The heatmap answers for this altitude. The large '
                      + 'figure is above sea level (MSL); the line below is '
                      + 'above the area’s mean terrain (AGL). Clicking an '
                      + 'aircraft uses its own altitude; clicking '
                      + 'bare map uses a default height above mean terrain.',
    altFromGlider:      'from aircraft · {n} m AGL',
    altFromDefault:     'default · {n} m AGL',
    metresMsl:          '{n} m MSL',
    metresAgl:          '{n} m AGL',
    thermalBaseMsl:     '≈ {n} m MSL (over the area’s terrain)',
    gliderCount:        ({ n }) => `${n} soaring aircraft`,
    circlingCount:      '{n} circling',
    towPlaneCount:      ({ n }) => `${n} tow plane${n === 1 ? '' : 's'}`,
    airborneOnly:       'Airborne only',
    airborneHint:       '(AGL > 10 m)',
    liveActivityLayer:  'Live activity layer',
    ognDensityHint:     '(thermal telemetry)',
    retrainModel:       'Retrain model',
    retrainTitle:       'Trigger model retraining on latest OGN data',

    compareToggle:      'Raw data ↔ ThermalSense AI',
    compareHint:        '(AI off / on)',
    compareHandle:      'Divider between the raw data and the ThermalSense AI '
                      + 'output — drag to compare',
    compareBefore:      'AI off',
    compareAfter:       'AI on',
    compareRawTitle:    'System off · raw data',
    compareBuiltTitle:  'ThermalSense AI · processed output',
    rawBeacons:         '{n} aircraft: reported position, altitude and climb rate',
    rawElevation:       '{n} elevation samples, ~{km} km apart',
    rawWeather:         'Standard weather service: one low-resolution sample for the whole area',
    rawNoGrid:          'Click the map to see the terrain and weather inputs',
    builtCells:         '{n} cells, {f} features each → lift probability',
    builtCellsOnly:     '{n} cells → lift probability',
    builtCircling:      '{n} circling (from turn rate)',
    builtThermals:      ({ n }) => `${n} active thermal${n === 1 ? '' : 's'} (from clustered climbs)`,

    retrainAlready:     'Already retraining…',
    retrainStarted:     'Retraining started',
    retrainFailed:      'Retrain request failed',
    fetchingPrediction: 'Fetching prediction…',
    clickMapToPredict:  'Click the map to predict thermals',
    predictFailed:      'Could not get a prediction. Check the connection and '
                      + 'click again.',
    noInsolationMap:    'No sun at this hour, so no thermals can form. Drag the '
                      + 'Forecast slider to look ahead.',
    meteoFallbackMap:   'The weather forecast is unavailable right now, so this '
                      + 'map was drawn on default weather. Trust the terrain '
                      + 'ranking, not the percentages.',

    legendTitle:        'Thermal probability',
    low:                'Low',
    high:               'High',
    circling:           'circling',
    glider:             'soaring aircraft',
    towPlane:           'tow plane',
    activeThermal:      'active thermal',

    searchPlaceholder:  'Search aircraft, airports, places…',
    clearSearch:        'Clear search',
    tagGlider:          'aircraft',
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
    nightlySeed:        'Nightly seed',
    seedAdded:          '{n} rows added',
    noSeedYet:          'Has not run yet',
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
    sectionMeteoApi:    'Weather API quota (today)',
    cacheHits:          'Served from cache',
    sectionTerrain:     'Terrain data (today)',
    terrainSource:      'Source',
    sourceCopernicus:   'Copernicus GLO-30 (no quota)',
    sourceOpenTopoData: 'OpenTopoData (1,000 requests/day)',
    copernicusToday:    'Copernicus',
    copernicusValue:    '{p} points · {b} blocks · {mb} MB',
    apiFallbackToday:   'Fallback API requests',
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
