"""src/hedefler.py için ağ bağlantısı gerektirmeyen birim testleri."""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from src import hedefler

# 0.0001 derece enlem ≈ 11 m, 0.0001 derece boylam ≈ 8,5 m (Ankara'da)


# --------------------------------------------------------------------------- yardımcılar

def _ham(kayitlar):
    """[{alan: değer, ..., "geometry": geom}, ...] listesinden HAM_SUTUNLAR şemasında tablo üretir."""
    satirlar = []
    for i, k in enumerate(kayitlar):
        satir = {s: None for s in hedefler.HAM_SUTUNLAR}
        satir.update({"element": "node", "id": i + 1})
        satir.update(k)
        satirlar.append(satir)
    return gpd.GeoDataFrame(satirlar, columns=hedefler.HAM_SUTUNLAR, geometry="geometry", crs="EPSG:4326")


def _noktalar(kayitlar):
    """[(element, id, isim, boylam, enlem), ...] listesinden GeoDataFrame üretir."""
    return gpd.GeoDataFrame(
        {"element": [k[0] for k in kayitlar], "id": [k[1] for k in kayitlar], "name": [k[2] for k in kayitlar]},
        geometry=[Point(k[3], k[4]) for k in kayitlar],
        crs="EPSG:4326",
    )


# --------------------------------------------------------------------------- isim ve tekrar

@pytest.mark.parametrize("isim", ["Eczane Temel", "Temel Eczanesi", "TEMEL ECZANESİ", "Temel Eczane."])
def test_isim_normallestir(isim):
    assert hedefler.isim_normallestir(isim) == "temel"


def test_isim_normallestir_turkce_buyuk_harf_ve_bos():
    assert hedefler.isim_normallestir("IŞIK ECZANESİ") == "ışık"
    assert hedefler.isim_normallestir(None) == ""
    assert hedefler.isim_normallestir(float("nan")) == ""


def test_isim_benzerligi():
    assert hedefler.isim_benzerligi("Bilir", "Bilir Eczanesi") == 1.0
    assert hedefler.isim_benzerligi("Oran Bayram Eczanesi", "Oran Gülcem Eczanesi") < 0.85
    assert hedefler.isim_benzerligi(None, "Temel Eczanesi") == 0.0
    assert hedefler.isim_benzerligi("Eczane", "Eczanesi") == 0.0  # isim kalmıyor


def test_tekrarlari_temizle_yakin_ve_ayni_isimliyi_birlestirir():
    gdf = _noktalar([
        ("node", 1, "Eczane Temel", 32.85, 39.9000),
        ("node", 2, "Temel Eczanesi", 32.85, 39.9001),  # 11 m, aynı isim -> tekrar
    ])
    assert hedefler.tekrarlari_temizle(gdf)["id"].tolist() == [1]


def test_tekrarlari_temizle_farkli_isim_veya_uzak_olani_birakir():
    gdf = _noktalar([
        ("node", 1, "Oran Bayram Eczanesi", 32.85, 39.9000),
        ("node", 2, "Oran Gülcem Eczanesi", 32.85, 39.9001),  # 11 m ama farklı isim
        ("node", 3, "Cepa Eczanesi", 32.86, 39.9000),
        ("node", 4, "Cepa Eczanesi", 32.86, 39.9008),  # aynı isim ama ~89 m
        ("node", 5, None, 32.87, 39.9000),
        ("node", 6, None, 32.87, 39.9001),  # isimsizler birleştirilmez
    ])
    assert hedefler.tekrarlari_temizle(gdf)["id"].tolist() == [1, 2, 3, 4, 5, 6]


def test_tekrarlari_temizle_nokta_kaydini_tercih_eder():
    gdf = _noktalar([
        ("way", 10, "Bina Eczanesi", 32.85, 39.9000),
        ("node", 20, "Bina Eczanesi", 32.85, 39.9001),
    ])
    assert hedefler.tekrarlari_temizle(gdf)["id"].tolist() == [20]


def test_tekrarlari_temizle_zincirleme_eslesmeleri_tek_grupta_toplar():
    gdf = _noktalar([
        ("node", 1, "Zincir", 32.85, 39.9000),
        ("node", 2, "Zincir", 32.85, 39.9002),
        ("node", 3, "Zincir", 32.85, 39.9004),
    ])
    assert hedefler.tekrarlari_temizle(gdf, esik_m=30)["id"].tolist() == [1]


