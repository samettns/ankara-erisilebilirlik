"""Düğüm başına yürüme sürelerini H3 altıgenlerine toplar.

Her altıgen için kategori başına: içindeki ana parça düğümlerinin medyan
süresi ve en sık geçen en yakın tesis (eşitlikte süresi kısa olan). Sadece
küçük parça düğümleri içeren altıgenler bagli=False olarak işaretlenir.
Sadece 7 ilçe içindeki düğümler kullanılır; düğümsüz altıgen üretilmez.

Çalıştırma (proje kökünden):
    python -m src.altigen
"""

import time
from pathlib import Path

import geopandas as gpd
import h3
import pandas as pd

try:
    from src import ag, mesafe
    from src.ayarlar import BIRLESIK_AG_DOSYASI, H3_COZUNURLUK, HEDEF_DIZINI, SINIR_DOSYASI, SONUC_DIZINI
    from src.mesafe import KATEGORILER
    from src.veri_cek import guncel_mi
except ModuleNotFoundError:
    import ag
    import mesafe
    from ayarlar import BIRLESIK_AG_DOSYASI, H3_COZUNURLUK, HEDEF_DIZINI, SINIR_DOSYASI, SONUC_DIZINI
    from mesafe import KATEGORILER
    from veri_cek import guncel_mi

ISIMSIZ = {
    "eczane": "isimsiz eczane",
    "hastane": "isimsiz hastane",
    "birinci_basamak": "isimsiz sağlık tesisi",
    "rayli": "isimsiz istasyon",
    "baskentray": "isimsiz istasyon",
}


def hucrelere_ata(dugumler: pd.DataFrame, cozunurluk: int = H3_COZUNURLUK) -> pd.Series:
    """Her düğümün (x=boylam, y=enlem) H3 hücre kimliğini döndürür."""
    return pd.Series([h3.latlng_to_cell(y, x, cozunurluk) for x, y in zip(dugumler["x"], dugumler["y"])],
                     index=dugumler.index, dtype=object)


def _en_sik_tesis(df: pd.DataFrame, sure: str, tesis: str) -> pd.Series:
    """Altıgen başına en sık geçen tesis; eşitlikte en kısa süreli, sonra kimlik sırası."""
    sayim = (df.dropna(subset=[tesis]).groupby(["h3", tesis])
             .agg(adet=(sure, "size"), en_kisa=(sure, "min")).reset_index())
    sayim = sayim.sort_values(["h3", "adet", "en_kisa", tesis], ascending=[True, False, True, True])
    return sayim.drop_duplicates("h3").set_index("h3")[tesis]


def altigenleri_topla(dugumler: pd.DataFrame, kategoriler: list[str] = KATEGORILER,
                      cozunurluk: int = H3_COZUNURLUK) -> pd.DataFrame:
    """Düğüm tablosunu H3 altıgenlerine toplar.

    dugumler: x, y, ilce, bagli (ana parçada mı) ve kategori başına
    '<k>_dk', '<k>_id' sütunları (küçük parça düğümlerinde boş).
    ilce'si boş (7 ilçe dışı) düğümler atılır.
    Döner (h3 indeksli): ilce (çoğunluk), dugum, bagli_dugum, bagli ve
    kategori başına '<k>_dk' (bağlı düğümlerin medyanı) ve '<k>_id'.
    """
    df = dugumler[dugumler["ilce"].notna()].copy()
    df["h3"] = hucrelere_ata(df, cozunurluk)
    gruplar = df.groupby("h3")
    sonuc = pd.DataFrame({
        "ilce": gruplar["ilce"].agg(lambda s: s.value_counts().index[0]),
        "dugum": gruplar.size(),
        "bagli_dugum": gruplar["bagli"].sum().astype(int),
    })
    sonuc["bagli"] = sonuc["bagli_dugum"] > 0

    bagli = df[df["bagli"]]
    for k in kategoriler:
        sonuc[f"{k}_dk"] = bagli.groupby("h3")[f"{k}_dk"].median().reindex(sonuc.index)
        sonuc[f"{k}_id"] = _en_sik_tesis(bagli, f"{k}_dk", f"{k}_id").reindex(sonuc.index)
    sonuc.index.name = "h3"
    return sonuc


