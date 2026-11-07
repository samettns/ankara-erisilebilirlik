"""Nüfus ağırlıklı erişilebilirlik: WorldPop rasterı -> H3 altıgenleri -> ağırlıklı özetler.

Akış:
1. WorldPop 2025 rasterını indir (bir kez), Ankara kesit kutusu penceresini oku.
2. Sıfır olmayan her pikseli, merkezinin düştüğü H3 altıgenine ve ilçeye ata.
3. Nüfusu olduğu halde süresi bilinmeyen altıgenleri raporla (veri boşluğu).
4. Kategori başına nüfus ağırlıklı medyan süre ve 15 dk dışında kalan nüfus:
   7 ilçe toplamı ve ilçe bazında; düğüm oranlarıyla karşılaştırma.
5. Raster ilçe toplamlarını TÜİK ADNKS 2025 ile karşılaştır.

Çalıştırma (proje kökünden):
    python -m src.nufus
"""

import hashlib
import json
from pathlib import Path
from typing import Callable

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import rasterio.transform
import rasterio.windows
import requests

try:
    from src.ayarlar import (ESIK_DK, H3_COZUNURLUK, ILCELER, KESIT_KUTUSU, NUFUS_DIZINI, NUFUS_YILI,
                             SINIR_DOSYASI, SOLUK_NUFUS_ESIGI, SONUC_DIZINI, WORLDPOP_URL)
    from src.indirme import tekrar_dene
    from src.mesafe import KATEGORILER
    from src.pbf import PARCA_BOYUTU, akisla_indir, veri_surumu_bolumu_yaz
except ModuleNotFoundError:
    from ayarlar import (ESIK_DK, H3_COZUNURLUK, ILCELER, KESIT_KUTUSU, NUFUS_DIZINI, NUFUS_YILI,
                         SINIR_DOSYASI, SOLUK_NUFUS_ESIGI, SONUC_DIZINI, WORLDPOP_URL)
    from indirme import tekrar_dene
    from mesafe import KATEGORILER
    from pbf import PARCA_BOYUTU, akisla_indir, veri_surumu_bolumu_yaz

TOPLAM = "7 ilçe toplamı"

# TÜİK ADNKS 2025 ilçe nüfusları (sağlama için)
TUIK_ADNKS_2025 = {
    "Çankaya": 952_198, "Keçiören": 931_722, "Yenimahalle": 721_531, "Mamak": 690_329,
    "Etimesgut": 632_458, "Sincan": 605_366, "Altındağ": 418_072,
}
TUIK_KAYNAK = {
    "veri": "TÜİK Adrese Dayalı Nüfus Kayıt Sistemi (ADNKS) 2025 sonuçları, ilçe nüfusları",
    "birincil_kaynak": "https://biruni.tuik.gov.tr/medas/?kn=95&locale=tr",
    "yayin": "ADNKS 2025 sonuçları Şubat 2026'da yayımlandı",
    "rakamlarin_alindigi_yer": ("Ankara Ticaret Odası, 'Rakamlarla Ankara' (Ağustos 2026), s. 9, Tablo 5; "
                                "kaynak olarak TÜİK ADNKS gösteriliyor (Erişim: 12 Şubat 2026)"),
    "rakamlarin_alindigi_url": ("https://atonet.org.tr/Uploads/Birimler/Internet/Flash%20Tan%C4%B1t%C4%B1m%20Alan%C4%B1/"
                                "2026-08-04-RakamlarlaAnkara/2026-08-03-Rakamlarla-Ankara.pdf"),
}
WORLDPOP_KAYNAK = {
    "urun": "WorldPop Global2 R2025A v1, sınırlandırılmış (constrained), 100 m (3 açı saniyesi)",
    "yil": NUFUS_YILI,
    "doi": "10.5258/SOTON/WP00839",
    "lisans": "CC BY 4.0 (https://hub.worldpop.org/data/licence.txt)",
    "alfa_notu": ("WorldPop: 'The dataset currently represents an alpha version (R2025A) product and may "
                  "change over the coming year as improvements are made.'"),
    "birim": "piksel başına kişi sayısı",
}


