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
2. Sadece özel yollarda, kamu ağına değdikleri düğümlerden (giriş kapıları)
   ilk aşamadaki sürelerle başlayan ikinci Dijkstra.

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

## Bilinen hata: ilçe graflarının ayrı sadeleştirilmesi (Faz 1b'den beri)

Yürüme ağı ilçe başına (2 km genişletilmiş) ayrı kurulup OSMnx ile
sadeleştirildikten sonra birleştiriliyor. Bir düğüm bir ilçe grafında ara düğüm
(silinir), komşu ilçenin grafında çıkmaz uç olarak kalabiliyor; birleşik grafta
bu düğüm sadece tek yönden bağlı kalıyor ve süresi olduğundan uzun çıkıyor.

Sadeleştirilmemiş tek parça grafla (534.927 düğüm) aynı kaynak bağlantılarıyla
yapılan karşılaştırma: 7 ilçe içinde ~180 düğümün (düğümlerin %0,15'i) süresi
0,1 dk'dan fazla uzun, ~50'sinde 5 dk'dan fazla (en uç 159 dk). Hata tek yönlü
(hiçbir süre gerçeğinden kısa değil) ve medyanları değiştirmiyor (eczane 7,41 /
7,41 dk). Düzeltme: ağı tek parça kurup bir kez sadeleştirmek.

## Bilerek dokunulmayanlar

- **Bisiklet yolları ve yaya meydanları (`area=yes`):** OSMnx `walk` filtresi
  bunları bilerek dışlıyor; birlikte ~580 düğümü bağlıyorlar. Standart filtreye
  sadık kalındı.
- **Piksel düzeyinde süre:** İçinde yol düğümü olmayan ama nüfusu olan
  altıgenler (~86.000 kişi, nüfusun %1,7'si) süresiz kalıyor. Bunun %80,7'si
  bağlı bir altıgenin bitişiğinde; yani çoğu, bloğu çevreleyen yolların
  düğümlerinin komşu altıgene düşmesinden kaynaklanan geometrik bir etki.
  Pikselleri doğrudan ağa bağlamak bunu kapatır; şimdilik kapsam dışı.
