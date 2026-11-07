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
    assert H[1][2] == {"length": 90.0, "ozel": False}
    assert not H.has_edge(3, 3)
    assert H.nodes[1] == {"x": 32.850, "y": 39.90}


def test_sade_graf_kaydet_oku(tmp_path):
    H = ag.sadelestir(_osmnx_grafi())
    yol = tmp_path / "sade.graphml"
    ag.sade_graf_kaydet(H, yol)
    G = ag.sade_graf_oku(yol)
    assert set(G.nodes) == {1, 2, 3}
    assert G[2][3]["length"] == pytest.approx(85.0)
    assert G.nodes[1]["x"] == pytest.approx(32.85)


def test_ag_kur_erisim_degisiminde_kenari_boler(tmp_path):
    # 1-2-3 kamu yolu, 3-4-5 site içi özel yol; 3 sadece iki yolu birleştiriyor (derece 2)
    xml = tmp_path / "site.osm"
    dugumler = "".join(f'<node id="{i}" lat="39.9" lon="{32.85 + i * 0.001}"/>' for i in range(1, 6))
    xml.write_text(
        f"<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">{dugumler}"
        '<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="highway" v="residential"/></way>'
        '<way id="20"><nd ref="3"/><nd ref="4"/><nd ref="5"/><tag k="highway" v="service"/>'
        '<tag k="access" v="private"/></way></osm>', encoding="utf-8")
    G = ag.ag_kur(xml)
    assert set(G.nodes) == {1, 3, 5}  # kapı düğümü 3 korunur, 2 ve 4 sadeleşir
    H = ag.sadelestir(G)
    assert H[1][3]["ozel"] is False and H[3][5]["ozel"] is True


def test_sadelestir_kamu_kenari_ozelden_oncelikli():
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=1.0, y=0.0)
    G.add_edge(1, 2, length=50.0, access="private")  # kısa ama özel (kestirme)
    G.add_edge(1, 2, length=90.0)  # uzun ama kamu
    G.add_edge(2, 1, length=40.0, access=["private", "yes"])  # birleşmiş kenarda liste
    H = ag.sadelestir(G)
    # kamu uzunluğu 1. aşama için; paralel özel bağlantının en kısası 2. aşama için saklanır
    assert H[1][2] == {"length": 90.0, "ozel": False, "ozel_length": 40.0}


@pytest.mark.parametrize("sira", [[("ozel", 30.0), ("kamu", 80.0)], [("kamu", 80.0), ("ozel", 30.0)]])
def test_kenar_ekle_sira_bagimsiz(sira):
    H = nx.Graph()
    for tur, L in sira:
        ag._kenar_ekle(H, 1, 2, L, tur == "ozel")
    assert H[1][2] == {"length": 80.0, "ozel": False, "ozel_length": 30.0}
    assert ag.ozel_uzunluk(H[1][2]) == 30.0


def test_ozel_uzunluk():
    assert ag.ozel_uzunluk({"length": 50.0, "ozel": True}) == 50.0
    assert ag.ozel_uzunluk({"length": 50.0, "ozel": False}) is None
    assert ag.ozel_uzunluk({"length": 50.0, "ozel": False, "ozel_length": 20.0}) == 20.0


def test_ag_kur_xmlden_iki_yonlu_ve_tum_bilesenler(tmp_path):
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
    G = ag.ag_kur(xml)

    assert set(G.nodes) == {1, 3, 8, 9}  # 2 ara düğüm, sadeleştirmede kalkar
    assert G.has_edge(1, 3) and G.has_edge(3, 1)  # oneway=yes olsa da iki yönlü
    uzunluk = G[1][3][0]["length"]
    assert 150 < uzunluk < 210  # ~85 m doğu + ~111 m kuzey
