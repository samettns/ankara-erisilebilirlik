"""Ham OSM özelliklerinden hedef katmanlarını (eczane, hastane, birinci basamak, raylı) üretir.

Bu modül dosya okumaz; osm_yerel'in kesitten çıkardığı ham tabloyu ayıklar,
noktaya çevirir, tekrarları temizler ve Dijkstra için kaynak noktalarını hazırlar.
"""

import math
import re
from difflib import SequenceMatcher

import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPolygon, Point, Polygon

try:
    from src.ayarlar import (GIRIS_ISTASYON_ESIGI_M, HAT_DURAK_ESIGI_M, ISIM_BENZERLIK_ESIGI,
                             KAMPUS_ORNEK_ARALIGI_M, TEKRAR_ESIK_M)
except ModuleNotFoundError:
    from ayarlar import (GIRIS_ISTASYON_ESIGI_M, HAT_DURAK_ESIGI_M, ISIM_BENZERLIK_ESIGI,
                         KAMPUS_ORNEK_ARALIGI_M, TEKRAR_ESIK_M)

# Kesitten okunacak hedeflerin etiketleri (değer True: anahtarın olması yeter)
HEDEF_ETIKETLERI = {
    "amenity": ["pharmacy", "hospital", "clinic", "doctors"],
    "healthcare": True,
    "railway": ["station", "halt", "subway_entrance"],
}
# Ham dosyada saklanan sütunlar
HAM_SUTUNLAR = [
    "element", "id", "name", "amenity", "healthcare", "healthcare:speciality",
    "operator", "operator:type", "emergency", "railway", "station", "subway",
    "light_rail", "train", "geometry",
]
NOKTA_SUTUNLARI = ["element", "id", "name", "geometry"]

# Birinci basamak: bu healthcare değerleri dışındakiler (diş, fizyoterapi, lab ...) alınmaz
BIRINCI_BASAMAK_HEALTHCARE = {"centre", "clinic", "doctor"}
HARIC_UZMANLIK = re.compile(r"dent|orthodont|physiother|dialysis|cosmetic|plastic|aesthetic")
HARIC_ISIM = re.compile(
    r"diş|dental|ortodont|fizik tedavi|fizyoterap|diyaliz|laboratuv|tahlil|görüntüleme|"
    r"radyoloji|veteriner|estetik|güzellik|lazer|epilasyon|psikolo|optik|işitme|tüp bebek|saç ekim"
)
ASM_ISIM = re.compile(r"aile sağlığı|\basm\b|sağlık ocağı|toplum sağlığı")
DIS_HASTANESI_ISIM = re.compile(r"ağız ve diş|diş hastane")

# Raylı sistem hatları (OSM route ilişkisi ref değeri)
HAT_REF_DESENI = r"^(M[0-9]+|A|B1)$"


# --------------------------------------------------------------------------- ortak yardımcılar

def kucuk_harf(metin) -> str:
    """Metni Türkçe kurallarla küçük harfe çevirir; metin değilse "" döner."""
    if not isinstance(metin, str):
        return ""
    return metin.replace("İ", "i").replace("I", "ı").lower()


def isim_normallestir(isim) -> str:
    """İsmi karşılaştırma için sadeleştirir.

    Türkçe kurallara göre küçük harfe çevirir, "eczane"/"eczanesi"
    kelimelerini ve noktalamayı atar. Boş/eksik isim için "" döner.
    Örn. "Eczane Temel" ve "TEMEL ECZANESİ" ikisi de "temel" olur.
    """
    isim = re.sub(r"\beczan(e|esi)\b", " ", kucuk_harf(isim))
    return " ".join(re.findall(r"\w+", isim))


