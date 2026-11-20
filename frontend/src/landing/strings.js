// Copy for the landing page (/about). The page is a pre-seed presentation
// about low-altitude fixed-wing UAVs, and still scrolls as one page. Same
// contract as i18n/strings.js: Turkish is the default, every key exists in
// both languages. A separate dictionary so the map app's bundle does not carry
// a page of prose.
//
// Wording rules from the TUSAŞ HANGAR BİGG mentor review (Nov 2026) — keep
// them when editing:
//   - Never "İHA"/"UAV" on its own: always "alçak irtifa sabit kanatlı İHA" /
//     "low-altitude fixed-wing UAV" (a bare İHA reads as a quadcopter, which
//     cannot soar). No "long/high endurance" class names (LALE is gone).
//   - The data is "termal verisi / telemetrisi" (thermal data), not "planör
//     verisi". Say once where it comes from (OGN), then call it what it is.
//   - Civil / dual-use framing. Do not write that defence is the main target.
//   - The claim is flight duration (havada kalış / uçuş süresi), never range.
//   - The aircraft flies its planned mission; a thermal is used inside the
//     mission area or planned corridor, never as a reason to divert.
//   - The aircraft has no internet in flight and carries no extra hardware.
//     Maps are computed before flight and handed to the ground control station.
//
// Three kinds of figure appear here and they must never be confused:
//
//   1. Third-party results, each with its source on the page (REFS in
//      Landing.jsx is the bibliography):
//      - NASA Dryden flight tests (2005 press release): 17 flights, +60 min
//        endurance, 23 updrafts averaging 172 m, best 844 m.
//      - Allen (2005), NTRS 20050041655: simulation, 2 h -> 8.6 h yearly
//        average, 14 h summer, 8 h winter maximum.
//      - Depenbusch, Bird & Langelaan (2018), J. Field Robotics 35(4): 7.8 h
//        with soaring engaged, ~3 h climbing in thermals.
//      - Market: Mordor Intelligence UAV market (7 Apr 2026 release: USD 17.23
//        B in 2025, 74.78% fixed-wing) and small UAV market (28 Jul 2026: USD
//        1.73 B in 2026, 53.65% fixed-wing of 2025 revenue). Prices: SkySight
//        USD 89/yr (Cumulus Soaring store), TopMeteo EUR 67.90–139.90/yr
//        (topmeteo.eu). Checked 17 Nov 2026.
//      - The chronology (section 08, `tl*` keys): every figure in it is the
//        cited publication's own, read from its abstract or page on 20 Nov
//        2026 — e.g. Hernández-Aguayo et al. (arXiv:2608.00241) 123.6 m/°C
//        for warm-season midday low mountains, not for all of France; SOARNET's
//        0.98 F1 is on its training/validation sets. Sources are TIMELINE in
//        Landing.jsx. The `fr*` frontier lines summarise those rows and claim
//        only what was not found in that search, never that nobody does it.
//   2. ThermalSense's own measurements: the offline score is copied from
//      CLAUDE.md (16 Nov 2026, `--variant base --by-hour`; Landing.jsx SCORES);
//      the horizon table is /verification/summary?days=30 on the live node,
//      read 19 Nov 2026 (Landing.jsx HORIZONS); the roadmap's full-resolution
//      terrain gain (m2Text) is the 17 Nov paired delta on the 6 Nov buffer,
//      +0.023 to +0.027 over three fold seeds (CLAUDE.md). Re-measure there,
//      change here.
//   3. Assumptions, labelled as such on the page: the 1 / (1 − f) calculator
//      and the SOM line.
//
// No figure on this page says how much ThermalSense itself extends flight
// duration — that has not been measured, and the page says so.

import { DEFAULT_LANG } from '../i18n/strings.js';