# --------------------------------------------------------------------------- indirme

def sha256_hesapla(yol: Path) -> str:
    """Dosyanın SHA-256 özetini (hex) parça parça okuyarak hesaplar."""
    ozet = hashlib.sha256()
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(PARCA_BOYUTU), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def raster_indir(url: str = WORLDPOP_URL, dizin: Path = NUFUS_DIZINI, istek: Callable = requests.get) -> Path:
    """Nüfus rasterını indirir; dosya varsa tekrar indirmez.

    WorldPop MD5 yayımlamadığı için bütünlük, inen bayt sayısının sunucunun
    bildirdiği boyuta eşitliğiyle kontrol edilir; eksikse .part silinir.
    """
    hedef = dizin / url.rsplit("/", 1)[-1]
    if hedef.exists():
        return hedef
    dizin.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_name(hedef.name + ".part")
    inen, beklenen = tekrar_dene(akisla_indir, url, gecici, istek=istek)
    if beklenen and inen != beklenen:
        gecici.unlink(missing_ok=True)
        raise ValueError(f"Eksik indirme: {inen} / {beklenen} bayt")
    gecici.replace(hedef)
    return hedef


# --------------------------------------------------------------------------- raster -> altıgen

def pencere_oku(raster: Path, kutu: tuple[float, float, float, float] = KESIT_KUTUSU) -> tuple[np.ndarray, object]:
    """Rasterın (batı, güney, doğu, kuzey) kutusundaki penceresini okur.

    nodata ve negatif değerler 0 yapılır. (dizi, pencere dönüşümü) döndürür.
    """
    with rasterio.open(raster) as kaynak:
        pencere = rasterio.windows.from_bounds(*kutu, transform=kaynak.transform).round_offsets().round_lengths()
        dizi = kaynak.read(1, window=pencere).astype("float64")
        if kaynak.nodata is not None:
            dizi[dizi == kaynak.nodata] = 0.0
        donusum = kaynak.window_transform(pencere)
    dizi[~np.isfinite(dizi) | (dizi < 0)] = 0.0
    return dizi, donusum


def pikselleri_altigenlere_ata(
    dizi: np.ndarray,
    donusum,
    sinirlar: gpd.GeoDataFrame,
    cozunurluk: int = H3_COZUNURLUK,
) -> pd.DataFrame:
    """Sıfır olmayan her pikseli merkezinin düştüğü H3 altıgenine ve ilçeye atar.

    İlçe, ilçe poligonlarının rastere işlenmesiyle (piksel merkezi poligonda
    mı) bulunur; 7 ilçe dışındaki pikseller atılır. Bir altıgen iki ilçeye
    taşıyorsa nüfusu ilçelere bölünmüş olarak ayrı satırlarda kalır.
    Döner: h3, ilce, nufus (altıgen-ilçe başına toplam).
    """
    ilce_adlari = list(sinirlar["ilce"])
    ilce_rasteri = rasterio.features.rasterize(
        ((g, i + 1) for i, g in enumerate(sinirlar.geometry)),
        out_shape=dizi.shape, transform=donusum, fill=0, dtype="int16", all_touched=False,
    )
    satir, sutun = np.nonzero((dizi > 0) & (ilce_rasteri > 0))
    if len(satir) == 0:
        return pd.DataFrame({"h3": pd.Series(dtype=object), "ilce": pd.Series(dtype=object),
                             "nufus": pd.Series(dtype=float)})
    xs, ys = rasterio.transform.xy(donusum, satir, sutun, offset="center")
    df = pd.DataFrame({
        "h3": [h3.latlng_to_cell(y, x, cozunurluk) for x, y in zip(xs, ys)],
        "ilce": np.array(ilce_adlari, dtype=object)[ilce_rasteri[satir, sutun] - 1],
        "nufus": dizi[satir, sutun],
    })
    return df.groupby(["h3", "ilce"], as_index=False)["nufus"].sum()


# --------------------------------------------------------------------------- ağırlıklı özetler

