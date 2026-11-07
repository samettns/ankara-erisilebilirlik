"""Geofabrik Türkiye PBF dosyasının indirilmesi, doğrulanması ve sürüm kaydı."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import osmium
import requests

try:
    from src.ayarlar import GEOFABRIK_URL, OSM_DIZINI, VERI_SURUMU_DOSYASI
    from src.indirme import tekrar_dene
except ModuleNotFoundError:
    from ayarlar import GEOFABRIK_URL, OSM_DIZINI, VERI_SURUMU_DOSYASI
    from indirme import tekrar_dene

ISTEMCI = {"User-Agent": "ankara-erisilebilirlik/0.1 (python-requests)"}
PARCA_BOYUTU = 1 << 20  # 1 MB


def md5_hesapla(yol: Path) -> str:
    """Dosyanın MD5 özetini (hex) parça parça okuyarak hesaplar."""
    ozet = hashlib.md5()
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(PARCA_BOYUTU), b""):
            ozet.update(parca)
    return ozet.hexdigest()


def pbf_veri_tarihi(yol: Path) -> str:
    """PBF başlığındaki OSM veri zaman damgasını (ISO 8601, UTC) döndürür.

    Geofabrik dosyalarında 'osmosis_replication_timestamp' bulunur; dosyadaki
    verinin hangi ana kadar güncel olduğunu gösterir.
    """
    # Biçim açıkça verilir: uzantı .part olduğunda pyosmium biçimi tanıyamaz
    okuyucu = osmium.io.Reader(osmium.io.File(str(yol), "pbf"), osmium.osm.osm_entity_bits.NOTHING)
    try:
        return okuyucu.header().get("osmosis_replication_timestamp")
    finally:
        okuyucu.close()


def _beklenen_md5(url: str, istek: Callable = requests.get) -> str:
    """Geofabrik'in .md5 dosyasından beklenen özeti okur."""
    yanit = istek(url + ".md5", headers=ISTEMCI, timeout=60)
    yanit.raise_for_status()
    eslesme = re.match(r"\s*([0-9a-f]{32})", yanit.text)
    if not eslesme:
        raise ValueError(f"MD5 dosyası okunamadı: {yanit.text[:80]!r}")
    return eslesme.group(1)


def akisla_indir(url: str, hedef: Path, istek: Callable = requests.get) -> tuple[int, int]:
    """URL'yi parça parça hedef dosyaya indirir, ilerlemeyi yazdırır.

    (inen_bayt, beklenen_bayt) döndürür; sunucu boyut bildirmezse beklenen 0'dır.
    """
    with istek(url, headers=ISTEMCI, stream=True, timeout=(30, 300)) as yanit:
        yanit.raise_for_status()
        toplam = int(yanit.headers.get("content-length", 0))
        inen, son_yuzde = 0, -10
        with open(hedef, "wb") as f:
            for parca in yanit.iter_content(PARCA_BOYUTU):
                f.write(parca)
                inen += len(parca)
                yuzde = int(100 * inen / toplam) if toplam else 0
                if yuzde >= son_yuzde + 10:
                    print(f"    {inen / 2**20:,.0f} / {toplam / 2**20:,.0f} MB (%{yuzde})", flush=True)
                    son_yuzde = yuzde
    return inen, toplam


def mevcut_pbf(dizin: Path = OSM_DIZINI) -> Path | None:
    """Dizindeki tarihli Türkiye PBF dosyalarından en yenisini döndürür, yoksa None."""
    adaylar = sorted(dizin.glob("turkey-????-??-??.osm.pbf"))
    return adaylar[-1] if adaylar else None


def pbf_indir(
    url: str = GEOFABRIK_URL,
    dizin: Path = OSM_DIZINI,
    istek: Callable = requests.get,
) -> Path:
    """Türkiye PBF dosyasını indirir, MD5 ile doğrular ve veri tarihiyle adlandırır.

    Dizinde tarihli bir dosya (turkey-YYYY-AA-GG.osm.pbf) varsa indirme
    yapılmaz, o dosya döner. Yeni indirme önce .part dosyasına yapılır;
    MD5 tutmazsa dosya silinir ve hata verilir. Önceki çalıştırmadan kalan
    ve MD5'i tutan bir .part dosyası varsa tekrar indirilmeden kullanılır.
    """
    var_olan = mevcut_pbf(dizin)
    if var_olan:
        return var_olan

    dizin.mkdir(parents=True, exist_ok=True)
    gecici = dizin / "turkey-latest.osm.pbf.part"
    beklenen = tekrar_dene(_beklenen_md5, url, istek=istek)
    if gecici.exists() and md5_hesapla(gecici) == beklenen:
        print("    önceki indirme tam, tekrar indirilmiyor", flush=True)
    else:
        tekrar_dene(akisla_indir, url, gecici, istek=istek)

    gercek = md5_hesapla(gecici)
    if gercek != beklenen:
        gecici.unlink()
        raise ValueError(f"MD5 uyuşmuyor: beklenen {beklenen}, inen {gercek}")

    hedef = dizin / f"turkey-{pbf_veri_tarihi(gecici)[:10]}.osm.pbf"
    gecici.replace(hedef)
    return hedef


def veri_surumu_guncelle(pbf: Path, yol: Path = VERI_SURUMU_DOSYASI, url: str = GEOFABRIK_URL) -> dict:
    """Kullanılan PBF'in kaynağını ve tarihini veri_surumu.json'a yazar, bilgiyi döndürür.

    Dosya zaten bu PBF'i anlatıyorsa (aynı dosya adı) dokunulmaz. İndirme
    tarihi olarak PBF dosyasının diske yazıldığı an (UTC) kullanılır.
    """
    mevcut = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
    if mevcut.get("dosya") == pbf.name:
        return mevcut
    indirme = datetime.fromtimestamp(pbf.stat().st_mtime, tz=timezone.utc)
    bilgi = {
        "kaynak": url,
        "dosya": pbf.name,
        "md5": md5_hesapla(pbf),
        "boyut_mb": round(pbf.stat().st_size / 2**20, 1),
        "osm_veri_tarihi": pbf_veri_tarihi(pbf),
        "indirme_tarihi": indirme.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ilce_sinirlari": "Aynı PBF: boundary=administrative, admin_level=6 ilişkileri",
        "lisans": "© OpenStreetMap katkıcıları, ODbL 1.0",
    }
    # OSM dışındaki bölümler (nüfus vb.) korunur
    bilgi = {**{k: v for k, v in mevcut.items() if isinstance(v, dict)}, **bilgi}
    yol.write_text(json.dumps(bilgi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bilgi


def veri_surumu_bolumu_yaz(anahtar: str, bolum: dict, yol: Path = VERI_SURUMU_DOSYASI) -> None:
    """veri_surumu.json'daki bir bölümü (ör. 'nufus') yazar; diğer içerik korunur."""
    mevcut = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else {}
    mevcut[anahtar] = bolum
    yol.write_text(json.dumps(mevcut, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
