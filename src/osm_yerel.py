"""Yerel OSM PBF dosyasından Ankara kesiti, ilçe sınırları, yürüme yolları, hedefler ve raylı hatlar.

İnternete çıkmaz. Akış:
1. ankara_kesiti_cikar: Türkiye PBF'inden sabit bir kutu içindeki ilgili
   nesneleri (yollar, sağlık, raylı sistem, ilçe sınırı ilişkileri) referans
   tamamlayarak küçük bir Ankara PBF'ine yazar (tek seferlik, yavaş adım).
2. Diğer fonksiyonlar bu küçük kesitten okur (hızlı).
"""

import multiprocessing as mp
import os
import re
import sys
import traceback
from pathlib import Path
from xml.sax.saxutils import quoteattr

import geopandas as gpd
import numpy as np
import osmium
import osmnx as ox
import shapely
import shapely.wkb
from shapely.geometry import MultiPolygon, Point

try:
    from src.ayarlar import AG_TIPI, ILCELER, KESIT_KUTUSU, KONUM_DEPOSU
    from src.hedefler import HAT_REF_DESENI, HEDEF_ETIKETLERI, ham_ozellikleri_hazirla
except ModuleNotFoundError:
    from ayarlar import AG_TIPI, ILCELER, KESIT_KUTUSU, KONUM_DEPOSU
    from hedefler import HAT_REF_DESENI, HEDEF_ETIKETLERI, ham_ozellikleri_hazirla

# Kesite açıkça (etiketleriyle) yazılacak nesnelerin anahtarları
KESIT_ANAHTARLARI = ("highway", "amenity", "healthcare", "railway", "public_transport", "route", "boundary")
YOL_ANAHTARLARI = ("highway", "railway", "amenity", "healthcare", "public_transport")
NOKTA_ANAHTARLARI = ("amenity", "healthcare", "railway", "public_transport")
HAT_TURLERI = {"subway", "light_rail", "train"}
XML_URETICI = "ankara-erisilebilirlik"  # 'OSMnx' içermemeli, yoksa OSMnx uyarı verir


# --------------------------------------------------------------------------- etiket yardımcıları

def _etiketler(nesne) -> dict[str, str]:
    """pyosmium nesnesinin etiketlerini sıradan sözlüğe çevirir."""
    return {t.k: t.v for t in nesne.tags}


def ag_filtresi_kurallari(ag_tipi: str = AG_TIPI) -> list[tuple[str, str | None]]:
    """OSMnx'in Overpass ağ filtresini (anahtar, dışlama_deseni) kurallarına çevirir.

    Örn. '["highway"]["foot"!~"no"]' -> [("highway", None), ("foot", "no")].
    Deseni None olan kural "anahtar bulunmalı" demektir. Kurallar OSMnx'in
    kendi filtre metninden okunduğu için OSMnx ile birebir aynı kalır.
    """
    metin = ox._overpass._get_network_filter(ag_tipi)
    kurallar = []
    for parca in re.finditer(r'\["([^"]+)"(!~"([^"]*)")?\]', metin):
        anahtar, dislama, desen = parca.groups()
        kurallar.append((anahtar, desen if dislama else None))
    if not kurallar or metin.count("[") != len(kurallar):
        raise ValueError(f"OSMnx ağ filtresi tanınmayan biçimde: {metin}")
    return kurallar


def ag_filtresinden_gecer(etiketler: dict[str, str], kurallar: list[tuple[str, str | None]]) -> bool:
    """Etiketlerin OSMnx ağ filtresinden geçip geçmediğini Overpass anlamıyla söyler.

    ["k"] -> k bulunmalı. ["k"!~"desen"] -> k yoksa geçer; varsa değeri desenle
    (büyük/küçük harf duyarlı, kısmi) eşleşmemeli. Örn. highway!~"motor"
    motorway ve motorway_link'i dışlar.
    """
    for anahtar, desen in kurallar:
        deger = etiketler.get(anahtar)
        if desen is None:
            if deger is None:
                return False
        elif deger is not None and re.search(desen, deger):
            return False
    return True


