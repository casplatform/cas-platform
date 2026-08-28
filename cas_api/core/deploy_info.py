"""Which commit is this instance actually serving.

The hand-written version literals were never wrong so much as meaningless:
cas_engine reported "0.7" and cas_api "0.1.0", both unchanged since the initial
commit on 2026-08-10, across 27 recorded deploys. Neither answered the question
anyone actually asks during an incident -- is the fix live yet? Yesterday that
question had to be answered with `ss -ltn`, because no endpoint could.

scripts/deploy.sh already knows the commit it is shipping, so it writes this
file at the moment production moves and on both rollback paths. Nothing is
compiled in and there is no build step; the file is data, written once per
deploy, next to the tree it describes.

MISSING FILE IS NORMAL, NOT AN ERROR. A developer checkout, a fresh install, or
an instance that has never been deployed to has no marker, and every field comes
back None. Callers report "unknown" and carry on -- refusing to serve /health
because a metadata file is absent would turn a cosmetic gap into an outage.
"""
import json
import os

_MARKER_NAME = ".deploy_version.json"
_cache = {"mtime": None, "data": None}


def deploy_info(cas_home=None):
    """{'commit', 'commit_short', 'deployed_at', 'ref'} -- values may be None.

    Re-read when the file changes: the services are long-running and a deploy
    restarts them, but --rollback rewrites this file too, and a stale answer
    from a cached read is the specific failure this whole file exists to
    prevent.
    """
    home = cas_home or os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
    path = os.path.join(home, _MARKER_NAME)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"commit": None, "commit_short": None, "deployed_at": None, "ref": None}
    if _cache["mtime"] != mtime:
        try:
            with open(path) as f:
                data = json.load(f)
            _cache["data"] = {
                "commit": data.get("commit"),
                "commit_short": (data.get("commit") or "")[:7] or None,
                "deployed_at": data.get("deployed_at"),
                "ref": data.get("ref"),
            }
            _cache["mtime"] = mtime
        except Exception:
            return {"commit": None, "commit_short": None, "deployed_at": None, "ref": None}
    return dict(_cache["data"])