def test_tekrarlari_temizle_bos_ve_tek_kayit():
    assert hedefler.tekrarlari_temizle(_noktalar([])).empty
    assert len(hedefler.tekrarlari_temizle(_noktalar([("node", 1, "Tek", 32.85, 39.9)]))) == 1


# --------------------------------------------------------------------------- nokta / ham / ilçe

def test_noktalara_cevir_poligonu_centroide_indirger():
    kare = Polygon([(32.850, 39.900), (32.851, 39.900), (32.851, 39.901), (32.850, 39.901)])
    index = pd.MultiIndex.from_tuples([("node", 1), ("way", 2)], names=["element", "id"])
    ham = gpd.GeoDataFrame({"name": ["Nokta", None], "amenity": ["pharmacy"] * 2},
                           geometry=[Point(32.86, 39.91), kare], index=index, crs="EPSG:4326")
    sonuc = hedefler.noktalara_cevir(ham)

    assert list(sonuc.columns) == hedefler.NOKTA_SUTUNLARI
    assert (sonuc.geometry.geom_type == "Point").all()
    merkez = sonuc.loc[sonuc["id"] == 2].geometry.iloc[0]
    assert merkez.x == pytest.approx(32.8505, abs=1e-5) and merkez.y == pytest.approx(39.9005, abs=1e-5)


def test_ham_ozellikleri_hazirla_sema_ve_tipler():
    index = pd.MultiIndex.from_tuples([("node", 5)], names=["element", "id"])
    gdf = gpd.GeoDataFrame({"name": ["X"], "amenity": ["pharmacy"], "beds": ["10"], "light_rail": [float("nan")]},
                           geometry=[Point(32.85, 39.9)], index=index, crs="EPSG:4326")
    ham = hedefler.ham_ozellikleri_hazirla(gdf)
    assert list(ham.columns) == hedefler.HAM_SUTUNLAR
    assert ham.loc[0, "element"] == "node" and ham.loc[0, "id"] == 5
    assert ham.loc[0, "light_rail"] is None and ham.loc[0, "railway"] is None


def test_ilce_ekle():
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı", "Doğu"]}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326")
    gdf = _noktalar([("node", 1, "a", 0.5, 0.5), ("node", 2, "b", 1.5, 0.5), ("node", 3, "c", 5, 5)])
    assert hedefler.ilce_ekle(gdf, sinirlar)["ilce"].tolist() == ["Batı", "Doğu", None]


# --------------------------------------------------------------------------- eczane

def test_eczaneleri_ayikla_sadece_eczane_ve_tekrarsiz():
    ham = _ham([
        {"name": "Temel Eczanesi", "amenity": "pharmacy", "geometry": Point(32.85, 39.9000)},
        {"name": "Eczane Temel", "healthcare": "pharmacy", "geometry": Point(32.85, 39.9001)},
        {"name": "Şifa Hastanesi", "amenity": "hospital", "geometry": Point(32.86, 39.9)},
    ])
    sonuc = hedefler.eczaneleri_ayikla(ham)
    assert sonuc["id"].tolist() == [1]


# --------------------------------------------------------------------------- hastane

def _metrik_kare(kenar_m):
    return Polygon([(0, 0), (kenar_m, 0), (kenar_m, kenar_m), (0, kenar_m)])


def test_sinir_ornekle_aralik_ve_sinir_ustunde():
    kare = _metrik_kare(100)  # çevre 400 m
    noktalar = hedefler.sinir_ornekle(kare, 50)
    assert len(noktalar) == 8
    assert all(kare.exterior.distance(n) < 1e-9 for n in noktalar)


def test_sinir_ornekle_kucuk_poligon_nokta_ve_multipolygon():
    assert len(hedefler.sinir_ornekle(_metrik_kare(5), 50)) == 1  # en az bir nokta
    assert hedefler.sinir_ornekle(Point(3, 4), 50) == [Point(3, 4)]
    iki = MultiPolygon([_metrik_kare(100), Polygon([(500, 0), (600, 0), (600, 100), (500, 100)])])
    assert len(hedefler.sinir_ornekle(iki, 50)) == 16


