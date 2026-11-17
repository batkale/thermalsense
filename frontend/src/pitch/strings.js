// Copy for the pitch page (/sunum): a pre-seed presentation to defence and
// aerospace companies, written around LALE-class UAVs (Low Altitude Long
// Endurance, roughly below 10,000 ft). Same contract as landing/strings.js:
// Turkish is the default, every key exists in both languages.
//
// Two kinds of figure appear here and they must never be confused:
//
//   1. Third-party results, each with its source on the page:
//      - NASA Dryden flight tests, Cloud Swift (2005 press release): 17 flights
//        over eight weeks, +60 min endurance, 23 updrafts averaging 565 ft
//        (172 m), best 2,770 ft (844 m).
//      - M. J. Allen, "Autonomous Soaring for Improved Endurance of a Small
//        Uninhabited Air Vehicle", AIAA 2005 (NTRS 20050041655): simulation,
//        nominal 2 h endurance -> 8.6 h yearly average, 14 h summer maximum,
//        8 h winter maximum.
//      - Depenbusch, Bird & Langelaan, "The AutoSOAR autonomous soaring
//        aircraft, part 2", J. Field Robotics 35(4), 2018: 7.8 h with the
//        soaring system engaged at Aberdeen Proving Ground, ~3 h of it climbing
//        in thermals.
//   2. ThermalSense's own measurements: copied from the landing page, which
//      copies them from CLAUDE.md (16 Nov 2026 evaluation run). Re-measure
//      there and change them here too.
//
// No figure on this page says how much ThermalSense itself extends a UAV's
// endurance — that has not been measured, and the page says so. The
// endurance calculator is arithmetic on a stated assumption, labelled as such.

import { DEFAULT_LANG } from '../i18n/strings.js';