def hedef_etiketi_mi(etiketler: dict[str, str], hedef_etiketleri: dict = HEDEF_ETIKETLERI) -> bool:
    """Etiketlerin hedef etiket tanımına (OSMnx 'tags' biçimi) uyup uymadığını söyler.

    Değer True ise anahtarın bulunması yeter; liste ise değer listede olmalı.
    """
    for anahtar, izinli in hedef_etiketleri.items():
        deger = etiketler.get(anahtar)
        if deger is None:
            continue
        if izinli is True or deger in izinli:
            return True
    return False


# --------------------------------------------------------------------------- 1. kesit

def _kutuda(konum, kutu) -> bool:
    """pyosmium konumunun (batı, güney, doğu, kuzey) kutusu içinde olup olmadığı."""
    if not konum.valid():
        return False
    bati, guney, dogu, kuzey = kutu
    return bati <= konum.lon <= dogu and guney <= konum.lat <= kuzey


def ankara_kesiti_cikar(
    pbf: Path,
    cikti: Path,
    kutu: tuple[float, float, float, float] = KESIT_KUTUSU,
    ilceler: list[str] = ILCELER,
    konum_deposu: str = KONUM_DEPOSU,
) -> Path:
    """Büyük PBF'ten Ankara için gerekli nesneleri referans-tam küçük bir PBF'e yazar.

    Etiketleriyle yazılanlar:
    - kutu içindeki hedef düğümleri (amenity/healthcare/railway/public_transport)
    - en az bir düğümü kutuda olan yol/raylı/sağlık yolları
    - adı ilceler'de olan admin_level=6 idari sınır ilişkileri (konuma bakılmaz,
      Ankara dışındaki adaşlar sınır okunurken elenir)
    - üyelerinden biri yukarıda seçilmiş olan hat (route), stop_area ve
      multipolygon ilişkileri
    Bunların referans verdiği düğüm ve yollar BackReferenceWriter ile etiketsiz
    eklenir. Çıktı varsa hiçbir şey yapılmaz.
    """
    if cikti.exists():
        return cikti
    cikti.parent.mkdir(parents=True, exist_ok=True)
    gecici = cikti.with_name("yaziliyor-" + cikti.name)
    gecici.unlink(missing_ok=True)
    # libosmium disk tabanlı konum deposunu Windows'ta süreç kapanana kadar açık
    # tutar; bu yüzden geçiş ayrı bir süreçte yapılır ve ~1,6 GB'lık depo sonra silinir.
    surec = mp.get_context("spawn").Process(
        target=_kesit_sureci, args=(pbf, gecici, kutu, sorted(ilceler), konum_deposu))
    surec.start()
    surec.join()
    konum_dosyasi = konum_deposu.split(",", 1)[1] if "_file_array," in konum_deposu else None
    if konum_dosyasi:
        Path(konum_dosyasi).unlink(missing_ok=True)
    if surec.exitcode != 0:
        gecici.unlink(missing_ok=True)
        raise RuntimeError(f"Kesit çıkarma süreci hata verdi (çıkış kodu {surec.exitcode})")
    gecici.replace(cikti)
    return cikti


def _kesit_sureci(pbf: Path, gecici: Path, kutu, isimler: list[str], konum_deposu: str) -> None:
    """Alt süreç girişi: kesiti yazar ve yorumlayıcı kapanışını beklemeden çıkar."""
    kod = 0
    try:
        _kesiti_yaz(pbf, gecici, kutu, set(isimler), konum_deposu)
    except BaseException:
        traceback.print_exc()
        kod = 1
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(kod)


