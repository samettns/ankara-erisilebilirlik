"""src/harita.py için testler: aralıklar, GeoJSON içeriği ve HTML çıktısı."""

import json
import math

import geopandas as gpd
import h3
import pandas as pd
import pytest
from shapely.geometry import Point, box

from src import harita

KATEGORILER = ["eczane", "hastane"]
A = h3.latlng_to_cell(39.92, 32.85, 9)
B = next(c for c in h3.grid_disk(A, 1) if c != A)


@pytest.mark.parametrize("dk, sinif", [
    (0.0, 0), (4.9, 0), (5.0, 0), (5.01, 1), (10.0, 1), (14.99, 2), (15.0, 2), (15.01, 3),
    (30.0, 3), (30.01, 4), (240.0, 4), (float("nan"), -1), (None, -1),
])
def test_aralik_sinifi_ustten_kapali(dk, sinif):
    assert harita.aralik_sinifi(dk) == sinif


@pytest.mark.parametrize("dk, gosterilen", [
    (0.4, 0), (1.0, 1), (6.2, 7), (14.2, 15), (15.0, 15), (float("nan"), -1),
])
def test_gosterilen_dakika(dk, gosterilen):
    assert harita.gosterilen_dakika(dk) == gosterilen


@pytest.mark.parametrize("dk, metin", [
    (0.4, "<1 dk"), (6.2, "7 dk"), (59.5, "60 dk"), (60.0, "60 dk"), (60.01, "60+ dk"),
    (240.0, "60+ dk"), (float("nan"), ""), (None, ""),
])
def test_sure_metni_60_ustu(dk, metin):
    assert harita.sure_metni(dk) == metin


def test_gosterilen_dakika_aralik_ust_sinirini_asmaz():
    for dk in [0.5 + i * 0.37 for i in range(200)]:
        sinif = harita.aralik_sinifi(dk)
        if sinif < 4:
            assert harita.gosterilen_dakika(dk) <= harita.ARALIK_SINIRLARI[sinif]


def _altigenler():
    return pd.DataFrame({
        "ilce": ["Çankaya", "Mamak"], "dugum": [12, 3], "bagli": [True, False],
        "eczane_dk": [6.2, float("nan")], "eczane_ad": ["Temel & Oğulları Eczanesi", None],
        "hastane_dk": [0.4, float("nan")], "hastane_ad": ["Şehir <Hastanesi>", None],
    }, index=pd.Index([A, B], name="h3"))


def test_altigen_geojson_bagli_olanlar_ve_kisa_ozellikler():
    g = harita.altigen_geojson(_altigenler(), KATEGORILER)
    assert len(g["features"]) == 1  # bağlı olmayan B ayrı katmanda
    p = g["features"][0]["properties"]
    assert p == {"i": "Çankaya", "n": 12, "s": [1, 0], "t": ["7 dk", "&lt;1 dk"],
                 "a": ["Temel &amp; Oğulları Eczanesi", "Şehir &lt;Hastanesi&gt;"]}
    halka = g["features"][0]["geometry"]["coordinates"][0]
    assert len(halka) == 7 and halka[0] == halka[-1]  # altıgen + kapanış
    assert all(len(str(v).split(".")[1]) <= harita.ONDALIK for nokta in halka for v in nokta)


@pytest.mark.parametrize("n, metin, soluk", [
    (0.0, "<10", True), (9.99, "<10", True), (float("nan"), "<10", True),
    (10.0, "~10", False), (37.4, "~40", False), (1236.0, "~1.240", False), (15_004.0, "~15.000", False),
])
def test_nufus_metni_ve_soluk(n, metin, soluk):
    assert harita.nufus_metni(n) == metin
    assert harita.soluk_mu(n) is soluk


def test_altigen_geojson_nufus_ve_soluk_bayragi():
    a = _altigenler()
    a["nufus"] = [4.0, 500.0]
    p = harita.altigen_geojson(a, KATEGORILER)["features"][0]["properties"]
    assert p["p"] == "<10" and p["f"] == 1
    a["nufus"] = [1236.0, 0.0]
    p = harita.altigen_geojson(a, KATEGORILER)["features"][0]["properties"]
    assert p["p"] == "~1.240" and "f" not in p


