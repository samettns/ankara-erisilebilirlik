"""Merkez ilçelerin yürüme ağını ve hedeflerini yerel OSM PBF dosyasından üretir.

İnternete sadece Geofabrik Türkiye PBF'i bir kez indirilirken çıkılır.
Sonrası tamamen yereldir:

1. PBF indir/doğrula, veri_surumu.json'u yaz
2. Türkiye'den Ankara kesitini çıkar (tek seferlik, birkaç dakika)
3. İlçe sınırlarını kesitteki idari sınır ilişkilerinden kur
4. İlçe başına 2 km genişletilmiş alanın yürüme ağını kur, birleştir
5. Hedefleri ve raylı hat ilişkilerini oku, hedef katmanlarını yaz

Her ara çıktı diske yazılır; kesitten yeni olan çıktı tekrar üretilmez.
Bir ilçe hata verirse diğerlerine geçilir, eksikler sonda raporlanır.

Çalıştırma (proje kökünden):
    python -m src.veri_cek
"""

import json
import time
from pathlib import Path

import geopandas as gpd
import osmnx as ox

try:
    from src import ag, hedefler, osm_yerel
    from src.ayarlar import (AG_DIZINI, BIRLESIK_AG_DOSYASI, GECICI_DIZIN, GENISLETME_M, HAM_DIZINI,
                             HEDEF_DIZINI, ILCELER, KESIT_KUTUSU, OSM_DIZINI, SINIR_DOSYASI)
    from src.pbf import pbf_indir, veri_surumu_guncelle
except ModuleNotFoundError:
    import ag
    import hedefler
    import osm_yerel
    from ayarlar import (AG_DIZINI, BIRLESIK_AG_DOSYASI, GECICI_DIZIN, GENISLETME_M, HAM_DIZINI,
                         HEDEF_DIZINI, ILCELER, KESIT_KUTUSU, OSM_DIZINI, SINIR_DOSYASI)
    from pbf import pbf_indir, veri_surumu_guncelle


def dosya_adi(ilce: str) -> str:
    """İlçe adını dosya adında kullanılabilecek ASCII küçük harfe çevirir."""
    tablo = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return ilce.translate(tablo).lower().replace(" ", "_")


def guncel_mi(cikti: Path, girdiler: list[Path]) -> bool:
    """Çıktı dosyası var ve tüm girdilerden daha yeniyse True döner."""
    if not cikti.exists():
        return False
    return all(cikti.stat().st_mtime >= g.stat().st_mtime for g in girdiler)


def kutuda_mi(geometri, kutu: tuple[float, float, float, float]) -> bool:
    """Geometrinin sınır kutusu (batı, güney, doğu, kuzey) kutunun tamamen içindeyse True."""
    bati, guney, dogu, kuzey = geometri.bounds
    return bati >= kutu[0] and guney >= kutu[1] and dogu <= kutu[2] and kuzey <= kutu[3]


def sinirlari_hazirla(kesit: Path, yol: Path = SINIR_DOSYASI) -> gpd.GeoDataFrame:
    """İlçe sınırlarını dosyadan okur; kesitten eski veya ilçe listesi farklıysa yeniden kurar."""
    if guncel_mi(yol, [kesit]):
        sinirlar = gpd.read_file(yol)
        if list(sinirlar["ilce"]) == ILCELER:
            return sinirlar
    sinirlar = osm_yerel.ilce_sinirlarini_oku(kesit)
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.unlink(missing_ok=True)
    sinirlar.to_file(yol, driver="GPKG")
    return sinirlar


