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

`tests/test_env_robustness.py` bunu **kök dizindeki `*.py`, `cas_api/**` ve
`ml/src/**`** altında yakalar; üçünü de baştan sona tarar, yeni dosya
eklendiği anda kapsama girer. Kural: kodda `/opt/cas` string sabiti olamaz —
tek istisna aynı satırda `CAS_HOME` geçen varsayılan
(`os.environ.get("CAS_HOME", "/opt/cas")`). Docstring ve yorumlar muaf, çünkü
scriptlerin çoğu gerçek crontab satırını docstring'inde tutuyor ve o satır
production hakkında doğru bir cümle. Tarama grep değil AST — muafiyet tam da
bunu gerektiriyor.

Kapsam 20 Ağustos 2026'da genişletildi. Öncesinde yalnızca `cas_api/` vardı ve
gerekçesi "kök scriptler mutlak yolla, instance başına ayrı crontab'dan
çağrılıyor" idi. Cron için doğru; pytest için değil — `decision_scanner.py`,
`rank_debris.py` ve `eusst_sync.py`'yi testler import ediyor. `/opt/cas`'in hiç
olmadığı bir runner için de değil: `decision_scanner.py` modül seviyesinde
`open("/opt/cas/.env")` yapıyordu ve ilk CI koşumu collection error ile durdu.

Kapsam dışı bırakılanlar ve nedenleri testin docstring'inde yazılı:
`deploy_directory.py`, `deploy_launches.py`, `setup_plans_account.py` (emekli
one-shot production yamaları — `/opt/cas` onların *hedefi*, çözemedikleri kök
değil; hepsi `SystemExit(2)` ile çalışmayı reddediyor), `tests/smoke/`
(bilerek production'a bakar), `migrations/env.py` (bilerek `CAS_HOME`
varsayılansız), ve shell scriptleri (ayrıştırılmıyor).

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
3. `.venv/bin/python -m pytest -q` — birkaç dakika. Sistem `python3`'ü değil:
   iki interpreter 58 pinli paketin 6'sını farklı sürüme çözüyor
4. Commit + `git push origin main`
5. `/opt/cas/scripts/deploy.sh` — **13 numaralı kapı**. Sırayla: iki
   interpreter ve unit'lerin *etkin* ExecStart'ı → production ağacı temiz mi →
   `origin/main` fetch → gelen diff → staging hedef commit'te ve temiz mi →
   staging'i hedef commit'le yeniden başlat → `casdb_test` production'ın
   Alembic sürümünde mi → **suite** → onay → DB yedeği → production venv'i
   senkronla ve import'ları kanıtla → production'ı taşı ve rollback noktasını
   aynı anda kaydet → restart + üç endpoint sağlık kontrolü. Biri bile
   başarısızsa kendisi geri alır (kod **ve** venv)

**`casdb_test` tek ve kilitli.** `tests/integration/conftest.py` koşum boyunca
`flock` tutuyor. İkinci bir koşum **hemen durur** ve kilidi tutan pid'i yazar —
klavye başında biri varsa sessizce asılmak yanlış cevap. Deploy kapısı ise
`CAS_TEST_LOCK_WAIT=180` ile **bekler**: kapı 1-7 zaten koştu, iptal o emeği
çöpe atar. Elle beklemek istersen aynı değişkeni ver. Kilit olmadan iki koşum
aynı DB'ye yazar ve sonuç sessizce karışır — Faz 7.2'de bir kez oldu, tesadüfen
fark edildi.

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

## Staging'in production'dan ayrıştığı yer: veri dosyaları

Staging'de cron **yok** (bilinçli: elle kontrol edilen instance, ayrıca
Space-Track kotası paylaşılıyor). Sonuç: kod her deploy'da hizalanır, veri
dosyaları hizalanmaz. İkisi elle kopyalandı ve zamanla bayatlar:

- `.spacetrack_catalog_cache.json` — 16 Ağustos 2026'da kopyalandı, o
  tarihte donmuş. Katalog davranışını staging'de test edeceksen tazele:
  `cp -p /opt/cas/.spacetrack_catalog_cache.json /opt/cas_staging/` ardından
  `chown cas:cas`.
- `ml/datasets/esa_kelvins/test_data.csv` — 20 Ağustos 2026'da kopyalandı
  (35 MB, gitignore'da). `tests/test_covariance_verification.py`'nin Layer 3
  gerçek-veri katmanı bunu okur; yoksa o dört test atlanır.

"Cache taze mi" testi bu yüzden `tests/smoke/test_data_freshness.py`'de,
production'ın dosyasına bakarak duruyor — tazelik, dosyayı **yazan**
instance'ın özelliği. Deploy gate 8 tam suite'i koşturduğu için kontrol
yerinde: production'ın sync'i durursa deploy yine bunu söyler. Cache'i
deploy adımıyla veya staging'e özel bir cron'la otomatik kopyalamak
reddedildi: deploy'un veri yönünü tersine çevirir (bugün yalnızca kod
staging'den production'a gider) ve bayat bir veri dosyasını kod kapısının
hatasına dönüştürür.

