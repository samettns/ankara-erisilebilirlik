"""src/pbf.py için ağ bağlantısı gerektirmeyen birim testleri."""

import hashlib
import json

import pytest

from src import pbf

ICERIK = b"sahte pbf verisi" * 1000


class SahteYanit:
    """requests.get yanıtının testte gereken kısmı."""

    def __init__(self, govde: bytes, durum: int = 200):
        self.govde, self.status_code = govde, durum
        self.headers = {"content-length": str(len(govde))}
        self.text = govde.decode("latin-1")

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def iter_content(self, boyut):
        for i in range(0, len(self.govde), boyut):
            yield self.govde[i:i + boyut]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sahte_istek(md5: str, istekler: list):
    def istek(url, **kwargs):
        istekler.append(url)
        if url.endswith(".md5"):
            return SahteYanit(f"{md5}  turkey-latest.osm.pbf\n".encode())
        return SahteYanit(ICERIK)
    return istek


def test_md5_hesapla(tmp_path):
    yol = tmp_path / "x.bin"
    yol.write_bytes(ICERIK)
    assert pbf.md5_hesapla(yol) == hashlib.md5(ICERIK).hexdigest()


def test_pbf_indir_dogrular_ve_tarihle_adlandirir(tmp_path, monkeypatch):
    monkeypatch.setattr(pbf, "pbf_veri_tarihi", lambda yol: "2026-11-04T21:21:03Z")
    istekler = []
    sonuc = pbf.pbf_indir("https://ornek/turkey-latest.osm.pbf", tmp_path,
                          istek=_sahte_istek(hashlib.md5(ICERIK).hexdigest(), istekler))

    assert sonuc == tmp_path / "turkey-2026-11-04.osm.pbf"
    assert sonuc.read_bytes() == ICERIK
    assert not list(tmp_path.glob("*.part"))
    assert len(istekler) == 2

    # dosya varken tekrar indirilmez
    ikinci = pbf.pbf_indir("https://ornek/turkey-latest.osm.pbf", tmp_path, istek=_sahte_istek("x", istekler))
    assert ikinci == sonuc and len(istekler) == 2


def test_pbf_indir_md5_tutmazsa_siler_ve_hata_verir(tmp_path):
    with pytest.raises(ValueError, match="MD5"):
        pbf.pbf_indir("https://ornek/t.osm.pbf", tmp_path, istek=_sahte_istek("0" * 32, []))
    assert not list(tmp_path.iterdir())


def test_pbf_indir_tam_part_dosyasini_yeniden_indirmez(tmp_path, monkeypatch):
    monkeypatch.setattr(pbf, "pbf_veri_tarihi", lambda yol: "2026-11-04T21:21:03Z")
    (tmp_path / "turkey-latest.osm.pbf.part").write_bytes(ICERIK)
    istekler = []
    sonuc = pbf.pbf_indir("https://ornek/turkey-latest.osm.pbf", tmp_path,
                          istek=_sahte_istek(hashlib.md5(ICERIK).hexdigest(), istekler))
    assert istekler == ["https://ornek/turkey-latest.osm.pbf.md5"]  # sadece md5 soruldu
    assert sonuc.name == "turkey-2026-11-04.osm.pbf"


def test_mevcut_pbf_en_yeniyi_secer(tmp_path):
    assert pbf.mevcut_pbf(tmp_path) is None
    for ad in ["turkey-2026-01-01.osm.pbf", "turkey-2026-11-04.osm.pbf", "turkey-latest.osm.pbf.part"]:
        (tmp_path / ad).write_bytes(b"x")
    assert pbf.mevcut_pbf(tmp_path).name == "turkey-2026-11-04.osm.pbf"


@pytest.mark.parametrize("ad", ["t.osm.pbf", "turkey-latest.osm.pbf.part"])
def test_pbf_veri_tarihi_basliktan_okur(tmp_path, ad):
    import osmium
    yazilan = tmp_path / "t.osm.pbf"
    baslik = osmium.io.Header()
    baslik.set("osmosis_replication_timestamp", "2026-11-04T21:21:03Z")
    yazici = osmium.SimpleWriter(str(yazilan), header=baslik)
    yazici.add_node(osmium.osm.mutable.Node(id=1, location=(32.8, 39.9)))
    yazici.close()
    yol = yazilan.rename(tmp_path / ad) if ad != yazilan.name else yazilan
    assert pbf.pbf_veri_tarihi(yol) == "2026-11-04T21:21:03Z"


def test_veri_surumu_guncelle(tmp_path, monkeypatch):
    monkeypatch.setattr(pbf, "pbf_veri_tarihi", lambda yol: "2026-11-04T21:21:03Z")
    dosya = tmp_path / "turkey-2026-11-04.osm.pbf"
    dosya.write_bytes(ICERIK)
    surum = tmp_path / "veri_surumu.json"

    bilgi = pbf.veri_surumu_guncelle(dosya, surum, url="https://ornek")
    kayit = json.loads(surum.read_text(encoding="utf-8"))
    assert kayit == bilgi
    assert kayit["osm_veri_tarihi"] == "2026-11-04T21:21:03Z"
    assert kayit["md5"] == hashlib.md5(ICERIK).hexdigest() and kayit["dosya"] == dosya.name

    # bölüm eklenir; yeni bir PBF için OSM kısmı yeniden yazılınca bölüm korunur
    pbf.veri_surumu_bolumu_yaz("nufus", {"yil": 2025}, surum)
    yeni = tmp_path / "turkey-2026-12-01.osm.pbf"
    yeni.write_bytes(ICERIK)
    pbf.veri_surumu_guncelle(yeni, surum, url="https://ornek")
    kayit = json.loads(surum.read_text(encoding="utf-8"))
    assert kayit["dosya"] == yeni.name and kayit["nufus"] == {"yil": 2025}

    # aynı dosya için tekrar yazılmaz
    dosya = yeni
    surum.write_text(json.dumps({"dosya": dosya.name, "el_ile": True}), encoding="utf-8")
    assert pbf.veri_surumu_guncelle(dosya, surum)["el_ile"] is True