def test_hastaneleri_ayikla():
    kampus = box(32.850, 39.900, 32.853, 39.902)  # ~255 m x 222 m
    ham = _ham([
        {"element": "way", "id": 100, "name": "Şehir Hastanesi", "amenity": "hospital",
         "operator:type": "government", "geometry": kampus},
        {"id": 101, "name": "Şehir Hastanesi Acil", "amenity": "hospital", "geometry": Point(32.851, 39.901)},  # kampüs içinde
        {"id": 102, "name": "Özel Can Hastanesi", "amenity": "hospital", "operator:type": "private",
         "geometry": Point(32.87, 39.91)},
        {"id": 103, "name": "Ağız ve Diş Sağlığı Hastanesi", "amenity": "hospital", "geometry": Point(32.88, 39.91)},
        {"id": 104, "name": "Diş Kliniği", "amenity": "hospital", "healthcare:speciality": "dentistry",
         "geometry": Point(32.89, 39.91)},
        {"id": 105, "name": "Merkez Eczanesi", "amenity": "pharmacy", "geometry": Point(32.86, 39.91)},
    ])
    tesisler, kaynaklar = hedefler.hastaneleri_ayikla(ham, aralik_m=50)

    assert sorted(tesisler["id"]) == [100, 102]
    assert (tesisler.geometry.geom_type == "Point").all()
    kampus_satiri = tesisler[tesisler["id"] == 100].iloc[0]
    assert kampus.contains(kampus_satiri.geometry)
    assert kampus_satiri["alan_m2"] > 40000
    assert tesisler.set_index("id").loc[102, "operator:type"] == "private"

    kampus_kaynak = kaynaklar[kaynaklar["tesis_id"] == 100]
    assert len(kampus_kaynak) >= 18  # ~950 m çevre / 50 m
    assert max(kampus.exterior.distance(n) for n in kampus_kaynak.geometry) < 1e-6
    assert len(kaynaklar[kaynaklar["tesis_id"] == 102]) == 1
    assert set(kaynaklar["tesis_id"]) == {100, 102}


# --------------------------------------------------------------------------- birinci basamak

def test_birinci_basamak_ayikla():
    ham = _ham([
        {"id": 1, "name": "Kızılay 5 Nolu Aile Sağlığı Merkezi", "amenity": "clinic", "geometry": Point(32.85, 39.90)},
        {"id": 2, "name": "Dikmen Sağlık Ocağı", "amenity": "doctors", "healthcare": "doctor", "geometry": Point(32.86, 39.90)},
        {"id": 3, "name": "Özel Merkez Polikliniği", "healthcare": "clinic", "geometry": Point(32.87, 39.90)},
        {"id": 4, "name": "Gülüş Ağız ve Diş Polikliniği", "amenity": "clinic", "geometry": Point(32.88, 39.90)},
        {"id": 5, "name": "Hayat Fizik Tedavi Merkezi", "amenity": "clinic", "geometry": Point(32.89, 39.90)},
        {"id": 6, "name": "Tahlil Lab", "amenity": "clinic", "healthcare": "laboratory", "geometry": Point(32.90, 39.90)},
        {"id": 7, "name": "Klinik X", "amenity": "clinic", "healthcare:speciality": "dentistry", "geometry": Point(32.91, 39.90)},
        {"id": 8, "name": "Yaşam Hastanesi", "amenity": "hospital", "healthcare": "hospital", "geometry": Point(32.92, 39.90)},
        {"id": 9, "name": "Can Diyaliz Merkezi", "healthcare": "centre", "geometry": Point(32.93, 39.90)},
    ])
    sonuc = hedefler.birinci_basamak_ayikla(ham)
    assert sonuc["id"].tolist() == [1, 2, 3]
    assert sonuc["asm"].tolist() == [True, True, False]


# --------------------------------------------------------------------------- raylı sistem

def _hat_json():
    """Sahte Overpass yanıtı: M1, Ankaray (A), Başkentray (B1) ve durak alanları."""
    return {"elements": [
        {"type": "relation", "id": 1, "tags": {"type": "route", "route": "subway", "ref": "M1"},
         "members": [{"type": "node", "ref": 901, "role": "stop"}, {"type": "node", "ref": 11, "role": "stop"}]},
        {"type": "relation", "id": 2, "tags": {"type": "route", "route": "light_rail", "ref": "A"},
         "members": [{"type": "node", "ref": 902, "role": "stop"}]},
        {"type": "relation", "id": 3, "tags": {"type": "route", "route": "train", "ref": "B1"},
         "members": [{"type": "node", "ref": 903, "role": "stop"}, {"type": "node", "ref": 904, "role": "stop"}]},
        # Kızılay durak alanı hem M1 hem Ankaray durağını içeriyor
        {"type": "relation", "id": 50, "tags": {"public_transport": "stop_area"},
         "members": [{"type": "node", "ref": 10}, {"type": "node", "ref": 901}, {"type": "node", "ref": 902}]},
        {"type": "relation", "id": 51, "tags": {"public_transport": "stop_area"},
         "members": [{"type": "node", "ref": 20}, {"type": "node", "ref": 903}]},
        {"type": "node", "id": 901, "lat": 39.9200, "lon": 32.8500},
        {"type": "node", "id": 902, "lat": 39.9201, "lon": 32.8501},
        {"type": "node", "id": 903, "lat": 39.9300, "lon": 32.8600},
        {"type": "node", "id": 904, "lat": 39.9400, "lon": 32.8700},  # stop_area'sı olmayan durak
        {"type": "node", "id": 11, "lat": 39.9500, "lon": 32.8800},
    ]}