def tesis_tablosu(dizin: Path = HEDEF_DIZINI) -> pd.DataFrame:
    """Kategori başına gösterilecek tesisler: kategori, tesis (node/123), ad, ic, geometry.

    Hastanelerde kampüs başına tek temsilî nokta, raylıda istasyonlar
    (girişler değil) kullanılır. Adı olmayan tesislere 'isimsiz ...' yazılır.
    ic: tesis 7 ilçeden birinin içinde mi (hedef dosyasındaki 'ilce' dolu mu);
    'ilce' sütunu yoksa True.
    """
    def oku(dosya, katman, kategori, maske=None):
        gdf = gpd.read_file(dizin / dosya, layer=katman)
        if maske is not None:
            gdf = gdf[maske(gdf)]
        ad = gdf["name"].where(gdf["name"].notna() & (gdf["name"].astype(str).str.strip() != ""), ISIMSIZ[kategori])
        return gpd.GeoDataFrame({
            "kategori": kategori,
            "tesis": gdf["element"].astype(str) + "/" + gdf["id"].astype("int64").astype(str),
            "ad": ad.astype(str),
            "ic": gdf["ilce"].notna().to_numpy() if "ilce" in gdf.columns else True,
        }, geometry=gdf.geometry.values, crs=gdf.crs)

    parcalar = [
        oku("eczaneler.gpkg", "tesisler", "eczane"),
        oku("hastaneler.gpkg", "tesisler", "hastane"),
        oku("birinci_basamak.gpkg", "tesisler", "birinci_basamak"),
        oku("rayli.gpkg", "istasyonlar", "rayli", lambda g: g["metro"] | g["ankaray"]),
        oku("rayli.gpkg", "istasyonlar", "baskentray", lambda g: g["baskentray"]),
    ]
    return gpd.GeoDataFrame(pd.concat(parcalar, ignore_index=True), crs="EPSG:4326")


def adlari_ekle(altigenler: pd.DataFrame, tesisler: pd.DataFrame,
                kategoriler: list[str] = KATEGORILER) -> pd.DataFrame:
    """Her kategori için '<k>_id' tesis kimliğini '<k>_ad' tesis adına çevirir."""
    sonuc = altigenler.copy()
    for k in kategoriler:
        adlar = tesisler[tesisler["kategori"] == k].drop_duplicates("tesis").set_index("tesis")["ad"]
        bulunan = sonuc[f"{k}_id"].map(adlar)
        sonuc[f"{k}_ad"] = bulunan.where(bulunan.notna() | sonuc[f"{k}_id"].isna(), ISIMSIZ[k])
    return sonuc


def tum_dugumler(sureler: pd.DataFrame, G, sinirlar: gpd.GeoDataFrame) -> pd.DataFrame:
    """Ana parça süreleriyle küçük parça düğümlerini tek tabloda birleştirir (bagli sütunu ile)."""
    ana = sureler.set_index("osmid")
    tablo = mesafe.dugum_tablosu(G, sinirlar).drop(columns="geometry")
    kucuk = tablo[~tablo.index.isin(ana.index)]
    birlesik = pd.concat([ana.assign(bagli=True), kucuk.assign(bagli=False)])
    return birlesik


def main() -> None:
    """Altıgenleri hesaplar ve data/sonuclar/altigenler.parquet'e yazar."""
    cikti = SONUC_DIZINI / "altigenler.parquet"
    sure_dosyasi = SONUC_DIZINI / "dugum_sureleri.parquet"
    if guncel_mi(cikti, [sure_dosyasi, *sorted(HEDEF_DIZINI.glob("*.gpkg"))]):
        print(f"[hazır] {cikti}")
        return
    t0 = time.time()
    dugumler = tum_dugumler(pd.read_parquet(sure_dosyasi), ag.sade_graf_oku(BIRLESIK_AG_DOSYASI),
                            gpd.read_file(SINIR_DOSYASI))
    altigenler = adlari_ekle(altigenleri_topla(dugumler), tesis_tablosu())
    altigenler.reset_index().to_parquet(cikti, index=False)
    print(f"{len(altigenler):,} altıgen (ağa bağlı olmayan {(~altigenler['bagli']).sum()}), "
          f"{int(altigenler['dugum'].sum()):,} düğüm, {time.time() - t0:.0f} sn -> {cikti}")


if __name__ == "__main__":
    main()