def isim_benzerligi(a, b) -> float:
    """İki ismin normalleştirilmiş hallerinin 0-1 arası benzerliğini verir.

    İsimlerden biri boşsa 0 döner: isimsiz kayıtlar hiçbir şeye benzemez.
    """
    na, nb = isim_normallestir(a), isim_normallestir(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _sutunlari_tamamla(gdf: gpd.GeoDataFrame, sutunlar: list[str]) -> gpd.GeoDataFrame:
    """Eksik sütunları None ile ekleyip sadece istenen sütunları (bu sırayla) bırakır."""
    gdf = gdf.copy()
    for sutun in sutunlar:
        if sutun not in gdf.columns:
            gdf[sutun] = None
    return gdf[sutunlar]


def ham_ozellikleri_hazirla(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """OSM özellik tablosunu kaydedilebilir ham tabloya (HAM_SUTUNLAR) çevirir.

    (element, id) indeksi varsa sütuna açar, sadece HAM_SUTUNLAR'ı tutar ve
    metin sütunlarını str/None yapar (parquet'e yazılabilsin diye).
    """
    gdf = gdf.reset_index() if "element" not in gdf.columns else gdf.reset_index(drop=True)
    gdf = _sutunlari_tamamla(gdf, HAM_SUTUNLAR)
    for sutun in HAM_SUTUNLAR:
        if sutun not in ("geometry", "id"):
            gdf[sutun] = gdf[sutun].map(lambda v: v if isinstance(v, str) else None)
    gdf["id"] = gdf["id"].astype("int64")
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326")


def noktalara_cevir(gdf: gpd.GeoDataFrame, sutunlar: list[str] = NOKTA_SUTUNLARI) -> gpd.GeoDataFrame:
    """OSM özelliklerini tek tip nokta katmanına çevirir.

    Poligon olarak çizilmiş yerler (bina vb.) metrik projeksiyonda
    centroid'e indirgenir. Sadece istenen sütunlar tutulur; çıktı EPSG:4326'dır.
    """
    if "element" not in gdf.columns:
        gdf = gdf.reset_index()
    gdf = _sutunlari_tamamla(gdf.reset_index(drop=True), sutunlar)
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326")
    if gdf.empty:
        return gdf

    metrik = gdf.to_crs(gdf.estimate_utm_crs())
    nokta_degil = ~metrik.geometry.geom_type.eq("Point")
    metrik.loc[nokta_degil, "geometry"] = metrik.loc[nokta_degil].geometry.centroid
    return metrik.to_crs("EPSG:4326")


def tekrarlari_temizle(
    gdf: gpd.GeoDataFrame,
    esik_m: float = TEKRAR_ESIK_M,
    benzerlik_esigi: float = ISIM_BENZERLIK_ESIGI,
) -> gpd.GeoDataFrame:
    """Aynı yerin OSM'deki tekrar kayıtlarını tek kayda indirir.

    Birbirine esik_m metreden yakın ve isim benzerliği benzerlik_esigi'nden
    yüksek kayıtlar aynı yer sayılır. Zincirleme eşleşmeler (A~B, B~C) tek
    grupta birleşir. Her gruptan önce 'node' (nokta) kaydı, sonra ilk kayıt
    tutulur. Girdi coğrafi CRS'te (EPSG:4326) olmalıdır.
    """
    gdf = gdf.reset_index(drop=True)
    if len(gdf) < 2:
        return gdf.copy()

    metrik = gdf.to_crs(gdf.estimate_utm_crs())
    sol, sag = metrik.sindex.query(metrik.geometry, predicate="dwithin", distance=esik_m)

    baglar = nx.Graph()
    baglar.add_nodes_from(range(len(gdf)))
    for i, j in zip(sol, sag):
        if i < j and isim_benzerligi(gdf.at[i, "name"], gdf.at[j, "name"]) >= benzerlik_esigi:
            baglar.add_edge(i, j)

    tutulacak = [
        min(grup, key=lambda k: (gdf.at[k, "element"] != "node", k))
        for grup in nx.connected_components(baglar)
    ]
    return gdf.loc[sorted(tutulacak)].reset_index(drop=True)


def ilce_ekle(gdf: gpd.GeoDataFrame, sinirlar: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Her noktaya içinde bulunduğu ilçenin adını 'ilce' sütunu olarak ekler.

    Genişletme bölgesinde (7 ilçenin dışında) kalan noktalarda ilce None olur.
    sinirlar'da 'ilce' sütunu ve EPSG:4326 geometri bulunmalıdır.
    """
    if gdf.empty:
        return gdf.assign(ilce=pd.Series(dtype=object))
    eslesme = gpd.sjoin(gdf[["geometry"]], sinirlar[["ilce", "geometry"]], how="left", predicate="within")
    ilk = eslesme[~eslesme.index.duplicated()]["ilce"]
    sonuc = gdf.copy()
    sonuc["ilce"] = pd.Series([v if isinstance(v, str) else None for v in ilk], index=sonuc.index, dtype=object)
    return sonuc


# --------------------------------------------------------------------------- eczane

def eczaneleri_ayikla(ham: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Ham özelliklerden eczaneleri nokta olarak ayıklar ve tekrarları temizler."""
    maske = ham["amenity"].eq("pharmacy") | ham["healthcare"].eq("pharmacy")
    return tekrarlari_temizle(noktalara_cevir(ham[maske]))


# --------------------------------------------------------------------------- hastane

def sinir_ornekle(geometri, aralik_m: float = KAMPUS_ORNEK_ARALIGI_M) -> list[Point]:
    """Metrik CRS'teki poligonun dış sınırından yaklaşık aralik_m arayla noktalar üretir.

    MultiPolygon'da her parça ayrı örneklenir. Nokta geometride noktanın
    kendisi döner. Her halka için en az bir nokta üretilir.
    """
    if isinstance(geometri, Point):
        return [geometri]
    if isinstance(geometri, Polygon):
        parcalar = [geometri]
    elif isinstance(geometri, MultiPolygon):
        parcalar = list(geometri.geoms)
    else:
        return [geometri.representative_point()]

    noktalar = []
    for parca in parcalar:
        halka = parca.exterior
        adet = max(1, math.ceil(halka.length / aralik_m))
        noktalar += [halka.interpolate(i * halka.length / adet) for i in range(adet)]
    return noktalar


def hastaneleri_ayikla(
    ham: gpd.GeoDataFrame,
    aralik_m: float = KAMPUS_ORNEK_ARALIGI_M,
    esik_m: float = TEKRAR_ESIK_M,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Hastaneleri ayıklar; (tesisler, kaynaklar) döndürür.

    - Özel/kamu tüm amenity=hospital (veya healthcare=hospital) kayıtları alınır,
      ağız ve diş hastaneleri çıkarılır. operator:type ileride kamu filtresi için saklanır.
    - Bir hastane poligonunun içinde/esik_m yakınındaki nokta kayıtlar atılır
      (aynı kampüsün tekrarı); kalan noktalar kendi aralarında tekrar temizlenir.
    - tesisler: hastane başına bir satır, kampüs içinde temsilî nokta ve alan_m2.
    - kaynaklar: Dijkstra başlangıç noktaları. Kampüslerde sınırdan aralik_m
      arayla örneklenmiş noktalar, nokta hastanelerde noktanın kendisi.
    """
    maske = ham["amenity"].eq("hospital") | ham["healthcare"].eq("hospital")
    isimler = ham["name"].map(kucuk_harf)
    dis = isimler.str.contains(DIS_HASTANESI_ISIM) | ham["healthcare:speciality"].map(kucuk_harf).str.contains("dent")
    secili = ham[maske & ~dis].reset_index(drop=True)
    sutunlar = ["element", "id", "name", "operator", "operator:type", "emergency", "geometry"]
    secili = _sutunlari_tamamla(secili, sutunlar)
    bos = gpd.GeoDataFrame(columns=sutunlar + ["alan_m2"], geometry="geometry", crs="EPSG:4326")
    if secili.empty:
        return bos, bos.drop(columns=["alan_m2"]).rename(columns={"element": "tesis_element", "id": "tesis_id"})

    secili = gpd.GeoDataFrame(secili, geometry="geometry", crs="EPSG:4326")
    utm = secili.estimate_utm_crs()
    metrik = secili.to_crs(utm)
    alanli = metrik.geom_type.isin(["Polygon", "MultiPolygon"])
    kampusler = metrik[alanli]
    noktalar = metrik[~alanli]
    if not kampusler.empty and not noktalar.empty:
        kampus_tamponu = kampusler.buffer(esik_m).union_all()
        noktalar = noktalar[~noktalar.intersects(kampus_tamponu)]
    if len(noktalar) > 1:
        noktalar = tekrarlari_temizle(noktalar.to_crs("EPSG:4326"), esik_m=esik_m).to_crs(utm)

    tesisler_m = pd.concat([kampusler, noktalar]).reset_index(drop=True)
    kaynak_satirlari = []
    for _, satir in tesisler_m.iterrows():
        for nokta in sinir_ornekle(satir.geometry, aralik_m):
            kaynak_satirlari.append({
                "tesis_element": satir["element"], "tesis_id": satir["id"], "name": satir["name"],
                "operator": satir["operator"], "operator:type": satir["operator:type"],
                "emergency": satir["emergency"], "geometry": nokta,
            })
    kaynaklar = gpd.GeoDataFrame(kaynak_satirlari, geometry="geometry", crs=utm).to_crs("EPSG:4326")

    tesisler_m = gpd.GeoDataFrame(tesisler_m, geometry="geometry", crs=utm)
    tesisler_m["alan_m2"] = tesisler_m.area.round(0)
    tesisler_m["geometry"] = tesisler_m.geometry.representative_point()
    return tesisler_m.to_crs("EPSG:4326"), kaynaklar


# --------------------------------------------------------------------------- birinci basamak

def birinci_basamak_ayikla(ham: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Aile sağlığı merkezi, sağlık ocağı ve genel klinikleri ayıklar.

    amenity=clinic/doctors veya healthcare=centre/clinic/doctor kayıtları
    alınır. Hastaneler, healthcare alt türü başka olanlar (diş, fizyoterapi,
    laboratuvar...), uzmanlığı veya ismi bu tür hizmetleri gösterenler çıkarılır.
    'asm' sütunu isminde aile sağlığı/sağlık ocağı/toplum sağlığı geçenleri işaretler.
    """
    saglik = ham["healthcare"]
    aday = ham["amenity"].isin(["clinic", "doctors"]) | saglik.isin(BIRINCI_BASAMAK_HEALTHCARE)
    hastane = ham["amenity"].eq("hospital") | saglik.eq("hospital")
    baska_tur = saglik.notna() & ~saglik.isin(BIRINCI_BASAMAK_HEALTHCARE)
    uzmanlik = ham["healthcare:speciality"].map(kucuk_harf).str.contains(HARIC_UZMANLIK)
    isimler = ham["name"].map(kucuk_harf)
    haric_isim = isimler.str.contains(HARIC_ISIM)

    secili = ham[aday & ~hastane & ~baska_tur & ~uzmanlik & ~haric_isim]
    sutunlar = ["element", "id", "name", "amenity", "healthcare", "healthcare:speciality",
                "operator", "operator:type", "geometry"]
    sonuc = tekrarlari_temizle(noktalara_cevir(secili, sutunlar))
    sonuc["asm"] = sonuc["name"].map(kucuk_harf).str.contains(ASM_ISIM)
    return sonuc


# --------------------------------------------------------------------------- raylı sistem

def istasyon_hatlari(
    hat_json: dict,
    istasyonlar: gpd.GeoDataFrame,
    esik_m: float = HAT_DURAK_ESIGI_M,
) -> dict[int, set[str]]:
    """Her istasyonun hangi hatlara (ref) ait olduğunu bulur.

    1. İstasyon doğrudan hat ilişkisinin üyesiyse o hat.
    2. İstasyonun bulunduğu stop_area, hattın bir durağını içeriyorsa o hat.
    3. Hâlâ hattı yoksa: istasyon türüne uygun (metro/hafif raylı istasyonu
       için subway/light_rail, tren istasyonu için train) hatların, hiçbir
       stop_area'ya ait olmayan durak düğümlerinden esik_m içinde olanlar.
    istasyonlar'da 'id', 'station', 'train' sütunları ve EPSG:4326 nokta olmalı.
    """
    elemanlar = hat_json.get("elements", [])
    hatlar = [e for e in elemanlar if e["type"] == "relation" and "route" in e.get("tags", {})]
    alanlar = [e for e in elemanlar if e["type"] == "relation" and e.get("tags", {}).get("public_transport") == "stop_area"]
    durak_konum = {e["id"]: (e["lon"], e["lat"]) for e in elemanlar if e["type"] == "node" and "lat" in e}

    def dugum_uyeler(iliski):
        return {m["ref"] for m in iliski.get("members", []) if m["type"] == "node"}

    hat_uyeleri = [(h["tags"]["ref"], h["tags"]["route"], dugum_uyeler(h)) for h in hatlar]
    sonuc: dict[int, set[str]] = {int(i): set() for i in istasyonlar["id"]}

    for ref, _, uyeler in hat_uyeleri:
        for ist_id in sonuc.keys() & uyeler:
            sonuc[ist_id].add(ref)
    for alan in alanlar:
        alan_uyeleri = dugum_uyeler(alan)
        for ist_id in sonuc.keys() & alan_uyeleri:
            for ref, _, uyeler in hat_uyeleri:
                if alan_uyeleri & uyeler:
                    sonuc[ist_id].add(ref)

    hatsizlar = istasyonlar[istasyonlar["id"].map(lambda i: not sonuc[int(i)])]
    # Başka bir durak alanına ait duraklar yedek kuralda kullanılmaz: o durak zaten
    # kendi istasyonuna bağlı (ör. Ankara Garı'nın B1 durağı, yanındaki Ankara YHT'ye geçmemeli).
    alandaki_duraklar = set().union(*(dugum_uyeler(a) for a in alanlar)) if alanlar else set()
    satirlar = [
        {"ref": ref, "route": route, "geometry": Point(durak_konum[d])}
        for ref, route, uyeler in hat_uyeleri for d in uyeler
        if d in durak_konum and d not in alandaki_duraklar
    ]
    if hatsizlar.empty or not satirlar:
        return sonuc

    duraklar = gpd.GeoDataFrame(satirlar, crs="EPSG:4326")
    utm = duraklar.estimate_utm_crs()
    duraklar, hatsizlar = duraklar.to_crs(utm), hatsizlar.to_crs(utm)
    for _, ist in hatsizlar.iterrows():
        tren = ist["train"] == "yes" or ist["station"] == "train"
        uygun = duraklar["route"].eq("train") if tren else duraklar["route"].isin(["subway", "light_rail"])
        yakin = duraklar[uygun & (duraklar.distance(ist.geometry) <= esik_m)]
        sonuc[int(ist["id"])].update(yakin["ref"])
    return sonuc


def rayli_ayikla(
    ham: gpd.GeoDataFrame,
    hat_json: dict,
    giris_esigi_m: float = GIRIS_ISTASYON_ESIGI_M,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Metro, Ankaray ve Başkentray istasyonlarını ve Dijkstra kaynaklarını üretir.

    - İstasyon: railway=station/halt (proposed/construction kendiliğinden dışarıda).
    - Hatlar istasyon_hatlari ile bulunur. Hiç hattı bulunamayan station=subway
      istasyonu etikete göre metro (light_rail=yes ise Ankaray) sayılır.
      Hiçbir sisteme girmeyen (YHT, bölgesel tren) istasyonlar atılır.
    - kaynaklar: metro/Ankaray istasyonunun en yakın (giris_esigi_m içi)
      girişleri; girişi olmayan istasyonda istasyonun kendi noktası.
    """
    sutunlar = ["element", "id", "name", "station", "subway", "light_rail", "train", "geometry"]
    istasyonlar = noktalara_cevir(ham[ham["railway"].isin(["station", "halt"])], sutunlar)
    hatlar = istasyon_hatlari(hat_json, istasyonlar)
    refler = istasyonlar["id"].map(lambda i: hatlar[int(i)])
    etiketle_metro = refler.map(len).eq(0) & istasyonlar["station"].eq("subway")
    hafif = istasyonlar["light_rail"].eq("yes")

    istasyonlar["hatlar"] = refler.map(lambda s: ",".join(sorted(s)))
    istasyonlar["metro"] = refler.map(lambda s: any(r.startswith("M") for r in s)) | (etiketle_metro & ~hafif)
    istasyonlar["ankaray"] = refler.map(lambda s: "A" in s) | (etiketle_metro & hafif)
    istasyonlar["baskentray"] = refler.map(lambda s: "B1" in s)
    sistemler = ["metro", "ankaray", "baskentray"]
    istasyonlar = istasyonlar[istasyonlar[sistemler].any(axis=1)].reset_index(drop=True)
    istasyonlar = istasyonlar.drop(columns=["station", "subway", "light_rail", "train"])

    utm = istasyonlar.estimate_utm_crs() if not istasyonlar.empty else "EPSG:32636"
    girisler = noktalara_cevir(ham[ham["railway"].eq("subway_entrance")]).to_crs(utm)
    yer_alti = istasyonlar[istasyonlar["metro"] | istasyonlar["ankaray"]].to_crs(utm)
    if not girisler.empty and not yer_alti.empty:
        eslesme = gpd.sjoin_nearest(
            girisler[["id", "geometry"]].rename(columns={"id": "giris_id"}),
            yer_alti[["id", "geometry"]].rename(columns={"id": "istasyon_id"}),
            max_distance=giris_esigi_m,
        )
        eslesme = eslesme[~eslesme.index.duplicated()]
    else:
        eslesme = gpd.GeoDataFrame(columns=["giris_id", "istasyon_id", "geometry"], geometry="geometry", crs=utm)

    kaynak_satirlari = []
    for _, ist in istasyonlar.to_crs(utm).iterrows():
        ozellik = {"istasyon_id": ist["id"], "istasyon_adi": ist["name"], "hatlar": ist["hatlar"],
                   **{s: bool(ist[s]) for s in sistemler}}
        girisleri = eslesme[eslesme["istasyon_id"] == ist["id"]]
        if len(girisleri):
            kaynak_satirlari += [{**ozellik, "tur": "giris", "geometry": g} for g in girisleri.geometry]
        else:
            kaynak_satirlari.append({**ozellik, "tur": "istasyon", "geometry": ist.geometry})
    kaynaklar = gpd.GeoDataFrame(
        kaynak_satirlari,
        columns=["istasyon_id", "istasyon_adi", "hatlar", *sistemler, "tur", "geometry"],
        geometry="geometry", crs=utm,
    ).to_crs("EPSG:4326")
    return istasyonlar, kaynaklar
