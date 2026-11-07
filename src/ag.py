"""Yürüme ağı: ilçe başına graf kurma, sadeleştirme ve tek grafta birleştirme."""

from pathlib import Path

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry.base import BaseGeometry


def genislet(geometri: BaseGeometry, metre: float) -> BaseGeometry:
    """EPSG:4326 geometriyi metrik projeksiyonda verilen metre kadar genişletir.

    Tampon derece cinsinden değil, ilçeye uygun UTM diliminde metre olarak
    hesaplanır; sonuç yine EPSG:4326'dır.
    """
    seri = gpd.GeoSeries([geometri], crs="EPSG:4326")
    metrik = seri.to_crs(seri.estimate_utm_crs())
    return metrik.buffer(metre).to_crs("EPSG:4326").iloc[0]


def ilce_agi_kur(xml_yolu: Path, cikti: Path) -> nx.MultiDiGraph:
    """Yürüme yollarını içeren OSM XML'den OSMnx grafı kurar ve GraphML olarak kaydeder.

    XML zaten walk filtresinden geçmiş yolları ve konut sitelerindeki özel
    yolları (access=private) içerir (osm_yerel). Yürüme ağı iki yönlü
    olduğundan bidirectional=True; tüm bileşenler (retain_all) tutulur.
    Sadeleştirme edge_attrs_differ=["access"] ile yapılır: kamu yolu ile özel
    yolun buluştuğu kapı düğümü korunur, iki tür tek kenarda birleşmez.
    """
    G = ox.graph_from_xml(xml_yolu, bidirectional=True, simplify=False, retain_all=True)
    G = ox.simplify_graph(G, edge_attrs_differ=["access"])
    cikti.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, cikti)
    return G


def ozel_kenar_mi(veri: dict) -> bool:
    """OSMnx kenarı özel (access=private) mi? Birleşmiş kenarlarda liste de olabilir."""
    erisim = veri.get("access")
    if isinstance(erisim, (list, tuple)):
        return "private" in erisim
    return erisim == "private"


def _kenar_ekle(H: nx.Graph, u, v, uzunluk: float, ozel: bool) -> None:
    """Kenarı ekler; varsa kamu kenarı özelden, aynı türde kısa olan uzundan öncelikli."""
    if H.has_edge(u, v):
        mevcut = H[u][v]
        if (ozel, uzunluk) >= (mevcut["ozel"], mevcut["length"]):
            return
    H.add_edge(u, v, length=uzunluk, ozel=ozel)


def sadelestir(G: nx.MultiDiGraph) -> nx.Graph:
    """OSMnx grafını analiz için hafif, yönsüz bir grafa çevirir.

    Düğümlerde sadece x, y; kenarlarda length (metre) ve ozel (konut sitesi
    içi özel yol) tutulur. Aynı iki düğüm arasında kamu kenarı varsa o kalır
    (iki ucu kamu ağında olan özel kenar sadece kestirme yaratır); aynı
    türden paralel kenarlardan en kısası kalır. Kendine dönen kenarlar atılır.
    Yürüme ağı iki yönlü olduğundan yönsüz graf sonuçları değiştirmez.
    """
    H = nx.Graph()
    H.add_nodes_from((n, {"x": float(d["x"]), "y": float(d["y"])}) for n, d in G.nodes(data=True))
    for u, v, d in G.edges(data=True):
        if u != v:
            _kenar_ekle(H, u, v, float(d["length"]), ozel_kenar_mi(d))
    return H


def birlestir(graflar: list[nx.Graph]) -> nx.Graph:
    """Sadeleştirilmiş ilçe graflarını tek grafta birleştirir.

    OSM düğüm kimlikleri ortak olduğundan örtüşen bölgelerdeki düğümler
    tekleşir ve ilçe sınırında ağ kesintisiz bağlanır. Aynı kenar birden
    fazla grafta varsa sadelestir'deki öncelik geçerlidir (kamu, sonra kısa).
    """
    B = nx.Graph()
    for G in graflar:
        B.add_nodes_from(G.nodes(data=True))
        for u, v, d in G.edges(data=True):
            _kenar_ekle(B, u, v, d["length"], bool(d.get("ozel", False)))
    return B


def sade_graf_kaydet(G: nx.Graph, yol: Path) -> None:
    """Sadeleştirilmiş grafı GraphML olarak kaydeder (tipler dosyada saklanır)."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(G, yol)


def sade_graf_oku(yol: Path) -> nx.Graph:
    """sade_graf_kaydet ile yazılmış grafı okur; düğüm kimliklerini int yapar."""
    return nx.read_graphml(yol, node_type=int)
