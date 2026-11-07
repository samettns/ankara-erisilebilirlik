"""Her ağ düğümünden kategori başına en yakın tesise yürüme süresi.

Yöntem (kategori başına tek Dijkstra):
1. Analiz, yürüme ağının en büyük bağlı parçasında yapılır; küçük parçalar raporlanır.
2. Her hedef noktası (eczane, kampüs sınırı noktası, metro girişi ...) en büyük
   parçadaki en yakın düğüme bağlanır; BAGLANTI_ESIGI_M'den uzaksa dışarıda kalır.
3. Grafa geçici bir sanal süper kaynak S eklenir. S'den her bağlanan düğüme,
   ağırlığı bağlantı mesafesi olan bir kenar çekilir. S'den tek bir Dijkstra,
   her düğüme "hedef noktasından ağa yürüme + ağ üzerinde yürüme" mesafesini verir.
   S en başta 0 mesafeyle kesinleştiği için hiçbir yolun ara durağı olamaz.
4. En yakın tesis, öncül zinciri S'ye kadar izlenerek bulunur.

Çalıştırma (proje kökünden):
    python -m src.mesafe
"""

import json
import time
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import shapely

try:
    from src import ag
    from src.ayarlar import (BAGLANTI_ESIGI_M, BIRLESIK_AG_DOSYASI, ESIK_DK, HEDEF_DIZINI, SINIR_DOSYASI,
                             SONUC_DIZINI, YURUME_HIZI_M_DK)
    from src.veri_cek import guncel_mi
except ModuleNotFoundError:
    import ag
    from ayarlar import (BAGLANTI_ESIGI_M, BIRLESIK_AG_DOSYASI, ESIK_DK, HEDEF_DIZINI, SINIR_DOSYASI,
                         SONUC_DIZINI, YURUME_HIZI_M_DK)
    from veri_cek import guncel_mi

SANAL_KAYNAK = -1  # OSM düğüm kimlikleri pozitif; çakışmaz
# Hesap yöntemi değişince artırılır; sonuçlar girdiler değişmese de yeniden hesaplanır.
# 2: iki aşamalı (son kilometre) hesap; 3: 2. aşamada paralel özel bağlantılar ve kamu cepleri
YONTEM_SURUMU = 3
KATEGORILER = ["eczane", "hastane", "birinci_basamak", "rayli", "baskentray"]


# --------------------------------------------------------------------------- graf ve düğümler

def dugum_tablosu(G: nx.Graph, sinirlar: gpd.GeoDataFrame | None = None) -> gpd.GeoDataFrame:
    """Graf düğümlerini (osmid indeksli, x, y, geometry) GeoDataFrame yapar.

    sinirlar verilirse her düğüme içinde bulunduğu ilçe 'ilce' olarak eklenir
    (7 ilçe dışındaki genişletme bölgesinde None).
    """
    osmid = np.fromiter(G.nodes, dtype=np.int64, count=G.number_of_nodes())
    x = np.array([G.nodes[n]["x"] for n in osmid], dtype=float)
    y = np.array([G.nodes[n]["y"] for n in osmid], dtype=float)
    dugumler = gpd.GeoDataFrame({"x": x, "y": y}, geometry=shapely.points(x, y),
                                index=pd.Index(osmid, name="osmid"), crs="EPSG:4326")
    if sinirlar is not None:
        eslesme = gpd.sjoin(dugumler[["geometry"]], sinirlar[["ilce", "geometry"]], how="left", predicate="within")
        ilk = eslesme[~eslesme.index.duplicated()]["ilce"]
        dugumler["ilce"] = pd.Series([v if isinstance(v, str) else None for v in ilk],
                                     index=dugumler.index, dtype=object)
    return dugumler


