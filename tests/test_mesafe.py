"""src/mesafe.py için testler: elle hesaplanabilen oyuncak graflar."""

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point, box

from src import mesafe

# Ankara enleminde (39.9°) 80 m ≈ 0.000937° boylam, 40 m ≈ 0.00036° enlem
ENLEM = 39.9
ADIM = 0.000937
BOYLAM0 = 32.80
METRE_ENLEM = 1 / 111_000  # 1 m'nin derece karşılığı (enlem)


def _konum(i):
    """Hattaki i. düğümün (boylam, enlem) konumu."""
    return BOYLAM0 + (i - 1) * ADIM, ENLEM


def oyuncak_graf():
    """1-2-3-4-5 hattı (kenarlar 80 m = 1 dk) ve 3'ün ~100 m kuzeyinde bağlantısız 8-9 adası."""
    G = nx.Graph()
    for i in range(1, 6):
        x, y = _konum(i)
        G.add_node(i, x=x, y=y)
    for u in range(1, 5):
        G.add_edge(u, u + 1, length=80.0)
    x3, _ = _konum(3)
    G.add_node(8, x=x3, y=ENLEM + 100 * METRE_ENLEM)
    G.add_node(9, x=x3 + ADIM, y=ENLEM + 100 * METRE_ENLEM)
    G.add_edge(8, 9, length=80.0)
    return G


def _kaynaklar(kayitlar):
    """[(tesis, boylam, enlem), ...] -> kaynak GeoDataFrame."""
    return gpd.GeoDataFrame({"tesis": [k[0] for k in kayitlar]},
                            geometry=[Point(k[1], k[2]) for k in kayitlar], crs="EPSG:4326")


@pytest.fixture
def hazirlik():
    G = oyuncak_graf()
    dugumler = mesafe.dugum_tablosu(G)
    ana, _ = mesafe.en_buyuk_parca(G)
    return G, dugumler, ana


# --------------------------------------------------------------------------- parça

def test_en_buyuk_parca_ozeti():
    ana, ozet = mesafe.en_buyuk_parca(oyuncak_graf())
    assert ana == {1, 2, 3, 4, 5}
    assert ozet == {"parca_sayisi": 2, "ana_parca_dugum": 5, "disarida_dugum": 2, "en_buyuk_kucuk_parca": 2}


def test_dugum_tablosu_ilce():
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı"]}, geometry=[box(32.79, 39.8, BOYLAM0 + 1.5 * ADIM, 40.0)],
                                crs="EPSG:4326")
    dugumler = mesafe.dugum_tablosu(oyuncak_graf(), sinirlar)
    assert dugumler.loc[[1, 2], "ilce"].tolist() == ["Batı", "Batı"]
    assert dugumler.loc[[3, 4, 5], "ilce"].tolist() == [None, None, None]


# --------------------------------------------------------------------------- bağlama

def test_kaynaklari_baglat_mesafe_esik_ve_kucuk_parca(hazirlik):
    _, dugumler, ana = hazirlik
    x5, _ = _konum(5)
    x3, _ = _konum(3)
    kaynaklar = _kaynaklar([
        ("node/101", *_konum(1)),  # tam düğüm 1 üstünde
        ("node/102", x5, ENLEM + 40 * METRE_ENLEM),  # düğüm 5'in 40 m kuzeyi
        ("node/103", x3, ENLEM + 100 * METRE_ENLEM),  # adadaki 8'in üstünde; ana parçada en yakını 3 (~100 m)
        ("node/104", x3, ENLEM + 1000 * METRE_ENLEM),  # her şeye uzak
    ])
    b = mesafe.kaynaklari_baglat(kaynaklar, dugumler, ana, esik_m=200).set_index("tesis")

    assert b.loc["node/101", "dugum"] == 1 and b.loc["node/101", "mesafe_m"] == pytest.approx(0, abs=0.5)
    assert b.loc["node/102", "dugum"] == 5 and b.loc["node/102", "mesafe_m"] == pytest.approx(40, abs=1)
    assert b.loc["node/103", "dugum"] == 3 and b.loc["node/103", "mesafe_m"] == pytest.approx(100, abs=1)
    assert b["baglandi"].tolist() == [True, True, True, False]
    assert b["kucuk_parcaya_yakin"].tolist() == [False, False, True, True]


