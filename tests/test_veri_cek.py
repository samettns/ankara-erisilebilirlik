"""src/veri_cek.py için ağ bağlantısı gerektirmeyen birim testleri."""

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from src import veri_cek


def _hata_ver(*args, **kwargs):
    raise AssertionError("Önbellek varken indirme yapılmamalıydı")


def test_dosya_adi_turkce_karakterleri_cevirir():
    assert veri_cek.dosya_adi("Çankaya") == "cankaya"
    assert veri_cek.dosya_adi("Gölbaşı") == "golbasi"
    assert veri_cek.dosya_adi("Yeni Mahalle") == "yeni_mahalle"


def test_yer_sorgusu():
    assert veri_cek.yer_sorgusu("Çankaya") == "Çankaya, Ankara, Türkiye"


def _kucuk_graf():
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=32.85, y=39.90)
    G.add_node(2, x=32.86, y=39.90)
    G.add_edge(1, 2, length=850.0)
    return G


def test_yuruyus_agi_onbellekten_okunur(tmp_path, monkeypatch):
    ox.save_graphml(_kucuk_graf(), tmp_path / "cankaya_walk.graphml")
    monkeypatch.setattr(veri_cek.ox, "graph_from_place", _hata_ver)

    G = veri_cek.yuruyus_agi_indir("Çankaya", veri_dizini=tmp_path)

    assert G.number_of_nodes() == 2
    assert G.number_of_edges() == 1


def test_yuruyus_agi_yoksa_indirir_ve_kaydeder(tmp_path, monkeypatch):
    cagrilar = []

    def sahte_indir(sorgu, network_type):
        cagrilar.append((sorgu, network_type))
        return _kucuk_graf()

    monkeypatch.setattr(veri_cek.ox, "graph_from_place", sahte_indir)

    veri_cek.yuruyus_agi_indir("Çankaya", veri_dizini=tmp_path)

    assert cagrilar == [("Çankaya, Ankara, Türkiye", "walk")]
    assert (tmp_path / "cankaya_walk.graphml").exists()


def _sahte_osm_ozellikleri():
    # osmnx 2.x features_from_place çıktısına benzer: (element, id) MultiIndex
    kare = Polygon([(32.850, 39.900), (32.851, 39.900), (32.851, 39.901), (32.850, 39.901)])
    index = pd.MultiIndex.from_tuples([("node", 1), ("way", 2)], names=["element", "id"])
    return gpd.GeoDataFrame(
        {"name": ["Nokta Eczanesi", None], "amenity": ["pharmacy", "pharmacy"], "opening_hours": [None, "24/7"]},
        geometry=[Point(32.86, 39.91), kare],
        index=index,
        crs="EPSG:4326",
    )


def test_noktalara_cevir_poligonu_centroide_indirger():
    sonuc = veri_cek.noktalara_cevir(_sahte_osm_ozellikleri())

    assert list(sonuc.columns) == veri_cek.TUTULACAK_SUTUNLAR
    assert (sonuc.geometry.geom_type == "Point").all()
    assert sonuc.crs.to_epsg() == 4326
    poligon_merkezi = sonuc.loc[sonuc["id"] == 2].geometry.iloc[0]
    assert poligon_merkezi.x == pytest.approx(32.8505, abs=1e-5)
    assert poligon_merkezi.y == pytest.approx(39.9005, abs=1e-5)
    nokta = sonuc.loc[sonuc["id"] == 1].geometry.iloc[0]
    assert nokta.x == pytest.approx(32.86) and nokta.y == pytest.approx(39.91)


@pytest.mark.parametrize("isim", ["Eczane Temel", "Temel Eczanesi", "TEMEL ECZANESİ", "Temel Eczane."])
def test_isim_normallestir(isim):
    assert veri_cek.isim_normallestir(isim) == "temel"


def test_isim_normallestir_turkce_buyuk_harf_ve_bos():
    assert veri_cek.isim_normallestir("IŞIK ECZANESİ") == "ışık"
    assert veri_cek.isim_normallestir(None) == ""
    assert veri_cek.isim_normallestir(float("nan")) == ""


