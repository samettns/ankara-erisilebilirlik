"""src/altigen.py için testler: bilinen iki H3 hücresine düşen oyuncak düğümler."""

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from src import altigen

KATEGORILER = ["eczane", "hastane"]
A = h3.latlng_to_cell(39.92, 32.85, 9)
B = next(c for c in h3.grid_disk(A, 1) if c != A)
C = next(c for c in h3.grid_disk(A, 3) if h3.grid_distance(A, c) == 3)


def _merkez(hucre, kayma=0.0):
    """Hücre merkezine yakın (x, y); kayma derece cinsinden küçük bir ötelemedir."""
    lat, lng = h3.cell_to_latlng(hucre)
    return lng + kayma, lat + kayma


def _dugum(hucre, kayma, ilce="Çankaya", bagli=True, eczane=(None, None), hastane=(None, None)):
    x, y = _merkez(hucre, kayma)
    return {"x": x, "y": y, "ilce": ilce, "bagli": bagli,
            "eczane_dk": eczane[0], "eczane_id": eczane[1], "hastane_dk": hastane[0], "hastane_id": hastane[1]}


def oyuncak_dugumler():
    return pd.DataFrame([
        # A: 3 bağlı düğüm -> eczane medyanı 4, en sık node/1 (2 kez)
        _dugum(A, 0.0000, eczane=(2.0, "node/1"), hastane=(20.0, "way/9")),
        _dugum(A, 0.0002, eczane=(4.0, "node/1"), hastane=(22.0, "way/9")),
        _dugum(A, -0.0002, eczane=(9.0, "node/2"), hastane=(30.0, "way/8"), ilce="Mamak"),
        # A'daki küçük parça düğümü: sayıya girer, medyana girmez
        _dugum(A, 0.0001, bagli=False),
        # B: 2 bağlı düğüm, eşit sayıda iki tesis -> süresi kısa olan (node/4) seçilir; medyan 6.5
        _dugum(B, 0.0000, eczane=(8.0, "node/3"), hastane=(None, None)),
        _dugum(B, 0.0002, eczane=(5.0, "node/4"), hastane=(None, None)),
        # C: sadece küçük parça -> bagli=False
        _dugum(C, 0.0000, bagli=False),
        _dugum(C, 0.0001, bagli=False),
        # 7 ilçe dışı düğüm: hiç sayılmaz
        _dugum(A, 0.0003, ilce=None, eczane=(0.5, "node/99"), hastane=(1.0, "way/99")),
    ])


def test_hucrelere_ata_bilinen_hucreler():
    df = oyuncak_dugumler()
    hucreler = altigen.hucrelere_ata(df)
    assert set(hucreler) == {A, B, C}


def test_altigenleri_topla_medyan_sayim_ve_ilce():
    a = altigen.altigenleri_topla(oyuncak_dugumler(), KATEGORILER)
    assert set(a.index) == {A, B, C}

    assert a.loc[A, "dugum"] == 4 and a.loc[A, "bagli_dugum"] == 3  # ilçe dışı düğüm sayılmadı
    assert a.loc[A, "ilce"] == "Çankaya"  # çoğunluk
    assert a.loc[A, "eczane_dk"] == 4.0  # medyan(2, 4, 9); ilçe dışı 0.5 girmedi
    assert a.loc[A, "hastane_dk"] == 22.0
    assert a.loc[B, "eczane_dk"] == pytest.approx(6.5)  # çift sayıda düğüm: ortalama


def test_altigenleri_topla_en_sik_tesis_ve_esitlik():
    a = altigen.altigenleri_topla(oyuncak_dugumler(), KATEGORILER)
    assert a.loc[A, "eczane_id"] == "node/1"  # 2'ye 1
    assert a.loc[A, "hastane_id"] == "way/9"
    assert a.loc[B, "eczane_id"] == "node/4"  # 1'e 1 eşit, 5 dk < 8 dk


