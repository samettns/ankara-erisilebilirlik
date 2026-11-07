# Yöntem notları ve bilinen sınırlamalar

## Ağa bağlı olmayan parçalar: `sidewalk=separate` değil, `access=private`

Faz 4'te nüfusu olduğu halde ana yürüme ağına bağlı olmayan altıgenler bulundu
(88 altıgen, ~26.500 kişi; çoğu Çankaya'da Maltepe–Anıtkabir çevresi). İlk
hipotez, kaldırımları ayrı çizilmiş caddelerin (`sidewalk=separate`) OSMnx'in
`walk` filtresinde dışlanıp mahalleleri kopardığıydı. **Bu hipotez yanlış çıktı.**

Kontrol yöntemi: Ankara kesitindeki yürüme yolları sadeleştirilmeden ağa
dönüştürüldü (832.729 düğüm, 712 bağlı parça; ana parça düğümlerin %98,2'si).
Sonra `walk` filtresinin dışladığı her yol için, o yolun ana parçayı bir küçük
parçaya bağlayıp bağlamadığına ("köprü yol") bakıldı.

| Dışlanma nedeni | Köprü yol | Bağlanacak küçük parça | Düğüm |
|---|---:|---:|---:|
| `access=private` | 199 | 202 | 5.719 |
| `highway=cycleway` | 25 | 29 | 289 |
| `area=yes` (yaya meydanları) | 31 | 37 | 287 |
| `highway=motorway_link` | 10 | 8 | 230 |
| `highway=construction` | 5 | 4 | 80 |
| `foot=no` | 8 | 6 | 72 |
| `highway=motorway` | 5 | 5 | 17 |
| `sidewalk:right=separate` | 1 | 1 | 5 |
| `highway=proposed` | 3 | 1 | 2 |
| **Toplam** | | **288** | **6.683** |

- Tüm kesitte `sidewalk*=separate` yüzünden dışlanan yol yalnızca 13 tane
  (~4 km); bunlardan sadece biri köprü konumunda (5 düğüm). Ankara'da ayrı
  çizilmiş kaldırım var (`footway=sidewalk`, 1.632 yol), ama ağı koparmıyor.
- Ağa bağlı olmayan en kalabalık 10 altıgenin 7'sinde `access=private` yol var.
  Köprü konumundaki özel yolların çoğu site içi yol: 122'si `highway=service`,
  56'sı `highway=residential`.
- Küçük parçalardaki 15.046 düğümün ~8.400'ünü hiçbir dışlanan yol bağlamıyor:
  bunlar OSM'de gerçekten kopuk çizilmiş ve analizde düzeltilmiyor.

Analiz tarihi: Kasım 2026, OSM verisi 2026-11-05 (`veri_surumu.json`).

## Yöntem kararı: konut sitelerindeki özel yollar "son kilometre" olarak

`access=private` olan ve düğümlerinin en az yarısı bir `landuse=residential`
alanında kalan yürünebilir yollar (`service`, `residential`, `footway`,
`living_street`, `pedestrian`) ağa eklenir. Site sakinleri bu yolları kullanır,
ama site dışındakiler siteyi kestirme olarak kullanamaz. Bu yüzden süreler iki
aşamada hesaplanır:

1. Sadece kamuya açık ağda Dijkstra (tesislerden).
2. Kamu ağına değdikleri düğümlerden (giriş kapıları) ilk aşamadaki
   sürelerle başlayan ikinci Dijkstra. Bu aşama özel yolları, bir kamu
   kenarına paralel giden özel bağlantıları ve yalnızca site içinden
   girilebilen kamu "ceplerini" (ör. site içindeki etiketsiz yaya yolu) gezer;
   ilk aşamada ulaşılmış iki kamu düğümü arasındaki kamu kenarlarına girmez.

Böylece kamuya açık düğümlerin süreleri değişmez; yalnızca site içindeki
düğümler süre kazanır. Tesisler ağa sadece kamuya açık kenarı olan düğümlerden
bağlanır.

Askerî alan, kampüs, sanayi vb. içindeki özel yollar dışarıda kalır.

### Sonuç: etkisi küçük, çünkü kopuk nüfus konut sitesinde değil

Kesitteki 1.866 özel yolun 1.334'ü konut alanında kaldı ve eklendi (2.586 kenar);
1.787 düğüm süresini site içi yoldan aldı. Önce/sonra:

| Metrik | Önce | Sonra | Fark |
|---|---:|---:|---:|
| Ağ parça sayısı | 557 | 459 | −98 |
| Ana parça düğümü | 122.162 | 124.884 | +2.722 |
| Ağa bağlı olmayan altıgen | 95 | 94 | −1 |
| Nüfus: ağa bağlı olmayan altıgende | 26.550 | 26.521 | −29 |
| Nüfus: süresi bilinen | 4.890.773 | 4.890.882 | +109 |

Nüfus ağırlıklı medyanlar 0,1 dk'dan az değişti; 15 dk dışında kalan nüfus en
fazla 0,1 puan oynadı. Nedeni: ağa bağlı olmayan en kalabalık 10 altıgen konut
sitesi değil, OSM'de şu alanların içinde:

| Alan | Altıgen |
|---|---:|
| `landuse=military` Kara Harp Okulu | 5 |
| `landuse=military` M.S.B. Lojmanları | 2 |
| `landuse=military` Hava Kuvvetleri Komutanlığı | 1 |
| `amenity=university` Ankara Üniversitesi Tandoğan Yerleşkesi | 1 |
| `leisure=garden` Çankaya Köşkü | 1 |

Bu yerleşkelerin yolları gerçekten halka kapalı; WorldPop'un buraya yazdığı
nüfus (yurt, lojman) yerleşke içinden dışarıya kendi kapılarıyla çıkar ve
OSM'de bu kapılar ağa bağlı değil. Model bunları bilerek dışarıda bırakıyor.
Lojmanların (ör. M.S.B. Lojmanları) konut gibi ele alınması ayrı bir karar
gerektirir.

Küçük bir yan etki: site içi düğümler altıgen medyanına katıldığı için bazı
altıgenlerin medyanı hafifçe uzadı (site içi, kapıdan uzaktır); bu yüzden 15 dk
dışında kalan sayısı birkaç bin kişi arttı (ör. Çankaya hastane +2.135).

## Bulunan ve düzeltilen hata: ilçe graflarının ayrı sadeleştirilmesi

**Hata (Faz 1b – Faz 4b):** Yürüme ağı ilçe başına (2 km genişletilmiş) ayrı
kurulup OSMnx ile sadeleştirildikten sonra birleştiriliyordu. Bir düğüm bir ilçe
grafında ara düğüm olarak siliniyor, komşu ilçenin grafında (o alana yollardan
yalnızca biri girdiği için) çıkmaz uç olarak kalıyordu. Birleşik grafta bu düğüm
tek yönden bağlı kalıyor ve süresi olduğundan uzun çıkıyordu.

Büyüklüğü (sadeleştirilmemiş tek parça grafla, 534.927 düğüm, aynı kaynak
bağlantılarıyla): 7 ilçe içinde ~180 düğümün (%0,15) süresi 0,1 dk'dan fazla
uzun, ~50'sinde 5 dk'dan fazla (en uç 159 dk). Tek yönlüydü ve medyanları
değiştirmiyordu.

**Düzeltme:** Ağ artık 7 ilçenin genişletilmiş birleşik alanı için tek parça
kurulup bir kez sadeleştiriliyor (`veri_cek.birlesik_agi_kur`, ağ sürümü 3;
tepe bellek ~2,3 GB). Doğrulama sırasında iki ek tutarsızlık bulunup düzeltildi:

- Bir kamu kenarına paralel daha kısa bir özel bağlantı varsa özel olan
  atılıyordu; artık `ozel_length` olarak saklanıp site içi (2.) aşamada
  kullanılıyor (40 düğüm, en fazla 0,59 dk).
- Yalnızca site içinden girilebilen kamu yolları (164 düğüm) hiç süre
  almıyordu; 2. aşama artık bu "cepleri" de geziyor.

**Doğrulama:** Sadeleştirilmiş ağdaki 124.633 düğümün tamamında, beş kategoride
de süreler sadeleştirilmemiş ağla aynı (en büyük fark 1·10⁻¹² dk; bir grafta
süresi olup diğerinde olmayan düğüm yok). Bu durumları yakalayan testler
`tests/test_veri_cek.py` ve `tests/test_mesafe.py` içinde; eski yöntemin hata
senaryosunda gerçekten yanlış sonuç verdiği de testte doğrulanıyor.

**Sonuçlara etkisi (Faz 4b → düzeltme, 7 ilçe, nüfus ağırlıklı):** Medyanlar
değişmedi (raylı 35,18 → 35,16 dk). 15 dk dışında kalan nüfus azaldı: raylı
−2.116, hastane −1.109, eczane −255 kişi; en büyük ilçe farkları Etimesgut
(raylı −1.176) ve Yenimahalle (hastane −1.033, raylı −880). Oranlar en fazla
0,04 puan değişti.

## Bilerek dokunulmayanlar

- **Askerî lojmanlar (ör. M.S.B. Lojmanları):** OSM'de `landuse=military` olduğu
  için konut sitesi sayılmıyor; yolları ağa eklenmiyor, gerçek bir konut nüfusu
  olsa da bu altıgenler "ağa bağlı değil" kalıyor.

- **Bisiklet yolları ve yaya meydanları (`area=yes`):** OSMnx `walk` filtresi
  bunları bilerek dışlıyor; birlikte ~580 düğümü bağlıyorlar. Standart filtreye
  sadık kalındı.
- **Piksel düzeyinde süre:** İçinde yol düğümü olmayan ama nüfusu olan
  altıgenler (~86.000 kişi, nüfusun %1,7'si) süresiz kalıyor. Bunun %80,7'si
  bağlı bir altıgenin bitişiğinde; yani çoğu, bloğu çevreleyen yolların
  düğümlerinin komşu altıgene düşmesinden kaynaklanan geometrik bir etki.
  Pikselleri doğrudan ağa bağlamak bunu kapatır; şimdilik kapsam dışı.
