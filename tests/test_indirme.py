"""src/indirme.py için ağ bağlantısı gerektirmeyen birim testleri."""

import pytest
import requests

from src import indirme


def _http_hatasi(kod):
    yanit = requests.Response()
    yanit.status_code = kod
    return requests.HTTPError(f"{kod} hata", response=yanit)


def test_gecici_hata_mi():
    assert indirme.gecici_hata_mi(requests.ConnectionError())
    assert indirme.gecici_hata_mi(requests.Timeout())
    assert indirme.gecici_hata_mi(_http_hatasi(503))
    assert indirme.gecici_hata_mi(_http_hatasi(429))
    assert not indirme.gecici_hata_mi(_http_hatasi(404))
    assert not indirme.gecici_hata_mi(ValueError("MD5 uyuşmuyor"))


def test_tekrar_dene_artan_bekleme_ile_basarir():
    beklemeler, sayac = [], {"n": 0}

    def iki_kez_bozuk():
        sayac["n"] += 1
        if sayac["n"] < 3:
            raise requests.ConnectionError()
        return "tamam"

    assert indirme.tekrar_dene(iki_kez_bozuk, deneme=4, ilk_bekleme=60, uyu=beklemeler.append) == "tamam"
    assert beklemeler == [60, 120]


def test_tekrar_dene_kalici_hatayi_hemen_yukseltir():
    beklemeler = []

    def bozuk():
        raise _http_hatasi(404)

    with pytest.raises(requests.HTTPError):
        indirme.tekrar_dene(bozuk, uyu=beklemeler.append)
    assert beklemeler == []


def test_tekrar_dene_denemeler_bitince_son_hatayi_yukseltir():
    beklemeler = []

    def hep_bozuk():
        raise requests.Timeout()

    with pytest.raises(requests.Timeout):
        indirme.tekrar_dene(hep_bozuk, deneme=3, ilk_bekleme=1, uyu=beklemeler.append)
    assert beklemeler == [1, 2]


def test_tekrar_dene_argumanlari_fonksiyona_gecirir():
    assert indirme.tekrar_dene(lambda a, b=0: a + b, 2, b=3, uyu=lambda s: None) == 5
