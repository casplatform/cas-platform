# Deployment configuration

Reference copies of system configuration that lives outside the repository.
These are **not** applied automatically — they record the deployed state.

| File | Deployed location |
|---|---|
| `nginx-cas.conf` | `/etc/nginx/sites-available/cas.conf` (`sites-enabled/cas.conf` is a symlink to it) |
| `cas.service` | `/etc/systemd/system/cas.service` |
| `cas-api.service` | `/etc/systemd/system/cas-api.service` |
| `cas-staging.service` | `/etc/systemd/system/cas-staging.service` |
| `cas-api-staging.service` | `/etc/systemd/system/cas-api-staging.service` |
| `crontab.reference` | root's crontab — **CAS lines only** (see below) |
| `cron.d/` | `/etc/cron.d/cas-*` |

## Checked by a test, not by remembering

`tests/smoke/test_config_drift.py` compares every file above against what is
installed. It skips when the installed file is absent, so a CI runner or a
developer checkout reports nothing instead of failing.

This used to be a `diff` command in this README, and the reason it is now a test
is that the command did not get run: `nginx-cas.conf` sat 4.5 months behind
while still claiming *"Nginx only listens on 127.0.0.1:80 — never exposed
publicly"*, which is not what `listen 80;` does. The same week turned up a
`prepared/` directory holding an already-applied unit whose `ExecStart` would
have reverted the venv migration if anyone had copied it back.

The cost is real and accepted: change a unit or a cron line and the suite goes
red until the copy is refreshed. That is the mechanism working. Refreshing is
one `cp`, and the failure message prints the exact command.

A stale reference copy is worse than none — it invites a "restore from the repo"
that quietly reverts production.

## `crontab.reference` is filtered, on purpose

root's crontab also drives Tribun, elarasim and kupam: 13 of its 28 active lines
belong to other systems and have no business in this repository. The copy holds
only lines matching `/opt/cas(_staging)?/`, and `test_config_drift.py` applies
the same expression, so the copy and the check cannot disagree about what counts.

To refresh after a crontab change:

```bash
crontab -l | grep -E '/opt/cas(_staging)?/' > /opt/cas_staging/deploy/crontab.reference
# then put the header comment back at the top
```

**Nothing here installs anything.** `crontab -e`, `systemctl edit` and a
`cp` into `/etc` are still how the real files change; these copies are what
makes such a change visible in git afterwards. Nothing pending goes in this
directory either — it holds what *is* deployed, never what might be, which is
exactly what the deleted `prepared/` directory got wrong.

## Applying an nginx change

1. Edit the copy here, commit
2. Write to a temp file, not into `sites-enabled/` — nginx reads every file in
   that directory, so a stray `.bak` or `.new` there breaks the config
3. `nginx -t` to validate, then move into place, then `systemctl reload nginx`
4. Keep timestamped backups in `/root/nginx_backups/`
5. Run `pytest tests/smoke/test_config_drift.py` — it should be green again

## Not in here

`.deploy_version.json` is written into each instance root by
`scripts/deploy.sh` on every deploy and rollback, and read by `/health` to
report the running commit. It is per-instance state, gitignored, and must stay
that way: deploy gate 2 refuses to run against a dirty production tree, so
tracking it would make the deploy script break the next deploy.
