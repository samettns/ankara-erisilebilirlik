"""src/nufus.py için testler: sentetik rasterlar ve elle hesaplanabilen ağırlıklı özetler."""

import math

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import pytest
import rasterio
from affine import Affine
from shapely.geometry import box

from src import nufus


# --------------------------------------------------------------------------- piksel -> altıgen

def _donusum(bati, kuzey, genislik, yukseklik):
    """Kuzeyi yukarı bakan raster dönüşümü (piksel boyutu derece)."""
    return Affine(genislik, 0, bati, 0, -yukseklik, kuzey)


def test_pikseller_merkezlerinin_altigenine_ve_ilcesine_atanir():
    # 2x2 piksel, 0.02° boyut; merkezler (32.85|32.87, 39.94|39.92)
    dizi = np.array([[5.0, 0.0],
                     [3.0, 2.0]])
    donusum = _donusum(32.84, 39.95, 0.02, 0.02)
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı", "Doğu"]},
                                geometry=[box(32.83, 39.90, 32.86, 39.96), box(32.86, 39.90, 32.88, 39.96)], crs=4326)
    sonuc = nufus.pikselleri_altigenlere_ata(dizi, donusum, sinirlar, cozunurluk=9)

    beklenen = pd.DataFrame({
        "h3": [h3.latlng_to_cell(39.94, 32.85, 9), h3.latlng_to_cell(39.92, 32.85, 9), h3.latlng_to_cell(39.92, 32.87, 9)],
        "ilce": ["Batı", "Batı", "Doğu"],
        "nufus": [5.0, 3.0, 2.0],
    })
    pd.testing.assert_frame_equal(sonuc.sort_values("nufus", ascending=False).reset_index(drop=True), beklenen)
    assert sonuc["nufus"].sum() == 10.0  # sıfır piksel atlandı


def test_ayni_altigene_dusen_pikseller_toplanir_ilce_sinirinda_bolunur():
    # Bir altıgenin merkezine yakın 1x3 küçük piksel (~17 m); ilçe sınırı 2. ve 3. piksel arasında
    lat, lng = h3.cell_to_latlng(h3.latlng_to_cell(39.92, 32.85, 9))
    dizi = np.array([[4.0, 6.0, 10.0]])
    donusum = _donusum(lng - 0.0003, lat + 0.0001, 0.0002, 0.0002)
    sinir_x = lng + 0.0001  # 3. pikselin merkezi (lng + 0.0002) doğuda kalır
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı", "Doğu"]},
                                geometry=[box(lng - 0.01, lat - 0.01, sinir_x, lat + 0.01),
                                          box(sinir_x, lat - 0.01, lng + 0.01, lat + 0.01)], crs=4326)
    sonuc = nufus.pikselleri_altigenlere_ata(dizi, donusum, sinirlar, cozunurluk=9)

    assert sonuc["h3"].nunique() == 1
    assert dict(zip(sonuc["ilce"], sonuc["nufus"])) == {"Batı": 10.0, "Doğu": 10.0}


def test_ilce_disindaki_pikseller_atilir():
    dizi = np.array([[7.0, 9.0]])
    donusum = _donusum(32.84, 39.93, 0.02, 0.02)  # merkezler 32.85 ve 32.87
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı"]}, geometry=[box(32.83, 39.90, 32.86, 39.96)], crs=4326)
    sonuc = nufus.pikselleri_altigenlere_ata(dizi, donusum, sinirlar)
    assert sonuc["nufus"].tolist() == [7.0]


def test_bos_raster_bos_tablo():
    sinirlar = gpd.GeoDataFrame({"ilce": ["Batı"]}, geometry=[box(0, 0, 1, 1)], crs=4326)
    sonuc = nufus.pikselleri_altigenlere_ata(np.zeros((2, 2)), _donusum(0, 1, 0.5, 0.5), sinirlar)
    assert sonuc.empty and list(sonuc.columns) == ["h3", "ilce", "nufus"]


