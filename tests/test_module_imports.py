"""Every cas_api module must actually import.

py_compile and ast.parse only check syntax. On 2026-08-16 a patch added
os.path.join(...) to services/launch_screen.py, which does not import os --
syntactically valid, NameError at runtime. It passed a compile check, was
deployed, and took cas-api down for six minutes because uvicorn imports the
service graph at startup and a single failure kills the process.

These tests import each module for real. They are deliberately cheap: no
network, no database writes, no fixtures beyond the invalid DB_URL the root
conftest already sets. A module that needs a live database to be *imported*
has a design problem this test is right to surface.
"""
import importlib
import os
import pkgutil
import sys

import pytest

CAS_API = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cas_api")
if CAS_API not in sys.path:
    sys.path.insert(0, CAS_API)


def _discover():
    """Module names under cas_api/, excluding __init__ and main."""
    found = []
    for root, dirs, files in os.walk(CAS_API):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py") or f == "__init__.py":
                continue
            rel = os.path.relpath(os.path.join(root, f), CAS_API)
            name = rel[:-3].replace(os.sep, ".")
            # main.py builds the whole FastAPI app and opens a DB pool at
            # import; the app-level smoke tests cover it against a running
            # service instead.
            if name == "main":
                continue
            found.append(name)
    return sorted(found)


MODULES = _discover()


def test_discovery_found_modules():
    """Guard the guard: an empty list would make every test below vacuous."""
    assert len(MODULES) >= 30, f"only {len(MODULES)} modules discovered under {CAS_API}"


@pytest.mark.parametrize("modname", MODULES)
def test_module_imports(modname):
    try:
        importlib.import_module(modname)
    except Exception as e:
        pytest.fail(f"import {modname} failed: {type(e).__name__}: {e}")


def test_paths_module_is_importable_and_sane():
    from core.paths import CAS_HOME, CAS_API_HOME, CAS_ENV_FILE
    assert os.path.isabs(CAS_HOME)
    assert CAS_API_HOME == os.path.join(CAS_HOME, "cas_api")
    assert CAS_ENV_FILE == os.path.join(CAS_HOME, ".env")