def en_buyuk_parca(G: nx.Graph) -> tuple[set, dict]:
    """En büyük bağlı parçanın düğüm kümesini ve küçük parçaların özetini döndürür.

    Özet: parça sayısı, en büyük parçadaki ve dışarıda kalan düğüm sayısı.
    Graf değiştirilmez.
    """
    parcalar = sorted(nx.connected_components(G), key=len, reverse=True)
    if not parcalar:
        return set(), {"parca_sayisi": 0, "ana_parca_dugum": 0, "disarida_dugum": 0}
    ana = parcalar[0]
    return ana, {
        "parca_sayisi": len(parcalar),
        "ana_parca_dugum": len(ana),
        "disarida_dugum": G.number_of_nodes() - len(ana),
        "en_buyuk_kucuk_parca": len(parcalar[1]) if len(parcalar) > 1 else 0,
    }


# --------------------------------------------------------------------------- hedefleri ağa bağlama

def kaynaklari_baglat(
    kaynaklar: gpd.GeoDataFrame,
    dugumler: gpd.GeoDataFrame,
    ana_parca: set,
    esik_m: float = BAGLANTI_ESIGI_M,
) -> pd.DataFrame:
    """Her hedef noktasını ana parçadaki en yakın düğüme bağlar.

    kaynaklar: 'tesis' (tesis kimliği) sütunu ve EPSG:4326 noktalar.
    dugumler: dugum_tablosu çıktısı. ana_parca: bağlanılabilecek düğümler
    (ana parçanın kamuya açık kenarı olan düğümleri). Mesafeler metrik
    projeksiyonda ölçülür.
    Döner: tesis, dugum, mesafe_m, baglandi (mesafe <= esik_m) ve
    kucuk_parcaya_yakin (tüm graftaki en yakın düğüm bağlanılabilir değil).
    """
    sutunlar = ["tesis", "dugum", "mesafe_m", "baglandi", "kucuk_parcaya_yakin"]
    if kaynaklar.empty:
        return pd.DataFrame(columns=sutunlar)
    utm = dugumler.estimate_utm_crs()
    tum = dugumler.to_crs(utm)
    ana_mi = tum.index.isin(list(ana_parca))
    ana = tum[ana_mi]
    noktalar = kaynaklar.to_crs(utm).geometry.values

    ana_agac = shapely.STRtree(ana.geometry.values)
    (_, ana_sira), ana_mesafe = ana_agac.query_nearest(noktalar, return_distance=True, all_matches=False)
    tum_agac = shapely.STRtree(tum.geometry.values)
    (_, tum_sira), _ = tum_agac.query_nearest(noktalar, return_distance=True, all_matches=False)

    return pd.DataFrame({
        "tesis": kaynaklar["tesis"].to_numpy(),
        "dugum": ana.index.to_numpy()[ana_sira],
        "mesafe_m": ana_mesafe,
        "baglandi": ana_mesafe <= esik_m,
        "kucuk_parcaya_yakin": ~ana_mi[tum_sira],
    }, columns=sutunlar)


def tesis_raporu(baglantilar: pd.DataFrame) -> dict:
    """Tesis bazında bağlantı özeti.

    Bir tesisin en az bir noktası bağlandıysa tesis analize girer. Bütün
    noktaları eşikten uzaksa 'disarida' sayılır. 'kucuk_parcaya_yakin',
    bağlanan ama en yakın düğümü küçük parçada olan tesis sayısıdır (bilgi amaçlı).
    """
    if baglantilar.empty:
        return {"tesis": 0, "nokta": 0, "disarida_tesis": 0, "disarida_ornek": [],
                "kucuk_parcaya_yakin_tesis": 0, "baglanti_mesafesi_medyan_m": None,
                "baglanti_mesafesi_p95_m": None}
    tesis = baglantilar.groupby("tesis").agg(baglandi=("baglandi", "any"),
                                             kucuk=("kucuk_parcaya_yakin", "all"))
    bagli = baglantilar[baglantilar["baglandi"]]
    disarida = tesis.index[~tesis["baglandi"]].tolist()
    return {
        "tesis": len(tesis),
        "nokta": len(baglantilar),
        "disarida_tesis": len(disarida),
        "disarida_ornek": disarida[:10],
        "kucuk_parcaya_yakin_tesis": int((tesis["baglandi"] & tesis["kucuk"]).sum()),
        "baglanti_mesafesi_medyan_m": round(float(bagli["mesafe_m"].median()), 1) if len(bagli) else None,
        "baglanti_mesafesi_p95_m": round(float(bagli["mesafe_m"].quantile(0.95)), 1) if len(bagli) else None,
    }


