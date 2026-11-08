"""src/dogrulama.py için testler: süper kaynaklı Dijkstra = tesis başına ayrı Dijkstra."""

import networkx as nx
import numpy as np
import pandas as pd
import pytest

from src import dogrulama, mesafe


def _baglantilar(kayitlar):
    """[(tesis, düğüm, bağlantı metre, bağlandı mı), ...] -> kaynaklari_baglat biçiminde tablo."""
    return pd.DataFrame({"tesis": [k[0] for k in kayitlar], "dugum": [k[1] for k in kayitlar],
                         "mesafe_m": [float(k[2]) for k in kayitlar], "baglandi": [k[3] for k in kayitlar],
                         "kucuk_parcaya_yakin": False})


def rastgele_graf(tohum: int, n: int = 60, m: int = 110):
    """Bağlı rastgele graf; tamsayı uzunluklar (eşitlikler çıksın), %20 özel kenar, bazı paralel özel bağlantılar."""
    rng = np.random.default_rng(tohum)
    G = nx.gnm_random_graph(n, m, seed=tohum)
    G.add_edges_from((i, i + 1) for i in range(n - 1))  # bağlı olsun
    for u, v in G.edges:
        G[u][v].update(length=float(rng.integers(10, 200)), ozel=bool(rng.random() < 0.2))
        if not G[u][v]["ozel"] and rng.random() < 0.05:
            G[u][v]["ozel_length"] = float(rng.integers(1, 10))
    dugumler = rng.choice(n, size=18, replace=False)
    kayitlar = []
    for i, d in enumerate(dugumler):
        tesis = f"t{i % 7}"  # 7 tesis, çoğunun birden fazla noktası var
        kayitlar.append((tesis, int(d), int(rng.integers(0, 60)), bool(rng.random() < 0.9)))
    return G, _baglantilar(kayitlar)


@pytest.mark.parametrize("tohum", range(12))
def test_rastgele_graflarda_super_kaynak_kaba_kuvvete_esit(tohum):
    G, baglantilar = rastgele_graf(tohum)
    sup = mesafe.en_yakin_sureler(G, baglantilar)
    matris = dogrulama.tesis_basina_sureler(G, baglantilar, list(G.nodes))
    sonuc = dogrulama.karsilastir(sup, matris)
    assert sonuc["sure_esit"].all(), sonuc[~sonuc["sure_esit"]]
    assert sonuc["tesis_dogru"].all(), sonuc[~sonuc["tesis_dogru"]]
    # Süper kaynakta olmayan düğüm kaba kuvvette de ulaşılamaz
    assert set(sup.index) == set(sonuc.index[sonuc["sure_kaba"].notna()])


def site_grafi():
    """0-1-2 kamu hattı (100 m), 2-3 ve 3-4 site içi özel yol (50 m), 4-5 site içi kamu cebi (10 m)."""
    G = nx.Graph()
    G.add_edge(0, 1, length=100.0, ozel=False)
    G.add_edge(1, 2, length=100.0, ozel=False)
    G.add_edge(2, 3, length=50.0, ozel=True)
    G.add_edge(3, 4, length=50.0, ozel=True)
    G.add_edge(4, 5, length=10.0, ozel=False)
    return G


def test_site_ici_dugumler_elle_hesap():
    G = site_grafi()
    b = _baglantilar([("A", 0, 20, True), ("B", 2, 300, True), ("B", 1, 0, True)])
    matris = dogrulama.tesis_basina_sureler(G, b, [0, 3, 5], hiz_m_dk=1.0)
    # A: 20 + kamu 200 + site içi 50 (3) / 100+10 (5); B: en iyi nokta düğüm 1 (0 + 100)
    assert matris.loc[3, "A"] == pytest.approx(270) and matris.loc[5, "A"] == pytest.approx(330)
    assert matris.loc[3, "B"] == pytest.approx(150) and matris.loc[0, "B"] == pytest.approx(100)
    sonuc = dogrulama.karsilastir(mesafe.en_yakin_sureler(G, b, hiz_m_dk=1.0), matris)
    assert sonuc["sure_esit"].all() and (sonuc["tesis_kaba"] == ["A", "B", "B"]).all()


def test_baglanmayan_nokta_kullanilmaz():
    G = site_grafi()
    b = _baglantilar([("A", 0, 0, True), ("A", 2, 0, False)])
    matris = dogrulama.tesis_basina_sureler(G, b, [2], hiz_m_dk=1.0)
    assert matris.loc[2, "A"] == pytest.approx(200)


def test_ulasilamayan_dugum_iki_yontemde_de_yok():
    G = site_grafi()
    G.add_edge(8, 9, length=5.0, ozel=False)  # kopuk ada
    b = _baglantilar([("A", 0, 0, True)])
    sonuc = dogrulama.karsilastir(mesafe.en_yakin_sureler(G, b), dogrulama.tesis_basina_sureler(G, b, [8, 1]))
    assert np.isnan(sonuc.loc[8, "sure_kaba"]) and np.isnan(sonuc.loc[8, "sure_super"])
    assert sonuc["sure_esit"].all() and sonuc["tesis_dogru"].all()


def test_esit_sureli_tesislerde_secim_dogru_sayilir():
    G = nx.Graph()
    G.add_edge(0, 1, length=50.0, ozel=False)
    G.add_edge(1, 2, length=50.0, ozel=False)
    b = _baglantilar([("A", 0, 0, True), ("B", 2, 0, True)])
    sonuc = dogrulama.karsilastir(mesafe.en_yakin_sureler(G, b), dogrulama.tesis_basina_sureler(G, b, [1]))
    assert sonuc.loc[1, "sure_esit"] and sonuc.loc[1, "tesis_dogru"]


def test_karsilastir_yanlis_sonucu_yakalar():
    G = site_grafi()
    b = _baglantilar([("A", 0, 0, True), ("B", 5, 0, True)])
    sup = mesafe.en_yakin_sureler(G, b)
    matris = dogrulama.tesis_basina_sureler(G, b, [0, 1])
    bozuk = sup.copy()
    bozuk.loc[1, "sure_dk"] += 0.5  # yanlış süre
    bozuk.loc[0, "tesis"] = "B"  # yanlış tesis (A daha yakın)
    sonuc = dogrulama.karsilastir(bozuk, matris)
    assert not sonuc.loc[1, "sure_esit"] and sonuc.loc[1, "fark_dk"] == pytest.approx(0.5)
    assert not sonuc.loc[0, "tesis_dogru"]


def test_ornek_dugumler_tekrarsiz_ve_tekrarlanabilir():
    sonuc = pd.DataFrame({"ilce": ["X"] * 8 + [None] * 2, "ozel_yol": [False] * 5 + [True] * 3 + [True, False]},
                         index=range(10))
    a = dogrulama.ornek_dugumler(sonuc, ornek=4, site_ici=2, tohum=1)
    assert a == dogrulama.ornek_dugumler(sonuc, ornek=4, site_ici=2, tohum=1)
    assert len(a) == len(set(a)) and set(a) <= set(range(8))  # ilçe dışı (8, 9) seçilmez
    assert len(set(a) & {5, 6, 7}) >= 2
