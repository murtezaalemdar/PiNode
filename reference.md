# PiFleet: İleri Düzey Çoklu Pi Node Yönetim Sistemi (Kapsamlı Referans Rehberi)

Bu doküman, tek bir fiziksel Linux sunucusu üzerinde birden fazla Pi Network Node'unu (Düğümünü) çalıştırmak, yönetmek ve izlemek için geliştirilen **PiFleet** projesinin detaylı mimarisini, derinlemesine çalışma mantığını ve sistem yönetiminde **kesinlikle dikkat edilmesi gereken kritik hususları** içermektedir.

---

## 1. Sistemin Temel Amacı ve Felsefesi
PiFleet, standart Pi Network masaüstü uygulamasının limitlerini yıkarak, güçlü bir Linux sunucusunun (Örn: 64GB RAM, NVMe SSD) potansiyelini sonuna kadar kullanmayı hedefler. 
Normal şartlarda her bir Node için ayrı bir bilgisayar gerekirken, PiFleet sayesinde tamamen izole edilmiş, kendi portlarına ve bağımsız veritabanlarına sahip onlarca Node aynı donanım üzerinde çalıştırılabilir.

---

## 2. Mimari ve Teknoloji Yığını
- **İşletim Sistemi:** Linux (Ubuntu) - *Windows üzerindeki WSL sanallaştırma katmanının yarattığı I/O darboğazını aşmak için doğrudan Kernel üzerinde çalışır.*
- **Konteynerleştirme:** Docker & Docker Compose - *Her Node izole bir konteynerdir.*
- **Backend:** Python 3 (Flask Framework) - *Hafif ve asenkron görevleri yönetebilen arka uç.*
- **Veritabanı:** SQLite (`fleet.db`) - *Sunucu tarafında Node kimliklerini ve durumlarını tutar.*
- **Frontend:** HTML5, CSS, JS - *Modern, dark-mode destekli ve anlık tepki veren kullanıcı arayüzü.*

---

## 3. Derinlemesine Bileşen Analizi

