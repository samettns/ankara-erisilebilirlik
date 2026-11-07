"""src/ag.py için ağ bağlantısı gerektirmeyen birim testleri."""

import geopandas as gpd
import networkx as nx
import pytest
from shapely.geometry import Point, box

from src import ag


def test_genislet_metre_cinsinden_tampon():
    kare = box(32.85, 39.90, 32.86, 39.91)
    genis = ag.genislet(kare, 2000)
    seri = gpd.GeoSeries([kare, genis], crs="EPSG:4326")
    metrik = seri.to_crs(seri.estimate_utm_crs())
    # Kareden 2000 m uzaktaki nokta içeride, 2100 m uzaktaki dışarıda olmalı
    dogu_kenari = metrik.iloc[0].bounds[2]
    orta_y = metrik.iloc[0].centroid.y
    assert metrik.iloc[1].contains(Point(dogu_kenari + 1990, orta_y))
    assert not metrik.iloc[1].contains(Point(dogu_kenari + 2100, orta_y))


def _osmnx_grafi():
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=32.850, y=39.90, street_count=2)
    G.add_node(2, x=32.851, y=39.90, street_count=2)
    G.add_node(3, x=32.852, y=39.90, street_count=1)
    G.add_edge(1, 2, length=90.0, highway="footway", osmid=10)
    G.add_edge(2, 1, length=90.0, highway="footway", osmid=10)
    G.add_edge(1, 2, length=120.0, highway="residential", osmid=11)  # paralel, daha uzun
    G.add_edge(2, 3, length=85.0, highway="footway", osmid=12)
    G.add_edge(3, 3, length=5.0, highway="footway", osmid=13)  # kendine dönen
    return G


def test_sadelestir_yonsuz_en_kisa_ve_sadece_length():
    H = ag.sadelestir(_osmnx_grafi())
    assert isinstance(H, nx.Graph) and not H.is_directed() and not H.is_multigraph()
    assert H.number_of_edges() == 2
    assert H[1][2] == {"length": 90.0}
    assert not H.has_edge(3, 3)
    assert H.nodes[1] == {"x": 32.850, "y": 39.90}


def test_birlestir_sinirda_kesintisiz_baglar():
    # İki ilçe grafı ortak 2 numaralı düğümü (sınırı kesen yol) paylaşıyor
    batı = nx.Graph()
    batı.add_node(1, x=0.0, y=0.0)
    batı.add_node(2, x=1.0, y=0.0)
    batı.add_edge(1, 2, length=100.0)
    doğu = nx.Graph()
    doğu.add_node(2, x=1.0, y=0.0)
    doğu.add_node(3, x=2.0, y=0.0)
    doğu.add_edge(2, 3, length=50.0)
    doğu.add_edge(1, 2, length=80.0)  # aynı kenar, farklı sadeleştirmeden daha kısa

    B = ag.birlestir([batı, doğu])

    assert B.number_of_nodes() == 3
    assert B[1][2]["length"] == 80.0
    assert nx.shortest_path_length(B, 1, 3, weight="length") == 130.0


def test_sade_graf_kaydet_oku(tmp_path):
    H = ag.sadelestir(_osmnx_grafi())
    yol = tmp_path / "sade.graphml"
    ag.sade_graf_kaydet(H, yol)
    G = ag.sade_graf_oku(yol)
    assert set(G.nodes) == {1, 2, 3}
    assert G[2][3]["length"] == pytest.approx(85.0)
    assert G.nodes[1]["x"] == pytest.approx(32.85)


def test_ilce_agi_kur_xmlden_iki_yonlu_ve_tum_bilesenler(tmp_path):
    xml = tmp_path / "ilce.osm"
    xml.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">\n"
        '  <node id="1" lat="39.9000" lon="32.8500"/>\n'
        '  <node id="2" lat="39.9000" lon="32.8510"/>\n'
        '  <node id="3" lat="39.9010" lon="32.8510"/>\n'
        '  <node id="8" lat="39.9500" lon="32.9000"/>\n'
        '  <node id="9" lat="39.9500" lon="32.9010"/>\n'
        '  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/>'
        '<tag k="oneway" v="yes"/></way>\n'
        '  <way id="20"><nd ref="8"/><nd ref="9"/><tag k="highway" v="path"/></way>\n'  # ayrı bileşen
        "</osm>\n",
        encoding="utf-8",
    )
    cikti = tmp_path / "ilce_walk.graphml"
    G = ag.ilce_agi_kur(xml, cikti)

    assert cikti.exists()
    assert set(G.nodes) == {1, 3, 8, 9}  # 2 ara düğüm, sadeleştirmede kalkar
    assert G.has_edge(1, 3) and G.has_edge(3, 1)  # oneway=yes olsa da iki yönlü
    uzunluk = G[1][3][0]["length"]
    assert 150 < uzunluk < 210  # ~85 m doğu + ~111 m kuzey