def _kesiti_yaz(pbf: Path, gecici: Path, kutu, isimler: set[str], konum_deposu: str) -> None:
    """ankara_kesiti_cikar'ın asıl geçişi (alt süreçte çalışır)."""
    secilen = osmium.IdTracker()
    isleyici = (osmium.FileProcessor(str(pbf))
                .with_locations(konum_deposu)
                .with_filter(osmium.filter.KeyFilter(*KESIT_ANAHTARLARI)))
    with osmium.BackReferenceWriter(str(gecici), ref_src=str(pbf), overwrite=True, remove_tags=True) as yazici:
        for nesne in isleyici:
            if nesne.is_node():
                if any(a in nesne.tags for a in NOKTA_ANAHTARLARI) and _kutuda(nesne.location, kutu):
                    yazici.add_node(nesne)
                    secilen.add_node(nesne.id)
            elif nesne.is_way():
                if any(a in nesne.tags for a in YOL_ANAHTARLARI) and any(_kutuda(n.location, kutu) for n in nesne.nodes):
                    yazici.add_way(nesne)
                    secilen.add_way(nesne.id)
            elif nesne.is_relation():
                etiket = nesne.tags
                ilce_siniri = (etiket.get("boundary") == "administrative" and etiket.get("admin_level") == "6"
                               and etiket.get("name") in isimler)
                ilgili = (etiket.get("route") in HAT_TURLERI or etiket.get("public_transport") == "stop_area"
                          or (etiket.get("type") == "multipolygon" and any(a in etiket for a in NOKTA_ANAHTARLARI)))
                if ilce_siniri or (ilgili and secilen.contains_any_references(nesne)):
                    yazici.add_relation(nesne)


# --------------------------------------------------------------------------- 2. sınırlar

def _geometri(fabrika, alan):
    """pyosmium alanını shapely geometriye çevirir; tek parçalıysa Polygon döner."""
    g = shapely.wkb.loads(fabrika.create_multipolygon(alan), hex=True)
    return g.geoms[0] if isinstance(g, MultiPolygon) and len(g.geoms) == 1 else g


def ilce_sinirlarini_oku(
    kesit: Path,
    ilceler: list[str] = ILCELER,
    kutu: tuple[float, float, float, float] = KESIT_KUTUSU,
) -> gpd.GeoDataFrame:
    """Kesitteki admin_level=6 idari sınır ilişkilerinden ilçe poligonlarını kurar.

    Temsilî noktası kutu dışında kalan adaş ilçeler elenir. Her ilçeden
    tam bir poligon bulunamazsa veya birden fazla bulunursa hata verilir.
    Çıktı ilceler sırasıyla ('ilce', geometry) EPSG:4326'dır.
    """
    fabrika = osmium.geom.WKBFactory()
    bulunan: dict[str, list] = {i: [] for i in ilceler}
    bati, guney, dogu, kuzey = kutu
    for nesne in osmium.FileProcessor(str(kesit)).with_areas():
        if not nesne.is_area() or nesne.from_way():
            continue
        etiket = nesne.tags
        if (etiket.get("boundary") != "administrative" or etiket.get("admin_level") != "6"
                or etiket.get("name") not in bulunan):
            continue
        geometri = _geometri(fabrika, nesne)
        nokta = geometri.representative_point()
        if bati <= nokta.x <= dogu and guney <= nokta.y <= kuzey:
            bulunan[etiket.get("name")].append(geometri)

    sorunlu = {i: len(g) for i, g in bulunan.items() if len(g) != 1}
    if sorunlu:
        raise ValueError(f"İlçe sınırı bulunamadı veya birden fazla: {sorunlu}")
    return gpd.GeoDataFrame({"ilce": ilceler}, geometry=[bulunan[i][0] for i in ilceler], crs="EPSG:4326")


# --------------------------------------------------------------------------- 3. yürüme yolları