def agirlikli_medyan(degerler, agirliklar) -> float:
    """Ağırlıklı (alt) medyan: sıralı değerlerde birikimli ağırlığın toplamın yarısına ulaştığı ilk değer.

    Değeri NaN olan veya ağırlığı <= 0 / NaN olan satırlar yok sayılır.
    Geçerli satır yoksa NaN döner.
    """
    d = np.asarray(degerler, dtype=float)
    w = np.asarray(agirliklar, dtype=float)
    gecerli = np.isfinite(d) & np.isfinite(w) & (w > 0)
    d, w = d[gecerli], w[gecerli]
    if len(d) == 0:
        return float("nan")
    sira = np.argsort(d, kind="stable")
    d, w = d[sira], w[sira]
    birikimli = np.cumsum(w)
    return float(d[np.searchsorted(birikimli, birikimli[-1] / 2, side="left")])


def _bolge_ozeti(df: pd.DataFrame, sure: str, esik_dk: float) -> dict:
    """Bir bölgenin (ilçe veya toplam) bir kategori için nüfus özeti."""
    toplam = float(df["nufus"].sum())
    bilinen = df[df[sure].notna()]
    kapsanan = float(bilinen["nufus"].sum())
    disarida = float(bilinen.loc[bilinen[sure] > esik_dk, "nufus"].sum())
    return {
        "nufus": round(toplam),
        "kapsanan_nufus": round(kapsanan),
        "kapsanan_oran": round(kapsanan / toplam, 4) if toplam else np.nan,
        "agirlikli_medyan_dk": round(agirlikli_medyan(bilinen[sure], bilinen["nufus"]), 2),
        "esik_disi_kisi": round(disarida),
        "esik_disi_oran": round(disarida / kapsanan, 4) if kapsanan else np.nan,
    }


def nufus_ozet_tablosu(
    altigen_nufus: pd.DataFrame,
    altigenler: pd.DataFrame,
    kategoriler: list[str] = KATEGORILER,
    esik_dk: float = ESIK_DK,
    ilceler: list[str] = ILCELER,
) -> pd.DataFrame:
    """Kategori x (7 ilçe toplamı + ilçeler) nüfus ağırlıklı özet tablosu.

    altigen_nufus: h3, ilce, nufus. altigenler: h3 indeksli, '<k>_dk' sütunları.
    Süresi bilinmeyen (düğümsüz ya da ağa bağlı olmayan) altıgenlerin nüfusu
    'nufus'a girer ama 'kapsanan_nufus'a girmez; oranlar kapsanan nüfusa göredir.
    15 dk dışı: süre > esik_dk (tam 15,0 içeride sayılır).
    """
    sureler = altigenler[[f"{k}_dk" for k in kategoriler]]
    df = altigen_nufus.join(sureler, on="h3")
    satirlar = []
    for k in kategoriler:
        for bolge in [TOPLAM, *ilceler]:
            parca = df if bolge == TOPLAM else df[df["ilce"] == bolge]
            satirlar.append({"kategori": k, "bolge": bolge, **_bolge_ozeti(parca, f"{k}_dk", esik_dk)})
    return pd.DataFrame(satirlar)


def bosluk_raporu(altigen_nufus: pd.DataFrame, altigenler: pd.DataFrame, en_cok: int = 10) -> dict:
    """Nüfusu olduğu halde süresi bilinmeyen altıgenler.

    (a) 'dugumsuz': içinde hiç yol düğümü olmayan altıgenler (OSM'de yol eksik olabilir).
    (b) 'bagsiz': sadece ana ağa bağlı olmayan küçük parça düğümleri olanlar.
    Her grup için altıgen sayısı, nüfus, toplam nüfusa oranı ve en kalabalık altıgenler.
    """
    hex_nufus = altigen_nufus.groupby("h3").agg(
        nufus=("nufus", "sum"), ilce=("ilce", lambda s: s.iloc[0]))
    toplam = float(hex_nufus["nufus"].sum())
    dugumsuz = hex_nufus[~hex_nufus.index.isin(altigenler.index)]
    bagsiz_idx = altigenler.index[~altigenler["bagli"].astype(bool)]
    bagsiz = hex_nufus[hex_nufus.index.isin(bagsiz_idx)]

    def grup(g):
        ilk = g.sort_values("nufus", ascending=False).head(en_cok)
        return {
            "altigen": int(len(g)),
            "nufus": round(float(g["nufus"].sum())),
            "oran": round(float(g["nufus"].sum()) / toplam, 4) if toplam else None,
            "en_kalabalik": [{"h3": h, "ilce": r["ilce"], "nufus": round(float(r["nufus"])),
                              "enlem": round(h3.cell_to_latlng(h)[0], 5), "boylam": round(h3.cell_to_latlng(h)[1], 5)}
                             for h, r in ilk.iterrows()],
        }

    return {"toplam_nufus": round(toplam), "dugumsuz": grup(dugumsuz), "bagsiz": grup(bagsiz)}


