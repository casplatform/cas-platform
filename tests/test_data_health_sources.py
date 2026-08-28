"""SOURCES entries must carry the fields the health logic depends on.

Every field here has a failure mode behind it:

  since     A source with no "since" can never leave "unknown", because that
            date is the only record of when it was added -- rows are written
            by report_success/report_failure and by nothing else. Missing it
            would restore the exact silence get_health()'s never_ran state
            exists to break, and it would do so quietly.
  interval  Both staleness rules are multiples of it. Without it a source can
            be neither stale nor overdue.
  label     Goes into the alert mail and the customer banner.

These are unit tests: no database, no network. They read a dict.
"""
import datetime
import os
import re
import sys

import pytest

_CAS_HOME = os.environ.get("CAS_HOME") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
_API = os.path.join(_CAS_HOME, "cas_api")
if _API not in sys.path:
    sys.path.insert(0, _API)

from core.data_health import SOURCES  # noqa: E402


def _ids():
    return sorted(SOURCES)


@pytest.mark.parametrize("source", _ids())
def test_source_has_label(source):
    label = SOURCES[source].get("label")
    assert label and isinstance(label, str), "%s: label eksik" % source


@pytest.mark.parametrize("source", _ids())
def test_source_has_positive_interval(source):
    interval = SOURCES[source].get("interval")
    assert isinstance(interval, int) and interval > 0, (
        "%s: interval bir pozitif tamsayi olmali (dakika)" % source)


@pytest.mark.parametrize("source", _ids())
def test_source_has_valid_since(source):
    """Bu testin tek isi, 'since' eklemeyi unutmayi imkansiz kilmak."""
    since = SOURCES[source].get("since")
    assert since, (
        "%s: 'since' eksik. Kaynagin eklendigi gunu YYYY-MM-DD olarak yazin; "
        "get_health() hic rapor etmemis bir kaynagin ne zamandir bekledigini "
        "baska hicbir yerden ogrenemez." % source)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", since), (
        "%s: 'since' YYYY-MM-DD olmali, su an %r" % (source, since))
    d = datetime.datetime.strptime(since, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc)
    assert d <= datetime.datetime.now(datetime.timezone.utc), (
        "%s: 'since' gelecekte (%s) -- kaynak asla vadesi gecmis sayilmaz"
        % (source, since))