class YuruyusYollari:
    """Kesitteki yürünebilir yolların düğüm dizileri, koordinatları ve etiketleri.

    Koordinatlar tek bir düz dizide tutulur; i. yolun düğümleri
    baslangic[i]:baslangic[i+1] aralığındadır. Bu yapı, bir ilçe
    poligonuna değen yolları vektörel olarak seçmeyi sağlar.
    """

    def __init__(self, yol_idleri, etiketler, dugum_idleri, lon, lat, baslangic):
        self.yol_idleri = np.asarray(yol_idleri, dtype=np.int64)
        self.etiketler = etiketler
        self.dugum_idleri = np.asarray(dugum_idleri, dtype=np.int64)
        self.lon = np.asarray(lon, dtype=float)
        self.lat = np.asarray(lat, dtype=float)
        self.baslangic = np.asarray(baslangic, dtype=np.int64)

    def __len__(self) -> int:
        return len(self.yol_idleri)


def yuruyus_yollarini_topla(kesit: Path, ag_tipi: str = AG_TIPI) -> YuruyusYollari:
    """Kesitten OSMnx ağ filtresinden geçen yolları toplar.

    Konumu geçersiz düğüm içeren yollar atlanır (kesit referans-tam olduğu
    için normalde olmaz). Etiketlerden sadece OSMnx'in kullandığı yol
    etiketleri tutulur.
    """
    kurallar = ag_filtresi_kurallari(ag_tipi)
    tutulacak = set(ox.settings.useful_tags_way) | {"highway"}
    yol_idleri, etiketler, dugumler, lon, lat, baslangic = [], [], [], [], [], [0]
    isleyici = osmium.FileProcessor(str(kesit)).with_locations().with_filter(osmium.filter.KeyFilter("highway"))
    for yol in isleyici:
        if not yol.is_way():
            continue
        etiket = _etiketler(yol)
        if not ag_filtresinden_gecer(etiket, kurallar):
            continue
        if not all(n.location.valid() for n in yol.nodes) or len(yol.nodes) < 2:
            continue
        yol_idleri.append(yol.id)
        etiketler.append({k: v for k, v in etiket.items() if k in tutulacak})
        for n in yol.nodes:
            dugumler.append(n.ref)
            lon.append(n.lon)
            lat.append(n.lat)
        baslangic.append(len(dugumler))
    return YuruyusYollari(yol_idleri, etiketler, dugumler, lon, lat, baslangic)


def poligona_degen_yollar(yollar: YuruyusYollari, poligon) -> np.ndarray:
    """En az bir düğümü poligonun içinde (veya sınırında) olan yolların sıra numaraları.

    OSMnx'in truncate_by_edge=True davranışına karşılık gelir: sınırı kesen
    yollar tamamıyla dahil edilir, böylece komşu ilçe grafıyla birleşir.
    """
    if len(yollar) == 0:
        return np.array([], dtype=np.int64)
    icinde = shapely.intersects_xy(poligon, yollar.lon, yollar.lat).astype(np.int64)
    yol_basina = np.add.reduceat(icinde, yollar.baslangic[:-1])
    return np.flatnonzero(yol_basina > 0)


