"""İki sonuç klasörünü (önce/sonra) karşılaştırır: ağ, veri boşluğu ve nüfus ağırlıklı sonuçlar.

Çalıştırma (proje kökünden; iki klasör de zorunlu):
    python -m src.karsilastir ONCEKI_DIZIN SONRAKI_DIZIN
    # örnek: python -m src.karsilastir data/sonuclar_onceki data/sonuclar

Önceki klasör, yöntemi değiştirmeden önce data/sonuclar'ın bir kopyasıdır.
"""

import json
import sys
from pathlib import Path

import pandas as pd

try:
    from src.ayarlar import SONUC_DIZINI
except ModuleNotFoundError:
    from ayarlar import SONUC_DIZINI

TOPLAM = "7 ilçe toplamı"
GEREKEN_DOSYALAR = ["rapor.json", "nufus_rapor.json", "altigenler.parquet", "nufus_ozet.csv"]
KULLANIM = """Kullanım: python -m src.karsilastir ONCEKI_DIZIN SONRAKI_DIZIN

İki sonuç klasörü gerekir: önceki (karşılaştırma tabanı) ve sonraki (yeni hesap).
Örnek akış:
  1. Yöntemi değiştirmeden önce data/sonuclar klasörünü kopyala (ör. data/sonuclar_onceki)
  2. Hesapları yeniden çalıştır (python -m src.mesafe, src.altigen, src.nufus)
  3. python -m src.karsilastir data/sonuclar_onceki data/sonuclar
Her klasörde şu dosyalar olmalı: """ + ", ".join(GEREKEN_DOSYALAR)


def _json(yol: Path) -> dict:
    return json.loads(yol.read_text(encoding="utf-8"))


def ozet_metrikleri(dizin: Path) -> dict:
    """Bir sonuç klasöründen ağ ve veri boşluğu metriklerini toplar."""
    graf = _json(dizin / "rapor.json")["graf"]
    bosluk = _json(dizin / "nufus_rapor.json")["bosluk"]
    altigen = pd.read_parquet(dizin / "altigenler.parquet")
    ozet = pd.read_csv(dizin / "nufus_ozet.csv")
    toplam = ozet[(ozet["bolge"] == TOPLAM)].iloc[0]
    return {
        "ağ: parça sayısı": graf["parca_sayisi"],
        "ağ: ana parça düğümü": graf["ana_parca_dugum"],
        "ağ: dışarıda kalan düğüm (7 ilçe içi)": graf["disarida_dugum_ilce_ici"],
        "ağ: konut sitesi özel kenarı": graf.get("ozel_kenar", 0),
        "altıgen: ağa bağlı olmayan": int((~altigen["bagli"].astype(bool)).sum()),
        "nüfus: düğümsüz altıgende": bosluk["dugumsuz"]["nufus"],
        "nüfus: ağa bağlı olmayan altıgende": bosluk["bagsiz"]["nufus"],
        "nüfus: süresi bilinen (kapsanan)": int(toplam["kapsanan_nufus"]),
    }


def metrik_tablosu(onceki: dict, sonraki: dict) -> pd.DataFrame:
    """İki metrik sözlüğünü önce | sonra | fark tablosuna çevirir."""
    return pd.DataFrame([{"metrik": k, "önce": onceki.get(k), "sonra": sonraki.get(k),
                          "fark": (sonraki.get(k) or 0) - (onceki.get(k) or 0)} for k in sonraki])