def test_tesis_raporu_tesis_bazinda_sayar():
    baglantilar = pd.DataFrame({
        "tesis": ["way/7", "way/7", "way/8", "way/8", "node/9"],
        "dugum": [1, 2, 3, 4, 5],
        "mesafe_m": [10.0, 500.0, 300.0, 400.0, 30.0],
        "baglandi": [True, False, False, False, True],
        "kucuk_parcaya_yakin": [False, False, False, False, True],
    })
    r = mesafe.tesis_raporu(baglantilar)
    assert r["tesis"] == 3 and r["nokta"] == 5
    assert r["disarida_tesis"] == 1 and r["disarida_ornek"] == ["way/8"]  # way/7'nin bir noktası bağlı
    assert r["kucuk_parcaya_yakin_tesis"] == 1
    assert r["baglanti_mesafesi_medyan_m"] == 20.0


# --------------------------------------------------------------------------- Dijkstra

def test_en_yakin_sureler_baglanti_mesafesi_eklenir(hazirlik):
    G, dugumler, ana = hazirlik
    x5, _ = _konum(5)
    kaynaklar = _kaynaklar([("node/101", *_konum(1)), ("node/102", x5, ENLEM + 40 * METRE_ENLEM)])
    baglantilar = mesafe.kaynaklari_baglat(kaynaklar, dugumler, ana)
    G.remove_nodes_from([8, 9])
    s = mesafe.en_yakin_sureler(G, baglantilar, hiz_m_dk=80)

    # Elle: A=düğüm 1 (bağlantı 0 m), B=düğüm 5 (bağlantı 40 m)
    # 1: 0 m -> 0 dk (A) | 2: 80 m -> 1 dk (A) | 3: A 160 m, B 40+160=200 m -> 2 dk (A)
    # 4: A 240 m, B 40+80=120 m -> 1.5 dk (B) | 5: B 40 m -> 0.5 dk (B)
    beklenen = {1: (0.0, "node/101"), 2: (1.0, "node/101"), 3: (2.0, "node/101"),
                4: (1.5, "node/102"), 5: (0.5, "node/102")}
    for dugum, (dk, tesis) in beklenen.items():
        assert s.loc[dugum, "sure_dk"] == pytest.approx(dk, abs=0.02), dugum
        assert s.loc[dugum, "tesis"] == tesis, dugum


def test_en_yakin_sureler_grafi_eski_haline_getirir_ve_bagsizlari_kullanmaz(hazirlik):
    G, _, _ = hazirlik
    once_dugum, once_kenar = G.number_of_nodes(), G.number_of_edges()
    baglantilar = pd.DataFrame({"tesis": ["a", "uzak"], "dugum": [1, 5], "mesafe_m": [0.0, 250.0],
                                "baglandi": [True, False], "kucuk_parcaya_yakin": [False, False]})
    s = mesafe.en_yakin_sureler(G, baglantilar)

    assert mesafe.SANAL_KAYNAK not in G
    assert (G.number_of_nodes(), G.number_of_edges()) == (once_dugum, once_kenar)
    assert set(s["tesis"]) == {"a"}  # bağlanmayan 'uzak' hiçbir düğüme atanmadı
    assert s.loc[5, "sure_dk"] == pytest.approx(4.0)
    assert 8 not in s.index and 9 not in s.index  # ada (ayrı parça) ulaşılamaz


def test_sanal_kaynak_kestirme_olmaz():
    # 1 ve 3 numaralı düğümlere 0 m ile bağlı iki tesis; aradaki 2 numaralı düğüm
    # S üzerinden "1 -> S -> 3" gibi bir kestirmeyle değil, ağ üzerinden 80 m ile ulaşılmalı.
    G = nx.Graph()
    G.add_edge(1, 2, length=80.0)
    G.add_edge(2, 3, length=80.0)
    G.add_edge(3, 4, length=500.0)
    baglantilar = pd.DataFrame({"tesis": ["x", "y"], "dugum": [1, 3], "mesafe_m": [0.0, 0.0],
                                "baglandi": [True, True], "kucuk_parcaya_yakin": [False, False]})
    s = mesafe.en_yakin_sureler(G, baglantilar, hiz_m_dk=80)
    assert s.loc[2, "sure_dk"] == pytest.approx(1.0)
    assert s.loc[4, "sure_dk"] == pytest.approx(500 / 80) and s.loc[4, "tesis"] == "y"


def test_ayni_dugume_baglanan_noktalardan_en_yakini_kalir():
    G = nx.Graph()
    G.add_edge(1, 2, length=80.0)
    baglantilar = pd.DataFrame({"tesis": ["uzak_kapi", "yakin_kapi"], "dugum": [1, 1], "mesafe_m": [60.0, 20.0],
                                "baglandi": [True, True], "kucuk_parcaya_yakin": [False, False]})
    s = mesafe.en_yakin_sureler(G, baglantilar, hiz_m_dk=80)
    assert s.loc[1, "tesis"] == "yakin_kapi" and s.loc[2, "sure_dk"] == pytest.approx(100 / 80)


