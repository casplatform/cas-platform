# CAS Platform — Claude Code çalışma notları

LEO/VLEO uydu operatörleri için conjunction decision support. Canlı sistem,
tek geliştirici. Bir şeye dokunmadan önce burayı oku.

Kod yorumları ve commit mesajları **İngilizce** yazılır. Bu dosya ve
konuşma dili Türkçe.

## Neredesin

`/opt/cas_staging` çalışma kopyası. **`/opt/cas`'i asla düzenleme.** Orası
production; onu yalnızca `scripts/deploy.sh` hareket ettirir. Deploy scripti
production ağacı kirliyse çalışmayı reddeder — yani orada düzenleme yapmak hem
kuralı çiğner hem sonraki deploy'u bloke eder.

İki instance tamamen ayrı: `casdb_staging` / `casdb`, portlar 8775-8776 /
8765-8766, servisler `cas-staging`+`cas-api-staging` / `cas`+`cas-api`.
Yollar `CAS_HOME` üzerinden çözülür; `sys.path` eklemesinde veya `.env`
okumasında **asla** `/opt/cas` sabitini yazma.

`tests/test_env_robustness.py` bunu **yalnızca `cas_api/` altında** yakalar —
orayı baştan sona tarar, yeni dosya eklendiği anda kapsama girer. Kök
dizindeki modüller (`cas_engine.py` ve yanındaki cron scriptleri) kapsam
dışı, ve birkaçında sabit hâlâ duruyor: `eusst_sync.py` içinde
`ENV_PATH = Path("/opt/cas/.env")`, `space_weather_sync.py` içinde
`sys.path.insert(0, "/opt/cas/cas_api")`. Bilerek açık bırakıldı: bu
scriptler mutlak yolla, instance başına ayrı crontab'dan çağrılıyor, yani
sabit ile çağıran bugün aynı fikirde. `cas_api/` farklı — hangi servis
yüklerse ona import edilen bir kütüphane ağacı, oradaki sabit hangi instance
çalışırsa çalışsın production'a çözülür.

Yani test geçiyor diye "hiçbir instance karışmıyor" deme; "`cas_api/` altında
karışan yok" demektir. Kök scriptleri kapsama almak önce onları düzeltmeyi
gerektirir, test bugün onlarda patlar.

Alembic'in `migrations/env.py`'si bilinçli olarak istisna: orada `CAS_HOME`
varsayılanı **yok**. Elle çalıştırılıyor ve DDL yazıyor; `CAS_HOME` veya
`DB_URL` açıkça verilmezse hata verir. `core/paths.py` ile `cas_engine.py`'de
varsayılan durmaya devam ediyor — onları systemd başlatıyor ve orada
varsayılan production davranışını koruyor.

## Döngü

1. `/opt/cas_staging` içinde düzenle
2. **Her iki staging servisini de yeniden başlat** — motor için
   `stop` → `sleep 3` → `start cas-staging`, sonra `restart cas-api-staging`;
   ardından `:8775/health` ve `:8776/api/v2/health` yanıt verene kadar bekle
3. `python3 -m pytest -q` — ~2dk30sn
4. Commit + `git push origin main`
5. `/opt/cas/scripts/deploy.sh` — production ağacı temiz mi, staging hedef
   commit'te mi diye bakar, **staging'i hedef commit'le yeniden başlatıp
   sağlığını doğrular**, testleri koşturur; sonra production'ı günceller, üç
   endpoint'i kontrol eder ve biri bile başarısızsa kendisi geri alır

**2. adımı atlama.** Ayakta duran servis, başladığı andaki kodu servis eder;
diskteki kodu değil. 18 Ağustos 2026'da bu iki kez yanlış teşhise yol açtı:
17 Ağustos 10:36'dan beri ayakta olan `cas-staging` bir gün eski kodla
yanıtladı (aynı istek staging'de 503, production'da 403 verdi ve fark koda
bağlandı), restart'tan sonra doğru sonucu döndü.

Staging'i tarayıcıda görmek için SSH tüneli: `http://localhost:8080`.
Giriş maskeli adreslerle (`u1@staging.invalid` admin) — staging e-postaları
maskeli, böylece bir test yanlışlıkla gerçek operatöre mail atamaz.

## Bize zaten pahalıya patlamış rate limit'ler

**Space-Track CDM: günde 3 istek, pay yok.** `fetch_cdm.py` üçünü de
00:00/08:00/16:00'da kullanıyor. Hesap Temmuz 2026'da saatlik çekim yüzünden
askıya alındı. Günde dördüncü çağrı teorik değil, gerçek risk. GP (katalog)
sınırı saatte 1 ve biz günde 1 kullanıyoruz — orada pay var.

