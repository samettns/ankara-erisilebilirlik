"""src/veri_cek.py için ağ bağlantısı gerektirmeyen testler."""

import json
import os

import networkx as nx
import pytest
from shapely.geometry import box

from src import ag, osm_yerel, veri_cek
from tests.test_osm_yerel import KUTU


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


# --------------------------------------------------------------------------- tek parça ağ (hata senaryosu)

# A ---a--- X ---b--- B  (doğu-batı hattı, 39.9 enleminde)
# w1: A-a-X ve w2: X-b-B iki ayrı yol; X'te sadece bu ikisi buluşur (derece 2, ara düğüm).
# A ve B'ye kuzey/güney kollar bağlı (derece 3, uç düğüm).
# "Doğu" alanı sadece b'yi içerir -> o alana yalnızca w2 değer; orada X çıkmaz uçtur.
_D = {"A": (1, 32.850), "a": (2, 32.8505), "X": (3, 32.851), "b": (4, 32.8515), "B": (5, 32.852)}
BATI_ALAN = box(32.849, 39.898, 32.853, 39.902)  # her şey
DOGU_ALAN = box(32.8512, 39.8995, 32.8518, 39.9005)  # sadece b


def _hata_senaryosu_kesiti(yol):
    dugumler = [f'<node id="{i}" version="1" lat="39.9" lon="{x}"/>' for i, x in _D.values()]
    dugumler += [f'<node id="{i}" version="1" lat="{lat}" lon="{lon}"/>' for i, lat, lon in
                 [(11, 39.901, 32.850), (12, 39.899, 32.850), (13, 39.901, 32.852), (14, 39.899, 32.852)]]
    yollar = [(101, [1, 2, 3]), (102, [3, 4, 5]), (103, [1, 11]), (104, [1, 12]), (105, [5, 13]), (106, [5, 14])]
    xml = "".join(f'<way id="{w}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in nd) +
                  '<tag k="highway" v="footway"/></way>' for w, nd in yollar)
    yol.write_text("<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">"
                   + "".join(dugumler) + xml + "</osm>", encoding="utf-8")
    return yol


def _eski_yontem(kesit, alanlar):
    """Faz 1b-4b yöntemi: alan başına kurup sadeleştir, sonra birleştir (hatalı)."""
    yollar = osm_yerel.yuruyus_yollarini_topla(kesit)
    B = nx.Graph()
    for i, alan in enumerate(alanlar):
        xml = kesit.with_name(f"eski_{i}.osm")
        osm_yerel.osm_xml_yaz(yollar, osm_yerel.poligona_degen_yollar(yollar, alan), xml)
        H = ag.sadelestir(ag.ag_kur(xml))
        B.add_nodes_from(H.nodes(data=True))
        for u, v, d in H.edges(data=True):
            ag._kenar_ekle(B, u, v, d["length"], d["ozel"])
    return B


def test_tek_parca_ag_sadelestirilmemis_agla_ayni_mesafeleri_verir(tmp_path):
    kesit = _hata_senaryosu_kesiti(tmp_path / "kesit.osm")
    alan = BATI_ALAN.union(DOGU_ALAN)
    A, X = _D["A"][0], _D["X"][0]

    # Gerçek mesafeler: sadeleştirilmemiş graf
    yollar = osm_yerel.yuruyus_yollarini_topla(kesit)
    ham = ag.ham_graf_kur(yollar, osm_yerel.poligona_degen_yollar(yollar, alan))
    gercek_ax = nx.shortest_path_length(ham, A, X, weight="length")

    # Senaryo eski hatayı gerçekten tetikliyor: X sadece doğu alanından geliyor, A->X dolambaçlı
    eski = _eski_yontem(kesit, [BATI_ALAN, DOGU_ALAN])
    assert X in eski and nx.shortest_path_length(eski, A, X, weight="length") > gercek_ax + 50

    # Yeni yöntem: tek parça kur, bir kez sadeleştir -> her düğüm çifti gerçek mesafede
    cikti = tmp_path / "ag.graphml"
    veri_cek.birlesik_agi_kur(kesit, alan, cikti=cikti, gecici_dizin=tmp_path / "tmp")
    yeni = ag.sade_graf_oku(cikti)
    sade_mesafe = dict(nx.all_pairs_dijkstra_path_length(yeni, weight="length"))
    ham_mesafe = dict(nx.all_pairs_dijkstra_path_length(ham, weight="length"))
    for u in yeni:
        for v in yeni:
            assert sade_mesafe[u][v] == pytest.approx(ham_mesafe[u][v]), (u, v)
    assert X not in yeni  # tek parçada X gerçekten ara düğüm


