"""Proje genelinde kullanılan yollar, sabitler ve OSMnx ayarları."""

from pathlib import Path

import osmnx as ox

# Proje kökü: bu dosyanın bir üst klasörü (src/..)
PROJE_KOKU = Path(__file__).resolve().parent.parent
VERI_DIZINI = PROJE_KOKU / "data"
ONBELLEK_DIZINI = VERI_DIZINI / "cache"

# Analiz sabitleri
YURUME_HIZI_M_DK = 80
H3_COZUNURLUK = 9
AG_TIPI = "walk"

# Tekrar eden OSM kayıtlarını birleştirme eşikleri
TEKRAR_ESIK_M = 50 # bu mesafeden yakın ...
ISIM_BENZERLIK_ESIGI = 0.85  # ... ve isimleri bu kadar benzeyen kayıtlar aynı yer sayılır


def osmnx_ayarla(onbellek_dizini: Path = ONBELLEK_DIZINI) -> None:
    """OSMnx'i HTTP yanıtlarını verilen klasörde önbellekleyecek şekilde ayarlar.

    Aynı Overpass/Nominatim sorgusu tekrar yapıldığında yanıt internetten
    değil, bu klasörden okunur.
    """
    onbellek_dizini.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(onbellek_dizini)
    ox.settings.log_console = False
