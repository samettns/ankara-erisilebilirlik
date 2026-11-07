"""Altıgen yürüme sürelerinden tek dosyalık etkileşimli folium haritası.

Boyutu küçük tutmak için altıgen geometrisi tek bir GeoJSON'da bir kez
yazılır; her altıgen 5 kategorinin süre ve en yakın tesis bilgisini taşır.
Katman seçicideki kategori (radio) değişince küçük bir JS bloğu altıgenleri
yeniden boyar, bilgi kutusunu ve tesis noktalarını günceller.

Çalıştırma (proje kökünden):
    python -m src.harita
"""

import html
import json
import math
import folium
import geopandas as gpd
import h3
import numpy as np
import pandas as pd
import xyzservices
from branca.element import Element, MacroElement
from jinja2 import Template

try:
    from src.altigen import tesis_tablosu
    from src.ayarlar import HEDEF_DIZINI, SINIR_DOSYASI, SOLUK_NUFUS_ESIGI, SONUC_DIZINI
    from src.mesafe import KATEGORILER
except ModuleNotFoundError:
    from altigen import tesis_tablosu
    from ayarlar import HEDEF_DIZINI, SINIR_DOSYASI, SOLUK_NUFUS_ESIGI, SONUC_DIZINI
    from mesafe import KATEGORILER

ETIKETLER = {
    "eczane": "Eczane",
    "hastane": "Hastane",
    "birinci_basamak": "Birinci basamak",
    "rayli": "Metro / Ankaray",
    "baskentray": "Başkentray",
}
# Süre aralıkları (dk), üstten kapalı: (0,5] (5,10] (10,15] (15,30] (30,∞)
ARALIK_SINIRLARI = [5, 10, 15, 30]
ARALIK_ETIKETLERI = ["0–5 dk", "5–10 dk", "10–15 dk", "15–30 dk", "30+ dk"]
# Ters viridis (açık sarı -> koyu mor): renk körlüğüne uygun, açıklık tekdüze artar.
# validate_palette.js: açıklık sırası ve adım farkları GEÇER, tüm çiftlerde CVD ΔE >= 13,8;
# çok tonlu olduğundan "tek ton" kontrolü bilerek uygulanmaz. Sarının açık zemine
# kontrastı düşük (1,23:1): değerler bilgi kutusu ve lejantta ayrıca okunur.
RENKLER = ["#fde725", "#5ec962", "#21918c", "#3b528b", "#440154"]
BAGSIZ_RENK = "#c9c8c4"  # ağa bağlı olmayan altıgenler (nötr gri, opak çizilir)
TESIS_DOLGU = "#ffffff"  # beyaz dolgu + koyu halka: sarı ve mor altıgende de seçilir
TESIS_HALKA = "#2b2a27"
DIS_TESIS_OPAKLIK = 0.4  # 7 ilçe dışındaki (2 km kenar bölgesi) tesisler
YUZEY = "#fcfcfb"
ONDALIK = 5  # ~1 m
UST_SINIR_DK = 60  # bilgi kutusunda bunun üstü "60+ dk"
SOLUK_OPAKLIK = 0.25  # nüfusu SOLUK_NUFUS_ESIGI'nin altındaki altıgenler


def _bos_mu(dk) -> bool:
    """Değer None veya NaN ise True."""
    return dk is None or (isinstance(dk, float) and math.isnan(dk))


def aralik_sinifi(dk) -> int:
    """Süreyi renk aralığı sırasına (0-4) çevirir; boş/NaN için -1.

    Aralıklar üstten kapalıdır: 5,0 -> 0 (0-5), 15,0 -> 2 (10-15), 30,0 -> 3 (15-30).
    Böylece '≤15 dk' tanımıyla (mesafe.ozet) tutarlıdır.
    """
    if _bos_mu(dk):
        return -1
    return int(np.searchsorted(ARALIK_SINIRLARI, dk, side="left"))