def test_isim_benzerligi():
    assert veri_cek.isim_benzerligi("Bilir", "Bilir Eczanesi") == 1.0
    assert veri_cek.isim_benzerligi("Oran Bayram Eczanesi", "Oran Gülcem Eczanesi") < 0.85
    assert veri_cek.isim_benzerligi(None, "Temel Eczanesi") == 0.0
    assert veri_cek.isim_benzerligi("Eczane", "Eczanesi") == 0.0  # isim kalmıyor


# 0.0001 derece enlem ≈ 11 m
def _noktalar(kayitlar):
    """[(element, id, isim, boylam, enlem), ...] listesinden GeoDataFrame üretir."""
    return gpd.GeoDataFrame(
        {"element": [k[0] for k in kayitlar], "id": [k[1] for k in kayitlar], "name": [k[2] for k in kayitlar]},
        geometry=[Point(k[3], k[4]) for k in kayitlar],
        crs="EPSG:4326",
    )


def test_tekrarlari_temizle_yakin_ve_ayni_isimliyi_birlestirir():
    gdf = _noktalar([
        ("node", 1, "Eczane Temel", 32.85, 39.9000),
        ("node", 2, "Temel Eczanesi", 32.85, 39.9001),  # 11 m, aynı isim -> tekrar
    ])
    sonuc = veri_cek.tekrarlari_temizle(gdf)
    assert sonuc["id"].tolist() == [1]


def test_tekrarlari_temizle_farkli_isim_veya_uzak_olani_birakir():
    gdf = _noktalar([
        ("node", 1, "Oran Bayram Eczanesi", 32.85, 39.9000),
        ("node", 2, "Oran Gülcem Eczanesi", 32.85, 39.9001),  # 11 m ama farklı isim
        ("node", 3, "Cepa Eczanesi", 32.86, 39.9000),
        ("node", 4, "Cepa Eczanesi", 32.86, 39.9008),  # aynı isim ama ~89 m
        ("node", 5, None, 32.87, 39.9000),
        ("node", 6, None, 32.87, 39.9001),  # isimsizler birleştirilmez
    ])
    sonuc = veri_cek.tekrarlari_temizle(gdf)
    assert sonuc["id"].tolist() == [1, 2, 3, 4, 5, 6]


def test_tekrarlari_temizle_nokta_kaydini_tercih_eder():
    gdf = _noktalar([
        ("way", 10, "Bina Eczanesi", 32.85, 39.9000),
        ("node", 20, "Bina Eczanesi", 32.85, 39.9001),
    ])
    sonuc = veri_cek.tekrarlari_temizle(gdf)
    assert sonuc["id"].tolist() == [20]


def test_tekrarlari_temizle_zincirleme_eslesmeleri_tek_grupta_toplar():
    gdf = _noktalar([
        ("node", 1, "Zincir", 32.85, 39.9000),
        ("node", 2, "Zincir", 32.85, 39.9002),  # 1'e 22 m
        ("node", 3, "Zincir", 32.85, 39.9004),  # 2'ye 22 m, 1'e 44 m
    ])
    assert veri_cek.tekrarlari_temizle(gdf)["id"].tolist() == [1]


def test_tekrarlari_temizle_bos_ve_tek_kayit():
    assert veri_cek.tekrarlari_temizle(_noktalar([])).empty
    assert len(veri_cek.tekrarlari_temizle(_noktalar([("node", 1, "Tek", 32.85, 39.9)]))) == 1


def test_eczaneler_yoksa_indirir_sonra_onbellekten_okur(tmp_path, monkeypatch):
    monkeypatch.setattr(veri_cek.ox, "features_from_place", lambda sorgu, tags: _sahte_osm_ozellikleri())
    ilk = veri_cek.eczaneleri_indir("Çankaya", veri_dizini=tmp_path)
    assert (tmp_path / "cankaya_eczaneler.gpkg").exists()

    monkeypatch.setattr(veri_cek.ox, "features_from_place", _hata_ver)
    ikinci = veri_cek.eczaneleri_indir("Çankaya", veri_dizini=tmp_path)

    assert len(ikinci) == len(ilk) == 2