**CelesTrak bu sunucuyu 24 Mayıs 2026'dan beri firewall'da tutuyor.** Sebep:
uydu başına saatlik döngüden çıkan ~3.000 istek/gün. Tüm otomatik çağrılar
kaldırıldı; geriye yalnızca `/tle/` proxy kaldı, o da üç başarısızlıkta altı
saat açılan bir circuit breaker arkasında. Yeni CelesTrak çağrısı ekleme, ve
hata alınca **tekrar deneme** — kullanım politikaları durmayı şart koşuyor,
bunu yok saymak bizi engelleten şeydi.

## Mimari

Strangler geçişi sürüyor. `cas_engine.py` (BaseHTTPRequestHandler, port 8765)
legacy ve **yalnızca import edilir**: oraya yeni özellik yazılmaz. Yeni işler
`cas_api/` (FastAPI, 8766) içine. Yönlendirme: `/api/*` motora, `/api/v2/*`
FastAPI'ye.

Şema değişiklikleri `migrations/` (Alembic) içinde. Request handler içinde
çalışma zamanı `CREATE TABLE`, `password_resets` tablosunun birbiriyle
çelişen iki tanımının olmasına yol açtı.

ML **deployed ve gated**, atıl değil: Space-Track public CDM'leri 107 kanonik
özelliğin ~%12'sini dolduruyor, coverage kapısı %70. Bu yüzden skorlanan
26.000 olayın hepsi UNAVAILABLE dönüp deterministik Pc hunisine devrediyor.
Kovaryans taşıyan operatör-tier CDM gelirse kod değişikliği olmadan devreye
girer. Böyle ifade et — "ML live" savunulabilir, "ML skorluyor" değil.

## Restart süreleri (deneyerek öğrenildi)

- Motor: `stop` → `sleep 3` → `start`, sonra ~10sn. `restart` **kullanma**:
  8765'i bind ediyor ve kendi kapanışıyla yarışıyor.
- cas-api: `restart` sonra **25 saniye**. Her uvicorn worker'ı XGBoost ve SHAP
  explainer yüklüyor. `systemctl is-active` worker'lar hazır olmadan `active`
  diyor — unit'e değil endpoint'e bak.

## Gerçek hata yakalamış doğrulama alışkanlıkları

- `py_compile` ve `ast.parse` sözdizimi görür, isimleri değil. `os` import
  etmeyen bir dosyaya `os.path.join` ekleyen yama compile kontrolünden geçti
  ve cas-api'yi 6 dakika düşürdü. Modülü **gerçekten import et**.
- Tanı sorgularına alt sınır koy. İki kez boş dönen bir karşılaştırma
  "birebir aynı" raporladı ve başarısız sorguyu gizledi.
- `os.environ.get(k, default)` anahtar varsa ama boşsa `""` döndürür. Sayıya
  çevrilen her şeyde `os.environ.get(k) or default` kullan.
- Her yamayı tam metne anchor'la ve yazmadan önce eşleşme sayısını assert et.
  Girintiye dikkat: modül seviyesi ve fonksiyon içi bloklar farklıdır.

## Yapma

- "Ne olacak görelim" diye yan etkili komut çalıştırma.
  `refresh_catalog_cache.py` iyi bir cache'i ezdi; `/spacetrack/auto` bir
  günün CDM kotasını harcadı.
- Çalıştırılmasını istemediğin bir komutu çalıştırılabilir bloğa koyma.
- `/opt/cas` içinde `git checkout` yapma — çalışan servislerin altındaki
  dosyaları takas eder.
- Bağlantıyı doğrulamak için DSN veya credential yazdırma.

## Dosya sahipliği

Bu oturum `root` olarak çalışıyor, staging servisleri `cas` kullanıcısıyla.
Dosya oluşturduktan veya `git reset` sonrası **her zaman**:

    chown -R cas:cas /opt/cas_staging

Atlanırsa servis dosyayı okuyamaz ve hata mesajı "dosya yok" gibi görünür.

## Reboot sonrası

Staging unit'leri `static` — `[Install]` bölümleri yok, boot'ta
kalkmazlar. Bilinçli: staging elle kontrol edilir, arka planda sessizce
çalışmaz. Production (`cas`, `cas-api`) `enabled`, kendiliğinden kalkar.

Reboot sonrası staging'i elle başlat:

    systemctl start cas-staging cas-api-staging

Deploy gate 5 de başlatır (stop→start yapıyor, kapalıysa sorun değil),
ama o zamana kadar `:8775` ve `:8776` cevapsızdır — "servis çöktü"
sanma, journal'da `No entries` görürsen sebep budur.
