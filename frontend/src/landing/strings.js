// Copy for the landing page (/about) — Turkish is the default, English the
// fallback, same contract as i18n/strings.js: every key exists in both.
//
// A separate dictionary on purpose. This is long-form prose that only the
// landing entry needs; in i18n/strings.js it would ride along in the map app's
// bundle for nothing. The language itself is still the app's — the page reads
// `lang` from the shared LanguageProvider, so a choice made here follows the
// visitor into the map and back.
//
// The page is written for people evaluating the project (investors, partners)
// as well as pilots. Confident, but every figure below is a measurement:
//
//   - Score tables: `python -m evaluation.evaluate --variant base --by-hour`,
//     run 16 Nov 2026 on the training buffer of 6 Nov 2026 with the 862
//     invented-weather rows removed (meteo_client.is_fallback_row), exactly as
//     ThermalModel.load() removes them. The 0.542 / 11,774 comparison is the
//     27 Sep 2026 table in CLAUDE.md.
//   - Label thresholds, gate, buffer, cadence: models/thermal_model.py,
//     data/ogn_client.py and config.py.
//   - The 27% pseudo-absence figure and the 7.7 M reports a day: CLAUDE.md.
//
// When one of those is re-measured, change it here too: this page is the only
// place a visitor reads them.

import { DEFAULT_LANG } from '../i18n/strings.js';

