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

    XML zaten walk filtresinden geçmiş yolları içerir (osm_yerel). Yürüme
    ağı iki yönlü olduğundan bidirectional=True; tüm bileşenler (retain_all)
    tutulur, OSMnx'in standart sadeleştirmesi uygulanır.
    """
    G = ox.graph_from_xml(xml_yolu, bidirectional=True, simplify=True, retain_all=True)
    cikti.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, cikti)
    return G


def sadelestir(G: nx.MultiDiGraph) -> nx.Graph:
    """OSMnx grafını analiz için hafif, yönsüz bir grafa çevirir.

    Düğümlerde sadece x, y; kenarlarda sadece length (metre) tutulur.
    Aynı iki düğüm arasındaki paralel/ters yönlü kenarlardan en kısası kalır,
    kendine dönen kenarlar atılır. Yürüme ağı iki yönlü olduğundan yönsüz
    graf en kısa yol sonuçlarını değiştirmez.
    """
    H = nx.Graph()
    H.add_nodes_from((n, {"x": float(d["x"]), "y": float(d["y"])}) for n, d in G.nodes(data=True))
    for u, v, d in G.edges(data=True):
        if u == v:
            continue
        uzunluk = float(d["length"])
        if not H.has_edge(u, v) or uzunluk < H[u][v]["length"]:
            H.add_edge(u, v, length=uzunluk)
    return H


def birlestir(graflar: list[nx.Graph]) -> nx.Graph:
    """Sadeleştirilmiş ilçe graflarını tek grafta birleştirir.

    OSM düğüm kimlikleri ortak olduğundan örtüşen bölgelerdeki düğümler
    tekleşir ve ilçe sınırında ağ kesintisiz bağlanır. Aynı kenar birden
    fazla grafta varsa en kısa uzunluk kalır.
    """
    B = nx.Graph()
    for G in graflar:
        B.add_nodes_from(G.nodes(data=True))
        for u, v, d in G.edges(data=True):
            if not B.has_edge(u, v) or d["length"] < B[u][v]["length"]:
                B.add_edge(u, v, length=d["length"])
    return B


def sade_graf_kaydet(G: nx.Graph, yol: Path) -> None:
    """Sadeleştirilmiş grafı GraphML olarak kaydeder (tipler dosyada saklanır)."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(G, yol)


def sade_graf_oku(yol: Path) -> nx.Graph:
    """sade_graf_kaydet ile yazılmış grafı okur; düğüm kimliklerini int yapar."""
    return nx.read_graphml(yol, node_type=int)
