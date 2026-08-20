"""Configuration must degrade, not crash.

Two failures on 2026-08-16 came from configuration rather than logic, and no
test caught either:

  - os.environ.get(k, default) returns "" for a key that is present but empty.
    Staging blanks SMTP_* and ST_* on purpose -- credentials a test instance
    must not hold -- and int("") took the engine down at import, before it
    could bind a port.
  - Paths were hard-coded to /opt/cas, so a second instance could not exist
    without silently reaching into production's tree.

A third instance of the same configuration bug reached CI on 2026-08-20:
decision_scanner.py opened "/opt/cas/.env" at module scope, and
tests/integration/test_decision_logic.py imports it for two pure functions. On
the server the file exists, so nothing ever failed; on a runner, where
/opt/cas does not exist at all, it raised FileNotFoundError during collection.
The guard below covered cas_api/ only, which is exactly why it missed.

The regression guards below read the source rather than call it. That is
deliberate: the bug was a *pattern*, and the point is to fail when someone
reintroduces it, not only when a particular call site misbehaves.
"""
import ast
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
    @pytest.fixture(autouse=True)
    def _restore_paths(self):
        """Put core.paths back the way the session found it.

        Every test in this class reloads the module under a different CAS_HOME,
        and the last reload in each one ran with CAS_HOME deleted -- so
        core.paths kept the production defaults for the rest of the session.
        tests/test_module_imports.py is collected after this file and imports
        core.config, whose pydantic Settings binds
        env_file=core.paths.CAS_ENV_FILE at class-creation time: the staging
        suite was therefore validating a configuration pointed at production's
        .env. It never failed, because pydantic-settings tolerates a missing
        env_file and the server has that file. monkeypatch restores the
        environment variable; only a reload restores the module.
        """
        import importlib
        import core.paths as paths
        # CAS_HOME is captured and restored here rather than left to
        # monkeypatch: pytest set monkeypatch up before this autouse fixture,
        # so monkeypatch's teardown runs after this one and the reload below
        # would still see the deleted variable.
        saved = os.environ.get("CAS_HOME")
        yield
        if saved is None:
            os.environ.pop("CAS_HOME", None)
        else:
            os.environ["CAS_HOME"] = saved
        importlib.reload(paths)

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


# Trees whose files are loaded by a running instance, walked in full. A path
# literal in any of them resolves against production regardless of which
# instance is executing.
SCANNED_TREES = ("cas_api", os.path.join("ml", "src"))

# Root-level scripts that name /opt/cas deliberately. These are the one-shot
# production patchers: they rewrite files under /opt/cas and restart the
# service, with no test, no gate and no rollback. Each one now refuses to run
# (exit 2) and is kept only as a record of what was shipped before
# scripts/deploy.sh existed. /opt/cas is their *target*, not an instance root
# they failed to resolve -- rewriting them to CAS_HOME would aim a disabled
# production patcher at staging, which is worse than leaving them alone.
EXCLUDED_ROOT_SCRIPTS = {
    "deploy_directory.py",
    "deploy_launches.py",
    "setup_plans_account.py",
}


def _scanned_python_files():
    """Root-level .py files plus everything under SCANNED_TREES, REPO-relative.

    Discovered rather than listed. The list this replaced named six files and
    was written when those six were the ones that read a path; cas_api/services/
    maneuver_sim.py was added later, opened "/opt/cas/.env" directly, and the
    test stayed green because nobody remembered to extend a constant. A new
    file is now covered the moment it exists.
    """
    found = []
    for name in sorted(os.listdir(REPO)):
        if name.endswith(".py") and name not in EXCLUDED_ROOT_SCRIPTS:
            found.append(name)
    for sub in SCANNED_TREES:
        base = os.path.join(REPO, sub)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith(".py"):
                    found.append(os.path.relpath(os.path.join(root, name), REPO))
    return sorted(found)


