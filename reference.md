# Pi Fleet Projesi - Sistem Referans Notları

## 1. Mimari Genel Bakış (3+3 Capsule Architecture)
Pi Fleet yönetim sistemi, tek bir Linux sunucusu (192.168.0.24) üzerinde 6 adet Pi Node çalıştırmak üzere **"Docker in Docker (dind)"** kapsül mimarisi kullanmaktadır. Sistem, 2 farklı dış IP adresi kullanarak, her IP başına 3 Node düşecek şekilde tasarlanmıştır.

### A. IP ve Port Yönlendirmeleri
Her iki dış IP (78.186.188.225 ve 85.106.1.46) üzerinden standart Pi Network portları (31401-31409) kullanılmaktadır. Ancak bu trafik sunucuya ulaştığında, 31401-31409 aralığı sunucu tarafından parçalanıp ilgili kapsüllere yönlendirilir.

**IP 1: 192.168.0.24 (Dış IP: 78.186.188.225)**
- **NihalA (Kapsül 1):** Dış Portlar 31401-31403 -> İç Kapsül Portları 31401-31403
- **RumeysaA (Kapsül 2):** Dış Portlar 31404-31406 -> İç Kapsül Portları 31401-31403
- **FatmaA (Kapsül 3):** Dış Portlar 31407-31409 -> İç Kapsül Portları 31401-31403

**IP 2: 192.168.0.80 (Dış IP: 85.106.1.46)** (192.168.0.24 sunucusuna yönlendirilmiş ikincil IP)
- **AhmetA (Kapsül 4):** Dış Portlar 31401-31403 -> İç Kapsül Portları 31401-31403
- **MelekC (Kapsül 5):** Dış Portlar 31404-31406 -> İç Kapsül Portları 31401-31403
- **MurtezaA1 (Kapsül 6):** Dış Portlar 31407-31409 -> İç Kapsül Portları 31401-31403

## 2. Veritabanı Yapısı (fleet.db)
SQLite veritabanı `/opt/pi-fleet/fleet.db` konumunda tutulur. Eski mimaride sadece `port_prefix` varken, 3+3 mimarisine geçişte `bind_ip` ve `base_port` kolonları eklendi.
- `bind_ip`: Node'un trafik aldığı yerel IP adresi (örn: 192.168.0.24 veya 192.168.0.80).
- `base_port`: Node'a atanan dış port bloğunun başlangıcı (örn: 31401, 31404, 31407).

## 3. Arayüz (Frontend - index.html)
Arayüz tasarımı `/opt/pi-fleet/templates/index.html` dosyasından sunulmaktadır.
- "Bağlantı (Ağ / Port)" kısmı dinamik olarak `bind_ip` ve `base_port` verilerini kullanır.
- "Erişilebilirlik (Availability)" verisi, Node'un veritabanına eklendiği tarih (`created_at`) ve kapalı kaldığı dakika (`downtime_minutes`) hesaplanarak %100 üzerinden dinamik oran oluşturur.
- Disk Meşguliyeti (I/O) `iostat` komutundan çekilen saf IO utilization yüzdesini (`data.disk_io`) gösterir.

## 4. Arka Plan (Backend - app.py)
`/opt/pi-fleet/app.py` Python Flask uygulamasıdır. 
- Sunucu başlangıcında otomatik başlaması için `nohup /usr/bin/python3 /opt/pi-fleet/app.py > /opt/pi-fleet/app.log 2>&1 &` şeklinde çalıştırılır.
- Kapsüllerin adlarını bulmak için dinamik bir RegEx arama fonksiyonu (`get_capsule_name`) kullanır.
- Sistem metriklerini okumak için `psutil` kütüphanesini kullanır.

## 5. Sorun Giderme (Troubleshooting)
- Node'lar "Durduruldu" görünüyorsa: `fleet.db` veritabanındaki `bind_ip` ve `base_port` değerlerinin `docker ps` çıktısındaki port yönlendirmeleri ile eşleştiğinden emin olun (örn: `192.168.0.80:31404->31401`).
- Arayüzde veri eski görünüyorsa: Tarayıcı önbelleği (cache) temizlenmelidir (CTRL + F5).
- Kapsül veritabanı onarımı/yedekten dönme: Node UUID'lerini koruyarak 14GB'lık history verisi `/tmp/pi-node-backup/` dizininden `docker cp` komutu ile aktarılır ve `docker compose up -d` ile yeniden başlatılır.
- **Ledger Uyuşmazlığı (Horizon vs Stellar-Core):** Nodeların "history" arşivleri disk dolmasından vb. silinirse, arayüze veri sağlayan Horizon (yardımcı) veritabanı hata verip eski ledger'da takılı kalabilir. Ancak bu durum, asıl ana çekirdek olan `stellar-core`'un senkronize olmasını engellemez. Arayüzün her zaman doğru veriyi göstermesi için `app.py` içinde Horizon verisi yerine `info['info']['ledger']['num']` (Stellar-Core gerçek zamanlı ledgeri) okunmalıdır.
- **UUID Yükleme Sorunu (Arayüz Bug'ı):** Arayüzde UUID'ler eksik olsa bile API'den dönen "Tanımsız" kelimesi, JavaScript tarafında 'ı' harfi hataları nedeniyle dolu zannedilerek "✅ Yüklü" görünebilir. Bunu önlemek için API tarafında "Missing" vb. ingilizce standart anahtar kelimeler kullanılmalıdır.
- **UUID Kopyalama Hatası:** Docker kapsülleri iki farklı sunucuda (`.24` ve `.80`) bulunuyorsa, sadece `.24`'te yerel çalışan uygulamanın yaptığı `docker cp` komutu `.80`'deki kapsüllere dosya yazamaz ve sessizce başarısız olur. Uzaktaki sunuculara UUID yüklemek için `paramiko` SSH veya özel Python betikleri kullanılmalıdır.