def karsilastirma_tablosu(ozet: pd.DataFrame, dugum_ozeti: dict, kategoriler: list[str] = KATEGORILER) -> pd.DataFrame:
    """Kategori başına ≤15 dk düğüm oranı (Faz 2) ile ≤15 dk nüfus oranını yan yana koyar."""
    toplam = ozet[ozet["bolge"] == TOPLAM].set_index("kategori")
    satirlar = []
    for k in kategoriler:
        dugum = dugum_ozeti[k]["esik_alti_oran"]
        nufus = 1 - toplam.loc[k, "esik_disi_oran"]
        satirlar.append({
            "kategori": k,
            "dugum_esik_ici_oran": round(dugum, 4),
            "nufus_esik_ici_oran": round(nufus, 4),
            "fark_puan": round(100 * (nufus - dugum), 1),
            "dugum_medyan_dk": dugum_ozeti[k]["medyan_dk"],
            "nufus_agirlikli_medyan_dk": toplam.loc[k, "agirlikli_medyan_dk"],
        })
    return pd.DataFrame(satirlar)


def tuik_karsilastirmasi(altigen_nufus: pd.DataFrame, tuik: dict = TUIK_ADNKS_2025) -> pd.DataFrame:
    """İlçe başına raster nüfus toplamını TÜİK ADNKS ile karşılaştırır."""
    raster = altigen_nufus.groupby("ilce")["nufus"].sum()
    satirlar = [{"ilce": i, "worldpop": round(float(raster.get(i, 0.0))), "tuik_adnks": t} for i, t in tuik.items()]
    satirlar.append({"ilce": TOPLAM, "worldpop": sum(s["worldpop"] for s in satirlar), "tuik_adnks": sum(tuik.values())})
    df = pd.DataFrame(satirlar)
    df["fark"] = df["worldpop"] - df["tuik_adnks"]
    df["fark_oran"] = (df["fark"] / df["tuik_adnks"]).round(4)
    return df


def altigen_nufuslari(altigen_nufus: pd.DataFrame) -> pd.Series:
    """Altıgen başına toplam nüfus (ilçe parçaları birleştirilmiş)."""
    return altigen_nufus.groupby("h3")["nufus"].sum()


def soluk_ozeti(altigenler: pd.DataFrame, nufuslar: pd.Series, esik: float = SOLUK_NUFUS_ESIGI) -> dict:
    """Haritada soluk gösterilecek (bağlı, nüfusu < esik) altıgenlerin sayısı ve nüfus payı."""
    bagli = altigenler[altigenler["bagli"].astype(bool)]
    n = nufuslar.reindex(bagli.index).fillna(0.0)
    soluk = n < esik
    return {
        "esik": esik,
        "soluk_altigen": int(soluk.sum()),
        "bagli_altigen": int(len(bagli)),
        "soluk_nufus": round(float(n[soluk].sum())),
        "soluk_nufus_orani": round(float(n[soluk].sum() / n.sum()), 5) if n.sum() else None,
    }


# --------------------------------------------------------------------------- akış

def _yuzde(x) -> str:
    return "-" if pd.isna(x) else f"%{100 * x:.1f}"


