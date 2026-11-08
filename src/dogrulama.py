"""Süper kaynaklı Dijkstra'yı kaba kuvvetle doğrular: tesis noktası başına ayrı Dijkstra.

Kaba kuvvet yöntemi (mesafe.en_yakin_sureler'den bağımsız):
1. Her bağlanan tesis noktası için kamuya açık ağda ayrı bir Dijkstra çalışır;
   noktanın süresi = bağlantı mesafesi + ağ mesafesi. Tesisin süresi, noktalarının
   (ör. hastane kampüs sınırı noktaları) en küçüğüdür.
2. Yalnızca site içi (özel) yollardan ulaşılan düğümler için, aynı 2. aşama grafında
   (mesafe.ikinci_asama_grafi) düğümden kapılara ters yönde Dijkstra çalışır;
   tesisin süresi = min(kapı) [kapının kamu süresi + kapıdan düğüme site içi mesafe].
En yakın tesis, tüm tesisler arasından en kısa süreli olandır. Eşit süreli tesisler
varsa süper kaynağın seçtiği tesisin süresi en kısa süreye eşitse doğru sayılır.

Çalıştırma (proje kökünden, gerçek ağda ~20 dk):
    python -m src.dogrulama              # 300 rastgele düğüm (+ 50 site içi), tohum 42
    python -m src.dogrulama --ornek 100 --tohum 7
"""

import argparse
import json
import time

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

try:
    from src import ag, mesafe
    from src.ayarlar import BIRLESIK_AG_DOSYASI, SINIR_DOSYASI, SONUC_DIZINI, YURUME_HIZI_M_DK
except ModuleNotFoundError:
    import ag
    import mesafe
    from ayarlar import BIRLESIK_AG_DOSYASI, SINIR_DOSYASI, SONUC_DIZINI, YURUME_HIZI_M_DK

TOLERANS_DK = 1e-9  # kayan nokta toplama sırası farkı için


def kamu_grafi(H: nx.Graph) -> nx.Graph:
    """Sadece kamuya açık (ozel=False) kenarlardan oluşan bağımsız kopya (hızlı Dijkstra için)."""
    K = nx.Graph()
    K.add_nodes_from(H.nodes)
    K.add_edges_from((u, v, {"length": d["length"]}) for u, v, d in H.edges(data=True) if not d.get("ozel", False))
    return K


def tesis_basina_sureler(H: nx.Graph, baglantilar: pd.DataFrame, hedef_dugumler,
                         hiz_m_dk: float = YURUME_HIZI_M_DK) -> pd.DataFrame:
    """Kaba kuvvet: hedef düğümlerin her tesise yürüme süresi (dk) matrisi.

    Satırlar hedef_dugumler, sütunlar tesis kimlikleri; ulaşılamayan hücre NaN.
    Sadece baglandi=True noktalar kullanılır, her nokta için ayrı Dijkstra çalışır.
    Kamu ağından ulaşılamayan hedefler için site içi 2. aşama uygulanır
    (bkz. modül açıklaması). Sanal süper kaynak kullanılmaz.
    """
    hedefler = list(dict.fromkeys(int(h) for h in hedef_dugumler))
    bagli = baglantilar[baglantilar["baglandi"]]
    tesisler = list(dict.fromkeys(bagli["tesis"]))
    K = kamu_grafi(H)

    # Kamu ağından herhangi bir tesis noktasına ulaşılabilen düğümler (sadece erişim, mesafe yok)
    ulasilan: set = set()
    for dugum in bagli["dugum"].astype(int):
        if dugum not in ulasilan:
            ulasilan |= nx.node_connected_component(K, dugum)

    # 2. aşama hazırlığı: kamu ağından ulaşılamayan hedeflere kapılardan site içi mesafeler
    O = mesafe.ikinci_asama_grafi(H, ulasilan)
    kapilar = {n for n in O if n in ulasilan and O.out_degree(n) > 0}
    ters = O.reverse(copy=False)
    site_ici: dict = {}  # hedef -> [(kapı, kapıdan hedefe metre)]
    for h in hedefler:
        if h not in ulasilan and h in O:
            uzak = nx.single_source_dijkstra_path_length(ters, h, weight="length")
            site_ici[h] = [(k, m) for k, m in uzak.items() if k in kapilar]
    sakla = set(hedefler) | {k for liste in site_ici.values() for k, _ in liste}

    # 1. aşama: nokta başına ayrı Dijkstra; tesis başına en kısa (sadece gereken düğümlerde saklanır)
    kamu: dict = {t: {} for t in tesisler}  # tesis -> {düğüm: metre}
    for tesis, dugum, baglanti_m in zip(bagli["tesis"], bagli["dugum"].astype(int), bagli["mesafe_m"]):
        uzaklik = nx.single_source_dijkstra_path_length(K, dugum, weight="length")
        d_t = kamu[tesis]
        for n in sakla.intersection(uzaklik):
            toplam = float(baglanti_m) + uzaklik[n]
            if toplam < d_t.get(n, np.inf):
                d_t[n] = toplam

    sutunlar = {}
    for t in tesisler:
        d_t = kamu[t]
        satir = []
        for h in hedefler:
            if h in site_ici:
                adaylar = [d_t[k] + m for k, m in site_ici[h] if k in d_t]
                satir.append(min(adaylar) if adaylar else np.nan)
            else:
                satir.append(d_t.get(h, np.nan))
        sutunlar[t] = satir
    matris = pd.DataFrame(sutunlar, index=pd.Index(hedefler, name="osmid"), columns=tesisler, dtype=float)
    return matris / hiz_m_dk


