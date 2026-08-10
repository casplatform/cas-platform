# Integration Tests

## Çalıştırma

```bash
# Sadece integration testler
cd /opt/cas && python3 -m pytest tests/integration/ -v

# Sadece auth flow
cd /opt/cas && python3 -m pytest tests/integration/test_auth.py -v

# Tek test
cd /opt/cas && python3 -m pytest tests/integration/test_auth.py::TestLogin::test_login_success -v

# Master runner script
bash /opt/cas/run_tests.sh unit         # 134 unit test
bash /opt/cas/run_tests.sh integration  # integration testler
bash /opt/cas/run_tests.sh all          # ikisi birlikte
```

## Veri İzolasyonu

- Tüm test kullanıcıları `pytest-<random>@cas.test` formatında oluşturulur.
- Production kullanıcılarıyla çakışma riski **sıfır**.
- Her test sonunda `db_committed` fixture cascade cleanup yapar (user_activity, notification_prefs, watchlist_results, decision_results, login_log, admin_log, users).
- Bir test fail olursa cleanup yine çalışır (pytest finalizer).

## DB Stratejisi

- Production `casdb` kullanılır (gerçek schema).
- `db_conn` fixture: rollback-only transaction (hiçbir veri yazılmaz).
- `db_committed` fixture: AUTH.register() gibi commit yapan modüller için, sonunda manuel cleanup.

## Sorun Giderme

**"AUTH_SECRET .env'de yok" → JWT testleri skip:**  
`/opt/cas/.env`'de `AUTH_SECRET` veya `JWT_SECRET` tanımlı olmalı.

**"Integration testleri icin gercek DB_URL gerekli":**  
`.env` dosyası okunamadı veya `DB_URL` boş. `cat /opt/cas/.env | grep DB_URL` ile kontrol.

**Test fail oldu, cleanup yapildi mi?**  
`psql $DB_URL -c "SELECT count(*) FROM users WHERE email LIKE 'pytest-%@cas.test';"` → 0 olmalı.  
Eğer 0 değilse manuel temizlik: `psql $DB_URL -c "DELETE FROM users WHERE email LIKE 'pytest-%@cas.test';"`  
(FK cascade için önce bağlı tablolar temizlenmeli — `cleanup_test_users.sh` ileride eklenebilir.)