def test_pencere_oku_nodata_ve_negatifleri_sifirlar(tmp_path):
    yol = tmp_path / "nufus.tif"
    veri = np.array([[1.0, -99999.0, 3.0, 4.0],
                     [5.0, -2.0, 7.0, np.nan]], dtype="float32")
    with rasterio.open(yol, "w", driver="GTiff", height=2, width=4, count=1, dtype="float32",
                       crs="EPSG:4326", transform=_donusum(32.0, 40.0, 0.1, 0.1), nodata=-99999.0) as hedef:
        hedef.write(veri, 1)

    dizi, donusum = nufus.pencere_oku(yol, kutu=(32.1, 39.8, 32.3, 40.0))  # orta iki sütun
    np.testing.assert_array_equal(dizi, [[0.0, 3.0], [0.0, 7.0]])
    assert donusum.c == pytest.approx(32.1) and donusum.f == pytest.approx(40.0)


# --------------------------------------------------------------------------- ağırlıklı medyan

@pytest.mark.parametrize("degerler, agirliklar, beklenen", [
    ([1, 2, 3], [1, 1, 1], 2),
    ([1, 2, 3, 4], [1, 1, 1, 1], 2),  # çift sayıda: alt medyan
    ([1, 10], [1, 100], 10),  # baskın ağırlık
    ([5, 20], [50, 50], 5),  # tam %50 sınırı: birikimli 50'ye ilk ulaşan
    ([30, 10, 20], [1, 1, 5], 20),  # sırasız girdi
    ([1, np.nan, 50], [1, 100, 1], 1),  # NaN süre yok sayılır (alt medyan)
    ([1, 50, 100], [0, 1, 1], 50),  # sıfır ağırlık yok sayılır
])
def test_agirlikli_medyan(degerler, agirliklar, beklenen):
    assert nufus.agirlikli_medyan(degerler, agirliklar) == beklenen


def test_agirlikli_medyan_bos():
    assert math.isnan(nufus.agirlikli_medyan([np.nan], [5]))
    assert math.isnan(nufus.agirlikli_medyan([], []))


# --------------------------------------------------------------------------- özet tabloları

# Gerçek H3 kimlikleri (rapor koordinat üretir): bir hücre ve komşuları
_KOMSULAR = sorted(h3.grid_disk(h3.latlng_to_cell(39.92, 32.85, 9), 1))
A, B, C, D, E = _KOMSULAR[:5]


def _oyuncak():
    altigen_nufus = pd.DataFrame({
        "h3": [A, B, C, D, E],
        "ilce": ["Batı", "Batı", "Doğu", "Doğu", "Doğu"],
        "nufus": [100.0, 50.0, 30.0, 20.0, 8.0],
    })
    altigenler = pd.DataFrame({
        "bagli": [True, True, True, False],
        "eczane_dk": [5.0, 20.0, 15.0, np.nan],  # C tam 15,0: içeride
    }, index=pd.Index([A, B, C, E], name="h3"))  # D düğümsüz (tabloda yok)
    return altigen_nufus, altigenler


def test_nufus_ozet_tablosu_toplam_ve_ilceler():
    altigen_nufus, altigenler = _oyuncak()
    t = nufus.nufus_ozet_tablosu(altigen_nufus, altigenler, ["eczane"], esik_dk=15,
                                 ilceler=["Batı", "Doğu"]).set_index("bolge")

    top = t.loc[nufus.TOPLAM]
    assert top["nufus"] == 208 and top["kapsanan_nufus"] == 180  # D (20) ve E (8) süresiz
    assert top["kapsanan_oran"] == round(180 / 208, 4)
    # sıralı süreler 5(100) 15(30) 20(50); birikimli 100 >= 90 -> 5
    assert top["agirlikli_medyan_dk"] == 5.0
    assert top["esik_disi_kisi"] == 50 and top["esik_disi_oran"] == round(50 / 180, 4)

    bati, dogu = t.loc["Batı"], t.loc["Doğu"]
    assert (bati["nufus"], bati["esik_disi_kisi"], bati["agirlikli_medyan_dk"]) == (150, 50, 5.0)
    assert (dogu["nufus"], dogu["kapsanan_nufus"], dogu["esik_disi_kisi"]) == (58, 30, 0)
    assert dogu["agirlikli_medyan_dk"] == 15.0