def ilce_aglarini_kur(kesit: Path, genis: dict, ag_dosyalari: dict, gecici_dizin: Path = GECICI_DIZIN) -> list[str]:
    """Güncel olmayan ilçe ağlarını kurar; hata veren ilçelerin adlarını döndürür.

    Yürüme yolları kesitten bir kez toplanır; her ilçe için genişletilmiş
    poligona değen yollar geçici bir .osm XML'e yazılır, OSMnx ile graf
    kurulur ve XML her durumda silinir.
    """
    yapilacak = [i for i in genis if not guncel_mi(ag_dosyalari[i], [kesit])]
    for ilce in genis:
        if ilce not in yapilacak:
            print(f"  [hazır] {ilce}", flush=True)
    if not yapilacak:
        return []

    baslangic = time.time()
    yollar = osm_yerel.yuruyus_yollarini_topla(kesit)
    print(f"  kesitte {len(yollar):,} yürünebilir yol ({time.time() - baslangic:.0f} sn)", flush=True)

    eksikler = []
    for ilce in yapilacak:
        xml = gecici_dizin / f"{dosya_adi(ilce)}_walk.osm"
        baslangic = time.time()
        try:
            siralar = osm_yerel.poligona_degen_yollar(yollar, genis[ilce])
            osm_yerel.osm_xml_yaz(yollar, siralar, xml)
            G = ag.ilce_agi_kur(xml, ag_dosyalari[ilce])
            print(f"  [tamam] {ilce}: {len(siralar):,} yol -> {G.number_of_nodes():,} düğüm, "
                  f"{G.number_of_edges():,} kenar ({time.time() - baslangic:.0f} sn)", flush=True)
            del G
        except Exception as hata:
            print(f"  [HATA] {ilce}: {type(hata).__name__}: {str(hata)[:200]}", flush=True)
            eksikler.append(ilce)
        finally:
            xml.unlink(missing_ok=True)
    if gecici_dizin.exists() and not any(gecici_dizin.iterdir()):
        gecici_dizin.rmdir()
    return eksikler


def birlesik_agi_uret(ag_dosyalari: list[Path], yol: Path = BIRLESIK_AG_DOSYASI) -> None:
    """İlçe graflarını teker teker okuyup sadeleştirir, birleştirir ve kaydeder.

    Çıktı girdilerden yeniyse hiçbir şey yapmaz. Bellek için her ilçenin
    tam grafı sadeleştirildikten hemen sonra bırakılır.
    """
    if guncel_mi(yol, ag_dosyalari):
        print("  [hazır] birleşik ağ", flush=True)
        return
    sadeler = [ag.sadelestir(ox.load_graphml(d)) for d in ag_dosyalari]
    B = ag.birlestir(sadeler)
    del sadeler
    ag.sade_graf_kaydet(B, yol)
    print(f"  birleşik ağ: {B.number_of_nodes():,} düğüm, {B.number_of_edges():,} kenar", flush=True)


def ham_verileri_hazirla(kesit: Path, alan, dizin: Path = HAM_DIZINI) -> tuple[gpd.GeoDataFrame, dict]:
    """Ham hedef özelliklerini ve raylı hat ilişkilerini kesitten okur ya da diskten alır."""
    ham_yolu, hat_yolu = dizin / "ozellikler.parquet", dizin / "hatlar.json"
    dizin.mkdir(parents=True, exist_ok=True)
    if guncel_mi(ham_yolu, [kesit]):
        ham = gpd.read_parquet(ham_yolu)
    else:
        ham = osm_yerel.hedef_ozelliklerini_oku(kesit, alan)
        ham.to_parquet(ham_yolu)
    if guncel_mi(hat_yolu, [kesit]):
        hatlar = json.loads(hat_yolu.read_text(encoding="utf-8"))
    else:
        hatlar = osm_yerel.hat_iliskilerini_oku(kesit)
        hat_yolu.write_text(json.dumps(hatlar, ensure_ascii=False), encoding="utf-8")
    return ham, hatlar


