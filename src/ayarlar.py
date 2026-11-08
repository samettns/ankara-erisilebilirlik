"""Proje genelinde kullanılan yollar ve sabitler."""

from pathlib import Path

# Proje kökü: bu dosyanın bir üst klasörü (src/..)
PROJE_KOKU = Path(__file__).resolve().parent.parent
VERI_DIZINI = PROJE_KOKU / "data"
OSM_DIZINI = VERI_DIZINI / "osm"  # Türkiye PBF ve Ankara kesiti
HAM_DIZINI = VERI_DIZINI / "ham"  # kesitten okunmuş ham hedef özellikleri ve hatlar
HEDEF_DIZINI = VERI_DIZINI / "hedefler"  # temizlenmiş hedef katmanları
SONUC_DIZINI = VERI_DIZINI / "sonuclar"  # düğüm başına yürüme süreleri ve raporlar
GECICI_DIZIN = VERI_DIZINI / "tmp"  # geçici .osm XML dosyaları; iş bitince silinir
SINIR_DOSYASI = VERI_DIZINI / "sinirlar.gpkg"
BIRLESIK_AG_DOSYASI = VERI_DIZINI / "ankara_walk.graphml"
VERI_SURUMU_DOSYASI = PROJE_KOKU / "veri_surumu.json"  # git'e girer, README için
YAYIN_HARITA_DOSYASI = PROJE_KOKU / "docs" / "index.html"  # GitHub Pages kopyası, git'e girer

# Yerel OSM verisi
GEOFABRIK_URL = "https://download.geofabrik.de/europe/turkey-latest.osm.pbf"
# Türkiye dosyasından kesilecek kutu (batı, güney, doğu, kuzey). 7 ilçenin sınır
# kutusu ~(32.23, 39.62, 33.17, 40.20); 2 km genişletme ~0.025° eder, kutu bunu bolca kapsar.
# Genişletilmiş alan kutudan taşarsa veri_cek hata verir.
KESIT_KUTUSU = (32.10, 39.50, 33.30, 40.32)
# Türkiye geçişinde düğüm konumları için depo. sparse_file_array diskte tutar, RAM'i yormaz.
KONUM_DEPOSU = "sparse_file_array," + str(OSM_DIZINI / "konumlar.tmp")

# Nüfus: WorldPop Global2 R2025A v1, sınırlandırılmış (constrained), 100 m, 2025
NUFUS_DIZINI = VERI_DIZINI / "nufus"
NUFUS_YILI = 2025
WORLDPOP_URL = ("https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/TUR/v1/100m/"
                "constrained/tur_pop_2025_CN_100m_R2025A_v1.tif")
SOLUK_NUFUS_ESIGI = 10  # bundan az nüfuslu altıgenler haritada soluk

# Çalışma alanı
ILCELER = ["Çankaya", "Keçiören", "Yenimahalle", "Mamak", "Etimesgut", "Sincan", "Altındağ"]
GENISLETME_M = 2000  # sınırdakilerin komşu ilçedeki hedeflere ulaşabilmesi için

# Analiz sabitleri
YURUME_HIZI_M_DK = 80
BAGLANTI_ESIGI_M = 200  # hedef noktası en yakın ağ düğümüne bundan uzaksa analize girmez
ESIK_DK = 15  # "15 dakikalık şehir" eşiği
H3_COZUNURLUK = 9
AG_TIPI = "walk"

# Tekrar eden OSM kayıtlarını birleştirme eşikleri
TEKRAR_ESIK_M = 50  # bu mesafeden yakın ...
ISIM_BENZERLIK_ESIGI = 0.85  # ... ve isimleri bu kadar benzeyen kayıtlar aynı yer sayılır

# Hedefler
KAMPUS_ORNEK_ARALIGI_M = 50  # hastane kampüs sınırında kaynak noktası aralığı
GIRIS_ISTASYON_ESIGI_M = 300  # metro girişi en fazla bu kadar uzaktaki istasyona bağlanır
HAT_DURAK_ESIGI_M = 100  # stop_area ile bağlanamayan istasyon için hat durağına mesafe

# İndirme (sadece Geofabrik)
TEKRAR_DENEME_SAYISI = 4
ILK_BEKLEME_SN = 60  # sonraki denemelerde ikiye katlanır: 60, 120, 240
