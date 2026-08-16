"""Configuration must degrade, not crash.

Two failures on 2026-08-16 came from configuration rather than logic, and no
test caught either:

  - os.environ.get(k, default) returns "" for a key that is present but empty.
    Staging blanks SMTP_* and ST_* on purpose -- credentials a test instance
    must not hold -- and int("") took the engine down at import, before it
    could bind a port.
  - Paths were hard-coded to /opt/cas, so a second instance could not exist
    without silently reaching into production's tree.

The regression guards below read the source rather than call it. That is
deliberate: the bug was a *pattern*, and the point is to fail when someone
reintroduces it, not only when a particular call site misbehaves.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAS_API = os.path.join(REPO, "cas_api")
if CAS_API not in sys.path:
    sys.path.insert(0, CAS_API)

# Files that read integers out of the environment.
INT_ENV_FILES = [
    "cas_engine.py", "insurance_watch_cron.py", "relvel_enrich.py",
    "fetch_cdm.py", "ml_enrich.py",
]

# int(os.environ.get("KEY", "default")) -- crashes on an empty value.
BAD_INT_PATTERN = re.compile(r'int\(\s*os\.environ\.get\(\s*["\'][A-Z_0-9]+["\']\s*,')


class TestEmptyEnvIsTreatedAsUnset:
    @pytest.mark.parametrize("fname", INT_ENV_FILES)
    def test_no_two_arg_int_env_reads(self, fname):
        path = os.path.join(REPO, fname)
        if not os.path.exists(path):
            pytest.skip(f"{fname} not present")
        hits = BAD_INT_PATTERN.findall(open(path).read())
        assert not hits, (
            f"{fname}: int(os.environ.get(KEY, default)) returns '' when KEY is "
            f"set-but-empty and raises ValueError. Use "
            f"int(os.environ.get(KEY) or default). Occurrences: {len(hits)}"
        )

    def test_or_default_pattern_survives_empty_string(self, monkeypatch):
        monkeypatch.setenv("CAS_TEST_PORT", "")
        assert int(os.environ.get("CAS_TEST_PORT") or "8765") == 8765

    def test_or_default_pattern_respects_real_value(self, monkeypatch):
        monkeypatch.setenv("CAS_TEST_PORT", "8775")
        assert int(os.environ.get("CAS_TEST_PORT") or "8765") == 8775


class TestInstanceRoot:
    def test_defaults_to_opt_cas(self):
        """An unset CAS_HOME must reproduce the pre-2026-08-16 literal."""
        import importlib
        import core.paths as paths
        os.environ.pop("CAS_HOME", None)
        importlib.reload(paths)
        assert paths.CAS_HOME == "/opt/cas"
        assert paths.CAS_API_HOME == "/opt/cas/cas_api"
        assert paths.CAS_ENV_FILE == "/opt/cas/.env"

    def test_honours_override(self, monkeypatch):
        import importlib
        import core.paths as paths
        monkeypatch.setenv("CAS_HOME", "/opt/cas_staging")
        importlib.reload(paths)
        assert paths.CAS_HOME == "/opt/cas_staging"
        assert paths.CAS_API_HOME == "/opt/cas_staging/cas_api"
        assert paths.CAS_ENV_FILE == "/opt/cas_staging/.env"
        monkeypatch.delenv("CAS_HOME")
        importlib.reload(paths)

    def test_trailing_slash_is_normalised(self, monkeypatch):
        import importlib
        import core.paths as paths
        monkeypatch.setenv("CAS_HOME", "/opt/cas_staging/")
        importlib.reload(paths)
        assert paths.CAS_HOME == "/opt/cas_staging"
        monkeypatch.delenv("CAS_HOME")
        importlib.reload(paths)


class TestNoCrossInstancePaths:
    """sys.path insertions and .env reads must not name /opt/cas literally.

    These are the two ways a second instance ends up running production's code
    or reporting into production's database. Other hard-coded paths (caches,
    logs, static assets) do not cross instances the same way and are not
    covered here.
    """
    CROSSING_FILES = [
        os.path.join("cas_api", "services", "launch_screen.py"),
        os.path.join("cas_api", "services", "vleo_service.py"),
        os.path.join("cas_api", "services", "mission_design.py"),
        os.path.join("cas_api", "core", "data_health.py"),
        os.path.join("cas_api", "core", "config.py"),
    ]

    @pytest.mark.parametrize("relpath", CROSSING_FILES)
    def test_no_literal_opt_cas_in_syspath_or_env(self, relpath):
        path = os.path.join(REPO, relpath)
        if not os.path.exists(path):
            pytest.skip(f"{relpath} not present")
        offenders = []
        for i, line in enumerate(open(path), 1):
            if line.lstrip().startswith("#"):
                continue
            if '"/opt/cas' not in line and "'/opt/cas" not in line:
                continue
            # A literal that is the *default* of a CAS_HOME lookup is fine --
            # that is how the root is defined in the first place. What must not
            # appear is /opt/cas used independently of it.
            if "CAS_HOME" in line:
                continue
            if "sys.path" in line or ".env" in line or "env_file" in line:
                offenders.append(f"{i}: {line.strip()}")
        assert not offenders, (
            f"{relpath} names /opt/cas in a sys.path or .env reference; use "
            f"core.paths.CAS_HOME so a second instance stays in its own tree:\n  "
            + "\n  ".join(offenders)
        )

    def test_engine_defines_instance_root(self):
        src = open(os.path.join(REPO, "cas_engine.py")).read()
        assert "_CAS_HOME" in src and "_CAS_API_HOME" in src


class TestDataHealthPrefersEnvironment:
    """data_health writes rows and sends mail; reading another instance's .env
    would mean staging reporting into the production database."""

    def test_environment_wins_over_file(self, monkeypatch):
        import importlib
        import core.data_health as dh
        monkeypatch.setenv("DB_URL", "postgresql://sentinel-value/only")
        importlib.reload(dh)
        assert dh._load_env().get("DB_URL") == "postgresql://sentinel-value/only"