# --------------------------------------------------------------------------- Dijkstra

def _kokenleri_bul(onculler: dict, baslangic_tesisi: dict) -> dict:
    """Her düğümün en yakın tesisini öncül zincirini S'ye kadar izleyerek bulur.

    baslangic_tesisi: S'nin doğrudan komşusu olan (öncülü S olan) düğüm -> tesis.
    Sıfır uzunluklu kenarlarda mesafe sırası güvenilmez olduğundan zincir
    açık bir yığınla çözülür ve sonuçlar not edilir.
    """
    koken: dict = {}
    for dugum in onculler:
        if dugum == SANAL_KAYNAK or dugum in koken:
            continue
        zincir = []
        simdiki = dugum
        while simdiki not in koken:
            oncul = onculler[simdiki][0]
            if oncul == SANAL_KAYNAK:
                koken[simdiki] = baslangic_tesisi[simdiki]
                break
            zincir.append(simdiki)
            simdiki = oncul
        for z in reversed(zincir):
            koken[z] = koken[onculler[z][0]]
    return koken


def kamu_dugumleri(H: nx.Graph) -> set:
    """En az bir kamuya açık (ozel olmayan) kenarı olan düğümler."""
    return {n for u, v, d in H.edges(data=True) if not d.get("ozel", False) for n in (u, v)}


def _super_kaynakli_dijkstra(G: nx.Graph, baslangic: dict, gorunum=None) -> tuple[dict, dict]:
    """baslangic {düğüm: ilk mesafe} ile sanal süper kaynaktan Dijkstra; (öncüller, mesafeler).

    Sanal kaynak ve kenarları G'ye eklenir; arama gorunum (G'nin bir
    alt-görünümü, ör. sadece kamu kenarları) üzerinde yapılır, verilmezse G.
    G her durumda eski haline döndürülür. Dönen mesafelerde sanal kaynak yoktur.
    """
    if SANAL_KAYNAK in G:
        raise ValueError(f"Grafta zaten {SANAL_KAYNAK} kimlikli düğüm var")
    G.add_node(SANAL_KAYNAK)
    try:
        G.add_edges_from((SANAL_KAYNAK, d, {"length": float(m), "ozel": False}) for d, m in baslangic.items())
        onculler, mesafeler = nx.dijkstra_predecessor_and_distance(
            G if gorunum is None else gorunum, SANAL_KAYNAK, weight="length")
    finally:
        G.remove_node(SANAL_KAYNAK)
    mesafeler.pop(SANAL_KAYNAK, None)
    return onculler, mesafeler


def ikinci_asama_grafi(H: nx.Graph, ulasilan: dict) -> nx.DiGraph:
    """2. aşamada (site içi) gezilecek yönlü graf.

    - Özel bağlantılar (ozel kenar veya kamu kenarına paralel ozel_length) iki yönlü.
    - 1. aşamada ulaşılamamış iki düğüm arasındaki kamu kenarı iki yönlü: sadece
      site içinden girilebilen kamu "cepleri" (ör. site içindeki etiketsiz yaya yolu).
    - Ulaşılmış düğümden ulaşılmamış düğüme kamu kenarı tek yönlü (kapıdan içeri).
    - Ulaşılmış iki düğüm arasındaki kamu kenarı yok: kamu ağının süresi zaten kesin,
      2. aşama oradan geçip kestirme üretemez.
    Aynı yönde birden fazla bağlantı olursa en kısası kalır.
    """
    O = nx.DiGraph()

    def ekle(a, b, uzunluk):
        if not O.has_edge(a, b) or uzunluk < O[a][b]["length"]:
            O.add_edge(a, b, length=uzunluk)

    for u, v, d in H.edges(data=True):
        oz = ag.ozel_uzunluk(d)
        if oz is not None:
            ekle(u, v, oz)
            ekle(v, u, oz)
        if not d.get("ozel", False):
            u_var, v_var = u in ulasilan, v in ulasilan
            if not u_var and not v_var:
                ekle(u, v, d["length"])
                ekle(v, u, d["length"])
            elif u_var and not v_var:
                ekle(u, v, d["length"])
            elif v_var and not u_var:
                ekle(v, u, d["length"])
    return O


