# Teknik doğrulama notu: deniz ve kara üzerinde termal, irtifa profili

TUSAŞ HANGAR BİGG mentör görüşmesinden (Kasım 2026) çıkan araştırma maddesi.
Durum: **açık**. Bu not, bulunanı ve henüz bulunamayanı ayrı yazar; kaynağı
olmayan hiçbir rakam yoktur.

## 1. Deniz ve kara üzerinde termal farkı

### Literatürün söylediği

- **Deniz üzerinde sınır tabakası sığdır.** Sınır tabakası derinliğini büyük
  ölçüde yüzeyin Bowen oranı (duyulur ısı / gizli ısı) belirler: okyanusta
  ~0,05, karada tipik olarak ~0,25'ten başlar. Deniz yüzeyinden atmosfere
  verilen duyulur ısı akısı çok küçüktür; termali besleyen de bu akıdır.
  ([Wisconsin Üniv. AOS, 2015 (PDF)](https://www.aos.wisc.edu/~lback/SBR_ZHLB_2015.pdf);
  [ACP 22, 7727, 2022](https://acp.copernicus.org/articles/22/7727/2022/))
- **Deniz sınır tabakası yarı-durağandır; kara sınır tabakası günlük döngüyle
  büyür ve söner.** Termal "günü" kavramı karaya aittir.
  ([Zheng ve ark., JAS 2020](https://www.atmos.umd.edu/~zli/PDF_papers/Zheng_et_al-JAS-2020.pdf))
- **Kıyıda bastırma.** Doğu Akdeniz'de (İsrail kıyısı) yazın serin ve nemli
  deniz havası kıyı şeridinde konveksiyonu zayıflatır; kıyıdaki sınır tabakası
  ~250 m alçalır. İç kesimde termaller tabakayı büyütmeye devam eder; deniz
  meltemi cephesi öğleden sonraya kadar ~30–50 km içeri ilerler. Kıyıdan
  7,5 km içerideki Bet Dagan'da ortalama ~900 m AGL ölçülmüş.
  ([Uzan ve ark., ACP 2020](https://acp.copernicus.org/articles/20/12177/2020/))
- **Deniz meltemi cephesi bir kaldırma hattıdır.** Cephedeki yükselişler kara
  tarafındaki termallerden daha geniş ama benzer şiddettedir.
  ([ACP 22, 7727, 2022](https://acp.copernicus.org/articles/22/7727/2022/))

### ThermalSense için anlamı

1. **Eğitim verisi yalnızca karadan.** Etiketler, karada uçan süzülen hava
   araçlarının termal telemetrisinden gelir. Açık deniz üzerindeki bir hücre
   için model eğitildiği rejimin dışında tahmin yapar. Yapılacak: `/predict`'in
   açık denizdeki bir noktada ne döndürdüğünü kontrol etmek (Copernicus deniz
   üzerinde karo içermez; yükseklik `None` → OpenTopoData yedeği) ve deniz
   hücrelerini maskelemek ya da düşük güvenle işaretlemek.
2. **Kıyı görevleri.** Kıyı gözetimi pitch'te bir görev profili olarak geçiyor.
   Literatüre göre kıyı şeridinin hemen üzerinde termal zayıftır; kazanç, meltem
   cephesinin içeride oluşturduğu hatta ve kıyıdan birkaç km içeride olur.
   Sunumda "kıyı üzerinde termal" iddiası yapılmamalı; "kıyı bölgesinde, kara
   tarafında" denmeli.
3. **Aday özellik:** kıyıya uzaklık ve kara/deniz maskesi. Önce
   `evaluation/` ile ölçülmeli; bugün ölçülmüş bir kazancı yok.

## 2. "0 m'den 3000 + 3000 m" İsrail operasyon profili

**Netleşti (17 Kasım 2026).** Mentörün sorusu, haritadaki yüksekliklerin deniz
seviyesinden (MSL) mi yoksa yerden (AGL) mi ölçüldüğüydü: kıyıda 0 m'den kalkan
bir araçla 3000 m'lik bir arazinin 3000 m üstünde uçan bir araç, ikisi de
"3000 m" yazsa bile bambaşka yerlerdedir.

Yapılan: haritadaki her yükseklik artık referansını söyler. Tahmin yüksekliği
`m MSL` (altında yerden `m AGL`), termal taban `m AGL` ve ayrıca alanın
ortalama arazisi üzerine `≈ m MSL` karşılığıyla gösterilir; araç kartı zaten
MSL / zemin / AGL'yi ayrı veriyordu. Sunumun mimari bölümüne, görev paketinin
her yüksekliği iki biçimde verdiğini söyleyen bir satır eklendi. Termal taban
hâlâ yalnızca yüzey sıcaklığından türetilen kaba bir tahmindir.

İsrail'e özgü bir operasyon profili için yayımlanmış bir kaynak aranmış,
bulunamamıştır; sunuma konmadı.

Bağlam için bilinen: İsrail kıyı ovasında yaz sınır tabakası ~1 km mertebesinde
(yukarıdaki Bet Dagan ölçümü), yani deniz seviyesinden kalkan bir aracın
termalden yararlanabileceği katman kıyıda bunun altında kalır. ThermalSense'in
tahmin irtifası `alt` parametresiyle verilir ve eğitim örnekleri 200 m AGL ile
tahmini termal tabanı + 300 m arasıyla sınırlıdır (bkz. `CLAUDE.md`, "Rejected
samples"); 3000 m'nin üstü modelin gördüğü rejim değildir.

## 3. Rakip taraması notu

- **Quadrobotics** (Ankara, AATG Holding şirketi) incelendi: dört bacaklı kara
  robotları üretiyor; termal tahmini ya da İHA alanında ürünü görülmedi. Rakip
  tablosuna eklenmedi. Mentörün kastettiği başka bir ürünü varsa
  netleştirilmeli.
  ([SAHA EXPO katılımcı sayfası](https://www.sahaexpo.com/en/exhibitor/quadrobotics);
  [International Defence Analysis](https://internationaldefenceanalysis.com/quadrobotics-to-unveil-arthropod-robots-at-saha-expo-2024/))
- Tabloya girenler: SkySight, TopMeteo (pilotlara süzülme tahmini), Windy /
  meteoblue (genel hava), ArduSoar (uçuşta termal tespiti, açık kaynak),
  geleneksel yöntem. Fiyatlar satıcı sayfalarından, 17 Kasım 2026.

## 4. Tahmin ufku tablosu

Doğruluk-ufuk tablosu canlı doğrulama defterinden alındı
(`/verification/summary?days=30`, 17 Kasım 2026) ve `/about` sayfasında
yayımlandı. Defter 15 dk–3 sa ufukları kaydediyordu; 6 saatlik ufuk
(`LEDGER_HORIZONS_MIN` içine 360) aynı gün eklendi. İlk 6 sa sonuçları birikene
kadar sayfada "ölçülüyor" yazar.
