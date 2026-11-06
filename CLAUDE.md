\# Ankara Erişilebilirlik Haritası



\## Amaç

Ankara merkez ilçelerinde her noktadan en yakın eczane, hastane ve

metro istasyonuna yürüme süresini hesaplayıp H3 altıgenleriyle

haritalamak. "15 dakikalık şehir" analizi.



\## Teknik kararlar

\- Python 3.11+, osmnx, networkx, geopandas, h3, folium, streamlit

\- Yürüme ağı: network\_type="walk", merkez ilçeler

\- Mesafe: networkx.multi\_source\_dijkstra\_path\_length (kategori başına tek çalıştırma)

\- Yürüme hızı: 80 m/dk

\- Altıgen: H3 çözünürlük 9

\- Nüfus: WorldPop 100m Türkiye rasterı



\## Kurallar

\- Veriler data/ klasörüne, data/ git'e eklenmez

\- İndirilen veri diske önbelleklenir, tekrar indirilmez

\- Kod yorumları ve değişken adları Türkçe olabilir, fonksiyonlara docstring yaz

\- Her yeni hesaplama fonksiyonu için tests/ altına test yaz

\- Git commit'lerini ben atacağım, sen commit atma

\- Bir şeyi değiştirmeden önce ne yapacağını kısaca açıkla

