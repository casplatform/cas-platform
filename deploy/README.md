# Deployment configuration

Reference copies of system configuration that lives outside the repository.
These are **not** applied automatically — they record the deployed state.

| File | Deployed location |
|---|---|
| `nginx-cas.conf` | `/etc/nginx/sites-available/cas.conf` (`sites-enabled/cas.conf` is a symlink to it) |
| `cas-api-staging.service` | `/etc/systemd/system/cas-api-staging.service` |

The other three units — `cas`, `cas-api`, `cas-staging` — have no copy here yet.

## These copies are only worth what their last check is worth

A reference copy that has drifted is worse than no copy: it invites a "restore
from the repo" that quietly reverts the live system. Both failures below were
real, and both were found by diffing rather than by reading:

- `nginx-cas.conf` sat 4.5 months behind and still carried the exposure claims
  that 2026-08-19 corrected — "Nginx only listens on 127.0.0.1:80 — never
  exposed publicly", which is not what `listen 80;` does. Refreshed 2026-08-27.
- A `prepared/` directory held an already-applied `cas-api-staging.service`
  whose `ExecStart` was still `/usr/bin/python3`. Applying it would have
  reverted the staging API to the system interpreter and undone the venv
  migration. Deleted 2026-08-27; the record of what it applied is in the git
  history and in the live nginx file's own header.

So check before trusting, and after any change on either side:

```bash
diff /etc/nginx/sites-available/cas.conf        deploy/nginx-cas.conf
diff /etc/systemd/system/cas-api-staging.service deploy/cas-api-staging.service
```

**Nothing pending goes in this directory.** It holds what *is* deployed, never
what might be — that is exactly what `prepared/` got wrong. A change waiting to
be applied belongs in the message that asks for it; the copy here is updated
once the change is live.

## Applying an nginx change

1. Edit the copy here, commit
2. Write to a temp file, not into `sites-enabled/` — nginx reads every file in
   that directory, so a stray `.bak` or `.new` there breaks the config
3. `nginx -t` to validate, then move into place, then `systemctl reload nginx`
4. Keep timestamped backups in `/root/nginx_backups/`
5. `diff` both ways afterwards, as above