def osm_xml_yaz(yollar: YuruyusYollari, siralar: np.ndarray, cikti: Path) -> None:
    """Seçilen yolları ve düğümlerini OSMnx'in graph_from_xml'i için OSM XML'e yazar."""
    dugumler: dict[int, tuple[float, float]] = {}
    for i in siralar:
        bas, son = yollar.baslangic[i], yollar.baslangic[i + 1]
        for j in range(bas, son):
            dugumler[int(yollar.dugum_idleri[j])] = (yollar.lat[j], yollar.lon[j])

    cikti.parent.mkdir(parents=True, exist_ok=True)
    with open(cikti, "w", encoding="utf-8") as f:
        f.write(f"<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\" generator={quoteattr(XML_URETICI)}>\n")
        for dugum, (enlem, boylam) in dugumler.items():
            f.write(f'  <node id="{dugum}" lat="{enlem:.7f}" lon="{boylam:.7f}"/>\n')
        for i in siralar:
            f.write(f'  <way id="{yollar.yol_idleri[i]}">\n')
            for j in range(yollar.baslangic[i], yollar.baslangic[i + 1]):
                f.write(f'    <nd ref="{yollar.dugum_idleri[j]}"/>\n')
            for k, v in yollar.etiketler[i].items():
                f.write(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>\n")
            f.write("  </way>\n")
        f.write("</osm>\n")


# --------------------------------------------------------------------------- 4. hedefler

def hedef_ozelliklerini_oku(kesit: Path, alan) -> gpd.GeoDataFrame:
    """Kesitten hedef etiketli düğüm ve alanları ham tablo (HAM_SUTUNLAR) olarak okur.

    Kapalı yollar ve multipolygon ilişkileri alan (poligon) olarak gelir;
    'element' kaynak türüdür (node/way/relation). Sadece 'alan' geometrisiyle
    kesişenler tutulur.
    """
    fabrika = osmium.geom.WKBFactory()
    satirlar = []
    anahtarlar = list(HEDEF_ETIKETLERI)
    for nesne in osmium.FileProcessor(str(kesit)).with_areas().with_filter(osmium.filter.KeyFilter(*anahtarlar)):
        if nesne.is_node():
            element, osm_id = "node", nesne.id
        elif nesne.is_area():
            element, osm_id = ("way" if nesne.from_way() else "relation"), nesne.orig_id()
        else:
            continue  # yol/ilişkinin kendisi değil, kurulan alanı kullanılır
        etiket = _etiketler(nesne)
        if not hedef_etiketi_mi(etiket):
            continue
        try:
            geometri = Point(nesne.location.lon, nesne.location.lat) if element == "node" else _geometri(fabrika, nesne)
        except (osmium.InvalidLocationError, RuntimeError):
            continue
        satirlar.append({**etiket, "element": element, "id": osm_id, "geometry": geometri})

    gdf = gpd.GeoDataFrame(satirlar, geometry="geometry", crs="EPSG:4326") if satirlar else \
        gpd.GeoDataFrame({"element": [], "id": []}, geometry=[], crs="EPSG:4326")
    gdf = gdf[gdf.intersects(alan)]
    return ham_ozellikleri_hazirla(gdf)


# --------------------------------------------------------------------------- 5. raylı hatlar

def hat_iliskilerini_oku(kesit: Path) -> dict:
    """Raylı hat ve durak alanı ilişkilerini Overpass JSON biçiminde döndürür.

    {"elements": [...]} içinde: ref'i HAT_REF_DESENI'ne uyan route ilişkileri
    (subway/light_rail/train) ve tüm stop_area ilişkileri üyeleriyle, ayrıca
    hatların düğüm üyelerinin koordinatları. hedefler.istasyon_hatlari bu
    biçimi bekler.
    """
    tur = {"n": "node", "w": "way", "r": "relation"}
    desen = re.compile(HAT_REF_DESENI)
    iliskiler, hat_dugumleri = [], set()
    for r in osmium.FileProcessor(str(kesit), osmium.osm.RELATION):
        etiket = _etiketler(r)
        hat = etiket.get("route") in HAT_TURLERI and desen.match(etiket.get("ref", ""))
        if not (hat or etiket.get("public_transport") == "stop_area"):
            continue
        uyeler = [{"type": tur[m.type], "ref": m.ref, "role": m.role} for m in r.members]
        iliskiler.append({"type": "relation", "id": r.id, "tags": etiket, "members": uyeler})
        if hat:
            hat_dugumleri |= {m["ref"] for m in uyeler if m["type"] == "node"}

    dugumler = []
    if hat_dugumleri:
        filtre = osmium.filter.IdFilter(hat_dugumleri)
        for n in osmium.FileProcessor(str(kesit), osmium.osm.NODE).with_filter(filtre):
            if n.location.valid():
                dugumler.append({"type": "node", "id": n.id, "lat": n.location.lat, "lon": n.location.lon})
    return {"elements": iliskiler + dugumler}
