# Paket 5 — H ve I: uygulama komutları

Bu iki değişikliği ben uygulayamadım (nginx dosyaları ve `systemctl` bu
oturumda yazılamıyor). Aşağıdakiler sırayla çalıştırılacak.

Hazırlanan dosyalar:
- `/opt/cas_staging/deploy/prepared/cas.conf.new`
- `/opt/cas_staging/deploy/prepared/cas-api-staging.service`

---

## H) nginx: sites-enabled'ı symlink yap + bayat kopyayı ve yanlış yorumu düzelt

Şu anki durum: `sites-enabled/cas.conf` normal dosya (10 Ağustos),
`sites-available/cas.conf` 28 Mart — **`/api/v2/` bloğu yok**. İçerik olarak
`cas.conf.new`, canlı dosyayla **gövde bazında birebir aynı**; yalnızca baş
yorum düzeltildi. Yani bu adım davranış değiştirmez, sadece drift'i kapatır.

```bash
# 1) Her iki mevcut dosyayı da yedekle
cp -a /etc/nginx/sites-available/cas.conf /root/nginx_backups/cas.conf.available.bak.$(date +%Y%m%d_%H%M%S)
cp -a /etc/nginx/sites-enabled/cas.conf   /root/nginx_backups/cas.conf.enabled.bak.$(date +%Y%m%d_%H%M%S)

# 2) Doğru içeriği kaynağa (sites-available) yaz
cp /opt/cas_staging/deploy/prepared/cas.conf.new /etc/nginx/sites-available/cas.conf
chown root:root /etc/nginx/sites-available/cas.conf
chmod 644       /etc/nginx/sites-available/cas.conf

# 3) sites-enabled'daki normal dosyayı symlink ile değiştir
rm /etc/nginx/sites-enabled/cas.conf
ln -s /etc/nginx/sites-available/cas.conf /etc/nginx/sites-enabled/cas.conf

# 4) Doğrula — reload ETMEDEN önce
nginx -t
ls -l /etc/nginx/sites-enabled/cas.conf          # -> symlink görünmeli
grep -c 'api/v2' /etc/nginx/sites-enabled/cas.conf   # -> 1

# 5) nginx -t OK ise reload (reload, restart değil: açık bağlantılar korunur)
systemctl reload nginx

# 6) Üç yüzey de ayakta mı
curl -s -o /dev/null -w '%{http_code} /\n'            https://www.casplatform.com/
curl -s -o /dev/null -w '%{http_code} /api/health\n'  https://www.casplatform.com/api/health
curl -s -o /dev/null -w '%{http_code} /api/v2/health\n' https://www.casplatform.com/api/v2/health
```

Geri alma: `rm /etc/nginx/sites-enabled/cas.conf && cp -a <enabled yedeği>
/etc/nginx/sites-enabled/cas.conf && nginx -t && systemctl reload nginx`

---

## I) cas-api-staging.service: ProtectSystem=full -> strict

`ReadWritePaths` **bilerek yok** — servisin diske hiçbir şey yazmadığı üç
ayrı yolla doğrulandı (gerekçe unit dosyasının içinde yazılı).

`systemd-run` ile ön probe'u bu oturumda çalıştıramadım (izin reddedildi).
Bu yüzden aşağıda önce **ayrı portta probe**, sonra gerçek uygulama var.
Probe, çalışan servise dokunmaz.

```bash
# 1) ÖNCE PROBE — 8779'da, gerçek servise dokunmadan
systemd-run --unit=cas-api-strict-probe --uid=cas --gid=cas \
  --property=WorkingDirectory=/opt/cas_staging/cas_api \
  --property=EnvironmentFile=/opt/cas_staging/.env \
  --property=Environment=CAS_HOME=/opt/cas_staging \
  --property=Environment=ENVIRONMENT=staging \
  --property=Environment=PYTHONDONTWRITEBYTECODE=1 \
  --property=NoNewPrivileges=yes --property=PrivateTmp=yes \
  --property=ProtectHome=yes --property=ProtectSystem=strict \
  /usr/bin/python3 -m uvicorn main:app --host 127.0.0.1 --port 8779 --workers 1

# 2) 25 sn bekle (her worker XGBoost + SHAP yüklüyor), sonra endpoint'e bak.
#    `systemctl is-active` worker hazır olmadan 'active' der — ona güvenme.
for i in $(seq 1 12); do sleep 5; curl -sf http://127.0.0.1:8779/api/v2/health && break; done; echo
journalctl -u cas-api-strict-probe --no-pager | grep -iE "read-only|erofs|permission denied|traceback" || echo "[ok] sandbox ihlali yok"

# 3) Probe'u kaldır
systemctl stop cas-api-strict-probe 2>/dev/null; systemctl reset-failed cas-api-strict-probe 2>/dev/null

# --- Probe temizse gerçek uygulama ---

# 4) Yedekle + yeni unit'i koy
cp -a /etc/systemd/system/cas-api-staging.service /root/nginx_backups/cas-api-staging.service.bak.$(date +%Y%m%d_%H%M%S)
cp /opt/cas_staging/deploy/prepared/cas-api-staging.service /etc/systemd/system/cas-api-staging.service
chown root:root /etc/systemd/system/cas-api-staging.service
chmod 644       /etc/systemd/system/cas-api-staging.service

systemctl daemon-reload
systemctl restart cas-api-staging

# 5) 25 saniye kuralı — unit'e değil endpoint'e bak
for i in $(seq 1 12); do sleep 5; curl -sf http://127.0.0.1:8776/api/v2/health && break; done; echo
systemd-analyze security cas-api-staging | tail -5
journalctl -u cas-api-staging -n 50 --no-pager | grep -iE "read-only|erofs|permission denied" || echo "[ok] sandbox ihlali yok"
```

Geri alma: `cp -a <unit yedeği> /etc/systemd/system/cas-api-staging.service &&
systemctl daemon-reload && systemctl restart cas-api-staging`

Not: aynı sertleştirme production'ın `cas-api.service`'ine **henüz
uygulanmadı**. Staging'de bir hafta sorunsuz durduktan sonra oraya da
taşınmalı — ama production unit'i iki worker çalıştırıyor, ayrıca doğrulanmalı.