const STRINGS = {
  tr: {
    docTitle:     'ThermalSense · LALE sınıfı İHA’lar için termal haritası',
    skipToContent:'İçeriğe geç',
    navLabel:     'Sunum bölümleri',
    navProblem:   'Sorun',
    navEvidence:  'Kanıt',
    navProduct:   'Ürün',
    navLale:      'LALE',
    navGain:      'Verim',
    navAsk:       'Ortaklık',
    openMap:      'Canlı sistemi aç',
    openMapShort: 'Canlı sistem',
    technical:    'Teknik ayrıntılar',
    slideOf:      '{n} / {total}',
    pct:          '%{n}',

    // 01 cover
    eyebrow:      'Ön tohum yatırım sunumu · Savunma ve havacılık',
    coverA:       'Atmosfer bedava enerji taşır.',
    coverB:       'Biz yerini haritalıyoruz.',
    coverLede:    'ThermalSense, yerden yükselen sıcak hava kolonlarını, yani '
                + 'termalleri, 50 metre çözünürlükle tahmin eden ve kendi '
                + 'kendini eğiten bir modeldir. Alçak irtifada uzun süre görev '
                + 'yapan insansız hava araçlarına, motoru kapatıp irtifa '
                + 'kazanabilecekleri yerleri gösterir.',
    coverCta:     'Verim hesabına geç',
    coverStatus:  'Sistem bugün canlı: dünya genelindeki planör trafiğinden '
                + 'her beş dakikada bir yeniden öğreniyor.',

    // 02 problem
    probTitle:    'LALE sınıfı bir İHA’nın görev süresini bataryası belirler.',
    probText:     'Her ek görev saati ya daha ağır bir batarya ya da daha az '
                + 'faydalı yük demektir. Oysa bu araçların uçtuğu katman, '
                + 'güneşli günlerde yerden birkaç bin metreye kadar yükselen '
                + 'termallerle doludur. Termal görünmez; ne radar ne kamera '
                + 'onu gösterir. Yeri bilinmeyen enerji kullanılamaz.',
    laleDefTerm:  'LALE',
    laleDef:      'Low Altitude Long Endurance: yaklaşık 3.000 metrenin '
                + '(10.000 ft) altında, uzun süreli görev yapan İHA sınıfı. '
                + 'Termallerin oluştuğu katman tam olarak bu irtifa bandıdır.',
    probPoints:   'Sonuç',
    probP1:       'Görev süresi, batarya kütlesiyle sınırlı.',
    probP2:       'Bekleme ve gözetleme süresi, aracın en pahalı saati.',
    probP3:       'Ücretsiz bir enerji kaynağı, her gün aracın altında boşa gidiyor.',

    // 03 evidence
    evTitle:      'Termalle uçmak, havada kalış süresini katlar.',
    evIntro:      'Bu bizim iddiamız değil. Aşağıdaki sonuçlar yayımlanmış uçuş '
                + 'testlerinden ve simülasyonlardan alınmıştır; hiçbiri '
                + 'ThermalSense ile elde edilmemiştir.',
    ev1Fig:       '+60 dk',
    ev1Who:       'NASA Dryden · uçuş testi',
    ev1Text:      'Küçük bir İHA, 17 uçuşluk test kampanyasında termalleri '
                + 'otonom olarak kullanarak havada kalış süresine 60 dakika '
                + 'ekledi. 23 termalde ortalama 172 m, en güçlüsünde 844 m '
                + 'tırmandı.',
    ev2Fig:       '2 → 8,6 sa',
    ev2Who:       'NASA, Allen (2005) · simülasyon',
    ev2Text:      'Nominal dayanıklılığı 2 saat olan bir İHA, termallerden '
                + 'yararlanarak yıllık ortalamada 8,6 saat havada kaldı. Yaz '
                + 'aylarında üst sınır 14 saat, kışın 8 saat.',
    ev3Fig:       '7,8 sa',
    ev3Who:       'Penn State AutoSOAR · uçuş testi',
    ev3Text:      'ABD Ordusu’nun Aberdeen test sahasında otonom süzülme '
                + 'sistemi devredeyken 7,8 saat uçuş. Bunun yaklaşık 3 saati '
                + 'termalde tırmanarak geçti.',
    evSource:     'Kaynak',

    // 04 gap
    gapTitle:     'Eksik halka: nereye bakılacağı.',
    gapText:      'Bu sistemlerin hepsi termali uçarken, kör arayarak buluyor. '
                + 'NASA’nın aracı termal bulana kadar sabit bir spiral çizerek '
                + 'tarıyordu. Aramaya harcanan her dakika, motorla uçulan bir '
                + 'dakikadır. Kaybı azaltmanın yolu, araç kalkmadan önce '
                + 'termalin en olası olduğu yerleri bilmektir.',
    gapBlind:     'Kör arama',
    gapBlindText: 'Spiral tarama, termale rastlayana kadar motor çalışır.',
    gapMap:       'Önsel harita',
    gapMapText:   'Arama, olasılığı en yüksek hücrelerden başlar.',

    // 05 product
    prodTitle:    'Ürün bugün çalışıyor.',
    prodIntro:    'Daire çizerek tırmanan her planör bir “burada termal var” '
                + 'örneği, düz uçarken alçalan her planör bir “burada yok” '
                + 'örneğidir. ThermalSense bu etiketleri Open Glider '
                + 'Network’ün dünya çapındaki canlı akışından kendisi toplar. '
                + 'İnsan etiketlemesi gerekmez; veri, filo uçurmadan büyür.',
    factCells:    'hücre',
    factCellsText:'Seçilen noktanın çevresindeki yaklaşık 11 km’lik alan, 50 '
                + 'metrelik hücrelerle puanlanır.',
    factFeed:     'konum raporu / gün',
    factFeedText: 'Dünya genelindeki planör trafiği, eğitim verisi olarak '
                + 'sürekli akar.',
    factRetrain:  'yeniden eğitim',
    factRetrainText:'Yeni model, mevcut modeli dondurulmuş bir test kümesinde '
                + 'istatistiksel olarak geçmedikçe devreye girmez.',
    factAhead:    'tahmin ufku',
    factAheadText:'Görev planlaması için harita, 24 saate kadar ileriye '
                + 'üretilebilir.',
    feedFig:      '7,7 M',
    aheadFig:     '24 sa',
    retrainFig:   '5 dk',
    scoreTitle:   'Ölçülen sonuç',
    scoreText:    'Ölçüt: aynı gün, aynı bölge ve aynı saat içinde, tırmanış '
                + 'olan noktayı olmayandan doğru sıralama olasılığı. 0,50 '
                + 'yazı-turadır.',
    rowModel:     'ThermalSense modeli',
    rowSun:       'Yalnızca güneş açısı',
    rowTerrain:   'Yalnızca arazi',
    rowAlps:      'ThermalSense · Alpler',
    rowIberia:    'ThermalSense · İberya',
    scoreHonest:  'Mütevazı ama gerçek bir sinyal: model, tek başına her '
                + 'ölçütten daha iyi sıralıyor ve en yoğun veri olan bölgelerde '
                + '0,61’e çıkıyor. Türkiye’de henüz ölçüm yapmaya yetecek veri '
                + 'yok; yatırımın ilk hedeflerinden biri bu.',
    scoreMeta:    'Mikro AUC, 16 Kasım 2026. 47.419 örnek, %95 bootstrap güven '
                + 'aralığı: model 0,539–0,601; arazi 0,493–0,557.',

    // 06 LALE
    laleTitle:    'LALE sınıfı bir İHA’da nasıl kullanılır.',
    laleIntro:    'ThermalSense uçuş kontrolünün yerini almaz; ona nereye '
                + 'bakacağını söyler. Termale girme kararı her zaman aracın '
                + 'kendi variometresinindir.',
    mode1:        'Görev planlama',
    mode1Text:    'Kalkıştan önce görev alanının termal haritası, görev '
                + 'saatine göre ileriye dönük alınır. Bekleme noktaları ve '
                + 'rota, görev geometrisini bozmadan olasılığı yüksek '
                + 'hücrelere kaydırılır.',
    mode2:        'Uçuş sırasında, yer istasyonu',
    mode2Text:    'Yer kontrol istasyonu haritayı uçuş boyunca yeniler ve '
                + 'otopilota aday tırmanış noktaları gönderir. Araç bir '
                + 'termali doğrularsa motoru kapatıp daire çizer; '
                + 'doğrulayamazsa görevine döner.',
    mode3:        'Araç üstü',
    mode3Text:    'Model dosyası yaklaşık 50 KB’tır. Hedef, haritanın veri '
                + 'bağlantısı kesildiğinde de görev bilgisayarında '
                + 'üretilebilmesidir. Bu aşama yol haritasında; bugün '
                + 'çalışan değil.',
    usesTitle:    'Termalden en çok kazanan görevler',
    use1:         'Sınır ve kıyı gözetlemesi',
    use1Text:     'Uzun bekleme süresi, termalin en çok işe yaradığı görev '
                + 'profilidir.',
    use2:         'Orman yangını erken uyarı',
    use2Text:     'Yangın riskinin en yüksek olduğu sıcak ve kuru günler, '
                + 'termallerin de en güçlü olduğu günlerdir.',
    use3:         'Haberleşme rölesi',
    use3Text:     'Belirli bir alan üzerinde saatlerce kalması gereken araç, '
                + 'konumunu termallerle koruyabilir.',
    use4:         'Hat ve boru hattı denetimi',
    use4Text:     'Uzun doğrusal rotalarda, termal olasılığı yüksek noktalar '
                + 'irtifa kazanma durakları olarak planlanabilir.',
    acoustic:     'Ek kazanç: motor kapalı süzülen bir aracın akustik izi daha '
                + 'düşüktür.',

    // 07 gain
    gainTitle:    'Ne kadar verim?',
    gainIntro:    'Hesap basit: motor kapalı geçen süre bataryadan düşmez. '
                + 'Uçuşun f kadarı motorsuz geçerse, aynı batarya ile havada '
                + 'kalış yaklaşık 1 / (1 − f) katına çıkar.',
    gainBase:     'Nominal havada kalış',
    gainShare:    'Motorsuz geçen pay (f)',
    gainResult:   'Termallerle',
    gainTimes:    '{x} kat',
    gainHours:    '{h} sa',
    gainExtra:    '+{h} sa görev süresi',
    gainRefAuto:  'AutoSOAR: termalde tırmanış payı',
    gainRefNasa:  'NASA simülasyonu: yıllık ortalama',
    gainChartAlt: 'Motorsuz geçen paya göre havada kalış çarpanı eğrisi',
    gainAxisX:    'motorsuz pay',
    gainAxisY:    'çarpan',
    gainAssume:   'Varsayım: aviyonik ve faydalı yükün tüketimi ihmal edilmiştir; '
                + 'bunlar dahil edildiğinde çarpan daha düşük çıkar.',
    gainHonestTitle: 'Bu bir hesap, ölçüm değil.',
    gainHonest:   'ThermalSense’in f’yi ne kadar artırdığını henüz ölçmedik. '
                + 'Hipotezimiz şu: önsel harita aramaya giden süreyi kısaltır '
                + 've kısalan her dakika f’ye eklenir. Bunu bir LALE '
                + 'platformunda ölçmek, pilot projenin ilk işidir.',

    // 08 why
    whyTitle:     'Neden biz.',
    why1:         'Kendiliğinden büyüyen veri',
    why1Text:     'Dünyada her gün milyonlarca planör konumu yayınlanıyor. Aynı '
                + 'veri kümesini kurmak isteyen bir rakibin, bir filo uçurması '
                + 'gerekir.',
    why2:         'Kendini denetleyen tahmin',
    why2Text:     'Her tahmin, zamanı gelmeden değiştirilemez bir deftere '
                + 'kaydedilir ve sonra gerçek uçuşlarla puanlanır. Kayıtların '
                + 'sonradan düzeltilmesi veritabanı düzeyinde engellenir. '
                + 'Savunma alımlarının istediği izlenebilirlik buradan gelir.',
    why3:         'Ölçtüğünü söyleyen ekip',
    why3Text:     'Her sonuç güven aralığıyla raporlanır; kazanç kanıtlayamayan '
                + 'dört deneme de kayıtlıdır. Bu sunumdaki her rakamın kaynağı '
                + 'gösterilebilir.',
    why4:         'Hafif çalışır',
    why4Text:     'Bütün sistem bugün 2 çekirdekli tek bir sunucuda çalışıyor; '
                + 'bir harita yaklaşık yarım saniyede üretiliyor.',

    // 09 roadmap & ask
    roadTitle:    'Yol haritası',
    road1When:    '0–6 ay',
    road1:        'Türkiye verisini ölçülebilir hale getirmek (İnönü, Eskişehir). '
                + 'Uydu kaynaklı yüzey sıcaklığını modele eklemek.',
    road2When:    '6–12 ay',
    road2:        'Bir platform ortağıyla donanım döngüde simülasyon ve uçuş '
                + 'testi. Haritalı ve haritasız uçuşlarda f’nin ölçülmesi.',
    road3When:    '12–18 ay',
    road3:        'Yer kontrol istasyonu entegrasyonu ve araç üstü çalışan '
                + 'model.',
    askTitle:     'Aradığımız',
    askText:      'Ön tohum yatırımı ve bir platform ortağı: test uçuşları için '
                + 'LALE sınıfı bir İHA ve o uçuşların verisi. Karşılığında, '
                + 'kendi platformunuzda ölçülmüş bir havada kalış kazancı.',
    askCta:       'Canlı sistemi inceleyin',

    disclaimer:   'Üçüncü taraf sonuçları belirtilen yayınlardan alınmıştır ve '
                + 'ThermalSense ile elde edilmemiştir. ThermalSense bir uçuş '
                + 'emniyeti aracı değildir.',
  },

  en: {
    docTitle:     'ThermalSense · Thermal maps for LALE-class UAVs',
    skipToContent:'Skip to content',
    navLabel:     'Presentation sections',
    navProblem:   'Problem',
    navEvidence:  'Evidence',
    navProduct:   'Product',
    navLale:      'LALE',
    navGain:      'Gain',
    navAsk:       'Partnership',
    openMap:      'Open the live system',
    openMapShort: 'Live system',
    technical:    'Technical details',
    slideOf:      '{n} / {total}',
    pct:          '{n}%',

    eyebrow:      'Pre-seed presentation · Defence and aerospace',
    coverA:       'The atmosphere carries free energy.',
    coverB:       'We map where it is.',
    coverLede:    'ThermalSense is a self-training model that forecasts '
                + 'thermals, the columns of warm air rising from the ground, '
                + 'at 50-metre resolution. It shows low-altitude, '
                + 'long-endurance unmanned aircraft where they can shut the '
                + 'motor off and still gain height.',
    coverCta:     'Go to the endurance estimate',
    coverStatus:  'The system is live today: it relearns every five minutes '
                + 'from glider traffic worldwide.',

    probTitle:    'A LALE-class UAV’s mission time is set by its battery.',
    probText:     'Every extra hour on station means either a heavier battery '
                + 'or less payload. Yet the layer these aircraft fly in is, on '
                + 'sunny days, full of thermals reaching several thousand '
                + 'metres above the ground. A thermal is invisible; neither '
                + 'radar nor camera shows it. Energy you cannot locate is '
                + 'energy you cannot use.',
    laleDefTerm:  'LALE',
    laleDef:      'Low Altitude Long Endurance: the UAV class that flies long '
                + 'missions below roughly 3,000 metres (10,000 ft). That is '
                + 'exactly the band in which thermals form.',
    probPoints:   'The result',
    probP1:       'Mission time is capped by battery mass.',
    probP2:       'Loiter and surveillance time is the aircraft’s most expensive hour.',
    probP3:       'A free energy source goes unused beneath the aircraft every day.',

    evTitle:      'Flying thermals multiplies endurance.',
    evIntro:      'This is not our claim. The results below come from '
                + 'published flight tests and simulations; none was obtained '
                + 'with ThermalSense.',
    ev1Fig:       '+60 min',
    ev1Who:       'NASA Dryden · flight test',
    ev1Text:      'Over a 17-flight campaign, a small UAV added 60 minutes to '
                + 'its endurance by using thermals autonomously. It climbed an '
                + 'average of 172 m in 23 thermals, and 844 m in the strongest.',
    ev2Fig:       '2 → 8.6 h',
    ev2Who:       'NASA, Allen (2005) · simulation',
    ev2Text:      'A UAV with a nominal 2-hour endurance stayed aloft for 8.6 '
                + 'hours on the yearly average by using thermals. The ceiling '
                + 'was 14 hours in summer and 8 in winter.',
    ev3Fig:       '7.8 h',
    ev3Who:       'Penn State AutoSOAR · flight test',
    ev3Text:      '7.8 hours of flight with the autonomous soaring system '
                + 'engaged at the US Army’s Aberdeen Proving Ground, about 3 '
                + 'of them spent climbing in thermals.',
    evSource:     'Source',

    gapTitle:     'The missing link: where to look.',
    gapText:      'Every one of these systems finds thermals in flight, by '
                + 'searching blind. NASA’s aircraft flew a fixed spiral until '
                + 'it hit one. Every minute spent searching is a minute flown '
                + 'on the motor. The way to cut that loss is to know where '
                + 'thermals are most likely before the aircraft takes off.',
    gapBlind:     'Blind search',
    gapBlindText: 'A spiral sweep; the motor runs until a thermal turns up.',
    gapMap:       'Prior map',
    gapMapText:   'The search starts in the most likely cells.',

    prodTitle:    'The product works today.',
    prodIntro:    'Every glider climbing in circles is a “thermal here” '
                + 'example; every glider flying straight and sinking is a '
                + '“none here” example. ThermalSense collects these labels '
                + 'itself from the Open Glider Network’s live worldwide feed. '
                + 'No human labelling is needed; the data grows without '
                + 'flying a fleet.',
    factCells:    'cells',
    factCellsText:'An area about 11 km across around the chosen point, scored '
                + 'in 50-metre cells.',
    factFeed:     'position reports / day',
    factFeedText: 'Glider traffic worldwide flows in continuously as training '
                + 'data.',
    factRetrain:  'retraining',
    factRetrainText:'A new model goes live only if it beats the current one, '
                + 'with statistical confidence, on a frozen test set.',
    factAhead:    'forecast horizon',
    factAheadText:'For mission planning, the map can be produced up to 24 '
                + 'hours ahead.',
    feedFig:      '7.7 M',
    aheadFig:     '24 h',
    retrainFig:   '5 min',
    scoreTitle:   'Measured result',
    scoreText:    'The measure: within the same day, region and hour, the '
                + 'probability of ranking a point with a climb above one '
                + 'without. 0.50 is a coin toss.',
    rowModel:     'ThermalSense model',
    rowSun:       'Sun angle alone',
    rowTerrain:   'Terrain alone',
    rowAlps:      'ThermalSense · Alps',
    rowIberia:    'ThermalSense · Iberia',
    scoreHonest:  'A modest but real signal: the model ranks better than any '
                + 'single input on its own, and reaches 0.61 in the regions '
                + 'with the densest data. Türkiye does not yet have enough '
                + 'data to measure; that is one of the first things this '
                + 'investment is for.',
    scoreMeta:    'Micro AUC, 16 November 2026. 47,419 samples, 95% bootstrap '
                + 'intervals: model 0.539–0.601; terrain 0.493–0.557.',

    laleTitle:    'How it works on a LALE-class UAV.',
    laleIntro:    'ThermalSense does not replace flight control; it tells it '
                + 'where to look. The decision to enter a thermal always '
                + 'belongs to the aircraft’s own variometer.',
    mode1:        'Mission planning',
    mode1Text:    'Before take-off, a thermal map of the mission area is '
                + 'produced for the mission time. Loiter points and the route '
                + 'shift towards high-probability cells without breaking the '
                + 'mission geometry.',
    mode2:        'In flight, from the ground station',
    mode2Text:    'The ground control station refreshes the map during the '
                + 'flight and sends candidate climb points to the autopilot. '
                + 'If the aircraft confirms a thermal it shuts the motor off '
                + 'and circles; if not, it returns to its task.',
    mode3:        'On board',
    mode3Text:    'The model file is about 50 KB. The goal is for the map to '
                + 'be produced on the mission computer even when the data link '
                + 'is lost. This stage is on the roadmap; it is not running '
                + 'today.',
    usesTitle:    'Missions that gain the most from thermals',
    use1:         'Border and coastal surveillance',
    use1Text:     'Long loiter time is the profile where thermals pay off most.',
    use2:         'Early wildfire warning',
    use2Text:     'The hot, dry days of highest fire risk are also the days of '
                + 'the strongest thermals.',
    use3:         'Communications relay',
    use3Text:     'An aircraft that must hold over one area for hours can keep '
                + 'its station on thermals.',
    use4:         'Line and pipeline inspection',
    use4Text:     'On long linear routes, high-probability cells can be planned '
                + 'as stops to regain height.',
    acoustic:     'A further gain: an aircraft gliding with the motor off has '
                + 'a lower acoustic signature.',

    gainTitle:    'How much gain?',
    gainIntro:    'The arithmetic is simple: time spent with the motor off '
                + 'does not draw on the battery. If a fraction f of the flight '
                + 'is unpowered, the same battery keeps the aircraft up about '
                + '1 / (1 − f) times as long.',
    gainBase:     'Nominal endurance',
    gainShare:    'Unpowered share (f)',
    gainResult:   'With thermals',
    gainTimes:    '{x}×',
    gainHours:    '{h} h',
    gainExtra:    '+{h} h on station',
    gainRefAuto:  'AutoSOAR: share spent climbing in thermals',
    gainRefNasa:  'NASA simulation: yearly average',
    gainChartAlt: 'Curve of the endurance multiplier against the unpowered share',
    gainAxisX:    'unpowered share',
    gainAxisY:    'multiplier',
    gainAssume:   'Assumption: avionics and payload draw are ignored; including '
                + 'them lowers the multiplier.',
    gainHonestTitle: 'This is arithmetic, not a measurement.',
    gainHonest:   'We have not yet measured how much ThermalSense raises f. '
                + 'Our hypothesis: a prior map shortens the time spent '
                + 'searching, and every minute saved adds to f. Measuring that '
                + 'on a LALE platform is the first task of the pilot project.',

    whyTitle:     'Why us.',
    why1:         'Data that grows by itself',
    why1Text:     'Millions of glider positions are broadcast worldwide every '
                + 'day. A competitor wanting the same dataset would have to '
                + 'fly a fleet.',
    why2:         'Forecasts that audit themselves',
    why2Text:     'Every forecast is written to a tamper-proof ledger before '
                + 'its time and scored afterwards against real flights. Later '
                + 'edits are blocked at the database level. That is the '
                + 'traceability defence procurement asks for.',
    why3:         'A team that reports what it measured',
    why3Text:     'Every result is reported with its confidence interval, and '
                + 'the four experiments that could not prove a gain are on '
                + 'record too. '
                + 'Every figure in this presentation can be traced.',
    why4:         'Runs light',
    why4Text:     'The whole system runs today on a single two-core server; a '
                + 'map takes about half a second.',

    roadTitle:    'Roadmap',
    road1When:    '0–6 months',
    road1:        'Make Türkiye measurable (İnönü, Eskişehir). Add '
                + 'satellite land surface temperature to the model.',
    road2When:    '6–12 months',
    road2:        'Hardware-in-the-loop simulation and flight tests with a '
                + 'platform partner. Measure f with and without the map.',
    road3When:    '12–18 months',
    road3:        'Ground control station integration and an on-board model.',
    askTitle:     'What we are looking for',
    askText:      'Pre-seed investment and a platform partner: a LALE-class '
                + 'UAV for test flights, and the data from those flights. In '
                + 'return, an endurance gain measured on your own platform.',
    askCta:       'Explore the live system',

    disclaimer:   'Third-party results are taken from the publications cited '
                + 'and were not obtained with ThermalSense. ThermalSense is '
                + 'not a flight safety tool.',
  },
};

if (import.meta.env?.DEV) {
  const tr = Object.keys(STRINGS.tr);
  const en = new Set(Object.keys(STRINGS.en));
  const missing = [
    ...tr.filter(k => !en.has(k)).map(k => `en.${k}`),
    ...[...en].filter(k => !(k in STRINGS.tr)).map(k => `tr.${k}`),
  ];
  if (missing.length) console.warn('[pitch] strings missing:', missing.join(', '));
}

export function translatePitch(lang, key, vars) {
  const entry = STRINGS[lang]?.[key] ?? STRINGS[DEFAULT_LANG][key] ?? key;
  if (typeof entry === 'function') return entry(vars ?? {});
  if (!vars) return entry;
  return entry.replace(/\{(\w+)\}/g, (m, name) => (vars[name] ?? m));
}