def sonuc_tablosu(onceki: pd.DataFrame, sonraki: pd.DataFrame) -> pd.DataFrame:
    """nufus_ozet.csv tablolarını kategori-bölge bazında yan yana koyar.

    Ağırlıklı medyan (dk), 15 dk dışı kişi ve oran (kapsanan nüfusa göre) için
    önce/sonra ve fark sütunları; oran farkı yüzde puan olarak.
    """
    anahtar = ["kategori", "bolge"]
    sutun = ["kapsanan_nufus", "agirlikli_medyan_dk", "esik_disi_kisi", "esik_disi_oran"]
    t = onceki[anahtar + sutun].merge(sonraki[anahtar + sutun], on=anahtar, suffixes=("_once", "_sonra"))
    t["kapsanan_fark"] = t["kapsanan_nufus_sonra"] - t["kapsanan_nufus_once"]
    t["medyan_fark_dk"] = (t["agirlikli_medyan_dk_sonra"] - t["agirlikli_medyan_dk_once"]).round(2)
    t["esik_disi_kisi_fark"] = t["esik_disi_kisi_sonra"] - t["esik_disi_kisi_once"]
    t["esik_disi_oran_fark_puan"] = (100 * (t["esik_disi_oran_sonra"] - t["esik_disi_oran_once"])).round(2)
    return t


def eksik_dosyalar(dizin: Path) -> list[str]:
    """Bir sonuç klasöründe karşılaştırma için eksik olan dosyaların adları (klasör yoksa hepsi)."""
    return [d for d in GEREKEN_DOSYALAR if not (Path(dizin) / d).is_file()]


def cli(argv: list[str]) -> int:
    """Komut satırı girişi: argümanları ve klasörleri denetler, main'i çalıştırır; çıkış kodu döner.

    Argüman sayısı yanlışsa kullanım mesajı, klasör/dosya eksikse hangisinin eksik
    olduğu stderr'e yazılır ve 2 döner.
    """
    if len(argv) != 2:
        sayi = f"{len(argv)} verildi." if argv else "hiç verilmedi."
        print(f"Hata: iki klasör bekleniyordu, {sayi}\n\n{KULLANIM}", file=sys.stderr)
        return 2
    onceki, sonraki = Path(argv[0]), Path(argv[1])
    hatalar = [f"{ad} klasörü ({dizin}): " + ("yok" if not dizin.is_dir() else "eksik dosya: " + ", ".join(eksik))
               for ad, dizin in [("önceki", onceki), ("sonraki", sonraki)]
               if (eksik := eksik_dosyalar(dizin))]
    if hatalar:
        satirlar = "\n  ".join(hatalar)
        print(f"Hata:\n  {satirlar}\n\n{KULLANIM}", file=sys.stderr)
        return 2
    main(onceki, sonraki)
    return 0


def main(onceki: Path, sonraki: Path = SONUC_DIZINI) -> None:
    """Karşılaştırmayı basar ve sonraki klasöre once_sonra_*.csv olarak yazar."""
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 20)
    metrik = metrik_tablosu(ozet_metrikleri(onceki), ozet_metrikleri(sonraki))
    print("AĞ VE VERİ BOŞLUĞU:")
    print(metrik.to_string(index=False))

    sonuc = sonuc_tablosu(pd.read_csv(onceki / "nufus_ozet.csv"), pd.read_csv(sonraki / "nufus_ozet.csv"))
    goster = ["kategori", "bolge", "agirlikli_medyan_dk_once", "agirlikli_medyan_dk_sonra", "medyan_fark_dk",
              "esik_disi_kisi_once", "esik_disi_kisi_sonra", "esik_disi_kisi_fark", "esik_disi_oran_fark_puan",
              "kapsanan_fark"]
    print("\nNÜFUS AĞIRLIKLI SONUÇLAR (7 ilçe toplamı):")
    print(sonuc[sonuc["bolge"] == TOPLAM][goster].drop(columns="bolge").to_string(index=False))
    print("\nİLÇE BAZINDA (15 dk dışı kişi farkı):")
    pivot = sonuc[sonuc["bolge"] != TOPLAM].pivot(index="bolge", columns="kategori", values="esik_disi_kisi_fark")
    print(pivot.to_string())

    metrik.to_csv(sonraki / "once_sonra_metrikler.csv", index=False, encoding="utf-8-sig")
    sonuc.to_csv(sonraki / "once_sonra_sonuclar.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
