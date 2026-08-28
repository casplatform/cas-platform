"""The reference copies in deploy/ must match what is installed.

WHY A TEST AND NOT A LINE IN THE README.

deploy/README.md has carried a `diff` command since 2026-08-27, and the reason
it was written is that the previous reference copy had drifted 4.5 months --
long enough that deploy/nginx-cas.conf still claimed "Nginx only listens on
127.0.0.1:80 -- never exposed publicly", which is not what `listen 80;` does.
A documented command only runs when somebody remembers it, and the evidence
that nobody remembered is the drift itself. The same week produced a
prepared/ directory holding an already-applied unit whose ExecStart would have
reverted the venv migration if anyone had copied it back.

The counter-argument is maintenance: this compares against the live machine, so
a legitimate config change turns the suite red until someone refreshes the copy.
That is the point. Refreshing is one `cp`, the failure message names it, and the
alternative -- a copy nobody checks -- is the state that produced both failures
above. A stale reference is worse than no reference: it invites a restore that
quietly reverts production.

Scope is deliberately narrow. These check that two files are identical, nothing
about their contents, so they say nothing about whether the configuration is
correct -- only whether the repository still describes reality.

Every check skips when the installed file is absent, so a CI runner or a
developer checkout (no /etc/systemd, no crontab) reports nothing rather than
failing on a machine where the question is meaningless.
"""
import os
import re
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEPLOY = os.path.join(_REPO, "deploy")

# Same expression the reference copy was filtered with -- see
# deploy/crontab.reference's header. If these two ever disagree the test starts
# comparing different sets of lines and quietly means nothing.
_CAS_LINE = re.compile(r"/opt/cas(_staging)?/")


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


UNITS = [
    ("cas.service", "/etc/systemd/system/cas.service"),
    ("cas-api.service", "/etc/systemd/system/cas-api.service"),
    ("cas-staging.service", "/etc/systemd/system/cas-staging.service"),
    ("cas-api-staging.service", "/etc/systemd/system/cas-api-staging.service"),
]


@pytest.mark.parametrize("name,installed", UNITS, ids=[u[0] for u in UNITS])
def test_unit_matches_reference(name, installed):
    live = _read(installed)
    if live is None:
        pytest.skip("%s kurulu degil (CI ya da gelistirme makinesi)" % installed)
    ref = _read(os.path.join(_DEPLOY, name))
    assert ref is not None, (
        "deploy/%s yok ama %s kurulu. Referans kopyayi ekleyin:\n"
        "    cp -p %s %s/deploy/%s" % (name, installed, installed, _REPO, name))
    assert ref == live, (
        "deploy/%s ile kurulu dosya farkli. Once hangisinin dogru oldugunu "
        "belirleyin, sonra kopyayi tazeleyin:\n"
        "    diff %s %s/deploy/%s\n"
        "    cp -p %s %s/deploy/%s" % (name, installed, _REPO, name,
                                       installed, _REPO, name))


def test_nginx_matches_reference():
    installed = "/etc/nginx/sites-available/cas.conf"
    live = _read(installed)
    if live is None:
        pytest.skip("%s yok" % installed)
    ref = _read(os.path.join(_DEPLOY, "nginx-cas.conf"))
    assert ref is not None, "deploy/nginx-cas.conf yok"
    assert ref == live, (
        "deploy/nginx-cas.conf ile kurulu dosya farkli:\n"
        "    diff %s %s/deploy/nginx-cas.conf" % (installed, _REPO))


def _installed_cron_lines():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                             timeout=10)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [l.rstrip() for l in out.stdout.splitlines()
            if _CAS_LINE.search(l) and not l.lstrip().startswith("#")]


def test_crontab_matches_reference():
    live = _installed_cron_lines()
    if live is None:
        pytest.skip("crontab okunamadi (root degil ya da cron kurulu degil)")
    ref_text = _read(os.path.join(_DEPLOY, "crontab.reference"))
    assert ref_text is not None, "deploy/crontab.reference yok"
    ref = [l.rstrip() for l in ref_text.splitlines()
           if _CAS_LINE.search(l) and not l.lstrip().startswith("#")]
    missing = [l for l in live if l not in ref]
    extra = [l for l in ref if l not in live]
    assert not missing and not extra, (
        "crontab ile deploy/crontab.reference farkli.\n"
        "  kayitli olmayan (canlida var): %s\n"
        "  fazladan (referansta var): %s\n"
        "Tazelemek icin: crontab -l | grep -E '/opt/cas(_staging)?/' "
        "> %s/deploy/crontab.reference  (basligi geri ekleyin)"
        % (missing or "-", extra or "-", _REPO))


def test_cron_d_matches_reference():
    live_dir = "/etc/cron.d"
    if not os.path.isdir(live_dir):
        pytest.skip("/etc/cron.d yok")
    live = sorted(f for f in os.listdir(live_dir) if f.startswith("cas-"))
    if not live:
        pytest.skip("/etc/cron.d altinda cas-* dosyasi yok")
    ref_dir = os.path.join(_DEPLOY, "cron.d")
    assert os.path.isdir(ref_dir), "deploy/cron.d/ yok"
    ref = sorted(os.listdir(ref_dir))
    assert ref == live, (
        "deploy/cron.d/ ile /etc/cron.d/cas-* dosya listesi farkli: "
        "canli=%s referans=%s" % (live, ref))
    for name in live:
        a = _read(os.path.join(live_dir, name))
        b = _read(os.path.join(ref_dir, name))
        assert a == b, (
            "deploy/cron.d/%s ile kurulu dosya farkli:\n"
            "    diff /etc/cron.d/%s %s/deploy/cron.d/%s" % (name, name, _REPO, name))