### 3.1. İzole Port Stratejisi (Port Prefixing)
Aynı sunucuda çalışan birden fazla Node'un birbiriyle ağ (Network) çakışması yaşamaması için matematiksel bir "Port Öneki (Port Prefix)" mantığı geliştirilmiştir.
Veritabanında oluşturulan her Node'a `3140`, `3141`, `3142` gibi eşsiz bir önek atanır.
Sistem, Docker Compose dosyasını oluştururken bu öneklere `1`, `2` ve `3` ekleyerek 3 temel servisin portlarını belirler:
- **[Önek]1 (Örn: 31421):** Horizon API Portu (Verileri dışarı servis eder)
- **[Önek]2 (Örn: 31422):** Stellar Core HTTP Portu (Node'un kalbine doğrudan bağlantı)
- **[Önek]3 (Örn: 31423):** P2P Peer Portu (Diğer Pi Node'ları ile haberleşme)
*Bu yapı sayesinde IP çakışması veya "Port kullanımda" hatası tamamen ortadan kaldırılmıştır.*

### 3.2. Akıllı Klonlama Motoru (Smart Sync/Clone)
Sistemin en can alıcı yeteneklerinden biridir. Yeni bir Node eklendiğinde, Pi ağına senkronize olmak sıfırdan başlanırsa günlerce sürebilir.
**Çalışma Mantığı:**
1. Arayüzden "Yeni Ekle" veya "Sıfırla" dendiğinde sistem veritabanını tarar ve durumu `Synced!` olan en sağlıklı Node'u (Örn: *HanifeO*) seçer.
2. Hedef Node'un konteyneri geçici olarak durdurulur (`docker compose down`).
3. Arka planda bir Python Thread (İş parçacığı) başlar ve `rsync -a` komutuyla sağlıklı Node'un `stellar-core` ve `history` klasörlerini saniyeler içinde yeni Node'a kopyalar.
4. Kopyalama bittiğinde yeni Node başlatılır (`docker compose up -d`). Sonuç: Yeni Node sıfır bekleme süresiyle hayata %99 senkronize olarak başlar.

### 3.3. Watchdog (Oto-Canlandırma) Mekanizması
Sunucu yeniden başlatıldığında, elektrik kesintisinde veya Pi ağındaki aşırı yüklenme sonucu bir konteyner çöktüğünde devreye girer.
- `app_24_fresh.py` içinde `watchdog_thread` adında bağımsız bir döngü çalışır.
- Her 60 saniyede bir `fleet.db` içindeki Node'ları tarar.
- Eğer bir Node'un `auto_restart` bayrağı aktifse (`1`) ve Docker üzerinde o Node durmuş görünüyorsa, anında `docker compose up -d` komutunu göndererek Node'u diriltir.

---

## 4. Geliştirme Sürecinde Aşılmış Kritik Sorunlar

### 4.1. "Sahte Donma" ve Horizon vs Core Kilitlenmesi
**Sorun:** Pi Network v23 altyapısında, Node'lar yoğun veri çekerken "Horizon" servisi yanıt vermeyi bırakıyordu. Eski sistem, Node'un durumunu Horizon'a sorduğu için cevap alamıyor ve tüm sunucu kilitleniyordu (Deadlock).
**Çözüm:** Arayüz mantığı tamamen değiştirildi. Sistem artık dolaylı bir servis olan Horizon'a güvenmek yerine, doğrudan çekirdeğe (`http://localhost:[Port2]/info`) bağlanır. Eğer çekirdek o an ağır bir işlem yapıyorsa (Catching up) ve 3 saniye içinde yanıt vermezse, sunucuyu kilitlememek için işlemi iptal eder ve arayüze kibarca **"Meşgul (Yanıt Yok)"** mesajını basar.

### 4.2. Tarayıcı Önbelleği (Browser Cache) Körlüğü
**Sorun:** Arayüz (UI) verileri her 4 saniyede bir güncellemesine rağmen ekrandaki rakamlar değişmiyordu.
**Çözüm:** Google Chrome gibi tarayıcılar `GET` isteklerini arka planda dondurur. Bunu kırmak için her Javascript isteğinin sonuna `?t={Timestamp}` şeklinde anlık milisaniye eklendi (Cache-Busting). Böylece tarayıcı her 4 saniyede bir sunucudan kesinlikle taze veri çekmeye mecbur bırakıldı.

---

## 5. DİKKAT EDİLMESİ GEREKENLER (Kritik Uyarılar)

Sistemi yönetirken veya müdahale ederken şu hususlara **kesinlikle** dikkat edilmelidir:

### 1. Pi Node Kimlikleri (UUID) Çakışması
Pi ağı, aynı "UUID" (Kimlik) ile bağlanan iki farklı Node gördüğünde ikisini de ağdan atar veya senkronizasyonlarını durdurur. 
**Dikkat:** Arayüz üzerinden yeni bir Node'a UUID tanımlarken, o UUID'nin **asla** başka bir Node'da (veya farklı bir evdeki bilgisayarda) aynı anda çalışmadığından emin olun. Kod tarafında aynı sunucudaki çift UUID'leri engelleyen bir koruma yazılmıştır ancak dış sunucuları kontrol edemez.

### 2. Disk Alanı (Storage) ve I/O Limiti
Her bir Pi Node ortalama 20-40 GB arası disk alanı kullanır ve anlık olarak diske sürekli okuma/yazma yapar (I/O).
**Dikkat:** 
- `df -h` komutu veya web arayüzündeki "Disk Kullanımı" kısmı düzenli izlenmelidir. Disk %100 dolarsa tüm Node'lar aynı anda çöker ve veritabanları bozulabilir.
- Ara sıra sunucuya bağlanıp `docker image prune -a` veya arayüzün "Bahar Temizliği" скрипtleri (do_cleanup_fast.py) ile eski/kullanılmayan imajlar temizlenmelidir.

### 3. "Sıfırla" Butonunun Yıkıcı Etkisi
Web panelindeki **Sıfırla** butonu çok tehlikeli ve güçlüdür. Tıklandığı an:
- O Node'un tüm blokzincir geçmişi acımasızca silinir (`rm -rf stellar-core/*`).
- Başka bir Node'dan klonlama başlatılır.
**Dikkat:** Çalışan ve eşzamanlı olan sağlıklı bir Node'da asla "Sıfırla" butonuna basmayın. Sadece günlerce geride kalmış ve "Catching up" döngüsünden çıkamayan bozuk Node'lar için kullanılmalıdır.

### 4. Docker Servisinin Bağımlılığı
Uygulama doğrudan işletim sisteminin `docker` ve `docker compose` komutlarıyla entegredir. 
**Dikkat:** Linux sunucuda manuel olarak Docker'ı durdurmak, silmek veya sürümünü uyumsuz bir versiyona güncellemek tüm Flask arka ucunu çökertebilir. 

### 5. Sunucu Kaynaklarının Sömürülmesi
Her Node çalıştığında bir miktar RAM ve CPU rezerve eder.
**Dikkat:** Arayüzden "Yeni Node Ekle" derken donanımın sınırlarını (Örn: 64 GB RAM için maksimum 10-12 Node) aşmamaya özen gösterin. Sınır aşıldığında Linux "OOM Killer" (Out of Memory) protokolünü devreye sokar ve Node'ları rastgele öldürmeye başlar.

---
*Bu doküman, sistemin stabilitesini korumak ve gelecekte projeyi devralacak veya müdahale edecek geliştiricilere / sistem yöneticilerine ışık tutmak için Antigravity (AI) tarafından hazırlanmıştır.*