def main() -> None:
    """Rasterı indirir, nüfusu altıgenlere atar, tabloları yazar ve basar."""
    raster = raster_indir()
    veri_surumu_bolumu_yaz("nufus", {**WORLDPOP_KAYNAK, "url": WORLDPOP_URL, "dosya": raster.name,
                                      "boyut_mb": round(raster.stat().st_size / 2**20, 1),
                                      "sha256": sha256_hesapla(raster)})
    veri_surumu_bolumu_yaz("nufus_dogrulama", {**TUIK_KAYNAK, "ilce_nufuslari": TUIK_ADNKS_2025})

    sinirlar = gpd.read_file(SINIR_DOSYASI)
    dizi, donusum = pencere_oku(raster)
    altigen_nufus = pikselleri_altigenlere_ata(dizi, donusum, sinirlar)
    SONUC_DIZINI.mkdir(parents=True, exist_ok=True)
    altigen_nufus.to_parquet(SONUC_DIZINI / "altigen_nufus.parquet", index=False)
    altigenler = pd.read_parquet(SONUC_DIZINI / "altigenler.parquet").set_index("h3")
    dugum_ozeti = json.loads((SONUC_DIZINI / "rapor.json").read_text(encoding="utf-8"))["ozet"]

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    tuik = tuik_karsilastirmasi(altigen_nufus)
    print("\nTÜİK ADNKS 2025 ile karşılaştırma (raster toplamı, piksel merkezi ilçe içinde):")
    print(tuik.assign(fark_oran=tuik["fark_oran"].map(_yuzde)).to_string(index=False))

    bosluk = bosluk_raporu(altigen_nufus, altigenler)
    print(f"\nVeri boşluğu (toplam {bosluk['toplam_nufus']:,} kişi):")
    for ad, aciklama in [("dugumsuz", "düğümsüz altıgen"), ("bagsiz", "sadece ağa bağlı olmayan düğüm")]:
        g = bosluk[ad]
        print(f"  {aciklama}: {g['altigen']:,} altıgen, {g['nufus']:,} kişi ({_yuzde(g['oran'])})")
        for r in g["en_kalabalik"][:5]:
            print(f"      {r['ilce']:12s} {r['nufus']:>6,} kişi  ({r['enlem']}, {r['boylam']})")

    ozet = nufus_ozet_tablosu(altigen_nufus, altigenler)
    ozet.to_csv(SONUC_DIZINI / "nufus_ozet.csv", index=False, encoding="utf-8-sig")
    print("\nNüfus ağırlıklı özet (oranlar kapsanan nüfusa göre):")
    goster = ozet.assign(kapsanan_oran=ozet["kapsanan_oran"].map(_yuzde),
                         esik_disi_oran=ozet["esik_disi_oran"].map(_yuzde))
    for k in KATEGORILER:
        print(f"\n[{k}]")
        print(goster[goster["kategori"] == k].drop(columns="kategori").to_string(index=False))

    karsilastirma = karsilastirma_tablosu(ozet, dugum_ozeti)
    karsilastirma.to_csv(SONUC_DIZINI / "dugum_nufus_karsilastirma.csv", index=False, encoding="utf-8-sig")
    print("\n≤15 dk: düğüm oranı (Faz 2) ve nüfus oranı:")
    print(karsilastirma.assign(dugum_esik_ici_oran=karsilastirma["dugum_esik_ici_oran"].map(_yuzde),
                               nufus_esik_ici_oran=karsilastirma["nufus_esik_ici_oran"].map(_yuzde)).to_string(index=False))

    soluk = soluk_ozeti(altigenler, altigen_nufuslari(altigen_nufus))
    print(f"\nSoluk (<{soluk['esik']} kişi): {soluk['soluk_altigen']:,} / {soluk['bagli_altigen']:,} bağlı altıgen, "
          f"{soluk['soluk_nufus']:,} kişi ({_yuzde(soluk['soluk_nufus_orani'])})")

    rapor = {"tuik": tuik.to_dict(orient="records"), "bosluk": bosluk, "soluk": soluk,
             "karsilastirma": karsilastirma.to_dict(orient="records")}
    (SONUC_DIZINI / "nufus_rapor.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2, default=float),
                                                   encoding="utf-8")


if __name__ == "__main__":
    main()