def _hardcoded_prod_paths(path):
    """String constants naming /opt/cas, excluding docstrings and CAS_HOME defaults.

    Parsed rather than grepped, for one reason: docstrings have to be exempt.
    Half these scripts document the real crontab in theirs --

        0 0,8,16 * * *   /usr/bin/python3 /opt/cas/fetch_cdm.py

    -- and that line is a true statement about production's crontab that must
    keep saying /opt/cas. Comments are exempt for the same reason and fall out
    of the AST for free. What is left is the code, where the literal is the bug.
    """
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)

    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            docstrings.add(id(node.body[0].value))

    lines = src.splitlines()
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings or "/opt/cas" not in node.value:
            continue
        line = lines[node.lineno - 1]
        # A literal that is the *default* of a CAS_HOME lookup is how the root
        # gets defined in the first place -- os.environ.get("CAS_HOME",
        # "/opt/cas"). What must not appear is /opt/cas used independently of it.
        if "CAS_HOME" in line:
            continue
        offenders.append("%d: %s" % (node.lineno, line.strip()))
    return offenders


class TestNoCrossInstancePaths:
    """No module may name /opt/cas outside a CAS_HOME default.

    SCOPE: root-level *.py, cas_api/** and ml/src/**, all walked in full.

    This used to cover cas_api/ only, and only sys.path insertions and .env
    reads. Both limits were deliberate and both were wrong:

      - The cas_api/-only scope was justified by "root scripts are cron entry
        points invoked by absolute path, one crontab per instance, so the
        literal and the caller agree". True of cron. Not true of pytest, which
        imports decision_scanner.py, rank_debris.py and eusst_sync.py from
        whichever tree it is running in -- and not true of a CI runner, which
        has no /opt/cas for the literal to agree with. That is the 2026-08-20
        collection error in the module docstring above.
      - The sys.path/.env-only rule let cache reads through, so
        cas_api/services/maneuver_sim.py and mission_design.py both read
        production's .spacetrack_catalog_cache.json from a staging request, and
        ml/src/canonical_scoring.py -- which cas-api imports at startup --
        loaded production's model files. Same class of bug, different file
        opened, invisible to the narrower pattern.

    NOT covered, each for a stated reason:

      - tests/smoke/: those tests point at production on purpose. Freshness is
        a property of the instance that WRITES the file, so the check has to
        read production's copy no matter where it runs.
      - migrations/env.py: deliberately has no CAS_HOME default. It is run by
        hand and emits DDL, so it must fail rather than guess an instance.
      - EXCLUDED_ROOT_SCRIPTS: see the comment on that constant.
      - Shell scripts: not parsed here. scripts/backup_db.sh already resolves
        CAS_HOME itself; deploy.sh names both trees because moving code between
        them is its whole job.
    """

    @pytest.mark.parametrize("relpath", _scanned_python_files())
    def test_no_literal_opt_cas(self, relpath):
        path = os.path.join(REPO, relpath)
        if not os.path.exists(path):
            pytest.skip("%s not present" % relpath)
        offenders = _hardcoded_prod_paths(path)
        assert not offenders, (
            "%s names /opt/cas in code. Resolve it from CAS_HOME "
            "(core.paths.CAS_HOME under cas_api/, the _CAS_HOME idiom "
            "elsewhere) so a second instance -- and a checkout with no "
            "/opt/cas at all -- stays in its own tree:\n  %s"
            % (relpath, "\n  ".join(offenders)))

    def test_discovery_found_files(self):
        """Guard the guard: an empty list would make every case above vacuous."""
        found = _scanned_python_files()
        assert len(found) >= 60, "only %d files discovered under %s" % (
            len(found), REPO)
        assert "decision_scanner.py" in found, (
            "the root scripts are not being scanned -- this is the exact gap "
            "that let decision_scanner.py break CI on 2026-08-20")
        assert any(f.startswith("cas_api" + os.sep) for f in found)
        assert any(f.startswith(os.path.join("ml", "src")) for f in found)

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