def test_altigenleri_topla_bagsiz_ve_eksik_deger():
    a = altigen.altigenleri_topla(oyuncak_dugumler(), KATEGORILER)
    assert a.loc[C, "bagli"] == False and a.loc[C, "bagli_dugum"] == 0  # noqa: E712
    assert np.isnan(a.loc[C, "eczane_dk"]) and pd.isna(a.loc[C, "eczane_id"])
    assert np.isnan(a.loc[B, "hastane_dk"]) and pd.isna(a.loc[B, "hastane_id"])  # B'de hastane bilgisi yok
    assert a.loc[A, "bagli"] == True  # noqa: E712  küçük parça düğümü olsa da


def test_adlari_ekle_isimsiz_ve_bos():
    a = altigen.altigenleri_topla(oyuncak_dugumler(), KATEGORILER)
    tesisler = gpd.GeoDataFrame({
        "kategori": ["eczane", "eczane", "hastane"],
        "tesis": ["node/1", "node/4", "way/9"],
        "ad": ["Temel Eczanesi", "Bilir Eczanesi", "Şehir Hastanesi"],
    }, geometry=[Point(0, 0)] * 3, crs="EPSG:4326")
    sonuc = altigen.adlari_ekle(a, tesisler, KATEGORILER)
    assert sonuc.loc[A, "eczane_ad"] == "Temel Eczanesi"
    assert sonuc.loc[B, "eczane_ad"] == "Bilir Eczanesi"
    assert sonuc.loc[A, "hastane_ad"] == "Şehir Hastanesi"
    assert pd.isna(sonuc.loc[C, "eczane_ad"])  # tesisi olmayan altıgende ad da yok


def test_adlari_ekle_listede_olmayan_tesis_isimsiz():
    a = pd.DataFrame({"eczane_id": ["node/77"], "hastane_id": [None]}, index=[A])
    tesisler = gpd.GeoDataFrame({"kategori": [], "tesis": [], "ad": []}, geometry=[], crs="EPSG:4326")
    sonuc = altigen.adlari_ekle(a, tesisler, KATEGORILER)
    assert sonuc.loc[A, "eczane_ad"] == "isimsiz eczane"


def test_tesis_tablosu(tmp_path):
    def yaz(dosya, katman, gdf):
        gdf.to_file(tmp_path / dosya, layer=katman, driver="GPKG")

    nokta = [Point(32.8, 39.9)]
    yaz("eczaneler.gpkg", "tesisler", gpd.GeoDataFrame({"element": ["node", "node"], "id": [1, 5], "name": [None, "Kenar"],
                                                        "ilce": ["Çankaya", None]}, geometry=nokta * 2, crs=4326))
    yaz("hastaneler.gpkg", "tesisler", gpd.GeoDataFrame({"element": ["way"], "id": [2], "name": ["H"]}, geometry=nokta, crs=4326))
    yaz("birinci_basamak.gpkg", "tesisler", gpd.GeoDataFrame({"element": ["node"], "id": [3], "name": ["ASM"]}, geometry=nokta, crs=4326))
    yaz("rayli.gpkg", "istasyonlar", gpd.GeoDataFrame(
        {"element": ["node", "node", "node"], "id": [10, 11, 12], "name": ["Kızılay", "Kurtuluş", "Sincan"],
         "metro": [True, False, False], "ankaray": [True, True, False], "baskentray": [False, False, True]},
        geometry=nokta * 3, crs=4326))
    t = altigen.tesis_tablosu(tmp_path)
    assert t[t.kategori == "eczane"]["ad"].tolist() == ["isimsiz eczane", "Kenar"]
    assert t[t.kategori == "eczane"]["ic"].tolist() == [True, False]  # ilce boş -> 7 ilçe dışı
    assert t[t.kategori == "hastane"]["ic"].tolist() == [True]  # ilce sütunu yok -> içte sayılır
    assert t[t.kategori == "hastane"]["tesis"].tolist() == ["way/2"]
    assert t[t.kategori == "rayli"]["ad"].tolist() == ["Kızılay", "Kurtuluş"]
    assert t[t.kategori == "baskentray"]["ad"].tolist() == ["Sincan"]
