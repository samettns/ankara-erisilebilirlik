"""src/osm_yerel.py için testler: pyosmium küçük sahte bir OSM dosyası üzerinde çalışır."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from src import ag, hedefler, osm_yerel

KUTU = (32.0, 39.0, 33.0, 40.0)


def _dugum(i, lon, lat, **etiket):
    etiketler = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in etiket.items())
    return f'<node id="{i}" version="1" lat="{lat}" lon="{lon}">{etiketler}</node>'


def _yol(i, dugumler, **etiket):
    nd = "".join(f'<nd ref="{d}"/>' for d in dugumler)
    etiketler = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in etiket.items())
    return f'<way id="{i}" version="1">{nd}{etiketler}</way>'


def _iliski(i, uyeler, **etiket):
    m = "".join(f'<member type="{t}" ref="{r}" role="{rol}"/>' for t, r, rol in uyeler)
    etiketler = "".join(f'<tag k="{k.replace("__", ":")}" v="{v}"/>' for k, v in etiket.items())
    return f'<relation id="{i}" version="1">{m}{etiketler}</relation>'


SAHTE_TURKIYE = "\n".join([
    "<?xml version='1.0' encoding='UTF-8'?>", '<osm version="0.6" generator="test">',
    # Çankaya sınırı (kutu içinde) ve Ankara dışındaki adaşı
    _dugum(1, 32.2, 39.2), _dugum(2, 32.4, 39.2), _dugum(3, 32.4, 39.4), _dugum(4, 32.2, 39.4),
    _dugum(5, 40.0, 45.0), _dugum(6, 40.2, 45.0), _dugum(7, 40.2, 45.2),
    # yürüme yolları
    _dugum(11, 32.300, 39.30), _dugum(12, 32.301, 39.30), _dugum(13, 32.302, 39.30),
    _dugum(14, 32.399, 39.31), _dugum(15, 32.450, 39.31), _dugum(16, 32.303, 39.30),
    _dugum(21, 41.0, 46.0), _dugum(22, 41.001, 46.0),
    # hedefler
    _dugum(31, 32.31, 39.31, amenity="pharmacy", name="Test Eczanesi"),
    _dugum(32, 32.32, 39.32, railway="station", station="subway", name="Kızılay"),
    _dugum(33, 32.3201, 39.32, railway="stop", public_transport="stop_position"),
    _dugum(34, 32.3202, 39.3202, railway="subway_entrance"),
    _dugum(35, 41.0, 46.0, amenity="pharmacy", name="Uzak Eczane"),
    _dugum(36, 32.33, 39.32),
    _dugum(41, 32.330, 39.330), _dugum(42, 32.335, 39.330), _dugum(43, 32.335, 39.335), _dugum(44, 32.330, 39.335),
    _dugum(51, 29.0, 41.0, railway="stop"), _dugum(52, 29.01, 41.0),
    # yollar
    _yol(101, [1, 2]), _yol(102, [2, 3]), _yol(103, [3, 4, 1]), _yol(104, [5, 6, 7, 5]),
    _yol(201, [11, 12], highway="footway"),
    _yol(202, [12, 13], highway="motorway"),
    _yol(203, [13, 16], highway="footway", foot="no"),
    _yol(204, [21, 22], highway="footway"),
    _yol(205, [14, 15], highway="residential", name="Sınır Sokak"),
    _yol(301, [41, 42, 43, 44, 41], amenity="hospital", name="Test Hastanesi", operator__type="government"),
    _yol(401, [33, 36], railway="subway"),
    _yol(402, [51, 52], railway="subway"),
    # ilişkiler
    _iliski(1001, [("way", 101, "outer"), ("way", 102, "outer"), ("way", 103, "outer")],
            type="boundary", boundary="administrative", admin_level="6", name="Çankaya"),
    _iliski(1002, [("way", 104, "outer")], type="boundary", boundary="administrative", admin_level="6", name="Çankaya"),
    _iliski(2001, [("node", 33, "stop"), ("way", 401, "")], type="route", route="subway", ref="M1"),
    _iliski(2002, [("node", 32, ""), ("node", 33, "stop")], type="public_transport", public_transport="stop_area"),
    _iliski(2003, [("node", 51, "stop"), ("way", 402, "")], type="route", route="subway", ref="M2"),
    "</osm>",
])


@pytest.fixture(scope="module")
def kesit(tmp_path_factory):
    """Sahte Türkiye dosyasından (disk tabanlı konum deposuyla) çıkarılmış kesit."""
    dizin = tmp_path_factory.mktemp("osm")
    kaynak = dizin / "turkey.osm"
    kaynak.write_text(SAHTE_TURKIYE, encoding="utf-8")
    cikti = dizin / "ankara.osm.pbf"
    osm_yerel.ankara_kesiti_cikar(kaynak, cikti, kutu=KUTU, ilceler=["Çankaya"],
                                  konum_deposu=f"sparse_file_array,{dizin / 'konum.tmp'}")
    assert not (dizin / "konum.tmp").exists()  # geçici konum deposu silindi
    return cikti


def _icerik(yol):
    import osmium
    icerik = {"n": {}, "w": {}, "r": {}}
    for o in osmium.FileProcessor(str(yol)):
        icerik[o.type_str()][o.id] = dict(o.tags)
    return icerik


# --------------------------------------------------------------------------- filtreler

def test_ag_filtresi_kurallari_osmnx_ile_ayni():
    kurallar = osm_yerel.ag_filtresi_kurallari("walk")
    assert ("highway", None) in kurallar
    assert ("foot", "no") in kurallar
    assert any(a == "highway" and d and "motor" in d for a, d in kurallar)


@pytest.mark.parametrize("etiket, beklenen", [
    ({"highway": "footway"}, True),
    ({"highway": "residential", "sidewalk": "both"}, True),
    ({"highway": "motorway"}, False),
    ({"highway": "motorway_link"}, False),  # Overpass !~ kısmi eşleşir
    ({"highway": "cycleway"}, False),
    ({"highway": "footway", "foot": "no"}, False),
    ({"highway": "service", "service": "private"}, False),
    ({"highway": "residential", "access": "private"}, False),
    ({"highway": "pedestrian", "area": "yes"}, False),
    ({"highway": "primary", "sidewalk": "separate"}, False),
    ({"building": "yes"}, False),
])
def test_ag_filtresinden_gecer(etiket, beklenen):
    assert osm_yerel.ag_filtresinden_gecer(etiket, osm_yerel.ag_filtresi_kurallari("walk")) is beklenen


def test_hedef_etiketi_mi():
    assert osm_yerel.hedef_etiketi_mi({"amenity": "pharmacy"})
    assert osm_yerel.hedef_etiketi_mi({"healthcare": "dentist"})  # healthcare: True
    assert osm_yerel.hedef_etiketi_mi({"railway": "subway_entrance"})
    assert not osm_yerel.hedef_etiketi_mi({"amenity": "school"})
    assert not osm_yerel.hedef_etiketi_mi({"railway": "stop"})


# --------------------------------------------------------------------------- kesit

def test_kesit_icerigi(kesit):
    icerik = _icerik(kesit)
    assert icerik["n"][31] == {"amenity": "pharmacy", "name": "Test Eczanesi"}  # hedef etiketleriyle
    assert 35 not in icerik["n"] and 204 not in icerik["w"]  # kutu dışı
    assert {201, 202, 203, 205, 301, 401} <= set(icerik["w"])
    assert icerik["w"][101] == {}  # sadece referans: etiketsiz ama var
    assert {1001, 1002, 2001, 2002} <= set(icerik["r"])  # adaş sınır da gelir, okurken elenir
    assert 2003 not in icerik["r"] and 402 not in icerik["w"]  # İstanbul'daki hat


def test_kesit_sureci_hata_verirse_temizler_ve_yukseltir(tmp_path):
    cikti = tmp_path / "ankara.osm.pbf"
    with pytest.raises(RuntimeError, match="çıkış kodu"):
        osm_yerel.ankara_kesiti_cikar(tmp_path / "olmayan.osm.pbf", cikti, kutu=KUTU,
                                      konum_deposu=f"sparse_file_array,{tmp_path / 'k.tmp'}")
    assert not cikti.exists() and not (tmp_path / "k.tmp").exists()
    assert not list(tmp_path.glob("yaziliyor-*"))


def test_kesit_varsa_tekrar_uretilmez(kesit, tmp_path):
    once = kesit.stat().st_mtime
    osm_yerel.ankara_kesiti_cikar(tmp_path / "yok.osm", kesit)  # kaynak yok ama çıktı var
    assert kesit.stat().st_mtime == once


# --------------------------------------------------------------------------- sınırlar

def test_ilce_sinirlarini_oku_adasi_eler(kesit):
    sinirlar = osm_yerel.ilce_sinirlarini_oku(kesit, ["Çankaya"], KUTU)
    assert sinirlar["ilce"].tolist() == ["Çankaya"]
    assert sinirlar.geometry.iloc[0].equals(box(32.2, 39.2, 32.4, 39.4))


def test_ilce_sinirlarini_oku_eksik_ilce_hata(kesit):
    with pytest.raises(ValueError, match="Mamak"):
        osm_yerel.ilce_sinirlarini_oku(kesit, ["Çankaya", "Mamak"], KUTU)


# --------------------------------------------------------------------------- yürüme yolları

def test_yuruyus_yollari_ve_poligon_secimi(kesit):
    yollar = osm_yerel.yuruyus_yollarini_topla(kesit)
    assert sorted(yollar.yol_idleri.tolist()) == [201, 205]  # motorway ve foot=no elendi
    i205 = yollar.yol_idleri.tolist().index(205)
    assert yollar.etiketler[i205] == {"highway": "residential", "name": "Sınır Sokak"}

    cankaya = box(32.2, 39.2, 32.4, 39.4)
    secilen = yollar.yol_idleri[osm_yerel.poligona_degen_yollar(yollar, cankaya)]
    assert sorted(secilen.tolist()) == [201, 205]  # 205 sınırı kesiyor, tamamen dahil
    uzak = box(32.9, 39.9, 33.0, 40.0)
    assert len(osm_yerel.poligona_degen_yollar(yollar, uzak)) == 0


def test_xml_yaz_ve_graf_kur(kesit, tmp_path):
    yollar = osm_yerel.yuruyus_yollarini_topla(kesit)
    siralar = osm_yerel.poligona_degen_yollar(yollar, box(32.2, 39.2, 32.4, 39.4))
    xml = tmp_path / "cankaya.osm"
    osm_yerel.osm_xml_yaz(yollar, siralar, xml)
    assert osm_yerel.XML_URETICI in xml.read_text(encoding="utf-8")

    G = ag.ilce_agi_kur(xml, tmp_path / "cankaya.graphml")
    assert set(G.nodes) == {11, 12, 14, 15}
    assert G.has_edge(14, 15) and G.has_edge(15, 14)
    assert G[14][15][0]["name"] == "Sınır Sokak"


# --------------------------------------------------------------------------- hedefler ve hatlar

def test_hedef_ozelliklerini_oku(kesit):
    ham = osm_yerel.hedef_ozelliklerini_oku(kesit, box(*KUTU))
    assert list(ham.columns) == hedefler.HAM_SUTUNLAR
    kayit = {(e, i): r for e, i, r in zip(ham["element"], ham["id"], ham.itertuples())}
    assert set(kayit) == {("node", 31), ("node", 32), ("node", 34), ("way", 301)}
    hastane = kayit[("way", 301)]
    assert hastane.geometry.geom_type == "Polygon" and hastane.amenity == "hospital"
    assert ham.loc[ham["id"] == 301, "operator:type"].iloc[0] == "government"
    # alan dışındakiler elenir
    assert osm_yerel.hedef_ozelliklerini_oku(kesit, box(32.9, 39.9, 33.0, 40.0)).empty


def test_hat_iliskileri_ve_istasyon_eslesmesi(kesit):
    hat_json = osm_yerel.hat_iliskilerini_oku(kesit)
    iliskiler = {e["id"]: e for e in hat_json["elements"] if e["type"] == "relation"}
    assert set(iliskiler) == {2001, 2002}
    assert {"type": "node", "ref": 33, "role": "stop"} in iliskiler[2001]["members"]
    dugumler = {e["id"]: e for e in hat_json["elements"] if e["type"] == "node"}
    assert dugumler[33]["lon"] == pytest.approx(32.3201)

    ham = osm_yerel.hedef_ozelliklerini_oku(kesit, box(*KUTU))
    istasyonlar, kaynaklar = hedefler.rayli_ayikla(ham, hat_json)
    assert istasyonlar["id"].tolist() == [32] and istasyonlar["hatlar"].tolist() == ["M1"]
    assert kaynaklar["tur"].tolist() == ["giris"]  # 34 numaralı giriş