## Mimari

Strangler geçişi **dondu** — bu bir karar, sürüklenme değil. `cas_engine.py`
(BaseHTTPRequestHandler, port 8765) legacy ve **yalnızca import edilir**: oraya
yeni özellik yazılmaz, ama güvenlik ve güvenilirlik düzeltmeleri gitmeye devam
eder. Yeni işler `cas_api/` (FastAPI, 8766) içine. Yönlendirme: `/api/*`
motora, `/api/v2/*` FastAPI'ye.

Motoru taşımıyoruz. Gerekçe ölçümle birlikte
[`docs/adr/0001-freeze-the-legacy-engine.md`](docs/adr/0001-freeze-the-legacy-engine.md)
içinde: motor 30 günde ~904 istek görüyor (%31'i kendi izlememiz), ilk
commit'ten sonra ona giden 19 değişikliğin hiçbiri "bu dosyada çalışmak zor"
yüzünden değildi, ve bağımlılık yönü zaten doğru (motor `cas_api`'den import
ediyor, tersi değil) — yani bugün hareket etmeden seçenek açık kalıyor.
**"45 endpoint hiç çağrılmıyor, silelim" fikri ADR'de gerekçesiyle
reddedildi**: sıfır istek ölü demek değil, portal JS'i onları çağırıyor.
Kararı yeniden açma tetikleyicileri de orada.

Şema değişiklikleri `migrations/` (Alembic) içinde. Request handler içinde
çalışma zamanı `CREATE TABLE`, `password_resets` tablosunun birbiriyle
çelişen iki tanımının olmasına yol açtı.

ML **deployed ve gated**, atıl değil: Space-Track public CDM'leri 107 kanonik
özelliğin ~%12'sini dolduruyor, coverage kapısı %70. Bu yüzden skorlanan
olayların **hepsi** UNAVAILABLE dönüp deterministik Pc hunisine devrediyor
(sayıyı buraya yazma, bayatlıyor — 26.000 yazıyordu, bir hafta sonra 27.528'di;
saymak istersen `raw_json ? 'ml'`).
Kovaryans taşıyan operatör-tier CDM gelirse kod değişikliği olmadan devreye
girer. Böyle ifade et — "ML live" savunulabilir, "ML skorluyor" değil.

## Sistem kendi durumu hakkında ne söylüyor

**`data_health` 14 kaynak izliyor** — 7 dış besleme, backup, ve 6 *işleme
adımı* (`decision_scanner`, `ml_enrich`, `relvel_enrich`, `rank_debris`,
`directory_satcat`, `smoke`). İşleme adımları için semantik farklı ve fark
kritik: **"script bitti" başarı değildir.** `ml_enrich` bozuk olduğu 38 günün
hepsinde düzgün bitti, her turda 200 hata yazdı ve exit 0 verdi. Bu yüzden
başarı şartı kaynağın kendi girdisinde yazılı (`ml_enrich`: `errors == 0`;
`relvel_enrich`: `miss_tle/candidates ≤ %25`; `decision_scanner`: yazılan karar
== watchlist uydu sayısı). Yeni kaynak eklerken `SOURCES` yorumunu oku.

İki durum sessizce yeşil kalabiliyordu, ikisi de kapatıldı: `status` sütunu bir
**mandal** (sessizce ölen kaynak son değerini sonsuza taşır) — `get_health()`
artık bayatlığı katıp `stale` döndürüyor, ham değer `reported_status`'ta duruyor.
Hiç koşmamış kaynak da sonsuza kadar `unknown` kalıyordu — `SOURCES`'taki
`since` tarihinden 2×interval geçince `never_ran` oluyor. **`since` zorunlu**,
`tests/test_data_health_sources.py` unutulmasını engelliyor.

**Sürüm kimliği artık gerçek.** `/health` ve `/api/v2/health` `commit` ve
`deployed_at` döndürüyor; `deploy.sh` bunu HEAD'i hareket ettiren üç yolda da
(deploy + iki rollback) `$PROD/.deploy_version.json`'a yazıyor. Dosya
**gitignore'da olmak zorunda**: izlenseydi kapı 2 production ağacını kirli bulur
ve script bir sonraki deploy'u kendi kendine bloke ederdi. Dosya yoksa alanlar
`unknown` döner, bu bir hata değil (geliştirme kopyası). Elle yazılı
`"version": "0.7"` duruyor ama hiçbir şeye karşılık gelmiyor — hangi kodun
canlı olduğunu `commit` söyler.

**`deploy/` altındaki referans kopyalar canlıyla eşleşmek zorunda.**
`tests/smoke/test_config_drift.py` dört unit'i, nginx config'ini, crontab'ı ve
`/etc/cron.d/cas-*`'ı karşılaştırıyor. **Bir cron satırı veya unit
değiştirdikten sonra kopyayı tazele**, yoksa suite kırmızıya döner — bu
mekanizmanın çalışması demek, hata mesajı tazeleme komutunu basıyor. Bunun
README'de bir `diff` komutu olarak durduğu dönemde `nginx-cas.conf` 4,5 ay
bayat kaldı; test o yüzden var. `crontab.reference` CAS satırlarıyla süzülü
(o crontab Tribun ve elarasim'i de sürüyor).

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
- **Toplu değiştirmede yardımcının kendi gövdesi de değişir.** 23 çağrı yerini
  `_auth_reject()`'e çevirirken aynı arama `_auth_reject`'in içindeki satırı da
  değiştirdi; her sıradan 401 sonsuz özyinelemeye girdi. Diff okuyarak değil
  `RecursionError` ile ortaya çıktı — toplu değiştirmeden sonra **değiştirdiğin
  fonksiyonun kendisini çalıştır**.
- **Ölçtüğün şeye inanmadan önce ölçen aleti doğrula — hata iki yöne de
  gider.** Eksik yön: şema karşılaştırması `information_schema` kullanıyordu,
  o görünüm yetkiyle filtreli, ve `cas` rolünün hakkı olmayan bir tablo
  "production'da yok" diye raporlandı; `pg_catalog` farkı kapattı. Fazla yön:
  DOCX taramasında `<w:t[^>]*>` ifadesi `<w:top .../>` ile de eşleşti (paragraf
  kenarlığı), aradaki işaretleme "metin" sanıldı ve altı belgede olmayan bir
  "ham XML sızıntısı" raporlandı — gerçek içerik `casplatform.com`'du.
  Desene değil ayrıştırıcıya sor (`ElementTree`, `pg_catalog`), ve
  karşılaştırmaya bir kontrol grubu koy: gerçekten farklı olan bir şey de
  karşılaştır, fark **görünmeli**. Geri çekme `docs/commit-message-errata.md`'de.
- **Commit mesajını rapordan değil diff'ten yaz.** İki mesaj, yapılmamış işi
  yapılmış gibi anlattı: biri hiç yazılmamış bir CI job'ını ayrıntısıyla
  gerekçelendirdi, diğeri aynı commit'in *eklediği* bir bayrağı "reddedildi"
  dedi. Geçmiş yeniden yazılmadı; düzeltmeler
  `docs/commit-message-errata.md`'de.

## Yapma

- "Ne olacak görelim" diye yan etkili komut çalıştırma.
  `refresh_catalog_cache.py` iyi bir cache'i ezdi; `/spacetrack/auto` bir
  günün CDM kotasını harcadı.
- Çalıştırılmasını istemediğin bir komutu çalıştırılabilir bloğa koyma.
- `/opt/cas` içinde `git checkout` yapma — çalışan servislerin altındaki
  dosyaları takas eder.
- Kök dizindeki tek seferlik "deploy" scriptlerini çalıştırma. `deploy_*.sh`,
  `deploy_*.py`, `setup_*.{sh,py}`, `add_*_tests.sh`, `fix_debris_widgets.sh`,
  `create_validation_docs.sh` — bunlar `/opt/cas`'e doğrudan yazıp servisi
  restart eden, test/gate/rollback'i olmayan eski yamalar. 19 Ağustos 2026'da
  hepsine "çalışmayı reddet" guard'ı kondu (exit 2); dosyalar neyin
  deploy edildiğinin kaydı olarak duruyor. Değişiklik göndermenin tek yolu
  `scripts/deploy.sh`.
- Bağlantıyı doğrulamak için DSN veya credential yazdırma.

## Dosya sahipliği

Bu oturum `root` olarak çalışıyor, staging servisleri `cas` kullanıcısıyla.
Dosya oluşturduktan sonra **her zaman**:

    chown -R cas:cas /opt/cas_staging

Atlanırsa servis dosyayı okuyamaz ve hata mesajı "dosya yok" gibi görünür.

Sadece `git reset` değil: root olarak yapılan **her git yazması** (`commit`,
`checkout`, `fetch`, `reset`) `.git/` içine root sahipli object/index dosyası
bırakır, ve root olarak çalıştırılan her `python3`/`pytest` root sahipli
`__pycache__` üretir. Yani kural pratikte "commit'ten sonra da chown" demek —
19 Ağustos 2026'da denetlendiğinde ağaçta 21 root sahipli dosya vardı, hepsi
bir önceki commit'ten ve pycache'ten geliyordu.

Denetlemek için (0 dönmeli):

    find /opt/cas_staging \( -not -user cas -o -not -group cas \) | wc -l

## Reboot sonrası

Staging unit'leri `static` — `[Install]` bölümleri yok, boot'ta
kalkmazlar. Bilinçli: staging elle kontrol edilir, arka planda sessizce
çalışmaz. Production (`cas`, `cas-api`) `enabled`, kendiliğinden kalkar.

Reboot sonrası staging'i elle başlat:

    systemctl start cas-staging cas-api-staging

Deploy gate 6 de başlatır (stop→start yapıyor, kapalıysa sorun değil),
ama o zamana kadar `:8775` ve `:8776` cevapsızdır — "servis çöktü"
sanma, journal'da `No entries` görürsen sebep budur.
