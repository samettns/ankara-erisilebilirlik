"""src/karsilastir.py için testler."""

import json

import h3
import pandas as pd

from src import karsilastir


def test_metrik_tablosu_fark():
    t = karsilastir.metrik_tablosu({"a": 10, "b": 5}, {"a": 7, "b": 5}).set_index("metrik")
    assert t.loc["a", "fark"] == -3 and t.loc["b", "fark"] == 0
    assert list(t.columns) == ["önce", "sonra", "fark"]


def _ozet(medyan, disarida, oran, kapsanan):
    return pd.DataFrame({"kategori": ["eczane"], "bolge": ["7 ilçe toplamı"], "nufus": [1000],
                         "kapsanan_nufus": [kapsanan], "kapsanan_oran": [kapsanan / 1000],
                         "agirlikli_medyan_dk": [medyan], "esik_disi_kisi": [disarida], "esik_disi_oran": [oran]})


def test_sonuc_tablosu_farklar():
    t = karsilastir.sonuc_tablosu(_ozet(6.0, 200, 0.2, 950), _ozet(5.5, 180, 0.18, 980)).iloc[0]
    assert t["medyan_fark_dk"] == -0.5
    assert t["esik_disi_kisi_fark"] == -20
    assert t["esik_disi_oran_fark_puan"] == -2.0
    assert t["kapsanan_fark"] == 30


def test_ozet_metrikleri_dosyalardan(tmp_path):
    (tmp_path / "rapor.json").write_text(json.dumps({"graf": {
        "parca_sayisi": 5, "ana_parca_dugum": 100, "disarida_dugum_ilce_ici": 7, "ozel_kenar": 3}}), encoding="utf-8")
    (tmp_path / "nufus_rapor.json").write_text(json.dumps({"bosluk": {
        "dugumsuz": {"nufus": 40}, "bagsiz": {"nufus": 12}}}), encoding="utf-8")
    hucre = h3.latlng_to_cell(39.9, 32.8, 9)
    pd.DataFrame({"h3": [hucre, hucre[:-1] + "0"], "bagli": [True, False]}).to_parquet(tmp_path / "altigenler.parquet")
    _ozet(5.0, 100, 0.1, 948).to_csv(tmp_path / "nufus_ozet.csv", index=False)

    m = karsilastir.ozet_metrikleri(tmp_path)
    assert m["ağ: parça sayısı"] == 5 and m["ağ: konut sitesi özel kenarı"] == 3
    assert m["altıgen: ağa bağlı olmayan"] == 1
    assert m["nüfus: düğümsüz altıgende"] == 40 and m["nüfus: süresi bilinen (kapsanan)"] == 948


def test_cli_argumansiz_anlasilir_hata(capsys):
    assert karsilastir.cli([]) == 2
    hata = capsys.readouterr().err
    assert "hiç verilmedi" in hata and "Kullanım: python -m src.karsilastir ONCEKI_DIZIN SONRAKI_DIZIN" in hata


def test_cli_tek_arguman_hata(capsys):
    assert karsilastir.cli(["data/sonuclar"]) == 2
    assert "1 verildi" in capsys.readouterr().err


def test_cli_eksik_klasor_ve_dosya(tmp_path, capsys):
    (tmp_path / "sonra").mkdir()
    (tmp_path / "sonra" / "rapor.json").write_text("{}", encoding="utf-8")
    assert karsilastir.cli([str(tmp_path / "yok"), str(tmp_path / "sonra")]) == 2
    hata = capsys.readouterr().err
    assert "önceki klasörü" in hata and "yok" in hata
    sonraki = next(s for s in hata.splitlines() if "sonraki klasörü" in s)
    assert "eksik dosya: nufus_rapor.json, altigenler.parquet, nufus_ozet.csv" in sonraki