def test_tek_parca_ag_iki_asamali_sureleri_sadelestirilmemis_agla_ayni(tmp_path):
    """Kamu yoluna paralel kısa site yolu (özel) varken site içi süreler de birebir aynı olmalı."""
    import pandas as pd
    from src import mesafe
    # 1 -(kamu: 1-2-3)- 3 ve paralel özel site yolu 1-21-3 (daha kısa); 4, sadece özel yolla 3'e bağlı
    dugum = {1: (39.9000, 32.8500), 2: (39.9010, 32.8510), 3: (39.9000, 32.8520), 21: (39.9002, 32.8510),
             4: (39.8995, 32.8525), 9: (39.9000, 32.8490), 5: (39.8993, 32.8528), 6: (39.8992, 32.8527)}
    site = [(31, 39.8990, 32.8495), (32, 39.8990, 32.8530), (33, 39.9006, 32.8530), (34, 39.9006, 32.8495)]
    satir = [f'<node id="{i}" version="1" lat="{la}" lon="{lo}"/>' for i, (la, lo) in dugum.items()]
    satir += [f'<node id="{i}" version="1" lat="{la}" lon="{lo}"/>' for i, la, lo in site]
    yollar = [(101, [9, 1], "footway", ""), (102, [1, 2, 3], "footway", ""),
              (103, [1, 21, 3], "service", '<tag k="access" v="private"/>'),
              (104, [3, 4], "service", '<tag k="access" v="private"/>'),
              (105, [4, 6, 5], "footway", "")]  # kamu cebi: sadece site yolundan girilir
    xml = "".join(f'<way id="{w}" version="1">' + "".join(f'<nd ref="{n}"/>' for n in nd) +
                  f'<tag k="highway" v="{h}"/>{ek}</way>' for w, nd, h, ek in yollar)
    xml += ('<way id="200" version="1">' + "".join(f'<nd ref="{i}"/>' for i, *_ in site) + f'<nd ref="{site[0][0]}"/>'
            '<tag k="landuse" v="residential"/></way>')
    kesit = tmp_path / "kesit.osm"
    kesit.write_text("<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator=\"test\">"
                     + "".join(satir) + xml + "</osm>", encoding="utf-8")
    alan = box(32.848, 39.898, 32.854, 39.902)

    cikti = tmp_path / "ag.graphml"
    veri_cek.birlesik_agi_kur(kesit, alan, cikti=cikti, gecici_dizin=tmp_path / "tmp")
    sade = ag.sade_graf_oku(cikti)
    yollar_ = osm_yerel.yuruyus_yollarini_topla(kesit, konut_alani=osm_yerel.konut_alanlarini_oku(kesit, KUTU))
    assert yollar_.ozel.sum() == 2  # 103 ve 104 site içi özel yol
    ham = ag.ham_graf_kur(yollar_, osm_yerel.poligona_degen_yollar(yollar_, alan))

    baglanti = pd.DataFrame({"tesis": ["t"], "dugum": [9], "mesafe_m": [0.0],
                             "baglandi": [True], "kucuk_parcaya_yakin": [False]})
    s_sade = mesafe.en_yakin_sureler(sade, baglanti)
    s_ham = mesafe.en_yakin_sureler(ham, baglanti)
    assert set(s_sade.index) == set(sade.nodes)  # cep dahil her düğüm süre aldı
    for d in s_sade.index:
        assert s_sade.loc[d, "sure_dk"] == pytest.approx(s_ham.loc[d, "sure_dk"]), d
    assert s_sade.loc[4, "ozel_yol"]  # 4'e kısa site yolu üzerinden ulaşılıyor
    assert s_sade.loc[5, "ozel_yol"]  # kamu cebi de site içinden süre alıyor


def test_birlesik_agi_kur_xml_siler_ve_onbellek_surumu(tmp_path, monkeypatch):
    kesit = _hata_senaryosu_kesiti(tmp_path / "kesit.osm")
    cikti, gecici = tmp_path / "ag.graphml", tmp_path / "tmp"
    ozet = veri_cek.birlesik_agi_kur(kesit, BATI_ALAN, cikti=cikti, gecici_dizin=gecici)

    assert cikti.exists() and not gecici.exists()  # geçici XML ve boş klasör silindi
    assert ozet["dugum"] == 6  # A, B ve 4 kol ucu
    kimlik = json.loads(cikti.with_suffix(".json").read_text(encoding="utf-8"))
    assert kimlik == {"ag_surumu": ag.AG_SURUMU, "kesit": kesit.name}

    # güncel: tekrar kurulmaz
    monkeypatch.setattr(veri_cek.ag, "ag_kur", lambda *a: (_ for _ in ()).throw(AssertionError("kurulmamalı")))
    assert veri_cek.birlesik_agi_kur(kesit, BATI_ALAN, cikti=cikti, gecici_dizin=gecici) == {}

    # ağ sürümü değişince yeniden kurulur
    monkeypatch.setattr(veri_cek.ag, "AG_SURUMU", ag.AG_SURUMU + 1)
    with pytest.raises(AssertionError, match="kurulmamalı"):
        veri_cek.birlesik_agi_kur(kesit, BATI_ALAN, cikti=cikti, gecici_dizin=gecici)
    assert not gecici.exists()  # hata olsa da geçici dosyalar silinir
