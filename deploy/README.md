# Deployment configuration

Reference copies of system configuration that lives outside the repository.
These are **not** applied automatically — they document the deployed state.

| File | Deployed location |
|---|---|
| `nginx-cas.conf` | `/etc/nginx/sites-enabled/cas.conf` |

## Applying an nginx change

1. Edit the copy here, commit
2. Write to a temp file, not into `sites-enabled/` — nginx reads every file in
   that directory, so a stray `.bak` or `.new` there breaks the config
3. `nginx -t` to validate, then move into place, then `systemctl reload nginx`
4. Keep timestamped backups in `/root/nginx_backups/`