def gosterilen_dakika(dk) -> int:
    """Bilgi kutusunda gösterilecek tam dakika; 1'in altı 0 ('<1'), boş -1.

    Yukarı yuvarlanır; böylece gösterilen sayı renk aralığının üst sınırını
    aşmaz (ör. 14,2 dk -> '15 dk', 10-15 aralığında).
    """
    if _bos_mu(dk):
        return -1
    if dk < 1:
        return 0
    return int(math.ceil(dk))


def sure_metni(dk) -> str:
    """Bilgi kutusundaki süre metni: '<1 dk', '7 dk', '60 dk', '60+ dk'; boşsa ''."""
    if _bos_mu(dk):
        return ""
    if dk > UST_SINIR_DK:
        return f"{UST_SINIR_DK}+ dk"
    d = gosterilen_dakika(dk)
    return "<1 dk" if d == 0 else f"{d} dk"


def nufus_metni(n) -> str:
    """Bilgi kutusundaki nüfus: 10'un altı '<10', üstü onluğa yuvarlanmış ve '~' ile ('~1.240').

    Yuvarlama, modelden gelen (WorldPop) tahminin sahte hassasiyetle okunmasını önler.
    """
    if _bos_mu(n) or n < SOLUK_NUFUS_ESIGI:
        return f"<{SOLUK_NUFUS_ESIGI}"
    return "~" + f"{int(round(n, -1)):,}".replace(",", ".")


def soluk_mu(n) -> bool:
    """Altıgen nüfusu eşiğin altındaysa (veya bilinmiyorsa) haritada soluk çizilir."""
    return _bos_mu(n) or n < SOLUK_NUFUS_ESIGI


def _halka(hucre: str) -> list[list[float]]:
    """H3 hücresinin kapalı sınır halkası, [boylam, enlem] ve ONDALIK basamak."""
    halka = [[round(lng, ONDALIK), round(lat, ONDALIK)] for lat, lng in h3.cell_to_boundary(hucre)]
    return halka + [halka[0]]


def altigen_geojson(altigenler: pd.DataFrame, kategoriler: list[str] = KATEGORILER) -> dict:
    """Ağa bağlı altıgenlerden kısa anahtarlı GeoJSON üretir.

    properties: i=ilçe, n=düğüm sayısı, s=[kategori başına aralık sırası],
    t=[süre metni (sure_metni), boşsa ''], a=[en yakın tesis adı (HTML kaçışlı)].
    'nufus' sütunu varsa ayrıca p=nüfus metni ve (eşik altındaysa) f=1 (soluk).
    """
    nufus_var = "nufus" in altigenler.columns
    ozellikler = []
    for h, satir in altigenler[altigenler["bagli"]].iterrows():
        p = {
            "i": satir["ilce"],
            "n": int(satir["dugum"]),
            "s": [aralik_sinifi(satir[f"{k}_dk"]) for k in kategoriler],
            "t": [html.escape(sure_metni(satir[f"{k}_dk"])) for k in kategoriler],
            "a": [html.escape(str(satir[f"{k}_ad"])) if isinstance(satir[f"{k}_ad"], str) else "" for k in kategoriler],
        }
        if nufus_var:
            p["p"] = nufus_metni(satir["nufus"])
            if soluk_mu(satir["nufus"]):
                p["f"] = 1
        ozellikler.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_halka(h)]},
                           "properties": p})
    return {"type": "FeatureCollection", "features": ozellikler}


def bagsiz_geojson(altigenler: pd.DataFrame) -> dict:
    """Sadece küçük parça düğümü içeren altıgenlerin GeoJSON'u."""
    ozellikler = [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [_halka(h)]},
        "properties": {"bilgi": f"Ağa bağlı değil · {satir['ilce']} · {int(satir['dugum'])} düğüm"},
    } for h, satir in altigenler[~altigenler["bagli"]].iterrows()]
    return {"type": "FeatureCollection", "features": ozellikler}


