"""src/veri_cek.py için ağ bağlantısı gerektirmeyen testler."""

import os

import networkx as nx
import osmnx as ox
from shapely.geometry import box

from src import veri_cek
from tests.test_osm_yerel import KUTU, SAHTE_TURKIYE


def test_dosya_adi_turkce_karakterleri_cevirir():
    assert veri_cek.dosya_adi("Çankaya") == "cankaya"
    assert veri_cek.dosya_adi("Keçiören") == "kecioren"
    assert veri_cek.dosya_adi("Altındağ") == "altindag"
    assert veri_cek.dosya_adi("Yeni Mahalle") == "yeni_mahalle"


def test_guncel_mi(tmp_path):
    girdi, cikti = tmp_path / "girdi", tmp_path / "cikti"
    girdi.write_text("x")
    assert not veri_cek.guncel_mi(cikti, [girdi])  # çıktı yok
    cikti.write_text("y")
    os.utime(girdi, (1000, 1000))
    os.utime(cikti, (2000, 2000))
    assert veri_cek.guncel_mi(cikti, [girdi])
    os.utime(girdi, (3000, 3000))  # girdi sonradan değişti
    assert not veri_cek.guncel_mi(cikti, [girdi])


def test_kutuda_mi():
    assert veri_cek.kutuda_mi(box(32.2, 39.2, 32.4, 39.4), KUTU)
    assert not veri_cek.kutuda_mi(box(32.9, 39.9, 33.1, 40.0), KUTU)


def test_ilce_aglarini_kur_xml_siler_hatada_devam_eder(tmp_path, monkeypatch):
    kesit = tmp_path / "kesit.osm"
    kesit.write_text(SAHTE_TURKIYE, encoding="utf-8")
    gecici = tmp_path / "tmp"
    ag_dosyalari = {"Çankaya": tmp_path / "ag" / "cankaya.graphml", "Bozuk": tmp_path / "ag" / "bozuk.graphml"}
    genis = {"Çankaya": box(32.2, 39.2, 32.4, 39.4), "Bozuk": box(32.2, 39.2, 32.4, 39.4)}

    gercek_kur = veri_cek.ag.ilce_agi_kur

    def bazen_bozuk(xml, cikti):
        assert xml.exists() and xml.parent == gecici  # XML data/tmp altında üretiliyor
        if "bozuk" in cikti.name:
            raise RuntimeError("deneme hatası")
        return gercek_kur(xml, cikti)

    monkeypatch.setattr(veri_cek.ag, "ilce_agi_kur", bazen_bozuk)
    eksikler = veri_cek.ilce_aglarini_kur(kesit, genis, ag_dosyalari, gecici_dizin=gecici)

    assert eksikler == ["Bozuk"]
    assert ag_dosyalari["Çankaya"].exists() and not ag_dosyalari["Bozuk"].exists()
    assert not gecici.exists()  # geçici XML'ler ve boş klasör silindi

    # ikinci çalıştırmada güncel ilçe tekrar kurulmaz
    monkeypatch.setattr(veri_cek.ag, "ilce_agi_kur", lambda *a: (_ for _ in ()).throw(AssertionError("kurulmamalı")))
    assert veri_cek.ilce_aglarini_kur(kesit, {"Çankaya": genis["Çankaya"]}, ag_dosyalari, gecici_dizin=gecici) == []


def test_birlesik_agi_uret(tmp_path):
    dosyalar = []
    for ad, (u, v) in {"a": (1, 2), "b": (2, 3)}.items():
        G = nx.MultiDiGraph(crs="EPSG:4326")
        G.add_node(u, x=32.0 + u / 1000, y=39.9)
        G.add_node(v, x=32.0 + v / 1000, y=39.9)
        G.add_edge(u, v, length=85.0)
        G.add_edge(v, u, length=85.0)
        yol = tmp_path / f"{ad}.graphml"
        ox.save_graphml(G, yol)
        dosyalar.append(yol)
    cikti = tmp_path / "birlesik.graphml"
    veri_cek.birlesik_agi_uret(dosyalar, cikti)
    B = veri_cek.ag.sade_graf_oku(cikti)
    assert nx.shortest_path_length(B, 1, 3, weight="length") == 170.0