def test_bosluk_raporu_dugumsuz_ve_bagsiz_ayri():
    altigen_nufus, altigenler = _oyuncak()
    r = nufus.bosluk_raporu(altigen_nufus, altigenler)
    assert r["toplam_nufus"] == 208
    assert (r["dugumsuz"]["altigen"], r["dugumsuz"]["nufus"]) == (1, 20)  # D
    assert (r["bagsiz"]["altigen"], r["bagsiz"]["nufus"]) == (1, 8)  # E
    assert r["dugumsuz"]["oran"] == round(20 / 208, 4)


def test_ayni_altigen_iki_ilcede_tek_sayilir_boslukta():
    altigen_nufus = pd.DataFrame({"h3": [D, D], "ilce": ["Batı", "Doğu"], "nufus": [4.0, 6.0]})
    altigenler = pd.DataFrame({"bagli": [True]}, index=pd.Index([A], name="h3"))
    r = nufus.bosluk_raporu(altigen_nufus, altigenler)
    assert (r["dugumsuz"]["altigen"], r["dugumsuz"]["nufus"]) == (1, 10)


def test_karsilastirma_tablosu():
    altigen_nufus, altigenler = _oyuncak()
    ozet = nufus.nufus_ozet_tablosu(altigen_nufus, altigenler, ["eczane"], ilceler=["Batı", "Doğu"])
    k = nufus.karsilastirma_tablosu(ozet, {"eczane": {"esik_alti_oran": 0.5, "medyan_dk": 14.0}}, ["eczane"])
    satir = k.iloc[0]
    assert satir["nufus_esik_ici_oran"] == round(1 - 50 / 180, 4)
    assert satir["fark_puan"] == round(100 * ((1 - round(50 / 180, 4)) - 0.5), 1)
    assert satir["nufus_agirlikli_medyan_dk"] == 5.0 and satir["dugum_medyan_dk"] == 14.0


def test_tuik_karsilastirmasi():
    altigen_nufus = pd.DataFrame({"h3": [A, B, C], "ilce": ["Batı", "Batı", "Doğu"], "nufus": [60.0, 50.0, 80.0]})
    t = nufus.tuik_karsilastirmasi(altigen_nufus, {"Batı": 100, "Doğu": 100}).set_index("ilce")
    assert (t.loc["Batı", "worldpop"], t.loc["Batı", "fark"], t.loc["Batı", "fark_oran"]) == (110, 10, 0.1)
    assert t.loc["Doğu", "fark_oran"] == -0.2
    assert (t.loc[nufus.TOPLAM, "worldpop"], t.loc[nufus.TOPLAM, "tuik_adnks"]) == (190, 200)


def test_soluk_ozeti_sadece_bagli_altigenler():
    _, altigenler = _oyuncak()
    nufuslar = pd.Series({A: 100.0, B: 5.0, C: 9.99, E: 1.0})  # E bağlı değil: sayılmaz
    s = nufus.soluk_ozeti(altigenler, nufuslar, esik=10)
    assert (s["soluk_altigen"], s["bagli_altigen"]) == (2, 3)
    assert s["soluk_nufus"] == 15 and s["soluk_nufus_orani"] == round(14.99 / 114.99, 5)


# --------------------------------------------------------------------------- indirme

def test_raster_indir_eksik_indirmeyi_reddeder(tmp_path, monkeypatch):
    monkeypatch.setattr(nufus, "akisla_indir", lambda url, hedef, istek: (hedef.write_bytes(b"x" * 5), (5, 9))[1])
    with pytest.raises(ValueError, match="Eksik"):
        nufus.raster_indir("https://ornek/nufus.tif", tmp_path, istek=None)
    assert not list(tmp_path.iterdir())


def test_raster_indir_tamamsa_kaydeder_sonra_tekrar_indirmez(tmp_path, monkeypatch):
    cagri = []

    def sahte(url, hedef, istek):
        cagri.append(url)
        hedef.write_bytes(b"veri")
        return 4, 4

    monkeypatch.setattr(nufus, "akisla_indir", sahte)
    yol = nufus.raster_indir("https://ornek/nufus.tif", tmp_path, istek=None)
    assert yol.name == "nufus.tif" and yol.read_bytes() == b"veri"
    nufus.raster_indir("https://ornek/nufus.tif", tmp_path, istek=None)
    assert len(cagri) == 1