const STRINGS = {
  tr: {
    docTitle:     'ThermalSense · Alçak irtifa sabit kanatlı İHA’lar için termal haritası',
    skipToContent:'İçeriğe geç',
    navLabel:     'Sunum bölümleri',
    navProblem:   'Sorun',
    navProduct:   'Ürün',
    navArch:      'Mimari',
    navHorizon:   'Tahmin ufku',
    navField:     'Kronoloji',
    navCompete:   'Rakipler',
    navMarket:    'Pazar',
    navGain:      'Verim',
    navRoad:      'Yol haritası',
    openMap:      'Canlı sistemi aç',
    openMapShort: 'Canlı sistem',
    openCompare:  'Ham veri ↔ AI karşılaştırmasını aç',
    technical:    'Eğitim panelini aç',
    slideOf:      '{n} / {total}',
    pct:          '%{n}',

    // 01 cover
    eyebrow:      'Ön tohum yatırım sunumu · Otonom havacılık · Çift kullanımlı teknoloji',
    coverA:       'Atmosfer bedava enerji taşır.',
    coverB:       'Biz yerini haritalıyoruz.',
    coverLede:    'ThermalSense, yerden yükselen sıcak hava kolonlarını, yani '
                + 'termalleri, 50 metre çözünürlükle tahmin eden ve kendi '
                + 'kendini eğiten bir yapay zekâ modelidir. Alçak irtifa sabit '
                + 'kanatlı İHA’lara, planlı görev bölgeleri içinde motoru kısıp '
                + 'irtifa kazanabilecekleri yerleri uçuştan önce gösterir.',
    coverCta:     'Uçuş süresi hesabına geç',
    coverStatus:  'Sistem bugün canlı: dünya genelinden gelen termal '
                + 'telemetrisiyle her beş dakikada bir yeniden öğreniyor.',

    // hero simulation (ThermalField)
    simAlt:        'Canlandırma: karanlık bir harita ızgarası üzerinde düz '
                 + 'uçan ve daire çizen hava araçları; daire çizilen yerlerde '
                 + 'hücreler ısınıyor',
    simCaption:    'Canlandırma, canlı veri değil. Göremediği bir termale giren '
                 + 'süzülen bir hava aracı daire çizmeye başlar; altındaki '
                 + 'hücreler de onun bıraktığı termal verisiyle ısınır. Modelin '
                 + 'öğrenme biçimi tam olarak budur.',
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

    // 02 problem
    probTitle:    'Alçak irtifa sabit kanatlı bir İHA’nın havada kalış süresini '
                + 'bataryası belirler.',
    probText:     'Her ek uçuş saati ya daha ağır bir batarya ya da daha az '
                + 'faydalı yük demektir. Oysa bu araçların uçtuğu katman, '
                + 'güneşli günlerde yerden birkaç bin metreye kadar yükselen '
                + 'termallerle doludur. Termal görünmez; ne radar ne kamera '
                + 'onu gösterir. Yeri bilinmeyen enerji kullanılamaz.',
    classDefTerm: 'Alçak irtifa sabit kanatlı İHA',
    classDef:     'Yaklaşık 3.000 metrenin (10.000 ft) altında uçan, kanadı '
                + 'sabit insansız hava aracı. Döner kanatlılar (quadcopter) bu '
                + 'kapsamda değildir: termalden yalnızca kanadıyla süzülebilen '
                + 'bir araç yararlanabilir. Termallerin oluştuğu katman da tam '
                + 'olarak bu irtifa bandıdır.',
    probPoints:   'Sonuç',
    probP1:       'Uçuş süresi, batarya kütlesiyle sınırlı.',
    probP2:       'Görev bölgesi üzerinde bekleme süresi, aracın en değerli saati.',
    probP3:       'Ücretsiz bir enerji kaynağı, her gün aracın altında boşa gidiyor.',

    // 03 evidence
    evTitle:      'Termalle uçmak, havada kalış süresini uzatır.',
    evIntro:      'Bu bizim iddiamız değil. Aşağıdaki sonuçlar yayımlanmış uçuş '
                + 'testlerinden ve simülasyonlardan alınmıştır; hiçbiri '
                + 'ThermalSense ile elde edilmemiştir. Tam kaynakça sunumun '
                + 'sonundadır.',
    ev1Fig:       '+60 dk',
    ev1Who:       'NASA Dryden · uçuş testi',
    ev1Text:      'Küçük sabit kanatlı bir İHA, 17 uçuşluk test kampanyasında '
                + 'termalleri otonom olarak kullanarak havada kalış süresine 60 '
                + 'dakika ekledi. 23 termalde ortalama 172 m, en güçlüsünde '
                + '844 m tırmandı.',
    ev2Fig:       '2 → 8,6 sa',
    ev2Who:       'NASA, Allen (2005) · simülasyon',
    ev2Text:      'Nominal havada kalış süresi 2 saat olan sabit kanatlı bir '
                + 'İHA, termallerden yararlanarak yıllık ortalamada 8,6 saat '
                + 'havada kaldı. Yaz aylarında üst sınır 14 saat, kışın 8 saat.',
    ev3Fig:       '7,8 sa',
    ev3Who:       'Penn State AutoSOAR · uçuş testi',
    ev3Text:      'Otonom süzülme sistemi devredeyken 7,8 saat uçuş. Bunun '
                + 'yaklaşık 3 saati termalde tırmanarak geçti.',
    evSource:     'Kaynak',

    // 04 gap
    gapTitle:     'Eksik halka: nereye bakılacağı.',
    gapText:      'Bu sistemlerin hepsi termali uçarken, kör arayarak buluyor. '
                + 'NASA’nın aracı termal bulana kadar sabit bir spiral çizerek '
                + 'tarıyordu. Aramaya harcanan her dakika, motorla uçulan bir '
                + 'dakikadır. Kaybı azaltmanın yolu, araç kalkmadan önce görev '
                + 'bölgesinde termalin en olası olduğu yerleri bilmektir.',
    gapBlind:     'Kör arama',
    gapBlindText: 'Spiral tarama, termale rastlayana kadar motor çalışır.',
    gapMap:       'Önsel harita',
    gapMapText:   'Arama, görev bölgesindeki olasılığı en yüksek hücrelerden başlar.',

    // 05 product
    prodTitle:    'Ürün bugün çalışıyor.',
    prodIntro:    'Eğitim verisi termal verisidir: daire çizerek tırmanan her '
                + 'süzülen hava aracı bir “burada termal var”, düz uçarken '
                + 'alçalan her biri bir “burada yok” örneğidir. ThermalSense bu '
                + 'etiketleri Open Glider Network’ün sivil telemetri akışından '
                + 'kendisi toplar. İnsan etiketlemesi gerekmez; veri, filo '
                + 'uçurmadan büyür.',
    factCells:    'hücre',
    factCellsText:'Seçilen noktanın çevresindeki yaklaşık 11 km’lik alan, 50 '
                + 'metrelik hücrelerle puanlanır.',
    factFeed:     'termal telemetrisi / gün',
    factFeedText: 'Dünya genelinden konum ve tırmanma raporları, eğitim verisi '
                + 'olarak sürekli akar.',
    factRetrain:  'yeniden eğitim',
    factRetrainText:'Yeni model, mevcut modeli dondurulmuş bir test kümesinde '
                + 'istatistiksel olarak geçmedikçe devreye girmez.',
    factAhead:    'doğrulanmış ufuk',
    factAheadText:'Harita 24 saate kadar ileriye üretilebilir; gerçek uçuşlarla '
                + 'doğrulanmış ufuk bugün 3 saattir. Ayrıntısı “Tahmin ufku” '
                + 'bölümünde.',
    feedFig:      '7,7 M',
    aheadFig:     '3 sa',
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
    demoTitle:    'Canlı demo: ham veri ve ThermalSense AI yan yana',
    demoText:     'Canlı sistemde “Ham veri ↔ ThermalSense AI” anahtarı haritayı '
                + 'ikiye böler. Solda sistem kapalıdır: standart hava servisinin '
                + 'tüm alan için verdiği tek, düşük çözünürlüklü değer ve '
                + 'seyrek yükseklik ölçümleri. Sağda yapay zekâ açıktır: aynı '
                + 'girdilerden üretilen 50 metrelik termal olasılık haritası. '
                + 'Harita açılışta Türkiye’ye, İnönü test bölgesine odaklanır.',

    // 06 architecture
    archTitle:    'Yapay zekâ bu işin neresinde?',
    archIntro:    'Ham hava verisi herkese açıktır ve kilometre ölçeğindedir: bir '
                + 'hava servisi 11 km’lik bir alan için tek bir sıcaklık, tek bir '
                + 'güneş ışınımı değeri verir. Yapay zekâ katmanı, o tek değeri '
                + 'araziyle birleştirip 40.401 hücrelik bir termal olasılık '
                + 'haritasına çeviren adımdır.',
    archOnline:   'Yerde, uçuştan önce · internet gerekir',
    archOffline:  'Uçuş sırasında · internet gerekmez',
    arch1Tag:     'Girdi · ham veri',
    arch1:        'Hava ve arazi',
    arch1Text:    'Sayısal hava tahmini (saatlik, ~0,1° ızgara), 30 m çözünürlüklü '
                + 'yükseklik modeli (Copernicus GLO-30), güneş konumu. Burada '
                + 'yapay zekâ yok; herkesin erişebildiği veri.',
    arch2Tag:     'Yapay zekâ katmanı',
    arch2:        'ThermalSense modeli',
    arch2Text:    'Her 50 m hücre için 22 özellik (eğim, bakı, güneş açısı, '
                + 'irtifa, kararsızlık…) hesaplanır; gradyan artırmalı karar '
                + 'ağaçları (XGBoost) bunları termal olasılığına ve belirsizliğe '
                + 'çevirir. Model, termal verisinden her beş dakikada yeniden '
                + 'eğitilir.',
    arch3Tag:     'Çıktı · uçuş öncesi',
    arch3:        'Görev paketi',
    arch3Text:    'Görev zaman penceresi için termal haritası ve olasılığı yüksek '
                + 'bekleme noktaları, uçuştan önce hesaplanıp Yer Kontrol '
                + 'İstasyonu’na (YKİ) ve uçuş planına aktarılır.',
    arch4Tag:     'Uçuş · çevrimdışı',
    arch4:        'Alçak irtifa sabit kanatlı İHA',
    arch4Text:    'Araç internete bağlanmaz; üzerine ek donanım, sensör ya da '
                + 'meteoroloji istasyonu takılmaz. Planlı görevini uçar; termale '
                + 'girme kararını kendi variometresi verir.',
    archAltRef:   'İrtifa referansı: paketteki her yükseklik iki biçimde '
                + 'verilir: deniz seviyesinden (MSL), otopilot ve hava sahası '
                + 'bunu kullandığı için, ve yerden (AGL), termal tabanı ve '
                + 'termal katmanı yerden ölçüldüğü için. Aynı 3.000 m, kıyıda '
                + 've 3.000 m’lik bir platoda bambaşka iki uçuştur; bu yüzden '
                + 'ikisi asla tek “m” ile yazılmaz.',
    opsTitle:     'Görev her zaman önce gelir',
    ops1:         'Uçuş öncesi planlama',
    ops1Text:     'Görev saatine göre ileriye dönük harita alınır. Bekleme '
                + 'noktaları, görev geometrisini bozmadan görev bölgesi içindeki '
                + 'olasılığı yüksek hücrelere yerleştirilir.',
    ops2:         'YKİ üzerinde, çevrimdışı',
    ops2Text:     'Harita paketi kalkıştan önce YKİ’ye yüklenir ve uçuş boyunca '
                + 'operatöre gösterilir; bağlantı gerektirmez. YKİ’nin yerde '
                + 'bağlantısı varsa paket yenilenebilir, ama bu isteğe bağlıdır.',
    ops3:         'Rota sapması yok',
    ops3Text:     'Araç termal buldu diye görevinden ayrılmaz. Termal yalnızca '
                + 'görev bölgesi üzerinde ya da planlı koridorda, zaten bekleme '
                + 'yapılacak yerde kullanılır. Amaç rota değiştirmek değil, aynı '
                + 'görevi daha az enerjiyle sürdürmektir.',
    usesTitle:    'Termalden en çok kazanan görev profilleri',
    use1:         'Kıyı, sınır ve altyapı gözetimi',
    use1Text:     'Uzun bekleme süresi, termalin en çok işe yaradığı görev '
                + 'profilidir.',
    use2:         'Orman yangını erken uyarı',
    use2Text:     'Yangın riskinin en yüksek olduğu sıcak ve kuru günler, '
                + 'termallerin de en güçlü olduğu günlerdir.',
    use3:         'Haberleşme rölesi',
    use3Text:     'Belirli bir alan üzerinde saatlerce kalması gereken araç, '
                + 'konumunu termallerle koruyabilir.',
    use4:         'Hat, boru hattı ve tarım denetimi',
    use4Text:     'Uzun planlı rotalarda, koridor üzerindeki olasılığı yüksek '
                + 'hücreler irtifa kazanma durakları olarak planlanabilir.',

    // 07 horizon
    hzTitle:      'Kaç saat ilerisi, ne kadar doğru?',
    hzIntro:      'Her tahmin, zamanı gelmeden değiştirilemez bir deftere yazılır; '
                + 'zamanı geldiğinde o bölgede gerçekten uçan hava araçlarıyla '
                + 'puanlanır. Tablo bu defterin son 30 gününden; hiçbir satır '
                + 'sonradan seçilmedi.',
    hzColHorizon: 'Ufuk',
    hzColPooled:  'Termal bölge ve saatini ayırt etme (AUC)',
    hzColHit:     'Tırmanışların sıcak noktaya düşme oranı (şans: %20)',
    hzColWithin:  'Harita içi sıralama (AUC)',
    hzColN:       'Trafikle puanlanan tahmin',
    hzMin:        '{n} dk',
    hzHour:       '{n} sa',
    hzPendingRow: 'Ölçülüyor',
    hzPending:    '6 saatlik ufuk 17 Kasım 2026’da deftere eklendi; 19 Kasım’da '
                + 'trafikle puanlanabilen yalnızca 8 tahmini vardı, okumak için '
                + 'çok az. Ölçülmemiş bir ufuk için rakam yazmıyoruz.',
    hzReadTitle:  'Okuma',
    hzRead:       'Model, termalin hangi bölgede ve hangi saatte olacağını 2 saat '
                + 'ileriye kadar 0,69–0,72 AUC ile ayırt ediyor ve bu ayrım '
                + 'ufukla düşmüyor. 3 saatte 0,58’e iniyor: yazı-turanın hâlâ '
                + 'üzerinde, ama aralığı geniş. Harita içindeki sıcak noktaların '
                + 'isabeti 15 ve 30 dakikada şansın (%20) açıkça üzerinde; 1 '
                + 'saatten itibaren nokta tahmini şans düzeyinde ya da hemen '
                + 'altında (%18–20) ve aralık şansı kapsıyor. Bugünkü dürüst '
                + 'iddiamız: uçuş öncesi planlamada 2 saat ileriye kadar '
                + '“nerede ve ne zaman termal var” sorusuna güvenilir yanıt; '
                + 'hücre ölçeğinde konum için ise yarım saat. Hücre ölçeğindeki '
                + 'isabeti uzatmak, yol haritasının ikinci kilometre taşıdır.',
    hzMeta:       'Doğrulama defteri, son 30 gün, 19 Kasım 2026. Köşeli '
                + 'parantezler %95 bootstrap güven aralığıdır. Sıcak nokta: '
                + 'haritanın en yüksek olasılıklı %20’lik hücreleri. AUC 0,50 '
                + 'yazı-turadır. Tahminlerin çoğu, puanlanacak trafik olmadığı '
                + 'için “doğrulanamaz” sayılır ve hesaba katılmaz.',

    // 08 field chronology
    tlTitle:      'Termal tahmini bugüne nasıl geldi?',
    tlIntro:      'Termalin yerini bilmek için dört yol denendi: fiziğini '
                + 'hesaplamak, hava tahminini yapay zekâyla yapmak, aracın '
                + 'termali uçarken kendisinin bulması ve gözlenen uçuşlardan '
                + 'harita çıkarmak. Aşağıda her adım yayınıyla birlikte, yayın '
                + 'yılına göre sıralı. Son satır dışındaki hiçbir sonuç '
                + 'ThermalSense ile elde edilmedi.',
    tlTrackPhysics: 'Fizik ve simülasyon',
    tlTrackOnboard: 'Uçuşta bulma',
    tlTrackData:  'Uçuş verisinden harita',
    tlTrackWeather: 'Yapay zekâ ile hava tahmini',
    tlTrackUs:    'ThermalSense',
    tlLenschowT:  'Termal, ölçülen bir nesne oluyor',
    tlLenschow:   'Araştırma uçağı ölçümleriyle konvektif sınır tabakasındaki '
                + 'termallerin yapısı ve bu katmandaki taşınımdaki rolü '
                + 'incelendi. Sonraki termal modellerinin fiziksel zemini.',
    tlWharingtonT:'Pekiştirmeli öğrenme süzülmeye giriyor',
    tlWharington: 'Melbourne’de (RMIT) bir çalışma, süzülen bir hava aracının '
                + 'termali merkezlemesini pekiştirmeli öğrenmeyle denedi. '
                + 'Yalnızca simülasyonda.',
    tlNasaT:      'Sahada kanıt: +60 dakika',
    tlNasa:       'NASA Dryden’de küçük sabit kanatlı bir İHA, 17 uçuşta '
                + 'termalleri otonom olarak kullanıp havada kalış süresine 60 '
                + 'dakika ekledi. Termali, içine girdiğinde ölçerek buluyordu.',
    tlRl2016T:    'Türbülanslı termalde öğrenen süzülme',
    tlRl2016:     'Konvektif türbülans simülasyonunda eğitilen pekiştirmeli '
                + 'öğrenme ajanları termalde tırmanmayı öğrendi ve süzülmeyi '
                + 'yönlendirmeye yeten algılayıcı ipuçlarını belirledi.',
    tlRl2018T:    'Öğrenilen politika gerçek planörde',
    tlRl2018:     '2 m kanat açıklıklı bir planör, sahada birkaç günlük kendi '
                + 'uçuş deneyiminden termalde tırmanmayı öğrendi. Kullandığı '
                + 'ipuçları: dikey rüzgâr ivmesi ve yatış eksenindeki tork.',
    tlAutopilotT: 'Otonom süzülme açık kaynak otopilotta',
    tlAutopilot:  'ArduSoar, termal tespitini yaygın bir açık kaynak otopilota '
                + 'taşıdı. Microsoft Research’ün POMDP tabanlı denetleyicisi, '
                + 'aynı anda termal kullanan iki küçük sabit kanatlı İHA ile '
                + 'uçuş testinden geçti. Penn State AutoSOAR, 7,8 saatlik '
                + 'uçuşun yaklaşık 3 saatini termalde tırmanarak geçirdi. Üçü '
                + 'de termali uçarken buluyor.',
    tlStorksT:    'Kuş izlerinden Avrupa yükselme haritası',
    tlStorks:     'Ak leylek GPS izleri ve yalnızca durağan arazi özellikleriyle '
                + '(topoğrafya, arazi örtüsü) eğitilen rastgele orman '
                + 'modelleri, Avrupa ölçeğinde yükselen hava olasılığı haritası '
                + 'çıkardı. Hava verisi kullanılmadı; belirli bir gün için '
                + 'tahmin değil.',
    tlSoarnetT:   'Pilot kayıtlarından sinir ağı',
    tlSoarnet:    'Bir yüksek lisans tezi, pilotların yayımladığı uçuş '
                + 'kayıtlarındaki termalleri etiket, hava ve araziyi girdi '
                + 'yaparak bir sinir ağı eğitti. Bildirilen 0,98 F1 skoru '
                + 'eğitim ve doğrulama kümesine ait; bağımsız bir test sonucu '
                + 'verilmiyor.',
    tlXcT:        'İlerlemek mi, tırmanmak mı?',
    tlXc:         'Stuttgart Üniversitesi’nin hiyerarşik pekiştirmeli öğrenme '
                + 'politikası, kros-kantri uçuşta mesafe almak ile termalde '
                + 'tırmanmak arasında karar veriyor. Simülasyonda eğitildi, '
                + 'otonom bir planörün gömülü donanımında uçuş testinden geçti.',
    tlGraphcastT: 'Yapay zekâ küresel hava tahmininde',
    tlGraphcast:  'GraphCast 10 günlük küresel tahmini bir dakikadan kısa sürede '
                + 'üretti ve ECMWF’nin deterministik modelini 1.380 doğrulama '
                + 'hedefinin %90’ında geçti. Izgarası 0,25° (yaklaşık 28 km); '
                + 'bir termal birkaç yüz metredir.',
    tlDayclassT:  'Gün düzeyinde: bugün termal olur mu?',
    tlDayclass:   'İngiltere’deki planör kulüplerinin on yıllık uçuşlarıyla '
                + 'eğitilen rastgele orman, bir günde en az bir uçuşun termalde '
                + 'süzülüp süzülmeyeceğini %81,2 doğrulukla tahmin etti. Cevap '
                + 'gün için; “nerede” sorusu yok.',
    tlHotspotsT:  'Kalabalık kaynaklı termal klimatolojisi',
    tlHotspots:   'WeGlide, kullanıcılarının yüklediği on milyonu aşkın '
                + 'termalden Orta Avrupa için 25 sıcak nokta haritası çıkardı: '
                + 'beş zaman dilimi × beş rüzgâr yönü. thermal.kk7.ch de '
                + 'haritalarını IGC uçuş kayıtlarından üretir. İkisi de '
                + '“genelde nerede” der, “bugün bu saatte nerede” demez.',
    tlVultureT:   'Rüzgârda sürüklenen termal, akbabadan ilhamla',
    tlVulture:    'Derin pekiştirmeli öğrenme, 0–6 m/s yatay rüzgârda '
                + 'sürüklenen bir termalde tırmanmayı öğrendi; öğrenilen '
                + 'davranış akbabalarınkiyle karşılaştırıldı. Basitleştirilmiş '
                + 'simülasyonda, uçuş testi yok.',
    tlStormcastT: 'Yapay zekâ kilometre ölçeğine iniyor',
    tlStormcast:  'NVIDIA’nın StormCast’i, NOAA’nın 3 km’lik konveksiyona izin '
                + 'veren modelini (HRRR) bir difüzyon modeliyle taklit ediyor: '
                + 'saatlik adım, sınır tabakasında sık dikey çözünürlük. '
                + 'Konvektif bulut kümeleri görünür hâle geliyor; tek tek '
                + 'termaller hâlâ ızgara aralığının altında.',
    tlAifsT:      'Yapay zekâ hava modeli operasyonda',
    tlAifs:       'ECMWF, yapay zekâ tahmin sistemi AIFS’i 25 Şubat 2025’te '
                + 'fizik tabanlı modelinin yanında operasyona aldı; topluluk '
                + '(ensemble) sürümü 1 Temmuz 2025’te izledi. Izgara aralığı '
                + '28 km.',
    tlLesT:       'Termalleri çözen simülasyon GPU’da',
    tlLes:        'Termalleri tek tek çözebilen Hollanda büyük girdap '
                + 'simülasyonu (LES) modeli DALES, A100 ve H100 GPU’lara '
                + 'taşındı. Termali fizikten hesaplamak hızlanıyor; ama her '
                + 'saha ve her gün için ayrı ve pahalı bir koşu olarak kalıyor.',
    tlParaglidersT: 'Yamaç paraşütçüleri konveksiyon algılayıcısı',
    tlParagliders:'Fransa’da 2017–2024 arasındaki 110.730 yamaç paraşütü '
                + 'uçuşundan 1,47 milyon tırmanış ayrıştırıldı. Sıcak mevsimde, '
                + 'öğle saatlerinde ve alçak dağlarda termal tavanı, sıcaklık '
                + 'ile çiy noktası farkının 123,6 m/°C katı çıktı: kuramsal '
                + 'yoğuşma seviyesine (125 m/°C) %1 yakın. Makine öğrenmesi ve '
                + 'tahmin skoru yok; amaç belirleyicileri bulmak.',
    tlUsT:        'Gözlenen tırmanışlardan sürekli öğrenen, kendini puanlayan '
                + 'harita',
    tlUs:         'Belirli bir saat için 50 m hücrede termal olasılığı veren, '
                + 'dünya genelindeki termal telemetrisinden her beş dakikada '
                + 'yeniden eğitilen ve her tahmini zamanından önce deftere '
                + 'yazıp sonra gerçek uçuşlarla puanlayan model. Skoru ve '
                + 'doğrulanmış ufku yukarıda, güven aralıklarıyla.',
    frTitle:      'Bugün sınır nerede?',
    fr1:          'Hava modelleri termal gününü görür',
    fr1Text:      'Yapay zekâ ile hava tahmini artık operasyonel, ama ızgarası '
                + '28 km (AIFS) ile 3 km (StormCast) arasında. Bir termal '
                + 'birkaç yüz metredir: bu modeller termalin değil, termal '
                + 'gününün tahminini verir.',
    fr2:          'Uçuşta öğrenme termali içinde bulur',
    fr2Text:      'Pekiştirmeli öğrenme gerçek uçuşlarda çalışıyor (2018, '
                + '2023). Ama araç termali ancak içine girince tanıyor; '
                + 'nereye bakacağını söyleyen bir önsel haritası yok.',
    fr3:          'Veriden haritalar ya ortalama ya gün',
    fr3Text:      'Uçuş verisinden çıkan sonuçlar ya iklimsel ortalama '
                + '(genelde nerede), ya gün düzeyinde evet/hayır (%81,2), ya '
                + 'da tahmin skoru olmayan bir belirleyici çalışması. Yüksek '
                + 'skor bildiren tek sinir ağı bağımsız testle doğrulanmadı.',
    frGapTitle:   'Boşluk',
    frGap:        'Belirli bir saat için hücre ölçeğinde termal olasılığı '
                + 'veren, gözlenen tırmanışlardan sürekli öğrenen ve '
                + 'tahminlerini önceden kaydedip sonra gerçek uçuşlarla '
                + 'puanlayan yayımlanmış bir sistem bulamadık. ThermalSense bu '
                + 'boşlukta duruyor. Skoru mütevazı (harita içi sıralamada '
                + '0,57 AUC) ve öyle raporlanıyor: boşluğun var olması, onu '
                + 'doldurmanın kolay olduğu anlamına gelmiyor.',
    tlMeta:       'Yalnızca bağlantısı açılabilen yayınlar alındı; her bağlantı '
                + '20 Kasım 2026’da açıldı. Liste kapsamlı değil, bir tarama. '
                + 'Pilotlara yönelik süzülme tahmin servisleri (SkySight, '
                + 'TopMeteo) rakip analizinde. Yıl, yayın yılıdır.',

    // 09 competition
    cmpTitle:     'Rakip analizi',
    cmpIntro:     'Termal bilgisi bugün iki yoldan geliyor: pilotlara yönelik, '
                + 'kilometre ölçekli süzülme tahmin servisleri ve aracın termali '
                + 'uçarken kendisinin bulması. Hiçbiri, gözlenen tırmanışlardan '
                + 'öğrenip uçuştan önce YKİ’ye hücre ölçeğinde harita veren bir '
                + 'ürün değil.',
    cmpColUs:     'ThermalSense',
    cmpColSoar:   'Süzülme tahmin servisleri (SkySight, TopMeteo)',
    cmpColNwp:    'Genel hava servisleri (Windy, meteoblue)',
    cmpColOnboard:'Uçuşta termal tespiti (ArduSoar)',
    cmpColTrad:   'Geleneksel yöntem (pilot bilgisi, kör arama)',
    cmpRowPre:    'Uçuştan önce harita',
    cmpRowRes:    'Mekânsal çözünürlük',
    cmpRowLearn:  'Gözlenen tırmanışlardan öğrenme',
    cmpRowHw:     'Araçta ek donanım / uçuşta internet',
    cmpRowAudit:  'Kamuya açık doğrulama',
    cmpRowUser:   'Hedef kullanıcı',
    cmpRowPrice:  'Fiyat',
    cmpYes:       'Var',
    cmpNo:        'Yok',
    cmpPartial:   'Kısmen',
    cmpNotNeeded: 'Gerekmez',
    cmpResUs:     '50 m hücre',
    cmpResNwp:    'Km ölçekli model ızgarası',
    cmpResOnboard:'Yalnızca aracın bulunduğu nokta',
    cmpLearnUs:   'Var, her 5 dakikada',
    cmpLearnNone: 'Yayımlanmış bir döngü bulunamadı',
    cmpLearnOnboard:'Uçuş içinde, kalıcı değil',
    cmpLearnTrad: 'Kişisel deneyim',
    cmpAuditUs:   'Değiştirilemez defter, ufuk bazında skor',
    cmpAuditNone: 'Bulunamadı',
    cmpUserUs:    'İHA operatörü, YKİ',
    cmpUserSoar:  'Planör ve yamaç paraşütü pilotu',
    cmpUserNwp:   'Genel kullanıcı',
    cmpUserOnboard:'Otopilot',
    cmpUserTrad:  'Pilot, operatör',
    cmpPriceUs:   'Kurumsal lisans (planlanan)',
    cmpPriceSoar: '89 $/yıl; 67,90–139,90 €/yıl',
    cmpPriceNwp:  'Ücretsiz / abonelik',
    cmpPriceOnboard:'Açık kaynak',
    cmpDash:      '—',
    cmpNote:      'ArduSoar bir rakipten çok tamamlayıcıdır: araç termali uçarken '
                + 'onunla tespit eder, harita ona nereye bakacağını söyler. '
                + 'Fiyatlar satıcı sayfalarından, Kasım 2026; bu servislerin '
                + 'gelirleri kamuya açık değil.',

    // 10 market
    mktTitle:     'Pazar büyüklüğü',
    mktIntro:     'ThermalSense bir platform değil, platformlara eklenen bir '
                + 'yazılım ve veri katmanıdır. İlk iki rakam platform pazarıdır; '
                + 'bizim payımız onun küçük bir kesridir ve öyle hesaplanmıştır.',
    tamLabel:     'TAM',
    tamFig:       '12,9 Mr $',
    tamWhat:      'Küresel sabit kanatlı İHA pazarı, 2025',
    tamText:      'Tüm İHA pazarı 17,23 Mr $; bunun %74,78’i sabit kanatlı. '
                + 'Sivil ve kamu kullanımı birlikte. 2031’e kadar yıllık %7 '
                + 'büyüme.',
    samLabel:     'SAM',
    samFig:       '0,93 Mr $',
    samWhat:      'Küçük (150 kg altı) sabit kanatlı İHA, 2026',
    samText:      'Küçük İHA pazarı 2026’da 1,73 Mr $, yıllık %11 büyümeyle '
                + '2031’de 2,92 Mr $; gelirin %53,65’i sabit kanatlı. Alçak '
                + 'irtifa sınıfı bu segmenttir.',
    somLabel:     'SOM',
    somFig:       '~2,8 M $ / yıl',
    somWhat:      '5. yıl hedefi · varsayım',
    somText:      'Ölçüm değil, varsayım: yazılım ve veri katmanı platform '
                + 'değerinin %3’ü, biz bu katmanın %10’u. 0,93 Mr $ × %3 × %10. '
                + 'Türkiye ve yakın bölgeyle başlanır.',
    mktPlayers:   'Mevcut oyuncuların fiyat noktası: pilotlara yönelik süzülme '
                + 'tahmin servisleri yıllık 70–150 $/€ aralığında abonelik satıyor '
                + '(SkySight 89 $/yıl; TopMeteo 67,90–139,90 €/yıl). Gelirleri '
                + 'kamuya açık değil.',
    mktMeta:      'Kaynak: Mordor Intelligence, İHA pazarı (7 Nisan 2026) ve küçük '
                + 'İHA pazarı (28 Temmuz 2026). Raporların tanımları birbirinden '
                + 'çok farklı: aynı yıl için sabit kanatlı İHA pazarı 2 Mr $ ile '
                + '14 Mr $ arasında veriliyor. Tanımlar tutarlı olsun diye tek bir '
                + 'yayıncının iki raporu kullanıldı.',

    // 11 gain
    gainTitle:    'Uçuş süresi ne kadar uzar?',
    gainIntro:    'Hesap basit: motor kapalı geçen süre bataryadan düşmez. '
                + 'Uçuşun f kadarı motorsuz geçerse, aynı batarya ile havada '
                + 'kalış süresi yaklaşık 1 / (1 − f) katına çıkar. Menzil değil, '
                + 'görev bölgesi üzerindeki süre uzar.',
    gainBase:     'Nominal havada kalış',
    gainShare:    'Motorsuz geçen pay (f)',
    gainResult:   'Termallerle',
    gainTimes:    '{x} kat',
    gainHours:    '{h} sa',
    gainExtra:    '+{h} sa havada kalış',
    gainPct:      'uçuş süresi +%{n}',
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
                + 've kısalan her dakika f’ye eklenir. Bunu alçak irtifa sabit '
                + 'kanatlı bir platformda ölçmek, uçuş testi kilometre taşının '
                + 'işidir.',

    // 12 why
    whyTitle:     'Neden biz.',
    why1:         'Kendiliğinden büyüyen veri',
    why1Text:     'Dünyada her gün milyonlarca termal telemetrisi kaydı '
                + 'yayınlanıyor. Aynı veri kümesini kurmak isteyen bir rakibin, '
                + 'bir filo uçurması gerekir.',
    why2:         'Kendini denetleyen tahmin',
    why2Text:     'Her tahmin, zamanı gelmeden değiştirilemez bir deftere '
                + 'kaydedilir ve sonra gerçek uçuşlarla puanlanır. Kayıtların '
                + 'sonradan düzeltilmesi veritabanı düzeyinde engellenir. '
                + 'Kurumsal ve kamu alımlarının istediği izlenebilirlik buradan '
                + 'gelir.',
    why3:         'Ölçtüğünü söyleyen ekip',
    why3Text:     'Her sonuç güven aralığıyla raporlanır; kazanç kanıtlayamayan '
                + 'denemeler de kayıtlıdır. Bu sunumdaki her rakamın kaynağı '
                + 'gösterilebilir.',
    why4:         'Hafif çalışır',
    why4Text:     'Bütün sistem bugün 2 çekirdekli tek bir sunucuda çalışıyor; '
                + 'bir harita yaklaşık yarım saniyede üretiliyor.',

    // 13 roadmap & ask
    roadTitle:    'Yol haritası ve kilometre taşları',
    roadDone:     'tamamlandı',
    m0When:       'Kasım 2026',
    m0:           'Canlı sistem',
    m0Text:       'Kendi kendini eğiten model, kalite kapısı ve değiştirilemez '
                + 'doğrulama defteri çalışıyor.',
    m1When:       '2027 · 1. çeyrek',
    m1:           'Türkiye ölçülebilir',
    m1Text:       'İnönü–Eskişehir bölge skoru ayrı raporlanır; 6 saatlik ufuk '
                + 'defterde ölçülmüş olur.',
    m2When:       '2027 · 2. çeyrek',
    m2:           'Hücre ölçeğinde isabet',
    m2Text:       'Tam çözünürlüklü arazi (çevrimdışı +0,02–0,03 AUC ölçüldü) ve '
                + 'uydu yüzey sıcaklığı üretimde; sıcak nokta isabeti 3 saatte '
                + 'şansın üzerinde.',
    m3When:       '2027 · 3. çeyrek',
    m3:           'YKİ görev paketi',
    m3Text:       'Harita ve bekleme noktalarının yaygın YKİ yazılımlarının görev '
                + 'dosyalarına aktarımı; donanım döngüde simülasyon.',
    m4When:       '2027 4. çeyrek – 2028 1. çeyrek',
    m4:           'Uçuş testi',
    m4Text:       'Bir platform ortağıyla, haritalı ve haritasız eşleştirilmiş '
                + 'uçuşlarda uçuş süresi artışının ölçülmesi.',
    m5When:       '2028',
    m5:           'Ürün',
    m5Text:       'YKİ eklentisi olarak kurumsal lisans; ilk müşteri.',
    askTitle:     'Aradığımız',
    askText:      'Ön tohum yatırımı ve bir platform ortağı: test uçuşları için '
                + 'alçak irtifa sabit kanatlı bir İHA ve o uçuşların verisi. '
                + 'Karşılığında, kendi platformunuzda ölçülmüş bir uçuş süresi '
                + 'artışı.',
    askCta:       'Canlı sistemi inceleyin',

    // 14 references
    refTitle:     'Kaynakça',
    refIntro:     'Sunumdaki üçüncü taraf sonuçlarının, termal modelinin ve '
                + 'kronolojinin dayandığı yayınlar.',

    disclaimer:   'Üçüncü taraf sonuçları belirtilen yayınlardan alınmıştır ve '
                + 'ThermalSense ile elde edilmemiştir. ThermalSense bir uçuş '
                + 'emniyeti aracı değildir.',
  },

  en: {
    docTitle:     'ThermalSense · Thermal maps for low-altitude fixed-wing UAVs',
    skipToContent:'Skip to content',
    navLabel:     'Presentation sections',
    navProblem:   'Problem',
    navProduct:   'Product',
    navArch:      'Architecture',
    navHorizon:   'Forecast horizon',
    navField:     'Timeline',
    navCompete:   'Competition',
    navMarket:    'Market',
    navGain:      'Gain',
    navRoad:      'Roadmap',
    openMap:      'Open the live system',
    openMapShort: 'Live system',
    openCompare:  'Open the raw data ↔ AI comparison',
    technical:    'Open the training panel',
    slideOf:      '{n} / {total}',
    pct:          '{n}%',

    eyebrow:      'Pre-seed presentation · Autonomous aviation · Dual-use technology',
    coverA:       'The atmosphere carries free energy.',
    coverB:       'We map where it is.',
    coverLede:    'ThermalSense is a self-training AI model that forecasts '
                + 'thermals, the columns of warm air rising from the ground, '
                + 'at 50-metre resolution. Before take-off, it shows '
                + 'low-altitude fixed-wing UAVs where, inside their planned '
                + 'mission area, they can throttle back and still gain height.',
    coverCta:     'Go to the flight-time estimate',
    coverStatus:  'The system is live today: it relearns every five minutes '
                + 'from thermal telemetry worldwide.',

    simAlt:        'Animation: aircraft cruising and circling over a dark map '
                 + 'grid; cells warm up where they circle',
    simCaption:    'An illustration, not live data. A soaring aircraft that '
                 + 'flies into a thermal it cannot see starts to circle, and '
                 + 'the cells beneath it warm with the thermal data it leaves. '
                 + 'That is exactly how the model learns.',
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

    probTitle:    'A low-altitude fixed-wing UAV’s time aloft is set by its battery.',
    probText:     'Every extra hour of flight means either a heavier battery '
                + 'or less payload. Yet the layer these aircraft fly in is, on '
                + 'sunny days, full of thermals reaching several thousand '
                + 'metres above the ground. A thermal is invisible; neither '
                + 'radar nor camera shows it. Energy you cannot locate is '
                + 'energy you cannot use.',
    classDefTerm: 'Low-altitude fixed-wing UAV',
    classDef:     'An unmanned aircraft with a fixed wing that flies below '
                + 'roughly 3,000 metres (10,000 ft). Rotary-wing aircraft '
                + '(quadcopters) are out of scope: only an aircraft that glides '
                + 'on its wing can use a thermal. That altitude band is exactly '
                + 'where thermals form.',
    probPoints:   'The result',
    probP1:       'Flight time is capped by battery mass.',
    probP2:       'Time on station over the mission area is the aircraft’s most valuable hour.',
    probP3:       'A free energy source goes unused beneath the aircraft every day.',

    evTitle:      'Flying thermals extends time aloft.',
    evIntro:      'This is not our claim. The results below come from '
                + 'published flight tests and simulations; none was obtained '
                + 'with ThermalSense. The full bibliography closes the '
                + 'presentation.',
    ev1Fig:       '+60 min',
    ev1Who:       'NASA Dryden · flight test',
    ev1Text:      'Over a 17-flight campaign, a small fixed-wing UAV added 60 '
                + 'minutes to its time aloft by using thermals autonomously. It '
                + 'climbed an average of 172 m in 23 thermals, and 844 m in '
                + 'the strongest.',
    ev2Fig:       '2 → 8.6 h',
    ev2Who:       'NASA, Allen (2005) · simulation',
    ev2Text:      'A fixed-wing UAV with a nominal 2 hours aloft stayed up for '
                + '8.6 hours on the yearly average by using thermals. The '
                + 'ceiling was 14 hours in summer and 8 in winter.',
    ev3Fig:       '7.8 h',
    ev3Who:       'Penn State AutoSOAR · flight test',
    ev3Text:      '7.8 hours of flight with the autonomous soaring system '
                + 'engaged, about 3 of them spent climbing in thermals.',
    evSource:     'Source',

    gapTitle:     'The missing link: where to look.',
    gapText:      'Every one of these systems finds thermals in flight, by '
                + 'searching blind. NASA’s aircraft flew a fixed spiral until '
                + 'it hit one. Every minute spent searching is a minute flown '
                + 'on the motor. The way to cut that loss is to know, before '
                + 'take-off, where in the mission area thermals are most likely.',
    gapBlind:     'Blind search',
    gapBlindText: 'A spiral sweep; the motor runs until a thermal turns up.',
    gapMap:       'Prior map',
    gapMapText:   'The search starts in the most likely cells of the mission area.',

    prodTitle:    'The product works today.',
    prodIntro:    'The training data is thermal data: every soaring aircraft '
                + 'climbing in circles is a “thermal here” example, and every '
                + 'one flying straight and sinking is a “none here” example. '
                + 'ThermalSense collects these labels itself from the Open '
                + 'Glider Network’s civil telemetry feed. No human labelling is '
                + 'needed; the data grows without flying a fleet.',
    factCells:    'cells',
    factCellsText:'An area about 11 km across around the chosen point, scored '
                + 'in 50-metre cells.',
    factFeed:     'thermal telemetry reports / day',
    factFeedText: 'Position and climb reports from worldwide flow in '
                + 'continuously as training data.',
    factRetrain:  'retraining',
    factRetrainText:'A new model goes live only if it beats the current one, '
                + 'with statistical confidence, on a frozen test set.',
    factAhead:    'verified horizon',
    factAheadText:'The map can be produced up to 24 hours ahead; the horizon '
                + 'verified against real flights is 3 hours today. Details '
                + 'under “Forecast horizon”.',
    feedFig:      '7.7 M',
    aheadFig:     '3 h',
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
    demoTitle:    'Live demo: raw data and ThermalSense AI side by side',
    demoText:     'In the live system, the “Raw data ↔ ThermalSense AI” switch '
                + 'splits the map in two. On the left the system is off: the '
                + 'single low-resolution value a standard weather service gives '
                + 'for the whole area, and sparse elevation samples. On the '
                + 'right the AI is on: the 50-metre thermal probability map '
                + 'built from the same inputs. The map opens on Türkiye, at the '
                + 'İnönü test area.',

    archTitle:    'Where exactly is the AI?',
    archIntro:    'Raw weather data is public and kilometre-scale: a weather '
                + 'service gives one temperature and one solar radiation value '
                + 'for an 11 km area. The AI layer is the step that combines '
                + 'that single value with the terrain and turns it into a '
                + '40,401-cell thermal probability map.',
    archOnline:   'On the ground, before flight · needs internet',
    archOffline:  'In flight · no internet needed',
    arch1Tag:     'Input · raw data',
    arch1:        'Weather and terrain',
    arch1Text:    'Numerical weather forecast (hourly, ~0.1° grid), a 30 m '
                + 'elevation model (Copernicus GLO-30), sun position. No AI '
                + 'here; data anyone can get.',
    arch2Tag:     'AI layer',
    arch2:        'ThermalSense model',
    arch2Text:    'For every 50 m cell, 22 features (slope, aspect, sun angle, '
                + 'altitude, instability…) are computed; gradient-boosted '
                + 'decision trees (XGBoost) turn them into a thermal '
                + 'probability and an uncertainty. The model retrains on '
                + 'thermal data every five minutes.',
    arch3Tag:     'Output · before flight',
    arch3:        'Mission package',
    arch3Text:    'For the mission’s time window, the thermal map and the '
                + 'high-probability loiter points are computed before flight '
                + 'and transferred to the Ground Control Station (GCS) and the '
                + 'flight plan.',
    arch4Tag:     'Flight · offline',
    arch4:        'Low-altitude fixed-wing UAV',
    arch4Text:    'The aircraft does not connect to the internet, and no extra '
                + 'hardware, sensor or weather station is fitted to it. It '
                + 'flies its planned mission; its own variometer decides '
                + 'whether to enter a thermal.',
    archAltRef:   'Altitude reference: every height in the package is given '
                + 'both ways: above sea level (MSL), because the autopilot and '
                + 'the airspace use it, and above ground (AGL), because the '
                + 'thermal base and the thermal layer are measured from the '
                + 'ground. The same 3,000 m is two very different flights over '
                + 'a coast and over a 3,000 m plateau, so the two are never '
                + 'written as a bare “m”.',
    opsTitle:     'The mission always comes first',
    ops1:         'Pre-flight planning',
    ops1Text:     'A forward map is produced for the mission time. Loiter '
                + 'points are placed on high-probability cells inside the '
                + 'mission area without breaking the mission geometry.',
    ops2:         'On the GCS, offline',
    ops2Text:     'The map package is loaded onto the GCS before take-off and '
                + 'shown to the operator throughout the flight; it needs no '
                + 'connection. If the GCS has a link on the ground the package '
                + 'can be refreshed, but that is optional.',
    ops3:         'No diversions',
    ops3Text:     'The aircraft does not leave its task because it found a '
                + 'thermal. Thermals are used only over the mission area or in '
                + 'the planned corridor, where it would loiter anyway. The aim '
                + 'is not a new route, but the same mission on less energy.',
    usesTitle:    'Mission profiles that gain the most from thermals',
    use1:         'Coastal, border and infrastructure monitoring',
    use1Text:     'Long loiter time is the profile where thermals pay off most.',
    use2:         'Early wildfire warning',
    use2Text:     'The hot, dry days of highest fire risk are also the days of '
                + 'the strongest thermals.',
    use3:         'Communications relay',
    use3Text:     'An aircraft that must hold over one area for hours can keep '
                + 'its station on thermals.',
    use4:         'Line, pipeline and agricultural inspection',
    use4Text:     'On long planned routes, high-probability cells along the '
                + 'corridor can be planned as stops to regain height.',

    hzTitle:      'How far ahead, and how accurate?',
    hzIntro:      'Every forecast is written to a tamper-proof ledger before its '
                + 'time, and scored afterwards against the aircraft that '
                + 'actually flew there. The table is the last 30 days of that '
                + 'ledger; no row was picked after the fact.',
    hzColHorizon: 'Horizon',
    hzColPooled:  'Telling thermal places and hours apart (AUC)',
    hzColHit:     'Climbs landing in the hotspot (chance: 20%)',
    hzColWithin:  'Ranking within one map (AUC)',
    hzColN:       'Forecasts scored with traffic',
    hzMin:        '{n} min',
    hzHour:       '{n} h',
    hzPendingRow: 'Being measured',
    hzPending:    'The 6-hour horizon was added to the ledger on 17 November '
                + '2026; by 19 November it had only 8 forecasts with traffic to '
                + 'score, too few to read. We do not write a number for a '
                + 'horizon we have not measured.',
    hzReadTitle:  'Reading',
    hzRead:       'Out to 2 hours, the model tells apart where and when '
                + 'thermals will be with an AUC of 0.69–0.72, and that does not '
                + 'fall with the horizon. At 3 hours it drops to 0.58: still '
                + 'above a coin toss, but with a wide interval. Hits on the '
                + 'hotspot cells inside one map are clearly above chance (20%) '
                + 'at 15 and 30 minutes; from 1 hour the point estimate is at '
                + 'or just below chance (18–20%) and the interval spans it. Our '
                + 'honest claim today: a reliable answer for pre-flight '
                + 'planning to “where and when are there thermals” out to 2 '
                + 'hours, and half an hour for cell-scale position. Extending '
                + 'cell-scale accuracy is the second milestone on the roadmap.',
    hzMeta:       'Verification ledger, last 30 days, 19 November 2026. Square '
                + 'brackets are 95% bootstrap intervals. Hotspot: the 20% of '
                + 'cells with the highest probability on the map. AUC 0.50 is '
                + 'a coin toss. Most forecasts have no traffic to score them '
                + 'against; they count as “unverifiable” and are left out.',

    tlTitle:      'How thermal prediction got here.',
    tlIntro:      'Four routes have been tried to know where a thermal is: '
                + 'compute its physics, forecast the weather with AI, let the '
                + 'aircraft find it in flight, and build maps from observed '
                + 'flights. Below, each step with its publication, in order of '
                + 'publication year. No result here except the last row was '
                + 'obtained with ThermalSense.',
    tlTrackPhysics: 'Physics and simulation',
    tlTrackOnboard: 'Finding it in flight',
    tlTrackData:  'Maps from flight data',
    tlTrackWeather: 'AI weather forecasting',
    tlTrackUs:    'ThermalSense',
    tlLenschowT:  'The thermal becomes something measured',
    tlLenschow:   'Research-aircraft measurements examined the structure of '
                + 'thermals in the convective boundary layer and their role in '
                + 'its transport. The physical footing for later thermal models.',
    tlWharingtonT:'Reinforcement learning meets soaring',
    tlWharington: 'Work in Melbourne (RMIT) tried reinforcement learning for '
                + 'centring a soaring aircraft in a thermal. In simulation only.',
    tlNasaT:      'Field evidence: +60 minutes',
    tlNasa:       'At NASA Dryden a small fixed-wing UAV used thermals '
                + 'autonomously over 17 flights and added 60 minutes to its '
                + 'time aloft. It found each thermal by measuring it once inside.',
    tlRl2016T:    'Learning to soar in turbulent thermals',
    tlRl2016:     'Reinforcement-learning agents trained in a simulation of '
                + 'convective turbulence learned to climb in thermals, and '
                + 'identified the sensory cues that suffice to guide soaring.',
    tlRl2018T:    'A learned policy on a real glider',
    tlRl2018:     'A glider with a 2 m wingspan learned to climb in thermals '
                + 'from its own pooled flight experience over several days in '
                + 'the field. Its cues: vertical wind acceleration and roll-wise '
                + 'torque.',
    tlAutopilotT: 'Autonomous soaring in an open-source autopilot',
    tlAutopilot:  'ArduSoar brought thermal detection to a widely used '
                + 'open-source autopilot. Microsoft Research’s POMDP-based '
                + 'controller was flight-tested on two small fixed-wing UAVs '
                + 'thermalling at once. Penn State’s AutoSOAR spent about 3 of '
                + '7.8 flight hours climbing in thermals. All three find the '
                + 'thermal in flight.',
    tlStorksT:    'A European uplift map from bird tracks',
    tlStorks:     'Random forests trained on white stork GPS tracks and static '
                + 'landscape features alone (topography, land cover) produced '
                + 'a Europe-wide map of uplift probability. No weather data '
                + 'was used; it is not a forecast for any given day.',
    tlSoarnetT:   'A neural network from pilots’ logs',
    tlSoarnet:    'A master’s thesis trained a neural network with thermals '
                + 'from pilots’ published flight logs as labels and weather '
                + 'and terrain as inputs. The reported 0.98 F1 is on the '
                + 'training and validation sets; no independent test result '
                + 'is given.',
    tlXcT:        'Press on, or climb?',
    tlXc:         'The University of Stuttgart’s hierarchical '
                + 'reinforcement-learning policy decides between covering '
                + 'distance and climbing in a thermal on a cross-country '
                + 'flight. Trained in simulation, it was flight-tested on '
                + 'embedded hardware aboard an autonomous glider.',
    tlGraphcastT: 'AI in global weather forecasting',
    tlGraphcast:  'GraphCast produced a 10-day global forecast in under a '
                + 'minute and beat ECMWF’s deterministic model on 90% of 1,380 '
                + 'verification targets. Its grid is 0.25° (about 28 km); a '
                + 'thermal is a few hundred metres across.',
    tlDayclassT:  'Day level: will there be thermals today?',
    tlDayclass:   'A random forest trained on ten years of flights at UK '
                + 'gliding clubs predicted, with 81.2% accuracy, whether at '
                + 'least one flight on a day would soar in thermals. The answer '
                + 'is per day; there is no “where”.',
    tlHotspotsT:  'Crowd-sourced thermal climatology',
    tlHotspots:   'WeGlide turned over ten million thermals uploaded by its '
                + 'users into 25 hotspot maps of Central Europe: five times of '
                + 'day × five wind directions. thermal.kk7.ch likewise builds '
                + 'its maps from IGC flight logs. Both say “where, usually”, '
                + 'not “where, today at this hour”.',
    tlVultureT:   'A drifting thermal, vulture-inspired',
    tlVulture:    'Deep reinforcement learning learned to climb in a thermal '
                + 'drifting in 0–6 m/s of horizontal wind, and the learned '
                + 'behaviour was compared with vultures’. In a simplified '
                + 'simulation; no flight test.',
    tlStormcastT: 'AI reaches the kilometre scale',
    tlStormcast:  'NVIDIA’s StormCast emulates NOAA’s 3 km '
                + 'convection-allowing model (HRRR) with a diffusion model: an '
                + 'hourly step and dense vertical levels in the boundary layer. '
                + 'Convective cloud clusters become visible; individual thermals '
                + 'are still below the grid spacing.',
    tlAifsT:      'An AI weather model goes operational',
    tlAifs:       'ECMWF took its AI forecasting system, AIFS, into operations '
                + 'beside its physics-based model on 25 February 2025; the '
                + 'ensemble version followed on 1 July 2025. Grid spacing 28 km.',
    tlLesT:       'Thermal-resolving simulation on GPUs',
    tlLes:        'DALES, the Dutch large-eddy simulation (LES) model that can '
                + 'resolve individual thermals, was ported to A100 and H100 '
                + 'GPUs. Computing thermals from physics is getting faster, but '
                + 'it remains a separate, costly run for every site and day.',
    tlParaglidersT: 'Paragliders as convection sensors',
    tlParagliders:'1.47 million climbs were extracted from 110,730 paragliding '
                + 'flights in France, 2017–2024. In the warm season, at midday '
                + 'over low mountains, thermal ceiling came out at 123.6 m per '
                + '°C of temperature–dew point spread: within 1% of the '
                + 'theoretical condensation level (125 m/°C). No machine '
                + 'learning and no forecast score; the aim is to find the '
                + 'predictors.',
    tlUsT:        'A map that learns from observed climbs and scores itself',
    tlUs:         'A model that gives a thermal probability per 50 m cell for '
                + 'a given hour, retrains every five minutes on thermal '
                + 'telemetry from worldwide, and writes every forecast to a '
                + 'ledger before its time, scoring it afterwards against real '
                + 'flights. Its score and verified horizon are above, with '
                + 'confidence intervals.',
    frTitle:      'Where the frontier is today',
    fr1:          'Weather models see the thermal day',
    fr1Text:      'AI weather forecasting is now operational, but its grid runs '
                + 'from 28 km (AIFS) to 3 km (StormCast). A thermal is a few '
                + 'hundred metres across: these models forecast the thermal '
                + 'day, not the thermal.',
    fr2:          'Learning in flight finds it from inside',
    fr2Text:      'Reinforcement learning works on real flights (2018, 2023). '
                + 'But the aircraft recognises a thermal only once inside it; '
                + 'it has no prior map telling it where to look.',
    fr3:          'Data-driven maps give an average or a day',
    fr3Text:      'Results from flight data are either a climatology (where, '
                + 'usually), a per-day yes/no (81.2%), or a study of '
                + 'predictors with no forecast score. The one neural network '
                + 'reporting a high score was not checked on an independent test.',
    frGapTitle:   'The gap',
    frGap:        'We found no published system that gives a cell-scale '
                + 'thermal probability for a given hour, learns continuously '
                + 'from observed climbs, and records its forecasts in advance '
                + 'to score them later against real flights. ThermalSense sits '
                + 'in that gap. Its score is modest (0.57 AUC ranking within a '
                + 'map) and reported as such: that a gap exists does not mean '
                + 'it is easy to fill.',
    tlMeta:       'Only publications with a resolvable link were taken; every '
                + 'link was opened on 20 November 2026. The list is a survey, '
                + 'not exhaustive. Soaring forecast services for pilots '
                + '(SkySight, TopMeteo) are under Competition. The year is the '
                + 'year of publication.',

    cmpTitle:     'Competitive analysis',
    cmpIntro:     'Thermal information comes from two places today: '
                + 'kilometre-scale soaring forecast services for pilots, and '
                + 'the aircraft finding the thermal itself in flight. None is a '
                + 'product that learns from observed climbs and hands the GCS a '
                + 'cell-scale map before flight.',
    cmpColUs:     'ThermalSense',
    cmpColSoar:   'Soaring forecast services (SkySight, TopMeteo)',
    cmpColNwp:    'General weather services (Windy, meteoblue)',
    cmpColOnboard:'In-flight thermal detection (ArduSoar)',
    cmpColTrad:   'Traditional (pilot knowledge, blind search)',
    cmpRowPre:    'Map before flight',
    cmpRowRes:    'Spatial resolution',
    cmpRowLearn:  'Learns from observed climbs',
    cmpRowHw:     'Extra hardware on board / internet in flight',
    cmpRowAudit:  'Public verification',
    cmpRowUser:   'Target user',
    cmpRowPrice:  'Price',
    cmpYes:       'Yes',
    cmpNo:        'No',
    cmpPartial:   'Partly',
    cmpNotNeeded: 'Not needed',
    cmpResUs:     '50 m cells',
    cmpResNwp:    'Kilometre-scale model grid',
    cmpResOnboard:'Only where the aircraft is',
    cmpLearnUs:   'Yes, every 5 minutes',
    cmpLearnNone: 'No published loop found',
    cmpLearnOnboard:'Within one flight, not kept',
    cmpLearnTrad: 'Personal experience',
    cmpAuditUs:   'Tamper-proof ledger, score per horizon',
    cmpAuditNone: 'None found',
    cmpUserUs:    'UAV operator, GCS',
    cmpUserSoar:  'Glider and paraglider pilots',
    cmpUserNwp:   'General public',
    cmpUserOnboard:'Autopilot',
    cmpUserTrad:  'Pilot, operator',
    cmpPriceUs:   'Enterprise licence (planned)',
    cmpPriceSoar: 'USD 89/yr; EUR 67.90–139.90/yr',
    cmpPriceNwp:  'Free / subscription',
    cmpPriceOnboard:'Open source',
    cmpDash:      '—',
    cmpNote:      'ArduSoar is a complement more than a competitor: it detects '
                + 'a thermal while the aircraft flies through it, and the map '
                + 'tells it where to look. Prices from vendor pages, November '
                + '2026; these services do not publish revenue.',

    mktTitle:     'Market size',
    mktIntro:     'ThermalSense is not a platform but a software and data layer '
                + 'added to platforms. The first two figures are the platform '
                + 'market; our share is a small fraction of it, and is '
                + 'computed as one.',
    tamLabel:     'TAM',
    tamFig:       'USD 12.9 B',
    tamWhat:      'Global fixed-wing UAV market, 2025',
    tamText:      'The whole UAV market is USD 17.23 B, 74.78% of it '
                + 'fixed-wing. Civil and public use together. 7% a year to 2031.',
    samLabel:     'SAM',
    samFig:       'USD 0.93 B',
    samWhat:      'Small (under 150 kg) fixed-wing UAVs, 2026',
    samText:      'The small UAV market is USD 1.73 B in 2026, growing 11% a '
                + 'year to USD 2.92 B in 2031; 53.65% of revenue is fixed-wing. '
                + 'The low-altitude class is this segment.',
    somLabel:     'SOM',
    somFig:       '~USD 2.8 M / yr',
    somWhat:      'Year-5 target · assumption',
    somText:      'An assumption, not a measurement: the software and data layer '
                + 'is 3% of platform value, and we take 10% of that layer. '
                + 'USD 0.93 B × 3% × 10%. Starting in Türkiye and its region.',
    mktPlayers:   'Existing players’ price point: soaring forecast services for '
                + 'pilots sell subscriptions at 70–150 USD/EUR a year (SkySight '
                + 'USD 89/yr; TopMeteo EUR 67.90–139.90/yr). They do not '
                + 'publish revenue.',
    mktMeta:      'Source: Mordor Intelligence, UAV market (7 April 2026) and '
                + 'small UAV market (28 July 2026). Report definitions differ '
                + 'widely: the same year’s fixed-wing UAV market is quoted '
                + 'anywhere from USD 2 B to 14 B. Two reports from one '
                + 'publisher are used so the definitions agree.',

    gainTitle:    'How much longer in the air?',
    gainIntro:    'The arithmetic is simple: time spent with the motor off '
                + 'does not draw on the battery. If a fraction f of the flight '
                + 'is unpowered, the same battery keeps the aircraft up about '
                + '1 / (1 − f) times as long. What grows is time over the '
                + 'mission area, not range.',
    gainBase:     'Nominal time aloft',
    gainShare:    'Unpowered share (f)',
    gainResult:   'With thermals',
    gainTimes:    '{x}×',
    gainHours:    '{h} h',
    gainExtra:    '+{h} h aloft',
    gainPct:      'flight time +{n}%',
    gainRefAuto:  'AutoSOAR: share spent climbing in thermals',
    gainRefNasa:  'NASA simulation: yearly average',
    gainChartAlt: 'Curve of the time-aloft multiplier against the unpowered share',
    gainAxisX:    'unpowered share',
    gainAxisY:    'multiplier',
    gainAssume:   'Assumption: avionics and payload draw are ignored; including '
                + 'them lowers the multiplier.',
    gainHonestTitle: 'This is arithmetic, not a measurement.',
    gainHonest:   'We have not yet measured how much ThermalSense raises f. '
                + 'Our hypothesis: a prior map shortens the time spent '
                + 'searching, and every minute saved adds to f. Measuring that '
                + 'on a low-altitude fixed-wing platform is the job of the '
                + 'flight-test milestone.',

    whyTitle:     'Why us.',
    why1:         'Data that grows by itself',
    why1Text:     'Millions of thermal telemetry records are broadcast worldwide '
                + 'every day. A competitor wanting the same dataset would have '
                + 'to fly a fleet.',
    why2:         'Forecasts that audit themselves',
    why2Text:     'Every forecast is written to a tamper-proof ledger before '
                + 'its time and scored afterwards against real flights. Later '
                + 'edits are blocked at the database level. That is the '
                + 'traceability enterprise and public procurement asks for.',
    why3:         'A team that reports what it measured',
    why3Text:     'Every result is reported with its confidence interval, and '
                + 'the experiments that could not prove a gain are on record '
                + 'too. Every figure in this presentation can be traced.',
    why4:         'Runs light',
    why4Text:     'The whole system runs today on a single two-core server; a '
                + 'map takes about half a second.',

    roadTitle:    'Roadmap and milestones',
    roadDone:     'done',
    m0When:       'November 2026',
    m0:           'Live system',
    m0Text:       'Self-training model, quality gate and tamper-proof '
                + 'verification ledger running.',
    m1When:       '2027 · Q1',
    m1:           'Türkiye measurable',
    m1Text:       'An İnönü–Eskişehir regional score reported on its own; the '
                + '6-hour horizon measured in the ledger.',
    m2When:       '2027 · Q2',
    m2:           'Cell-scale accuracy',
    m2Text:       'Full-resolution terrain (+0.02–0.03 AUC measured offline) and '
                + 'satellite land surface temperature in production; hotspot '
                + 'hits above chance at 3 hours.',
    m3When:       '2027 · Q3',
    m3:           'GCS mission package',
    m3Text:       'Map and loiter points exported to the mission files of '
                + 'common GCS software; hardware-in-the-loop simulation.',
    m4When:       '2027 Q4 – 2028 Q1',
    m4:           'Flight test',
    m4Text:       'With a platform partner, measure the flight-time gain on '
                + 'paired flights with and without the map.',
    m5When:       '2028',
    m5:           'Product',
    m5Text:       'Enterprise licence as a GCS plug-in; first customer.',
    askTitle:     'What we are looking for',
    askText:      'Pre-seed investment and a platform partner: a low-altitude '
                + 'fixed-wing UAV for test flights, and the data from those '
                + 'flights. In return, a flight-time gain measured on your own '
                + 'platform.',
    askCta:       'Explore the live system',

    refTitle:     'References',
    refIntro:     'The publications behind the third-party results in this '
                + 'presentation, the thermal model and the timeline.',

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
  if (missing.length) console.warn('[landing] strings missing:', missing.join(', '));
}

export function translateLanding(lang, key, vars) {
  const entry = STRINGS[lang]?.[key] ?? STRINGS[DEFAULT_LANG][key] ?? key;
  if (typeof entry === 'function') return entry(vars ?? {});
  if (!vars) return entry;
  return entry.replace(/\{(\w+)\}/g, (m, name) => (vars[name] ?? m));
}