def en_yakin_sureler(
    H: nx.Graph,
    baglantilar: pd.DataFrame,
    hiz_m_dk: float = YURUME_HIZI_M_DK,
) -> pd.DataFrame:
    """İki aşamalı Dijkstra; her düğüm için süre (dk), en yakın tesis ve ozel_yol bayrağı.

    1. Sadece kamuya açık kenarlarda (ozel=False), sanal süper kaynaktan
       Dijkstra. Kaynak kenarlarının ağırlığı bağlantı mesafesidir.
    2. ikinci_asama_grafi üzerinde (özel bağlantılar ve sadece site içinden
       girilebilen kamu cepleri), 1. aşamada süresi bulunan "kapı"
       düğümlerinden o sürelerle başlayan ikinci Dijkstra. Yalnızca 1. aşamada
       ulaşılamayan düğümler bu aşamadan süre alır; kamu düğümlerinin süresi
       değişmez, özel yol kestirme olamaz.

    Sadece baglandi=True noktalar kullanılır; aynı düğüme bağlanan noktalardan
    en kısa bağlantılı olan kalır. Ulaşılamayan düğümler sonuçta yer almaz.
    """
    bos = pd.DataFrame({"sure_dk": pd.Series(dtype=float), "tesis": pd.Series(dtype=object),
                        "ozel_yol": pd.Series(dtype=bool)}, index=pd.Index([], name="osmid"))
    bagli = baglantilar[baglantilar["baglandi"]].sort_values(["dugum", "mesafe_m", "tesis"])
    bagli = bagli.drop_duplicates("dugum")
    if bagli.empty:
        return bos

    # 1. aşama: kamu ağı (sanal kaynağın kenarları da ozel=False olduğundan görünümde kalır)
    kamu = nx.subgraph_view(H, filter_edge=lambda u, v: not H[u][v].get("ozel", False))
    baslangic = {int(d): float(m) for d, m in zip(bagli["dugum"], bagli["mesafe_m"])}
    onculler, mesafeler = _super_kaynakli_dijkstra(H, baslangic, gorunum=kamu)
    koken = _kokenleri_bul(onculler, dict(zip(bagli["dugum"].astype(int), bagli["tesis"])))
    ozel_yol = dict.fromkeys(mesafeler, False)

    O = ikinci_asama_grafi(H, mesafeler)
    kapilar = {n: mesafeler[n] for n in O if n in mesafeler and O.out_degree(n) > 0}
    if kapilar:
        onculler2, mesafeler2 = _super_kaynakli_dijkstra(O, kapilar)
        koken2 = _kokenleri_bul(onculler2, {k: koken[k] for k in kapilar})
        for d, m in mesafeler2.items():
            if d not in mesafeler:
                mesafeler[d], koken[d], ozel_yol[d] = m, koken2[d], True

    osmid = np.fromiter(mesafeler.keys(), dtype=np.int64, count=len(mesafeler))
    return pd.DataFrame({
        "sure_dk": np.fromiter(mesafeler.values(), dtype=float, count=len(mesafeler)) / hiz_m_dk,
        "tesis": [koken[d] for d in osmid],
        "ozel_yol": [ozel_yol[d] for d in osmid],
    }, index=pd.Index(osmid, name="osmid"))


def ozet(sureler: pd.Series, esik_dk: float = ESIK_DK) -> dict:
    """Süre serisinin ortalama, medyan ve eşik altı (<= esik_dk) oranını verir."""
    sureler = sureler.dropna()
    if sureler.empty:
        return {"dugum": 0, "ortalama_dk": None, "medyan_dk": None, "esik_alti_oran": None}
    return {
        "dugum": int(len(sureler)),
        "ortalama_dk": round(float(sureler.mean()), 2),
        "medyan_dk": round(float(sureler.median()), 2),
        "esik_alti_oran": round(float((sureler <= esik_dk).mean()), 4),
    }