def _katman_yaz(yol: Path, katmanlar: dict[str, gpd.GeoDataFrame]) -> None:
    """GeoPackage dosyasını silip verilen katmanlarla yeniden yazar."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.unlink(missing_ok=True)
    for ad, gdf in katmanlar.items():
        gdf.to_file(yol, layer=ad, driver="GPKG")


def hedefleri_uret(ham: gpd.GeoDataFrame, hat_json: dict, sinirlar: gpd.GeoDataFrame,
                   dizin: Path = HEDEF_DIZINI) -> dict[str, int]:
    """Ham özelliklerden dört hedef katmanını yazar; özet sayıları döndürür."""
    eczaneler = hedefler.ilce_ekle(hedefler.eczaneleri_ayikla(ham), sinirlar)
    hastaneler, hastane_kaynak = hedefler.hastaneleri_ayikla(ham)
    hastaneler = hedefler.ilce_ekle(hastaneler, sinirlar)
    birinci = hedefler.ilce_ekle(hedefler.birinci_basamak_ayikla(ham), sinirlar)
    istasyonlar, rayli_kaynak = hedefler.rayli_ayikla(ham, hat_json)
    istasyonlar = hedefler.ilce_ekle(istasyonlar, sinirlar)

    _katman_yaz(dizin / "eczaneler.gpkg", {"tesisler": eczaneler})
    _katman_yaz(dizin / "hastaneler.gpkg", {"tesisler": hastaneler, "kaynaklar": hastane_kaynak})
    _katman_yaz(dizin / "birinci_basamak.gpkg", {"tesisler": birinci})
    _katman_yaz(dizin / "rayli.gpkg", {"istasyonlar": istasyonlar, "kaynaklar": rayli_kaynak})
    ic = lambda gdf: int(gdf["ilce"].notna().sum())  # noqa: E731  7 ilçe içindekiler
    return {
        "eczane (7 ilçe içi / toplam)": f"{ic(eczaneler)} / {len(eczaneler)}",
        "hastane (7 ilçe içi / toplam)": f"{ic(hastaneler)} / {len(hastaneler)}",
        "  kamu (operator:type=public/government)": int(hastaneler["operator:type"].isin(["public", "government"]).sum()),
        "  operator:type boş": int(hastaneler["operator:type"].isna().sum()),
        "hastane kaynak noktası": len(hastane_kaynak),
        "birinci basamak (7 ilçe içi / toplam)": f"{ic(birinci)} / {len(birinci)}",
        "  isminden ASM/sağlık ocağı": int(birinci["asm"].sum()),
        "metro istasyonu": int(istasyonlar["metro"].sum()),
        "Ankaray istasyonu": int(istasyonlar["ankaray"].sum()),
        "Başkentray istasyonu": int(istasyonlar["baskentray"].sum()),
        "raylı kaynak noktası (giriş + girişsiz istasyon)": len(rayli_kaynak),
    }


def main() -> None:
    """Tüm akışı çalıştırır ve eksik kalanları raporlar."""
    toplam = time.time()
    print("1/5 Türkiye PBF", flush=True)
    pbf = pbf_indir()
    surum = veri_surumu_guncelle(pbf)
    print(f"  {pbf.name}, OSM veri tarihi {surum['osm_veri_tarihi']}", flush=True)

    print("2/5 Ankara kesiti", flush=True)
    kesit = OSM_DIZINI / pbf.name.replace("turkey-", "ankara-")
    baslangic = time.time()
    kesit_vardi = kesit.exists()
    osm_yerel.ankara_kesiti_cikar(pbf, kesit)
    print(f"  {'[hazır]' if kesit_vardi else f'çıkarıldı ({time.time() - baslangic:.0f} sn)'} "
          f"{kesit.name}, {kesit.stat().st_size / 2**20:.1f} MB", flush=True)

    print("3/5 İlçe sınırları", flush=True)
    sinirlar = sinirlari_hazirla(kesit)
    genis = {i: ag.genislet(g, GENISLETME_M) for i, g in zip(sinirlar["ilce"], sinirlar.geometry)}
    alan = gpd.GeoSeries(list(genis.values()), crs="EPSG:4326").union_all()
    if not kutuda_mi(alan, KESIT_KUTUSU):
        raise ValueError(f"Genişletilmiş alan {alan.bounds} kesit kutusundan taşıyor; KESIT_KUTUSU'nu büyütün.")
    km2 = sinirlar.to_crs(sinirlar.estimate_utm_crs()).area / 1e6
    for ilce, a in zip(sinirlar["ilce"], km2):
        print(f"  {ilce}: {a:,.0f} km²", flush=True)

    print("4/5 Yürüme ağları", flush=True)
    ag_dosyalari = {i: AG_DIZINI / f"{dosya_adi(i)}_walk.graphml" for i in ILCELER}
    eksik_ag = ilce_aglarini_kur(kesit, genis, ag_dosyalari)
    if eksik_ag:
        print(f"  birleşik ağ üretilmedi, eksik: {', '.join(eksik_ag)}", flush=True)
    else:
        birlesik_agi_uret(list(ag_dosyalari.values()))

    print("5/5 Hedefler", flush=True)
    eksik_hedef = False
    try:
        ham, hatlar = ham_verileri_hazirla(kesit, alan)
        for ad, sayi in hedefleri_uret(ham, hatlar, sinirlar).items():
            print(f"  {ad}: {sayi}", flush=True)
    except Exception as hata:
        print(f"  [HATA] hedefler: {type(hata).__name__}: {str(hata)[:200]}", flush=True)
        eksik_hedef = True

    eksikler = [f"{i} ağı" for i in eksik_ag] + (["hedefler"] if eksik_hedef else [])
    print(f"\nSONUÇ ({(time.time() - toplam) / 60:.1f} dk):",
          "hepsi tamam" if not eksikler else f"eksik kalanlar -> {', '.join(eksikler)}", flush=True)


if __name__ == "__main__":
    main()