def test_sifir_uzunluklu_kenarda_koken_dogru():
    G = nx.Graph()
    G.add_edge(1, 2, length=0.0)
    G.add_edge(2, 3, length=0.0)
    G.add_edge(3, 4, length=80.0)
    baglantilar = pd.DataFrame({"tesis": ["t"], "dugum": [1], "mesafe_m": [0.0],
                                "baglandi": [True], "kucuk_parcaya_yakin": [False]})
    s = mesafe.en_yakin_sureler(G, baglantilar)
    assert s["tesis"].tolist() == ["t"] * 4
    assert s.loc[4, "sure_dk"] == pytest.approx(1.0)


def _site_grafi():
    """1-2-3 kamu hattı (80 m'lik kenarlar) ve 1 ile 3 arasından geçen özel site yolu 1-10-3 (20+20 m).

    11, sadece özel yolla 10'a bağlı bir site içi düğüm (30 m).
    """
    G = nx.Graph()
    G.add_edge(1, 2, length=80.0, ozel=False)
    G.add_edge(2, 3, length=80.0, ozel=False)
    G.add_edge(1, 10, length=20.0, ozel=True)
    G.add_edge(10, 3, length=20.0, ozel=True)
    G.add_edge(10, 11, length=30.0, ozel=True)
    return G


def _bag(tesis, dugum, mesafe=0.0):
    return pd.DataFrame({"tesis": [tesis], "dugum": [dugum], "mesafe_m": [mesafe],
                         "baglandi": [True], "kucuk_parcaya_yakin": [False]})


def test_iki_asama_ozel_yol_kestirme_olmaz():
    s = mesafe.en_yakin_sureler(_site_grafi(), _bag("t", 1), hiz_m_dk=80)
    # 3: kamu yoluyla 160 m (2 dk); özel yoldan 40 m olurdu ama site dışındaki düğüm kestirme kullanamaz
    assert s.loc[3, "sure_dk"] == pytest.approx(2.0) and not s.loc[3, "ozel_yol"]
    assert s.loc[2, "sure_dk"] == pytest.approx(1.0)


def test_iki_asama_site_ici_dugumler_kapidan_sure_alir():
    s = mesafe.en_yakin_sureler(_site_grafi(), _bag("t", 1), hiz_m_dk=80)
    # 10: kapılar 1 (0 m) ve 3 (160 m) -> min(0+20, 160+20) = 20 m; 11: 20+30 = 50 m
    assert s.loc[10, "sure_dk"] == pytest.approx(20 / 80) and s.loc[10, "ozel_yol"]
    assert s.loc[11, "sure_dk"] == pytest.approx(50 / 80) and s.loc[11, "tesis"] == "t"
    assert not s.loc[1, "ozel_yol"]


def test_iki_asama_yakin_kapi_secilir_ve_tesis_kokeni_dogru():
    G = _site_grafi()
    baglantilar = pd.concat([_bag("bati", 1), _bag("dogu", 3, 10.0)], ignore_index=True)
    s = mesafe.en_yakin_sureler(G, baglantilar, hiz_m_dk=80)
    # 10: batı kapısı 1'den 0+20, doğu kapısı 3'ten 10+20 -> batı (20 m)
    assert s.loc[10, "tesis"] == "bati" and s.loc[10, "sure_dk"] == pytest.approx(20 / 80)
    assert s.loc[3, "tesis"] == "dogu" and s.loc[3, "sure_dk"] == pytest.approx(10 / 80)  # kapı süresi değişmez


def test_kamu_kenarina_paralel_ozel_baglanti_site_icinde_kullanilir():
    # 1-2 arası: kamu 45 m, paralel özel 40 m (ozel_length). 3 sadece özel yolla 2'ye bağlı (10 m).
    G = nx.Graph()
    G.add_edge(1, 2, length=45.0, ozel=False, ozel_length=40.0)
    G.add_edge(2, 3, length=10.0, ozel=True)
    s = mesafe.en_yakin_sureler(G, _bag("t", 1), hiz_m_dk=80)
    assert s.loc[2, "sure_dk"] == pytest.approx(45 / 80)  # kamu düğümü: kestirme yok
    assert s.loc[3, "sure_dk"] == pytest.approx(50 / 80)  # site içi: 40 + 10 (55 değil)


def test_site_icinden_girilen_kamu_cebi_sure_alir():
    # 1-2 kamu (80 m); 2-20 özel site yolu (30 m); 20-21 kamu ama sadece site içinden ulaşılıyor (40 m)
    G = nx.Graph()
    G.add_edge(1, 2, length=80.0, ozel=False)
    G.add_edge(2, 20, length=30.0, ozel=True)
    G.add_edge(20, 21, length=40.0, ozel=False)
    s = mesafe.en_yakin_sureler(G, _bag("t", 1), hiz_m_dk=80)
    assert s.loc[21, "sure_dk"] == pytest.approx(150 / 80) and s.loc[21, "ozel_yol"]
    assert s.loc[20, "sure_dk"] == pytest.approx(110 / 80)