def test_bagsiz_geojson():
    g = harita.bagsiz_geojson(_altigenler())
    assert [f["properties"]["bilgi"] for f in g["features"]] == ["Ağa bağlı değil · Mamak · 3 düğüm"]


def test_tesis_geojson_kategori_sirasi_ve_ilce_disi():
    tesisler = gpd.GeoDataFrame({"kategori": ["hastane", "eczane", "rayli"], "ad": ["H", "E", "R"],
                                 "ic": [True, False, True]},
                                geometry=[Point(32.1234567, 39.1), Point(32.2, 39.2), Point(32.3, 39.3)], crs=4326)
    g = harita.tesis_geojson(tesisler, KATEGORILER)
    # rayli listede yok; 7 ilçe dışındaki eczane o=1 ile işaretli
    assert [f["properties"] for f in g["features"]] == [{"k": 1, "a": "H"}, {"k": 0, "a": "E", "o": 1}]
    assert g["features"][0]["geometry"]["coordinates"][0] == 32.12346


def test_tesis_geojson_ic_sutunu_yoksa_hepsi_icte():
    tesisler = gpd.GeoDataFrame({"kategori": ["eczane"], "ad": ["E"]}, geometry=[Point(32.2, 39.2)], crs=4326)
    assert harita.tesis_geojson(tesisler, KATEGORILER)["features"][0]["properties"] == {"k": 0, "a": "E"}


def test_js_json_script_kapatmaz():
    assert "</script>" not in harita._js_json({"a": "</script><b>"})


def test_harita_olustur_html(tmp_path):
    tesisler = gpd.GeoDataFrame({"kategori": ["eczane", "hastane"], "ad": ["Temel Eczanesi", "Şehir Hastanesi"]},
                                geometry=[Point(32.85, 39.92), Point(32.86, 39.93)], crs=4326)
    sinirlar = gpd.GeoDataFrame({"ilce": ["Çankaya"]}, geometry=[box(32.8, 39.9, 32.9, 39.95)], crs=4326)
    yol = tmp_path / "harita.html"
    harita.harita_olustur(_altigenler(), tesisler, sinirlar, KATEGORILER).save(str(yol))
    metin = yol.read_text(encoding="utf-8")

    for parca in ["Eczane", "Hastane", "Tesisler", "Ağa bağlı değil", "İlçe sınırları",
                  "baselayerchange", "Temel Eczanesi", "Temel &amp; Oğulları Eczanesi", "0–5 dk", "30+ dk",
                  "Tesis (7 ilçe dışı)", "kişiden az nüfus", "Nüfus: "]:
        # folium katman adlarını JSON içinde \\u kaçışlı yazar; iki biçim de kabul
        assert parca in metin or json.dumps(parca)[1:-1] in metin, parca
    assert "World_Light_Gray_Base" in metin and "cartocdn" not in metin
    # Lisans atıfları: Esri altlığı + OSM (ODbL) + WorldPop (CC BY 4.0)
    for atif in ["Esri", "openstreetmap.org/copyright", "ODbL", "WorldPop", "CC BY 4.0"]:
        assert atif in metin or json.dumps(atif)[1:-1] in metin, atif
    for renk in harita.RENKLER:
        assert renk in metin
    assert metin.count('"type":"Feature"') >= 3  # 1 altıgen + 2 tesis (kısa JSON)


def test_kaydet_ana_dosya_ve_yayin_kopyasi(tmp_path):
    sinirlar = gpd.GeoDataFrame({"ilce": ["Çankaya"]}, geometry=[box(32.8, 39.9, 32.9, 39.95)], crs=4326)
    tesisler = gpd.GeoDataFrame({"kategori": ["eczane"], "ad": ["E"]}, geometry=[Point(32.85, 39.92)], crs=4326)
    h = harita.harita_olustur(_altigenler(), tesisler, sinirlar, KATEGORILER)
    ana, kopya = tmp_path / "sonuclar" / "harita.html", tmp_path / "docs" / "index.html"
    assert harita.kaydet(h, ana, kopya) == ana
    assert ana.exists() and kopya.read_bytes() == ana.read_bytes()
