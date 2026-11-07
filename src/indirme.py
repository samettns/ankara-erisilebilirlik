"""Sunucuyu zorlamadan indirme için artan beklemeli tekrar deneme."""

import time
from typing import Callable

import requests

try:
    from src.ayarlar import ILK_BEKLEME_SN, TEKRAR_DENEME_SAYISI
except ModuleNotFoundError:
    from ayarlar import ILK_BEKLEME_SN, TEKRAR_DENEME_SAYISI


def gecici_hata_mi(hata: Exception) -> bool:
    """Hatanın sunucu yoğunluğu/bağlantı kaynaklı (tekrar denenebilir) olup olmadığını söyler.

    Bağlantı/zaman aşımı hataları ile 429, 500, 502, 503, 504 yanıtları geçicidir.
    404 veya veri hataları geçici değildir, tekrar denenmez.
    """
    if isinstance(hata, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(hata, requests.HTTPError) and hata.response is not None:
        return hata.response.status_code in (429, 500, 502, 503, 504)
    metin = str(hata)
    return any(kod in metin for kod in ("429", "502", "503", "504", "timed out"))


def tekrar_dene(
    fonk: Callable,
    *args,
    deneme: int = TEKRAR_DENEME_SAYISI,
    ilk_bekleme: float = ILK_BEKLEME_SN,
    uyu: Callable[[float], None] = time.sleep,
    **kwargs,
):
    """fonk'u çağırır; geçici hatada artan bekleme ile (ilk, 2x, 4x ...) tekrar dener.

    Geçici olmayan hata hemen yükseltilir. Tüm denemeler biterse son hata
    yükseltilir.
    """
    bekleme = ilk_bekleme
    for sira in range(1, deneme + 1):
        try:
            return fonk(*args, **kwargs)
        except Exception as hata:
            if not gecici_hata_mi(hata) or sira == deneme:
                raise
            print(f"    sunucu meşgul ({type(hata).__name__}), {bekleme:.0f} sn sonra tekrar ({sira}/{deneme - 1})", flush=True)
            uyu(bekleme)
            bekleme *= 2