def _istasyon_ham():
    return _ham([
        {"id": 10, "name": "Kızılay", "railway": "station", "station": "subway", "light_rail": "yes", "geometry": Point(32.8500, 39.9200)},
        {"id": 11, "name": "Doğrudan Üye", "railway": "station", "station": "subway", "geometry": Point(32.8800, 39.9500)},
        {"id": 20, "name": "Kurtuluş", "railway": "station", "train": "yes", "geometry": Point(32.8600, 39.9300)},
        {"id": 21, "name": "Yakın Durak", "railway": "station", "train": "yes", "geometry": Point(32.8700, 39.9405)},  # 904'e ~55 m
        {"id": 22, "name": "Ankara YHT", "railway": "station", "train": "yes", "geometry": Point(32.9000, 39.9700)},
        # Kurtuluş'un B1 durağına (903, stop_area 51'de) ~40 m, ama durak başka istasyonun
        {"id": 25, "name": "Yan İstasyon", "railway": "station", "train": "yes", "geometry": Point(32.8604, 39.9302)},
        {"id": 23, "name": "Etiketsiz Metro", "railway": "station", "station": "subway", "geometry": Point(32.9100, 39.9800)},
        {"id": 24, "name": "Gelecek", "railway": "proposed", "station": "subway", "geometry": Point(32.9200, 39.9900)},
        {"id": 30, "name": None, "railway": "subway_entrance", "geometry": Point(32.8502, 39.9201)},  # Kızılay girişi
        {"id": 31, "name": None, "railway": "subway_entrance", "geometry": Point(32.8498, 39.9199)},  # Kızılay girişi
        {"id": 32, "name": None, "railway": "subway_entrance", "geometry": Point(33.5, 40.5)},  # hiçbir istasyona yakın değil
    ])


def test_istasyon_hatlari_uyelik_stop_area_ve_mesafe():
    ham = _istasyon_ham()
    istasyonlar = hedefler.noktalara_cevir(ham[ham["railway"] == "station"],
                                           ["element", "id", "name", "station", "train", "geometry"])
    hatlar = hedefler.istasyon_hatlari(_hat_json(), istasyonlar)
    assert hatlar[10] == {"M1", "A"}  # stop_area üzerinden iki hat
    assert hatlar[11] == {"M1"}  # doğrudan üye
    assert hatlar[20] == {"B1"}
    assert hatlar[21] == {"B1"}  # mesafe yedeği, tren istasyonu -> tren hattı
    assert hatlar[22] == set() and hatlar[23] == set()
    assert hatlar[25] == set()  # başka stop_area'nın durağı yedek kuralda kullanılmaz


def test_rayli_ayikla_sistemler_ve_kaynaklar():
    istasyonlar, kaynaklar = hedefler.rayli_ayikla(_istasyon_ham(), _hat_json())
    ist = istasyonlar.set_index("id")

    assert sorted(ist.index) == [10, 11, 20, 21, 23]  # YHT ve proposed dışarıda
    assert ist.loc[10, "hatlar"] == "A,M1"
    assert ist.loc[10, ["metro", "ankaray", "baskentray"]].tolist() == [True, True, False]
    assert ist.loc[20, ["metro", "ankaray", "baskentray"]].tolist() == [False, False, True]
    assert ist.loc[23, ["metro", "ankaray", "hatlar"]].tolist() == [True, False, ""]  # etiketten metro

    kizilay = kaynaklar[kaynaklar["istasyon_id"] == 10]
    assert len(kizilay) == 2 and set(kizilay["tur"]) == {"giris"}
    assert set(kaynaklar[kaynaklar["istasyon_id"] == 20]["tur"]) == {"istasyon"}
    assert len(kaynaklar) == 2 + 4  # Kızılay'ın 2 girişi + diğer 4 istasyonun kendisi