# --------------------------------------------------------------------------- hedef dosyaları

def _tesis_kimligi(element, osm_id) -> pd.Series:
    """OSM türü ve kimliğinden 'node/123' biçiminde tesis kimliği üretir."""
    return pd.Series(element).astype(str).str.cat(pd.Series(osm_id).astype("int64").astype(str), sep="/").to_numpy()


def kategori_kaynaklari(dizin: Path = HEDEF_DIZINI) -> dict[str, gpd.GeoDataFrame]:
    """Hedef katmanlarından kategori başına ('tesis', geometry) kaynak noktalarını okur.

    - eczane, birinci_basamak: tesis noktaları
    - hastane: kampüs sınırı örnek noktaları (tesis = hastane)
    - rayli: metro veya Ankaray istasyonlarının girişleri (girişi yoksa istasyon)
    - baskentray: Başkentray istasyon noktaları
    """
    def tesisler(dosya, katman="tesisler"):
        gdf = gpd.read_file(dizin / dosya, layer=katman)
        return gpd.GeoDataFrame({"tesis": _tesis_kimligi(gdf["element"], gdf["id"])},
                                geometry=gdf.geometry.values, crs=gdf.crs)

    hastane = gpd.read_file(dizin / "hastaneler.gpkg", layer="kaynaklar")
    istasyonlar = gpd.read_file(dizin / "rayli.gpkg", layer="istasyonlar")
    rayli = gpd.read_file(dizin / "rayli.gpkg", layer="kaynaklar")
    element = dict(zip(istasyonlar["id"].astype("int64"), istasyonlar["element"]))
    rayli["tesis"] = _tesis_kimligi(rayli["istasyon_id"].astype("int64").map(element), rayli["istasyon_id"])

    def secim(gdf, maske):
        return gpd.GeoDataFrame({"tesis": gdf.loc[maske, "tesis"].to_numpy()},
                                geometry=gdf.loc[maske].geometry.values, crs=gdf.crs)

    return {
        "eczane": tesisler("eczaneler.gpkg"),
        "hastane": gpd.GeoDataFrame({"tesis": _tesis_kimligi(hastane["tesis_element"], hastane["tesis_id"])},
                                    geometry=hastane.geometry.values, crs=hastane.crs),
        "birinci_basamak": tesisler("birinci_basamak.gpkg"),
        "rayli": secim(rayli, rayli["metro"] | rayli["ankaray"]),
        "baskentray": secim(rayli, rayli["baskentray"]),
    }


# --------------------------------------------------------------------------- akış

