"""OSM'den yürüme ağı ve hedef noktaları (eczane vb.) indirip data/ altına kaydeder.

İndirilen her dosya diske yazılır; dosya zaten varsa tekrar indirilmez.
Çalıştırma (proje kökünden):
    python -m src.veri_cek
"""

import re
from difflib import SequenceMatcher
from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox

try:
    from src.ayarlar import (AG_TIPI, ISIM_BENZERLIK_ESIGI, TEKRAR_ESIK_M,
                             VERI_DIZINI, osmnx_ayarla)
except ModuleNotFoundError:  # doğrudan `python src/veri_cek.py` ile çalıştırılırsa
    from ayarlar import (AG_TIPI, ISIM_BENZERLIK_ESIGI, TEKRAR_ESIK_M,
                         VERI_DIZINI, osmnx_ayarla)

ECZANE_ETIKETLERI = {"amenity": "pharmacy"}
TUTULACAK_SUTUNLAR = ["element", "id", "name", "geometry"]


def yer_sorgusu(ilce: str) -> str:
    """İlçe adından Nominatim'e gönderilecek yer sorgusunu üretir."""
    return f"{ilce}, Ankara, Türkiye"


def dosya_adi(ilce: str) -> str:
    """İlçe adını dosya adında kullanılabilecek ASCII küçük harfe çevirir."""
    tablo = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return ilce.translate(tablo).lower().replace(" ", "_")


def yuruyus_agi_indir(ilce: str, veri_dizini: Path = VERI_DIZINI) -> nx.MultiDiGraph:
    """İlçenin yürüme ağını döndürür.

    data/<ilce>_walk.graphml varsa oradan okur, yoksa OSM'den indirip
    bu dosyaya kaydeder.
    """
    yol = veri_dizini / f"{dosya_adi(ilce)}_walk.graphml"
    if yol.exists():
        return ox.load_graphml(yol)

    G = ox.graph_from_place(yer_sorgusu(ilce), network_type=AG_TIPI)
    veri_dizini.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, yol)
    return G


def noktalara_cevir(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """OSM özelliklerini tek tip nokta katmanına çevirir.

    Poligon olarak çizilmiş eczaneler (bina vb.) metrik projeksiyonda
    centroid'e indirgenir. Sadece element, id, name ve geometry sütunları
    tutulur; çıktı EPSG:4326'dır.
    """
    gdf = gdf.reset_index()
    for sutun in TUTULACAK_SUTUNLAR:
        if sutun not in gdf.columns:
            gdf[sutun] = None
    gdf = gdf[TUTULACAK_SUTUNLAR].copy()

    if gdf.empty:
        return gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326")

    metrik = gdf.to_crs(gdf.estimate_utm_crs())
    nokta_degil = ~metrik.geometry.geom_type.eq("Point")
    metrik.loc[nokta_degil, "geometry"] = metrik.loc[nokta_degil].geometry.centroid
    return metrik.to_crs("EPSG:4326")


def isim_normallestir(isim) -> str:
    """İsmi karşılaştırma için sadeleştirir.

    Türkçe kurallara göre küçük harfe çevirir, "eczane"/"eczanesi"
    kelimelerini ve noktalamayı atar. Boş/eksik isim için "" döner.
    Örn. "Eczane Temel" ve "TEMEL ECZANESİ" ikisi de "temel" olur.
    """
    if not isinstance(isim, str):
        return ""
    isim = isim.replace("İ", "i").replace("I", "ı").lower()
    isim = re.sub(r"\beczan(e|esi)\b", " ", isim)
    return " ".join(re.findall(r"\w+", isim))


def isim_benzerligi(a, b) -> float:
    """İki ismin normalleştirilmiş hallerinin 0-1 arası benzerliğini verir.

    İsimlerden biri boşsa 0 döner: isimsiz kayıtlar hiçbir şeye benzemez.
    """
    na, nb = isim_normallestir(a), isim_normallestir(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def tekrarlari_temizle(
    gdf: gpd.GeoDataFrame,
    esik_m: float = TEKRAR_ESIK_M,
    benzerlik_esigi: float = ISIM_BENZERLIK_ESIGI,
) -> gpd.GeoDataFrame:
    """Aynı yerin OSM'deki tekrar kayıtlarını tek kayda indirir.

    Birbirine esik_m metreden yakın ve isim benzerliği benzerlik_esigi'nden
    yüksek kayıtlar aynı yer sayılır. Zincirleme eşleşmeler (A~B, B~C) tek
    grupta birleşir. Her gruptan önce 'node' (nokta) kaydı, sonra ilk kayıt
    tutulur. Girdi coğrafi CRS'te (EPSG:4326) olmalıdır.
    """
    gdf = gdf.reset_index(drop=True)
    if len(gdf) < 2:
        return gdf.copy()

    metrik = gdf.to_crs(gdf.estimate_utm_crs())
    sol, sag = metrik.sindex.query(metrik.geometry, predicate="dwithin", distance=esik_m)

    baglar = nx.Graph()
    baglar.add_nodes_from(range(len(gdf)))
    for i, j in zip(sol, sag):
        if i < j and isim_benzerligi(gdf.at[i, "name"], gdf.at[j, "name"]) >= benzerlik_esigi:
            baglar.add_edge(i, j)

    tutulacak = [
        min(grup, key=lambda k: (gdf.at[k, "element"] != "node", k))
        for grup in nx.connected_components(baglar)
    ]
    return gdf.loc[sorted(tutulacak)].reset_index(drop=True)


def eczaneleri_indir(ilce: str, veri_dizini: Path = VERI_DIZINI) -> gpd.GeoDataFrame:
    """İlçedeki eczaneleri nokta katmanı olarak döndürür.

    data/<ilce>_eczaneler.gpkg varsa oradan okur, yoksa OSM'den indirip
    noktaya çevirir, tekrar kayıtları temizler ve bu dosyaya kaydeder.
    """
    yol = veri_dizini / f"{dosya_adi(ilce)}_eczaneler.gpkg"
    if yol.exists():
        return gpd.read_file(yol)

    ham = ox.features_from_place(yer_sorgusu(ilce), tags=ECZANE_ETIKETLERI)
    eczaneler = tekrarlari_temizle(noktalara_cevir(ham))
    veri_dizini.mkdir(parents=True, exist_ok=True)
    eczaneler.to_file(yol, driver="GPKG")
    return eczaneler


if __name__ == "__main__":
    osmnx_ayarla()
    ILCE = "Çankaya"

    G = yuruyus_agi_indir(ILCE)
    print(f"{ILCE} yürüme ağı: {G.number_of_nodes():,} düğüm, {G.number_of_edges():,} kenar")

    eczaneler = eczaneleri_indir(ILCE)
    print(f"{ILCE} eczane sayısı: {len(eczaneler)}")
    print(f"  isimsiz: {eczaneler['name'].isna().sum()}")
    print(f"Dosyalar: {VERI_DIZINI}")
