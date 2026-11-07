\# Ankara Erişilebilirlik Haritası



\## Amaç

Ankara merkez ilçelerinde her noktadan en yakın eczane, hastane ve

metro istasyonuna yürüme süresini hesaplayıp H3 altıgenleriyle

haritalamak. "15 dakikalık şehir" analizi.



\## Teknik kararlar

\- Python 3.11+, osmnx, networkx, geopandas, h3, folium, streamlit

\- Yürüme ağı: network\_type="walk", merkez ilçeler

\- Mesafe: sanal süper kaynak düğümü (her hedef noktasından en yakın ağ düğümüne bağlantı mesafesi kenar ağırlığı, eşik 200 m) + networkx.dijkstra\_predecessor\_and\_distance (kategori başına tek çalıştırma; en yakın tesis öncül zincirinden). Analiz en büyük bağlı parçada yapılır

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