def test_ikinci_asama_grafi_ulasilan_kamu_kenarlarini_almaz():
    G = nx.Graph()
    G.add_edge(1, 2, length=80.0, ozel=False)  # ulaşılmış iki düğüm: alınmaz
    G.add_edge(2, 3, length=50.0, ozel=False)  # ulaşılmış -> ulaşılmamış: tek yön
    G.add_edge(3, 4, length=20.0, ozel=False)  # iki ulaşılmamış: iki yön
    G.add_edge(1, 5, length=60.0, ozel=False, ozel_length=10.0)  # paralel özel: iki yön, 10 m
    O = mesafe.ikinci_asama_grafi(G, {1: 0.0, 2: 1.0, 5: 0.5})
    assert not O.has_edge(1, 2) and not O.has_edge(2, 1)
    assert O.has_edge(2, 3) and not O.has_edge(3, 2)
    assert O.has_edge(3, 4) and O.has_edge(4, 3)
    assert O[1][5]["length"] == 10.0 and O[5][1]["length"] == 10.0


def test_kamu_dugumleri():
    assert mesafe.kamu_dugumleri(_site_grafi()) == {1, 2, 3}


def test_sanal_kaynak_kimligi_cakisirsa_hata():
    G = nx.Graph()
    G.add_edge(mesafe.SANAL_KAYNAK, 1, length=1.0)
    baglantilar = pd.DataFrame({"tesis": ["t"], "dugum": [1], "mesafe_m": [0.0],
                                "baglandi": [True], "kucuk_parcaya_yakin": [False]})
    with pytest.raises(ValueError):
        mesafe.en_yakin_sureler(G, baglantilar)


def test_hic_bagli_kaynak_yoksa_bos():
    baglantilar = pd.DataFrame({"tesis": ["t"], "dugum": [1], "mesafe_m": [999.0],
                                "baglandi": [False], "kucuk_parcaya_yakin": [False]})
    assert mesafe.en_yakin_sureler(oyuncak_graf(), baglantilar).empty


# --------------------------------------------------------------------------- özet ve kaynak dosyaları

def test_ozet():
    o = mesafe.ozet(pd.Series([5.0, 10.0, 15.0, 20.0, None]), esik_dk=15)
    assert o == {"dugum": 4, "ortalama_dk": 12.5, "medyan_dk": 12.5, "esik_alti_oran": 0.75}
    assert mesafe.ozet(pd.Series([], dtype=float))["dugum"] == 0


def test_kategori_kaynaklari(tmp_path):
    def yaz(dosya, katman, gdf):
        gdf.to_file(tmp_path / dosya, layer=katman, driver="GPKG")

    nokta = lambda i: Point(32.8 + i / 1000, 39.9)  # noqa: E731
    yaz("eczaneler.gpkg", "tesisler", gpd.GeoDataFrame({"element": ["node"], "id": [1]}, geometry=[nokta(1)], crs=4326))
    yaz("birinci_basamak.gpkg", "tesisler", gpd.GeoDataFrame({"element": ["way"], "id": [2]}, geometry=[nokta(2)], crs=4326))
    yaz("hastaneler.gpkg", "kaynaklar", gpd.GeoDataFrame({"tesis_element": ["way", "way"], "tesis_id": [3, 3]},
                                                         geometry=[nokta(3), nokta(4)], crs=4326))
    yaz("rayli.gpkg", "istasyonlar", gpd.GeoDataFrame({"element": ["node", "node"], "id": [10, 20]},
                                                      geometry=[nokta(5), nokta(6)], crs=4326))
    yaz("rayli.gpkg", "kaynaklar", gpd.GeoDataFrame(
        {"istasyon_id": [10, 10, 20], "metro": [True, True, False], "ankaray": [False, False, False],
         "baskentray": [False, False, True]}, geometry=[nokta(7), nokta(8), nokta(9)], crs=4326))

    k = mesafe.kategori_kaynaklari(tmp_path)
    assert k["eczane"]["tesis"].tolist() == ["node/1"]
    assert k["birinci_basamak"]["tesis"].tolist() == ["way/2"]
    assert k["hastane"]["tesis"].tolist() == ["way/3", "way/3"]
    assert k["rayli"]["tesis"].tolist() == ["node/10", "node/10"]  # iki giriş, aynı istasyon
    assert k["baskentray"]["tesis"].tolist() == ["node/20"]