def karsilastir(super_kaynak: pd.DataFrame, matris: pd.DataFrame, tolerans_dk: float = TOLERANS_DK) -> pd.DataFrame:
    """Süper kaynaklı sonucu (mesafe.en_yakin_sureler çıktısı) kaba kuvvet matrisiyle karşılaştırır.

    Döner (matris satırları için): sure_super, sure_kaba, fark_dk (mutlak), tesis_super,
    tesis_kaba, sure_esit, tesis_dogru. tesis_dogru: süper kaynağın seçtiği tesisin
    kaba kuvvet süresi en kısa süreye tolerans içinde eşit (eşitlikte başka tesis de olabilir).
    İki yöntemde de ulaşılamayan düğüm sure_esit ve tesis_dogru sayılır.
    """
    s = super_kaynak.reindex(matris.index)
    sure_kaba = matris.min(axis=1, skipna=True)
    dolu = matris.notna().any(axis=1)
    tesis_kaba = pd.Series(None, index=matris.index, dtype=object)
    if dolu.any():
        tesis_kaba[dolu] = matris[dolu].idxmin(axis=1)
    ikisi_yok = s["sure_dk"].isna() & sure_kaba.isna()
    fark = (s["sure_dk"] - sure_kaba).abs()
    sure_esit = ikisi_yok | (fark <= tolerans_dk)

    def secilen_sure(satir):
        t = s.at[satir, "tesis"]
        return matris.at[satir, t] if isinstance(t, str) and t in matris.columns else np.nan

    secilen = pd.Series([secilen_sure(i) for i in matris.index], index=matris.index, dtype=float)
    tesis_dogru = ikisi_yok | ((secilen - sure_kaba).abs() <= tolerans_dk)
    return pd.DataFrame({"sure_super": s["sure_dk"], "sure_kaba": sure_kaba, "fark_dk": fark,
                         "tesis_super": s["tesis"], "tesis_kaba": tesis_kaba,
                         "sure_esit": sure_esit, "tesis_dogru": tesis_dogru}, index=matris.index)


def ornek_dugumler(sonuc: pd.DataFrame, ornek: int, site_ici: int, tohum: int) -> list:
    """İlçe içi düğümlerden rastgele `ornek` + site içi (ozel_yol) düğümlerden `site_ici` tane seçer."""
    rng = np.random.default_rng(tohum)
    ilce_ici = sonuc.index[sonuc["ilce"].notna()].to_numpy()
    ozel = sonuc.index[sonuc["ilce"].notna() & sonuc["ozel_yol"]].to_numpy()
    secim = list(rng.choice(ilce_ici, size=min(ornek, len(ilce_ici)), replace=False))
    secim += list(rng.choice(ozel, size=min(site_ici, len(ozel)), replace=False))
    return list(dict.fromkeys(int(n) for n in secim))


