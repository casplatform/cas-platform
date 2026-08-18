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
    def test_defaults_to_opt_cas(self, monkeypatch):
        """An unset CAS_HOME must reproduce the pre-2026-08-16 literal."""
        import importlib
        import core.paths as paths
        # monkeypatch, not os.environ.pop: the conftest sets CAS_HOME to the
        # tree under test, and popping it outright leaked into every later
        # test in the session.
        monkeypatch.delenv("CAS_HOME", raising=False)
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


def _cas_api_python_files():
    """Every .py file under cas_api/, relative to REPO.

    Discovered rather than listed. The list this replaced named six files and
    was written when those six were the ones that read a path; cas_api/services/
    maneuver_sim.py was added later, opened "/opt/cas/.env" directly, and the
    test stayed green because nobody remembered to extend a constant. A new
    file is now covered the moment it exists.
    """
    found = []
    for root, dirs, files in os.walk(CAS_API):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name.endswith(".py"):
                found.append(os.path.relpath(os.path.join(root, name), REPO))
    return sorted(found)


class TestNoCrossInstancePaths:
    """sys.path insertions and .env reads must not name /opt/cas literally.

    These are the two ways a second instance ends up running production's code
    or reporting into production's database. Other hard-coded paths (caches,
    logs, static assets) do not cross instances the same way and are not
    covered here.

    SCOPE: cas_api/ only, walked in full.

    Root-level modules -- cas_engine.py and the cron scripts beside it
    (eusst_sync.py, space_weather_sync.py, fetch_cdm.py, ...) -- are NOT
    covered, and several of them do still name /opt/cas literally:
    eusst_sync.py sets ENV_PATH = Path("/opt/cas/.env"), space_weather_sync.py
    inserts "/opt/cas/cas_api" on sys.path. That is a real gap, left open
    deliberately rather than by oversight: those scripts are cron entry points
    invoked by absolute path, one crontab per instance, so the literal and the
    caller agree today. cas_api/ is different -- it is a library tree imported
    by whichever service loads it, so a literal there resolves against
    production no matter which instance is running.

    Do not read a pass here as "no instance can cross". It means no file under
    cas_api/ crosses. Extending this to the root scripts means fixing them
    first; the test would fail on them today.
    """

    @pytest.mark.parametrize("relpath", _cas_api_python_files())
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


class TestTestsImportTheirOwnTree:
    """A test run must exercise the tree the tests live in.

    tests/conftest.py puts INSTANCE_ROOT on sys.path, but ten `sys.path.insert(
    0, "/opt/cas")` lines in six test modules landed *after* it and won for the
    rest of the session: collection order put tests/integration first, so by the
    time the unit tests ran, `import cas_engine` resolved to production. The
    staging suite was green about code it had never loaded, and a staging-only
    change could not fail it.

    The check is on the imported module's __file__ rather than on source text,
    because the failure was an ordering effect no single line reveals.
    """

    IMPORTS = ["cas_engine", "vleo", "rank_debris", "decision_scanner"]

    @pytest.mark.parametrize("modname", IMPORTS)
    def test_module_resolves_inside_repo(self, modname):
        mod = pytest.importorskip(modname)
        path = os.path.abspath(getattr(mod, "__file__", "") or "")
        assert path, f"{modname} has no __file__"
        assert path.startswith(REPO + os.sep), (
            f"{modname} imported from {path}, outside the tree under test "
            f"({REPO}). Something put another instance's root earlier on "
            f"sys.path -- use INSTANCE_ROOT from tests/conftest.py, never a "
            f"literal path.\nsys.path[0:5] = {sys.path[0:5]}"
        )

    def test_syspath_has_no_foreign_instance_root(self):
        """No other CAS install tree may sit on sys.path during a test run."""
        foreign = []
        for entry in sys.path:
            ap = os.path.abspath(entry) if entry else ""
            if not ap or ap.startswith(REPO + os.sep) or ap == REPO:
                continue
            # Another instance is any path holding a sibling CAS tree: it has a
            # cas_engine.py at its root, or is a subdir of one that does.
            probe = ap
            for _ in range(3):
                if os.path.exists(os.path.join(probe, "cas_engine.py")):
                    foreign.append(entry)
                    break
                probe = os.path.dirname(probe)
        assert not foreign, (
            f"sys.path contains another CAS instance: {foreign}. Tests must "
            f"stay inside {REPO}.")


class TestDataHealthPrefersEnvironment:
    """data_health writes rows and sends mail; reading another instance's .env
    would mean staging reporting into the production database."""

    def test_environment_wins_over_file(self, monkeypatch):
        import importlib
        import core.data_health as dh
        monkeypatch.setenv("DB_URL", "postgresql://sentinel-value/only")
        importlib.reload(dh)
        assert dh._load_env().get("DB_URL") == "postgresql://sentinel-value/only"