def main() -> None:
    """Tüm kategoriler için süreleri hesaplar, kaydeder ve özetleri basar."""
    sonuc_yolu = SONUC_DIZINI / "dugum_sureleri.parquet"
    girdiler = [BIRLESIK_AG_DOSYASI, SINIR_DOSYASI, *sorted(HEDEF_DIZINI.glob("*.gpkg"))]
    rapor_yolu = SONUC_DIZINI / "rapor.json"
    eski_rapor = json.loads(rapor_yolu.read_text(encoding="utf-8")) if rapor_yolu.exists() else {}
    if guncel_mi(sonuc_yolu, girdiler) and eski_rapor.get("yontem_surumu") == YONTEM_SURUMU:
        print(f"[hazır] {sonuc_yolu} girdilerden yeni ve aynı yöntem sürümüyle hesaplanmış.")
        print(json.dumps(eski_rapor["ozet"], ensure_ascii=False, indent=2))
        return

    baslangic = time.time()
    G = ag.sade_graf_oku(BIRLESIK_AG_DOSYASI)
    sinirlar = gpd.read_file(SINIR_DOSYASI)
    dugumler = dugum_tablosu(G, sinirlar)
    ana_parca, parca_ozeti = en_buyuk_parca(G)
    disarida = dugumler[~dugumler.index.isin(list(ana_parca))]
    parca_ozeti["disarida_dugum_ilce_ici"] = int(disarida["ilce"].notna().sum())
    parca_ozeti["disarida_dugum_ilceye_gore"] = disarida["ilce"].value_counts().to_dict()
    G.remove_nodes_from([n for n in G.nodes if n not in ana_parca])
    kamu = kamu_dugumleri(G)
    ozel_kenar = sum(1 for _, _, d in G.edges(data=True) if d.get("ozel", False))
    parca_ozeti["ozel_kenar"] = ozel_kenar
    parca_ozeti["sadece_ozel_yol_dugumu"] = len(ana_parca) - len(kamu)
    print(f"Graf: {parca_ozeti['ana_parca_dugum']:,} düğüm ana parçada; {parca_ozeti['parca_sayisi'] - 1} küçük "
          f"parçada {parca_ozeti['disarida_dugum']:,} düğüm dışarıda "
          f"(7 ilçe içinde {parca_ozeti['disarida_dugum_ilce_ici']:,}) ({time.time() - baslangic:.0f} sn)")
    print(f"Konut sitesi özel yolları: {ozel_kenar:,} kenar, sadece özel yola bağlı "
          f"{parca_ozeti['sadece_ozel_yol_dugumu']:,} düğüm (2. aşamada süre alır)")

    sonuc = dugumler.loc[dugumler.index.isin(list(ana_parca)), ["x", "y", "ilce"]].copy()
    ilce_ici = sonuc["ilce"].notna()
    kaynaklar = kategori_kaynaklari()
    tum_baglantilar, rapor = [], {"yontem_surumu": YONTEM_SURUMU, "graf": parca_ozeti, "tesisler": {}, "ozet": {}}

    for kategori in KATEGORILER:
        t0 = time.time()
        # Tesisler sadece kamuya açık kenarı olan düğümlere bağlanır (site içine değil)
        baglantilar = kaynaklari_baglat(kaynaklar[kategori], dugumler, kamu)
        sureler = en_yakin_sureler(G, baglantilar)
        sonuc[f"{kategori}_dk"] = sureler["sure_dk"].reindex(sonuc.index)
        sonuc[f"{kategori}_id"] = sureler["tesis"].reindex(sonuc.index)
        if kategori == KATEGORILER[0]:
            sonuc["ozel_yol"] = sureler["ozel_yol"].reindex(sonuc.index).fillna(False).astype(bool)
        sure_sn = time.time() - t0

        tesis = tesis_raporu(baglantilar)
        ozet_k = {**ozet(sonuc.loc[ilce_ici, f"{kategori}_dk"]), "hesap_sn": round(sure_sn, 2)}
        rapor["tesisler"][kategori], rapor["ozet"][kategori] = tesis, ozet_k
        tum_baglantilar.append(baglantilar.assign(kategori=kategori))
        print(f"{kategori:16s} ort {ozet_k['ortalama_dk']:6.1f} dk | medyan {ozet_k['medyan_dk']:5.1f} dk | "
              f"≤{ESIK_DK} dk %{100 * ozet_k['esik_alti_oran']:5.1f} | {sure_sn:4.1f} sn | "
              f"tesis {tesis['tesis']} (dışarıda {tesis['disarida_tesis']}, küçük parçaya yakın "
              f"{tesis['kucuk_parcaya_yakin_tesis']}), bağlantı medyan {tesis['baglanti_mesafesi_medyan_m']} m")

    SONUC_DIZINI.mkdir(parents=True, exist_ok=True)
    sonuc.reset_index().to_parquet(sonuc_yolu, index=False)
    pd.concat(tum_baglantilar, ignore_index=True).to_parquet(SONUC_DIZINI / "kaynak_baglantilari.parquet", index=False)
    (SONUC_DIZINI / "rapor.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Kaydedildi: {sonuc_yolu} ({len(sonuc):,} düğüm), toplam {time.time() - baslangic:.0f} sn")


if __name__ == "__main__":
    main()