def main(ornek: int = 300, site_ici: int = 50, tohum: int = 42) -> dict:
    """Gerçek ağda kaba kuvvet doğrulaması; data/sonuclar/kaba_kuvvet.json ve .csv yazar."""
    t0 = time.time()
    G = ag.sade_graf_oku(BIRLESIK_AG_DOSYASI)
    dugumler = mesafe.dugum_tablosu(G, gpd.read_file(SINIR_DOSYASI))
    ana, _ = mesafe.en_buyuk_parca(G)
    G.remove_nodes_from([n for n in list(G.nodes) if n not in ana])
    kamu = mesafe.kamu_dugumleri(G)
    kaynaklar = mesafe.kategori_kaynaklari()

    baglantilar = {k: mesafe.kaynaklari_baglat(kaynaklar[k], dugumler, kamu) for k in mesafe.KATEGORILER}
    supers = {k: mesafe.en_yakin_sureler(G, b) for k, b in baglantilar.items()}
    sonuc = dugumler.loc[dugumler.index.isin(list(ana)), ["ilce"]].copy()
    sonuc["ozel_yol"] = supers[mesafe.KATEGORILER[0]]["ozel_yol"].reindex(sonuc.index).fillna(False).astype(bool)
    secim = ornek_dugumler(sonuc, ornek, site_ici, tohum)
    print(f"Hazırlık {time.time() - t0:.0f} sn; {len(secim)} örnek düğüm "
          f"({int(sonuc.loc[secim, 'ozel_yol'].sum())} site içi)")

    rapor = {"ornek_dugum": len(secim), "site_ici_dugum": int(sonuc.loc[secim, "ozel_yol"].sum()),
             "tohum": tohum, "tolerans_dk": TOLERANS_DK, "kategoriler": {}}
    tablolar = []
    for k in mesafe.KATEGORILER:
        t1 = time.time()
        matris = tesis_basina_sureler(G, baglantilar[k], secim)
        k_sonuc = karsilastir(supers[k], matris)
        sure_sn = time.time() - t1
        bagli = baglantilar[k][baglantilar[k]["baglandi"]]
        rapor["kategoriler"][k] = {
            "tesis": int(bagli["tesis"].nunique()), "dijkstra": int(len(bagli)),
            "sure_esit": int(k_sonuc["sure_esit"].sum()), "tesis_dogru": int(k_sonuc["tesis_dogru"].sum()),
            "tesis_ayni_kimlik": int((k_sonuc["tesis_super"] == k_sonuc["tesis_kaba"]).sum()),
            "en_buyuk_fark_dk": float(k_sonuc["fark_dk"].max(skipna=True)) if k_sonuc["fark_dk"].notna().any() else 0.0,
            "kaba_kuvvet_sn": round(sure_sn, 1),
        }
        tablolar.append(k_sonuc.assign(kategori=k))
        r = rapor["kategoriler"][k]
        print(f"{k:16s} {r['dijkstra']:5d} Dijkstra, {sure_sn:6.0f} sn | süre eşit {r['sure_esit']}/{len(secim)} | "
              f"tesis doğru {r['tesis_dogru']}/{len(secim)} | en büyük fark {r['en_buyuk_fark_dk']:.2e} dk")

    rapor["toplam_sn"] = round(time.time() - t0, 1)
    SONUC_DIZINI.mkdir(parents=True, exist_ok=True)
    pd.concat(tablolar).reset_index().to_csv(SONUC_DIZINI / "kaba_kuvvet.csv", index=False, encoding="utf-8-sig")
    (SONUC_DIZINI / "kaba_kuvvet.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kaydedildi: {SONUC_DIZINI / 'kaba_kuvvet.json'} ({rapor['toplam_sn']:.0f} sn)")
    return rapor


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Süper kaynaklı Dijkstra'yı tesis başına Dijkstra ile doğrular.")
    ap.add_argument("--ornek", type=int, default=300, help="rastgele ilçe içi düğüm sayısı")
    ap.add_argument("--site-ici", type=int, default=50, help="ek site içi (özel yol) düğüm sayısı")
    ap.add_argument("--tohum", type=int, default=42)
    a = ap.parse_args()
    main(a.ornek, a.site_ici, a.tohum)