const STRINGS = {
  tr: {
    docTitle:      'ThermalSense: planörlerden öğrenen termal tahmini',
    skipToContent: 'İçeriğe geç',

    // nav
    navSee:        'Pilot için',
    navLearn:      'Nasıl öğrenir',
    navScore:      'Sonuçlar',
    navData:       'Veri',
    navPitch:      'Yatırımcı sunumu',
    navLabel:      'Sayfa bölümleri',
    openMap:       'Canlı haritayı aç',
    openMapShort:  'Haritayı aç',

    // hero
    heroUnseen:    'Termal görünmez.',
    heroSeen:      'Planörler yerini gösterir.',
    heroLede:      'ThermalSense, Open Glider Network’teki canlı planör '
                 + 'trafiğini eğitim verisine dönüştürür. Haritada bir noktaya '
                 + 'tıkladığınızda çevresindeki yaklaşık 11 × 8 km’lik alanı 50 '
                 + 'metrelik hücrelerle puanlar ve tırmanışın en olası olduğu '
                 + 'yerleri gösterir. Model her beş dakikada bir kendini yeniden '
                 + 'eğitir; her uçuş gününden, yaşandığı anda öğrenir.',
    heroSecondary: 'Nasıl öğrendiğine bakın',

    // hero simulation
    simAlt:        'Canlandırma: karanlık bir harita ızgarası üzerinde düz '
                 + 'uçan ve daire çizen planörler; daire çizilen yerlerde '
                 + 'hücreler ısınıyor',
    simCaption:    'Canlandırma, canlı veri değil. Göremediği bir termale giren '
                 + 'planör daire çizmeye başlar; altındaki hücreler de onun '
                 + 'bıraktığı kanıtla ısınır. Modelin öğrenme biçimi tam olarak '
                 + 'budur.',
    simReveal:     'Gizli termalleri göster',
    simHide:       'Termalleri gizle',
    simPause:      'Durdur',
    simPlay:       'Oynat',
    simCircling:   'daire çiziyor',
    simCruising:   'düz uçuş',
    simScale:      'termal olasılığı',
    low:           'düşük',
    high:          'yüksek',

    // live strip
    liveNow:       'ThermalSense şu anda dünya genelinde {n} aktif termali '
                 + 'izliyor.',
    liveNone:      'Şu an dünya genelinde aktif termal yok. Ana uçuş '
                 + 'bölgelerinde gece ya da sakin bir saat olabilir.',

    // what you see
    seeTitle:      'Pilot ne görür',
    seeCellsUnit:  'hücre',
    seeCells:      'Tıkladığınız noktanın çevresindeki yaklaşık 11 × 8 km’lik '
                 + 'alan, 50 metrelik 40.401 hücreye bölünür. Her hücre 22 '
                 + 'girdiyle puanlanır: arazi, hava durumu, günün saati ve '
                 + 'mevsim, bir de yerden yükseklik.',
    seePassesUnit: 'geçiş',
    seePasses:     'Her tahmin, hava verisi hafifçe değiştirilerek 50 kez '
                 + 'çalıştırılır. Böylece her hücre, olasılığın yanında bir '
                 + 'belirsizlik tahmini de taşır.',
    seeAheadFig:   '+23 sa',
    seeAheadUnit:  'ileri tahmin',
    seeAhead:      'Aynı alan, Open-Meteo tahmin verisiyle 23 saat sonrasına '
                 + 'kadar yeniden puanlanabilir: ilk üç saat 15 dakikada bir, '
                 + 'sonrası saat başı. Çeyrek saatler, saatlik değerlerin '
                 + 'arasından enterpolasyonla bulunur.',
    seeLiveFig:    '1 sn',
    seeLiveUnit:   'canlı trafik',
    seeLive:       'Canlı trafik saniyede bir yenilenir. Daire çizerek '
                 + 'tırmanan planörler öne çıkarılır ve bulundukları yer aktif '
                 + 'termal olarak işaretlenir.',
    noteAltTitle:  'Pilotun irtifası için yanıt verir',
    noteAlt:       'Bir planöre tıklarsanız tahmin onun irtifası için yapılır. '
                 + '900 m’de biten bir termal, 1.500 m’deki pilotun işine '
                 + 'yaramaz.',
    noteNightTitle:'Dayanağı yoksa tahmin göstermez',
    noteNight:     'Güneş zemini ısıtamayacak kadar alçaktaysa harita boş kalır '
                 + 've nedenini söyler. Dayanağı olmayan bir tahmini asla '
                 + 'göstermez.',
    noteRawTitle:  'Ham veriyi yanında gösterir',
    noteRaw:       'Haritayı ikiye bölen ayırıcının bir yanında ham girdiler, '
                 + 'öbür yanında bitmiş tahmin durur. Modelin tam olarak neyle '
                 + 'çalıştığı bir bakışta görülür.',
    noteRawLink:   'Karşılaştırmayı aç',

    // how it learns
    learnTitle:    'Kendi kendini eğiten bir model',
    learnIntro:    'Bir planörün barogramı testere dişine benzer: termalde '
                 + 'dönerek tırmanır, sonra süzülerek irtifa kaybeder. '
                 + 'ThermalSense bu izin iki net ucunu etiket olarak kullanır: '
                 + 'dönerek tırmanış termalin varlığı, alçalan havada düz '
                 + 'süzülüş yokluğudur. Belirsiz kalan her şey dışarıda bırakılır.',
    baroAlt:       'Şematik barogram: zamana karşı irtifa. Dönerek tırmanılan '
                 + 'bölümler pozitif, düz alçalınan bölümler negatif, aradaki '
                 + 'belirsiz bölüm ile 200 m altı ve termal tabanının 300 m '
                 + 'üstü atılan olarak işaretli.',
    baroAxisAlt:   'irtifa',
    baroAxisTime:  'zaman',
    baroCeil:      'termal tabanı + 300 m',
    baroFloor:     '200 m AGL',
    baroClimb:     '+2,4 m/s',
    baroSink:      '−0,9 m/s',
    baroWeak:      '+0,3 m/s',
    labelPos:      'Pozitif',
    labelPosRule:  'Daire çiziyor ve 1,5 m/s’den hızlı tırmanıyor.',
    labelNeg:      'Negatif',
    labelNegRule:  'Düz uçuyor ve 0,5 m/s’den hızlı alçalıyor.',
    labelDrop:     'Kullanılmaz',
    labelDropRule: 'İkisinin arası; ayrıca 200 m AGL altı ve termal tabanının '
                 + '300 m üstü.',
    whyDropTitle:  'Belirsiz orta kısım neden dışarıda',
    whyDrop:       'Termalin içinden ya da bir yamaç boyunca düz uçan planör '
                 + 'dönmüyordur, ama alçalan havada da değildir. Onu “termal '
                 + 'yok” diye saymak, modele termalin tam olduğu yerde '
                 + 'olmadığını öğretirdi. Canlı akışın 24 dakikasında, daha '
                 + 'basit bir kuralın “termal yok” diyeceği 55.856 satırın '
                 + 'yalnızca 14.940’ı (%27) gerçekten alçalan havadaydı. Aynı '
                 + 'veriden çok daha temiz etiketler.',
    stepsTitle:    'Bildirimden haritaya',
    stepFeed:      'Akış',
    stepFeedText:  'Open Glider Network’ün dünya çapındaki APRS akışı '
                 + 'kesintisiz dinlenir: günde yaklaşık 7,7 milyon konum '
                 + 'bildirimi.',
    stepGate:      'Eleme',
    stepGateText:  'Her bildirim önce akla yatkınlık kontrolünden geçer. '
                 + '15.000 m üstü irtifa, 400 km/sa üstü hız ve hiçbir uçağın '
                 + 'yapamayacağı sıçramalar elenir; üç kez yakalanan kimlik 30 '
                 + 'dakika karantinaya alınır.',
    stepLabel:     'Etiket',
    stepLabelText: 'Kalan bildirimler yukarıdaki kuralla etiketlenir. Güneş '
                 + 'ışınımı 50 W/m² altındaysa, yani zemin ısınmıyorsa örnek '
                 + 'alınmaz.',
    stepFit:       'Eğitim',
    stepFitText:   'Her beş dakikada bir XGBoost modeli, en fazla 200.000 '
                 + 'örneklik bir havuzdan sıfırdan eğitilir; her gece de önceki '
                 + 'günün uçuş arşivi taranarak havuza yeni örnekler eklenir. '
                 + 'Havuz dolunca her güne eşit pay ayrılır, böylece model tek '
                 + 'bir hava dönemine bağlı kalmaz.',
    stepQuality:   'Kalite kapısı',
    stepQualityText: 'Yeni model, yayındaki modeli aynı sabit sınav verisinde '
                 + 'geçmedikçe yayına alınmaz: farkın %95 güven aralığı '
                 + 'tümüyle sıfırın üstünde olmalıdır. Üst üste 12 retten sonra '
                 + 'kapı referansını yeniler; böylece tek bir şanslı skor modeli '
                 + 'yerinde donduramaz.',

    // results
    scoreTitle:    'İddia değil, ölçüm',
    scoreLead:     'Şansın açıkça üstünde ve tek bir sinyalden daha güçlü.',
    scoreIntro:    'Modeli en katı biçimde sınıyoruz: yalnızca aynı alanda ve '
                 + 'aynı saatte, havanın birebir aynı olduğu hücrelere karşı. '
                 + 'Böylece skor, günün iyi olup olmadığını değil, termalin '
                 + 'nerede olduğunu ölçer. Skor, modelin bir planörün tırmandığı '
                 + 'noktayı bir planörün alçaldığı noktanın üstüne koyma '
                 + 'olasılığıdır; 0,50 hiçbir ayırt etme gücü olmadığı anlamına '
                 + 'gelir.',
    colRanker:     'Yöntem',
    colRegion:     'Bölge',
    colScore:      'Skor',
    colCi:         '%95 güven aralığı',
    chance:        'şans',
    rowAlt:        'Yalnızca yerden yükseklik',
    rowPhysics:    'Fizik temelli kural (öğrenmesiz)',
    rowTerrain:    'Yalnızca arazi şekli',
    rowSun:        'Yalnızca yamaca düşen güneş açısı',
    rowModel:      'ThermalSense modeli',
    scoreCaption:  'Yöntemlerin aynı saat içindeki skoru ve %95 güven aralığı',
    scoreMeta:     '16 Kasım 2026’da, 6 Kasım itibarıyla eğitim verisi '
                 + 'üzerinde ölçüldü: 47.419 örnek, 6.259 tırmanış. Gruplanmış '
                 + 'beş katlı çapraz doğrulama, %95 bootstrap güven aralıkları.',
    scoreRead:     '27 Eylül’de, verinin dörtte biriyle (11.774 örnek) aynı '
                 + 'test 0,542 vermişti. Model her beş dakikada bir yeni '
                 + 'trafikle yeniden eğitiliyor ve her uçuş günü, öğrenebileceği '
                 + 'veriyi büyütüyor.',
    regionTitle:   'Bölgelere göre',
    regionCaption: 'ThermalSense modelinin bölgelere göre skoru ve %95 güven '
                 + 'aralığı',
    regAlps:       'Alpler',
    regIberia:     'İberya',
    regGermany:    'Almanya',
    regOther:      'Dünyanın geri kalanı',
    regionNote:    'Alpler, İberya ve dünyanın geri kalanında model, yalnızca '
                 + 'arazi şekline dayalı sıralamayı tümüyle sıfırın üstünde '
                 + 'kalan güven aralıklarıyla geçiyor. Almanya’da fark henüz '
                 + 'istatistiksel olarak net değil.',
    nextTitle:     'Yol haritasında sıradaki',
    nextText:      'Her hücre için zeminin şu an ne kadar sıcak olduğunu ölçen '
                 + 'bir girdi (uydudan yer yüzeyi sıcaklığı ya da gün '
                 + 'doğumundan beri biriken güneşlenme) ve her hücrenin uçuş '
                 + 'arşivinden çıkarılan kendi tırmanış geçmişi. İkincisi şu '
                 + 'anda test aşamasında.',
    useTitle:      'Gözlem ve tahmin bir arada',
    useText:       'Canlı trafik ve aktif termaller, planörlerin şu an '
                 + 'bildirdiğidir. Isı haritası ise modelin geri kalan her yer '
                 + 'için tahminidir; henüz kimsenin uçmadığı yerler de dahil.',
    turkiyeTitle:  'Türkiye',
    turkiyeText:   'ThermalSense önce Türk pilotları için yapıldı, ancak OGN '
                 + 'akışında Türkiye trafiği henüz seyrek: 47.419 eğitim '
                 + 'örneğinin 55’i. Bu sayı büyüyene kadar Türkiye’deki '
                 + 'tahminler, modelin Alpler’de ve başka bölgelerde '
                 + 'öğrendiklerine dayanıyor.',
    turkiyeLink:   'Eğitim durumunu aç',

    // data
    dataTitle:     'Açık veri üzerine kurulu',
    dataCaption:   'Veri kaynakları ve kullanım amaçları',
    colSource:     'Kaynak',
    colUse:        'Ne için',
    srcOgnUse:     'Canlı konumlar ve eğitim etiketleri',
    srcMeteoUse:   'Yerde ve yükseklerde sıcaklık, nem, rüzgâr, CAPE ve CIN, '
                 + 'güneş ışınımı, sınır tabaka yüksekliği, toprak sıcaklığı',
    srcDemUse:     'Yükseklik, eğim ve bakı',
    machineTitle:  'Hafif altyapı',
    machineText:   'Tüm sistem tek bir küçük bulut sunucusunda, tek bir servis '
                 + 'olarak çalışır. Önbellek sıcakken bir tahmin yaklaşık yarım '
                 + 'saniye sürer.',

    // closing
    closeTitle:    'Bugün nerede tırmanılıyor?',
    disclaimer:    'ThermalSense pilotun kararını destekler; pilotun '
                 + 'muhakemesinin ya da resmî meteoroloji brifinginin yerini '
                 + 'tutmaz.',
    creditTerrain: 'Arazi: Copernicus DEM © DLR e.V. 2010–2014, © Airbus '
                 + 'Defence and Space GmbH 2014–2018; COPERNICUS kapsamında AB '
                 + 've ESA tarafından sağlanmıştır.',
    creditWeather: 'Hava verisi: Open-Meteo.com.',
    creditTraffic: 'Uçak konumları: Open Glider Network.',
  },

  en: {
    docTitle:      'ThermalSense: thermal forecasting that learns from gliders',
    skipToContent: 'Skip to content',

    navSee:        'For pilots',
    navLearn:      'How it learns',
    navScore:      'Results',
    navData:       'Data',
    navPitch:      'Investor pitch',
    navLabel:      'Page sections',
    openMap:       'Open the live map',
    openMapShort:  'Open the map',

    heroUnseen:    'Thermals are invisible.',
    heroSeen:      'Gliders give them away.',
    heroLede:      'ThermalSense turns live glider traffic on the Open Glider '
                 + 'Network into training data. Click any point and it scores '
                 + 'the surrounding area of about 11 × 8 km in 50 m cells, '
                 + 'showing where a climb is most likely. The model retrains '
                 + 'itself every five minutes, learning from every soaring day '
                 + 'as it happens.',
    heroSecondary: 'See how it learns',

    simAlt:        'Animation: gliders cruising and circling over a dark map '
                 + 'grid; cells warm up where they circle',
    simCaption:    'An illustration, not live data. A glider that flies into a '
                 + 'thermal it cannot see starts to circle, and the cells '
                 + 'beneath it warm with the evidence it leaves. That is '
                 + 'exactly how the model learns.',
    simReveal:     'Show the hidden thermals',
    simHide:       'Hide the thermals',
    simPause:      'Pause',
    simPlay:       'Play',
    simCircling:   'circling',
    simCruising:   'cruising',
    simScale:      'thermal probability',
    low:           'low',
    high:          'high',

    liveNow:       ({ n }) => `ThermalSense is tracking ${n} active `
                            + `thermal${n === 1 ? '' : 's'} worldwide right now.`,
    liveNone:      'No active thermals worldwide right now. It may be night, '
                 + 'or a quiet hour, over the main soaring regions.',

    seeTitle:      'What a pilot sees',
    seeCellsUnit:  'cells',
    seeCells:      'Around any point you click, about 11 × 8 km of ground is '
                 + 'split into 40,401 cells of about 50 m. Each is scored from '
                 + '22 inputs: terrain, weather, time of day and season, and '
                 + 'height above the ground.',
    seePassesUnit: 'passes',
    seePasses:     'Every prediction runs 50 times over slightly varied '
                 + 'weather, so each cell carries an uncertainty estimate as '
                 + 'well as a probability.',
    seeAheadFig:   '+23 h',
    seeAheadUnit:  'forecast',
    seeAhead:      'The same area can be scored up to 23 hours ahead on '
                 + 'Open-Meteo forecast data: every 15 minutes for the first '
                 + 'three hours, then hourly. The quarter hours are '
                 + 'interpolated between the hourly values.',
    seeLiveFig:    '1 s',
    seeLiveUnit:   'live traffic',
    seeLive:       'Live traffic refreshes every second. Gliders circling in a '
                 + 'climb are highlighted, and where they are the map marks an '
                 + 'active thermal.',
    noteAltTitle:  'It answers for the pilot’s altitude',
    noteAlt:       'Click a glider and the prediction is made for that '
                 + 'glider’s height. A thermal that tops out at 900 m is no '
                 + 'use to a pilot at 1,500 m.',
    noteNightTitle:'No basis, no forecast',
    noteNight:     'When the sun is too low to heat the ground, the map stays '
                 + 'empty and says why. It never shows a forecast it has no '
                 + 'basis for.',
    noteRawTitle:  'It shows the raw data beside it',
    noteRaw:       'A divider splits the map: raw inputs on one side, the '
                 + 'finished prediction on the other. What the model works '
                 + 'from is visible at a glance.',
    noteRawLink:   'Open the comparison',

    learnTitle:    'A model that trains itself',
    learnIntro:    'A glider’s barogram is a sawtooth: it circles up in a '
                 + 'thermal, then glides on, losing height. ThermalSense uses '
                 + 'the two clear ends of that trace as labels: climbing in a '
                 + 'turn is lift, gliding straight through sinking air is its '
                 + 'absence. Everything ambiguous is left out.',
    baroAlt:       'Schematic barogram: altitude against time. Circling '
                 + 'climbs are marked positive, straight sinking glides '
                 + 'negative, and the ambiguous stretch between, everything '
                 + 'below 200 m and everything more than 300 m above the '
                 + 'thermal base are marked not used.',
    baroAxisAlt:   'altitude',
    baroAxisTime:  'time',
    baroCeil:      'thermal base + 300 m',
    baroFloor:     '200 m AGL',
    baroClimb:     '+2.4 m/s',
    baroSink:      '−0.9 m/s',
    baroWeak:      '+0.3 m/s',
    labelPos:      'Positive',
    labelPosRule:  'Circling and climbing faster than 1.5 m/s.',
    labelNeg:      'Negative',
    labelNegRule:  'Flying straight and sinking faster than 0.5 m/s.',
    labelDrop:     'Not used',
    labelDropRule: 'Everything between; also anything below 200 m AGL or more '
                 + 'than 300 m above the thermal base.',
    whyDropTitle:  'Why the ambiguous middle is left out',
    whyDrop:       'A glider flying straight through lift, or along a ridge, '
                 + 'is not circling, but it is not in sinking air either. '
                 + 'Counting it as “no lift” would teach the model that lift is '
                 + 'missing exactly where it is. On 24 minutes of live feed, '
                 + 'only 14,940 of the 55,856 rows (27%) a simpler rule would '
                 + 'have called “no lift” were truly in sinking air. Far cleaner '
                 + 'labels, from the same data.',
    stepsTitle:    'From a position report to the map',
    stepFeed:      'Feed',
    stepFeedText:  'The worldwide Open Glider Network APRS feed is read '
                 + 'continuously: about 7.7 million position reports a day.',
    stepGate:      'Screening',
    stepGateText:  'Every report passes a plausibility check first. Altitudes '
                 + 'above 15,000 m, speeds above 400 km/h and jumps no '
                 + 'aircraft could fly are filtered out; an id caught three '
                 + 'times is quarantined for 30 minutes.',
    stepLabel:     'Label',
    stepLabelText: 'What remains is labelled by the rule above. No sample is '
                 + 'taken when solar radiation is under 50 W/m², because '
                 + 'nothing is heating the ground.',
    stepFit:       'Training',
    stepFitText:   'Every five minutes an XGBoost model is retrained from '
                 + 'scratch on a pool of up to 200,000 examples, and every '
                 + 'night the previous day’s flight archive is mined for more. '
                 + 'Once the pool is full each day keeps an equal share, so the '
                 + 'model is never tied to one stretch of weather.',
    stepQuality:   'Quality gate',
    stepQualityText: 'A new model goes live only if it beats the current one '
                 + 'on the same frozen benchmark, with a 95% confidence '
                 + 'interval entirely above zero. After 12 rejections in a row '
                 + 'the gate resets its baseline, so one lucky score cannot '
                 + 'freeze the model in place.',

    scoreTitle:    'Measured, not claimed',
    scoreLead:     'Clearly above chance, and stronger than any single signal '
                 + 'alone.',
    scoreIntro:    'We test the model the strict way: only against other cells '
                 + 'in the same area and the same hour, where the weather is '
                 + 'identical. The score therefore reflects where the lift is, '
                 + 'not whether it was a good soaring day. It is the chance '
                 + 'that the model ranks a spot where a glider climbed above '
                 + 'one where a glider sank; 0.50 means no ability to tell '
                 + 'them apart.',
    colRanker:     'Method',
    colRegion:     'Region',
    colScore:      'Score',
    colCi:         '95% confidence interval',
    chance:        'chance',
    rowAlt:        'Height above ground alone',
    rowPhysics:    'Physics-based rule (no learning)',
    rowTerrain:    'Terrain shape alone',
    rowSun:        'Sun angle on the slope alone',
    rowModel:      'ThermalSense model',
    scoreCaption:  'Score within the same hour and 95% confidence interval for '
                 + 'each method',
    scoreMeta:     'Measured 16 November 2026 on the training data as of '
                 + '6 November: 47,419 examples, 6,259 climbs. Grouped '
                 + 'five-fold cross-validation, 95% bootstrap intervals.',
    scoreRead:     'On 27 September, with a quarter of the data (11,774 '
                 + 'examples), the same test gave 0.542. The model is retrained '
                 + 'on fresh traffic every five minutes, and every soaring day '
                 + 'adds to what it can learn from.',
    regionTitle:   'By region',
    regionCaption: 'ThermalSense model score by region, with 95% confidence '
                 + 'interval',
    regAlps:       'Alps',
    regIberia:     'Iberia',
    regGermany:    'Germany',
    regOther:      'Rest of the world',
    regionNote:    'In the Alps, Iberia and the rest of the world the model '
                 + 'beats a ranking by terrain shape alone, with confidence '
                 + 'intervals entirely above zero. In Germany the margin is not '
                 + 'yet statistically clear.',
    nextTitle:     'Next on the roadmap',
    nextText:      'A per-cell input that measures how hot the ground is right '
                 + 'now (satellite land surface temperature, or sunshine '
                 + 'accumulated since sunrise), and each cell’s own climb '
                 + 'history mined from the flight archive. The second is '
                 + 'already in testing.',
    useTitle:      'Observation and prediction, side by side',
    useText:       'Live traffic and active thermals are what gliders are '
                 + 'reporting right now. The heatmap is the model’s estimate '
                 + 'for everywhere else, including where nobody is flying yet.',
    turkiyeTitle:  'Türkiye',
    turkiyeText:   'ThermalSense is built for Turkish pilots first, but Turkish '
                 + 'traffic on the OGN feed is still sparse: 55 of the 47,419 '
                 + 'training examples. Until that grows, predictions in Türkiye '
                 + 'draw on what the model has learned in the Alps and '
                 + 'elsewhere.',
    turkiyeLink:   'Open training status',

    dataTitle:     'Built on open data',
    dataCaption:   'Data sources and what each is used for',
    colSource:     'Source',
    colUse:        'Used for',
    srcOgnUse:     'Live positions and training labels',
    srcMeteoUse:   'Temperature at the surface and aloft, humidity, wind, CAPE '
                 + 'and CIN, solar radiation, boundary-layer height, soil '
                 + 'temperature',
    srcDemUse:     'Elevation, slope and aspect',
    machineTitle:  'Lean to run',
    machineText:   'The whole system runs as one service on a single small '
                 + 'cloud server. On a warm cache a prediction takes about half '
                 + 'a second.',

    closeTitle:    'Where are they climbing today?',
    disclaimer:    'ThermalSense supports a pilot’s judgement. It does not '
                 + 'replace it, or an official weather briefing.',
    creditTerrain: 'Terrain: Copernicus DEM © DLR e.V. 2010–2014, © Airbus '
                 + 'Defence and Space GmbH 2014–2018, provided under '
                 + 'COPERNICUS by the EU and ESA.',
    creditWeather: 'Weather data: Open-Meteo.com.',
    creditTraffic: 'Aircraft positions: Open Glider Network.',
  },
};

// Key parity is the contract, and nothing else enforces it: a key missing from
// one language silently falls back to the other, which reads as a stray
// sentence in the wrong language rather than as an error.
if (import.meta.env?.DEV) {
  const tr = Object.keys(STRINGS.tr);
  const en = new Set(Object.keys(STRINGS.en));
  const missing = [
    ...tr.filter(k => !en.has(k)).map(k => `en.${k}`),
    ...[...en].filter(k => !(k in STRINGS.tr)).map(k => `tr.${k}`),
  ];
  if (missing.length) console.warn('[landing] strings missing:', missing.join(', '));
}

/** Same lookup rules as i18n/strings.js `translate`, over the landing copy. */
export function translateLanding(lang, key, vars) {
  const entry = STRINGS[lang]?.[key] ?? STRINGS[DEFAULT_LANG][key] ?? key;
  if (typeof entry === 'function') return entry(vars ?? {});
  if (!vars) return entry;
  return entry.replace(/\{(\w+)\}/g, (m, name) => (vars[name] ?? m));
}