def tesis_geojson(tesisler: pd.DataFrame, kategoriler: list[str] = KATEGORILER) -> dict:
    """Tesis noktaları: k=kategori sırası, a=ad (HTML kaçışlı), o=1 ise 7 ilçe dışında.

    'ic' sütunu yoksa tüm tesisler 7 ilçe içinde sayılır.
    """
    sira = {k: i for i, k in enumerate(kategoriler)}
    ic = tesisler["ic"] if "ic" in tesisler.columns else pd.Series(True, index=tesisler.index)
    ozellikler = []
    for k, a, g, i in zip(tesisler["kategori"], tesisler["ad"], tesisler.geometry, ic):
        if k not in sira:
            continue
        p = {"k": sira[k], "a": html.escape(a)}
        if not i:
            p["o"] = 1
        ozellikler.append({"type": "Feature", "properties": p,
                           "geometry": {"type": "Point", "coordinates": [round(g.x, ONDALIK), round(g.y, ONDALIK)]}})
    return {"type": "FeatureCollection", "features": ozellikler}


def _js_json(veri) -> str:
    """Script içine gömülecek JSON; '</' dizisi script'i kapatmasın diye kaçışlanır."""
    return json.dumps(veri, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


class KategoriKatmani(MacroElement):
    """Altıgenleri, tesis noktalarını ve lejantı çizen; kategori değişimini dinleyen JS."""

    _template = Template("""
{% macro script(this, kwargs) %}
(function() {
  var harita = {{ this.harita }};
  var tesisGrubu = {{ this.tesis_grubu }};
  var etiketler = {{ this.etiketler }};
  var renkler = {{ this.renkler }};
  var secili = 0;
  var cizici = L.canvas({padding: 0.5});

  // Kenar çizgisi dolguyla aynı renkte: bitişik altıgenler arasındaki ince
  // kenar yumuşatma boşlukları kapanır, uzaktan çizgili/desenli görünmez.
  function stil(f) {
    var s = f.properties.s[secili];
    var renk = s < 0 ? "{{ this.bagsiz }}" : renkler[s];
    // Nüfusu eşiğin altındaki altıgenler soluk; kenar çizgisi yok (yarı saydam kenarlar üst üste binip çizgi yapar)
    if (f.properties.f) return {fillColor: renk, fillOpacity: {{ this.soluk_opaklik }}, stroke: false};
    return {fillColor: renk, fillOpacity: 0.85, color: renk, opacity: 0.85, weight: 1};
  }
  function noktaYaricapi() { var z = harita.getZoom(); return z >= 15 ? 5 : (z >= 13 ? 3.5 : 2); }
  function bilgi(f) {
    var p = f.properties;
    var sure = p.t[secili] || "hesaplanamadı";
    var yakin = p.a[secili] ? "<br>En yakın: " + p.a[secili] : "";
    var nufusSatiri = p.p ? "<br>Nüfus: " + p.p + (p.f ? " (seyrek)" : "") : "";
    return "<b>" + etiketler[secili] + ": " + sure + "</b>" + yakin + nufusSatiri +
           "<br><span style='color:#6b6a66'>" + p.i + " · " + p.n + " düğüm</span>";
  }
  var altigenler = L.geoJSON({{ this.altigen }}, {
    renderer: cizici, style: stil,
    onEachFeature: function(f, katman) {
      katman.bindTooltip(function() { return bilgi(f); }, {sticky: true});
    }
  }).addTo(harita);

  var tesisVeri = {{ this.tesis }};
  function tesisleriCiz() {
    tesisGrubu.clearLayers();
    L.geoJSON(tesisVeri, {
      filter: function(f) { return f.properties.k === secili; },
      pointToLayer: function(f, konum) {
        var r = noktaYaricapi(), op = f.properties.o ? {{ this.dis_opaklik }} : 1;
        return L.circleMarker(konum, {renderer: cizici, radius: r, weight: r > 2 ? 1.5 : 1,
                                      color: "{{ this.tesis_halka }}", opacity: op,
                                      fillColor: "{{ this.tesis_dolgu }}", fillOpacity: op});
      },
      onEachFeature: function(f, katman) {
        katman.bindTooltip(f.properties.a + (f.properties.o ? " <span style='color:#6b6a66'>(7 ilçe dışı)</span>" : ""));
      }
    }).addTo(tesisGrubu);
  }

  var lejant = L.control({position: "bottomright"});
  lejant.onAdd = function() {
    var div = L.DomUtil.create("div", "erisim-lejant");
    div.style.cssText = "background:{{ this.yuzey }};padding:8px 10px;border-radius:6px;" +
      "font:12px/1.5 system-ui,sans-serif;color:#2b2a27;box-shadow:0 1px 4px rgba(0,0,0,.25)";
    return div;
  };
  lejant.addTo(harita);
  function lejantiYaz() {
    var satirlar = {{ this.aralik_etiketleri }}.map(function(e, i) {
      return "<div><span style='display:inline-block;width:14px;height:10px;margin-right:6px;" +
             "background:" + renkler[i] + "'></span>" + e + "</div>";
    }).join("");
    document.querySelector(".erisim-lejant").innerHTML =
      "<b>" + etiketler[secili] + " · yürüme süresi</b>" + satirlar +
      // Soluk örneği tüm renklerin soluk şeridi: herhangi bir süre rengi soluk olabilir
      "<div><span style='display:inline-block;width:14px;height:10px;margin-right:6px;background:linear-gradient(90deg," +
      renkler.join(",") + ");opacity:{{ this.soluk_opaklik }}'></span>Soluk: {{ this.soluk_esik }} kişiden az nüfus<br>" +
      "<span style='margin-left:20px;color:#6b6a66'>(seyrek yerleşim / boş alan)</span></div>" +
      "<div><span style='display:inline-block;width:14px;height:10px;margin-right:6px;background:{{ this.bagsiz }}'></span>Ağa bağlı değil</div>" +
      "<div><span style='display:inline-block;width:8px;height:8px;margin:0 8px 0 2px;border-radius:50%;" +
      "background:{{ this.tesis_dolgu }};border:1.5px solid {{ this.tesis_halka }}'></span>Tesis</div>" +
      "<div><span style='display:inline-block;width:8px;height:8px;margin:0 8px 0 2px;border-radius:50%;" +
      "background:{{ this.tesis_dolgu }};border:1.5px solid {{ this.tesis_halka }};opacity:{{ this.dis_opaklik }}'>" +
      "</span>Tesis (7 ilçe dışı)</div>";
  }

  harita.on("baselayerchange", function(e) {
    var i = etiketler.indexOf(e.name);
    if (i < 0) return;
    secili = i;
    altigenler.setStyle(stil);
    tesisleriCiz();
    lejantiYaz();
  });
  var sonYaricap = noktaYaricapi();
  harita.on("zoomend", function() {
    if (noktaYaricapi() !== sonYaricap) { sonYaricap = noktaYaricapi(); tesisleriCiz(); }
  });
  tesisleriCiz();
  lejantiYaz();
})();
{% endmacro %}
""")

    def __init__(self, harita: folium.Map, tesis_grubu: folium.FeatureGroup, altigen: dict, tesis: dict,
                 kategoriler: list[str] = KATEGORILER):
        super().__init__()
        self._name = "KategoriKatmani"
        self.harita = harita.get_name()
        self.tesis_grubu = tesis_grubu.get_name()
        self.altigen = _js_json(altigen)
        self.tesis = _js_json(tesis)
        self.etiketler = _js_json([ETIKETLER[k] for k in kategoriler])
        self.renkler = _js_json(RENKLER)
        self.aralik_etiketleri = _js_json(ARALIK_ETIKETLERI)
        self.bagsiz, self.yuzey = BAGSIZ_RENK, YUZEY
        self.tesis_dolgu, self.tesis_halka, self.dis_opaklik = TESIS_DOLGU, TESIS_HALKA, DIS_TESIS_OPAKLIK
        self.soluk_opaklik, self.soluk_esik = SOLUK_OPAKLIK, SOLUK_NUFUS_ESIGI


def harita_olustur(altigenler: pd.DataFrame, tesisler: pd.DataFrame, sinirlar: gpd.GeoDataFrame,
                   kategoriler: list[str] = KATEGORILER) -> folium.Map:
    """Folium haritasını kurar.

    Katman seçicide kategoriler radio (tek seçim, varsayılan ilki), ayrıca
    açılıp kapanabilen 'Tesisler', 'Ağa bağlı değil' ve 'İlçe sınırları'.
    """
    bati, guney, dogu, kuzey = sinirlar.total_bounds
    harita = folium.Map(location=[(guney + kuzey) / 2, (bati + dogu) / 2], zoom_start=11, max_zoom=18,
                        tiles=None, prefer_canvas=True, control_scale=True)
    # Anahtar gerektirmeyen açık gri altlık (CartoDB artık API anahtarı istiyor)
    folium.TileLayer(xyzservices.providers.Esri.WorldGrayCanvas, name="Altlık", control=False,
                     max_native_zoom=16, max_zoom=18).add_to(harita)

    # Radio düğmeleri: boş temel katmanlar; seçim JS'deki baselayerchange ile dinlenir
    for i, k in enumerate(kategoriler):
        folium.FeatureGroup(name=ETIKETLER[k], overlay=False, show=(i == 0)).add_to(harita)

    tesis_grubu = folium.FeatureGroup(name="Tesisler", overlay=True, show=True).add_to(harita)

    bagsiz = bagsiz_geojson(altigenler)
    if bagsiz["features"]:
        folium.GeoJson(
            bagsiz, name="Ağa bağlı değil", overlay=True, show=True,
            # Opak: yarı saydam kenarlar ortak kenarda üst üste binip koyu çizgi oluşturuyordu
            style_function=lambda f: {"fillColor": BAGSIZ_RENK, "fillOpacity": 1,
                                      "color": BAGSIZ_RENK, "opacity": 1, "weight": 1},
            tooltip=folium.GeoJsonTooltip(fields=["bilgi"], labels=False),
        ).add_to(harita)

    folium.GeoJson(
        json.loads(sinirlar[["ilce", "geometry"]].to_json()), name="İlçe sınırları", overlay=True, show=True,
        style_function=lambda f: {"fill": False, "color": "#4a4945", "weight": 1.2, "opacity": 0.8},
        tooltip=folium.GeoJsonTooltip(fields=["ilce"], labels=False),
    ).add_to(harita)

    harita.add_child(KategoriKatmani(harita, tesis_grubu, altigen_geojson(altigenler, kategoriler),
                                     tesis_geojson(tesisler, kategoriler), kategoriler))
    folium.LayerControl(collapsed=False, position="topright").add_to(harita)
    harita.fit_bounds([[guney, bati], [kuzey, dogu]])
    harita.get_root().header.add_child(Element(
        "<title>Ankara yürüme erişilebilirliği</title>"
        "<style>.leaflet-control-layers{font:13px/1.5 system-ui,sans-serif}</style>"))
    return harita


def main() -> None:
    """data/sonuclar/altigenler.parquet'ten data/sonuclar/harita.html üretir."""
    altigenler = pd.read_parquet(SONUC_DIZINI / "altigenler.parquet").set_index("h3")
    nufus_dosyasi = SONUC_DIZINI / "altigen_nufus.parquet"
    if nufus_dosyasi.exists():  # Faz 4 çıktısı: altıgen başına nüfus (ilçe parçaları toplanır)
        altigenler["nufus"] = pd.read_parquet(nufus_dosyasi).groupby("h3")["nufus"].sum().reindex(altigenler.index)
    else:
        print("Uyarı: altigen_nufus.parquet yok, harita nüfussuz üretiliyor (önce: python -m src.nufus)")
    harita = harita_olustur(altigenler, tesis_tablosu(HEDEF_DIZINI), gpd.read_file(SINIR_DOSYASI))
    cikti = SONUC_DIZINI / "harita.html"
    harita.save(str(cikti))
    print(f"{cikti} ({cikti.stat().st_size / 2**20:.1f} MB, {int(altigenler['bagli'].sum()):,} renkli + "
          f"{int((~altigenler['bagli']).sum())} gri altıgen)")


if __name__ == "__main__":
    main()
