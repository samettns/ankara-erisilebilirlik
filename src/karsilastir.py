"""İki sonuç klasörünü (önce/sonra) karşılaştırır: ağ, veri boşluğu ve nüfus ağırlıklı sonuçlar.

Çalıştırma (proje kökünden):
    python -m src.karsilastir                      # data/sonuclar_onceki -> data/sonuclar
    python -m src.karsilastir ONCEKI_DIZIN SONRAKI_DIZIN
"""

import json
import sys
from pathlib import Path

import pandas as pd

try:
    from src.ayarlar import SONUC_DIZINI, VERI_DIZINI
except ModuleNotFoundError:
    from ayarlar import SONUC_DIZINI, VERI_DIZINI

ONCEKI_DIZIN = VERI_DIZINI / "sonuclar_onceki"
TOPLAM = "7 ilçe toplamı"


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


def main(onceki: Path = ONCEKI_DIZIN, sonraki: Path = SONUC_DIZINI) -> None:
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
    if len(sys.argv) == 3:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
    else:
        main()
