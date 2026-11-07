"""Yürüme ağı: OSM XML'den tek parça graf kurma ve analiz için sadeleştirme."""

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


# Ağ kurma yöntemi değişince artırılır; birleşik ağ yeniden kurulur.
# 2: ağ ilçe ilçe değil tek parça kurulup bir kez sadeleştiriliyor
# 3: kamu kenarına paralel özel bağlantı atılmıyor, ozel_length olarak saklanıyor
AG_SURUMU = 3


def ag_kur(xml_yolu: Path) -> nx.MultiDiGraph:
    """Yürüme yollarını içeren OSM XML'den sadeleştirilmiş OSMnx grafı kurar.

    XML zaten walk filtresinden geçmiş yolları ve konut sitelerindeki özel
    yolları (access=private) içerir (osm_yerel). Yürüme ağı iki yönlü
    olduğundan bidirectional=True; tüm bileşenler (retain_all) tutulur.
    Sadeleştirme edge_attrs_differ=["access"] ile yapılır: kamu yolu ile özel
    yolun buluştuğu kapı düğümü korunur, iki tür tek kenarda birleşmez.
    Sadeleştirme tüm ağ üzerinde bir kez yapılmalıdır (bkz. veri_cek.birlesik_agi_kur).
    """
    G = ox.graph_from_xml(xml_yolu, bidirectional=True, simplify=False, retain_all=True)
    return ox.simplify_graph(G, edge_attrs_differ=["access"])


def ozel_kenar_mi(veri: dict) -> bool:
    """OSMnx kenarı özel (access=private) mi? Birleşmiş kenarlarda liste de olabilir."""
    erisim = veri.get("access")
    if isinstance(erisim, (list, tuple)):
        return "private" in erisim
    return erisim == "private"


def _kenar_ekle(H: nx.Graph, u, v, uzunluk: float, ozel: bool) -> None:
    """u-v kenarını ekler; aynı iki düğüm arasında kamu ve özel bağlantı ayrı tutulur.

    - length/ozel: kamu bağlantısı varsa en kısa kamu uzunluğu ve ozel=False,
      yoksa en kısa özel uzunluk ve ozel=True. 1. aşama (kamu) sadece
      ozel=False kenarları length ile kullanır; özel yol kestirme olamaz.
    - ozel_length: kamu kenarının yanında ayrıca paralel bir özel bağlantı
      varsa onun en kısa uzunluğu. 2. aşama (site içi) bunu kullanır; aksi
      halde kısa özel bağlantı kaybolur ve site içi süreler uzar.
    """
    if not H.has_edge(u, v):
        H.add_edge(u, v, length=uzunluk, ozel=ozel)
        return
    m = H[u][v]
    if ozel and m["ozel"]:
        m["length"] = min(m["length"], uzunluk)
    elif ozel:  # mevcut kamu kenarının yanına paralel özel bağlantı
        m["ozel_length"] = min(m.get("ozel_length", uzunluk), uzunluk)
    elif m["ozel"]:  # mevcut özel kenar, şimdi kamu bağlantısı da var
        m["ozel_length"] = m["length"]
        m["length"], m["ozel"] = uzunluk, False
    else:
        m["length"] = min(m["length"], uzunluk)


def ozel_uzunluk(veri: dict) -> float | None:
    """Kenarın 2. aşamada (site içi yollar) kullanılacak uzunluğu; özel bağlantı yoksa None."""
    if veri.get("ozel", False):
        return veri["length"]
    return veri.get("ozel_length")


def sadelestir(G: nx.MultiDiGraph) -> nx.Graph:
    """OSMnx grafını analiz için hafif, yönsüz bir grafa çevirir.

    Düğümlerde sadece x, y; kenarlarda length (metre), ozel (konut sitesi
    içi özel yol) ve gerekirse ozel_length tutulur (bkz. _kenar_ekle): kamu
    bağlantısı varsa length onundur, paralel özel bağlantı ozel_length'te
    saklanır. Aynı türden paralel kenarlardan en kısası kalır. Kendine dönen
    kenarlar atılır.
    Yürüme ağı iki yönlü olduğundan yönsüz graf sonuçları değiştirmez.
    """
    H = nx.Graph()
    H.add_nodes_from((n, {"x": float(d["x"]), "y": float(d["y"])}) for n, d in G.nodes(data=True))
    for u, v, d in G.edges(data=True):
        if u != v:
            _kenar_ekle(H, u, v, float(d["length"]), ozel_kenar_mi(d))
    return H


def ham_graf_kur(yollar, siralar) -> nx.Graph:
    """Seçilen yollardan sadeleştirilmemiş yalın graf kurar (doğrulama için).

    Her ardışık düğüm çifti bir kenardır; uzunluk OSMnx ile aynı büyük daire
    formülüyle hesaplanır, ozel bayrağı yolun bayrağıdır. Sadeleştirilmiş
    ağın mesafeleri, ortak düğümlerde bu grafınkiyle aynı olmalıdır.
    yollar: osm_yerel.YuruyusYollari, siralar: seçilen yol sıra numaraları.
    """
    H = nx.Graph()
    for i in siralar:
        bas, son = yollar.baslangic[i], yollar.baslangic[i + 1]
        d, x, y = yollar.dugum_idleri[bas:son], yollar.lon[bas:son], yollar.lat[bas:son]
        uzunluk = ox.distance.great_circle(y[:-1], x[:-1], y[1:], x[1:])
        for j in range(len(d)):
            H.add_node(int(d[j]), x=float(x[j]), y=float(y[j]))
        for j in range(len(d) - 1):
            if d[j] != d[j + 1]:
                _kenar_ekle(H, int(d[j]), int(d[j + 1]), float(uzunluk[j]), bool(yollar.ozel[i]))
    return H


def sade_graf_kaydet(G: nx.Graph, yol: Path) -> None:
    """Sadeleştirilmiş grafı GraphML olarak kaydeder (tipler dosyada saklanır)."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(G, yol)


def sade_graf_oku(yol: Path) -> nx.Graph:
    """sade_graf_kaydet ile yazılmış grafı okur; düğüm kimliklerini int yapar."""
    return nx.read_graphml(yol, node_type=int)
