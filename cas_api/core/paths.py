"""Filesystem root for this CAS instance.

Everything under the install tree is addressed relative to CAS_HOME so a
second instance (staging) can run from a different directory on the same
host without reaching back into production. The default keeps existing
behaviour byte-for-byte: unset CAS_HOME means /opt/cas, which is what every
call site hard-coded before 2026-08-16.
"""
import os

CAS_HOME = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
CAS_API_HOME = os.path.join(CAS_HOME, "cas_api")
CAS_ENV_FILE = os.path.join(CAS_HOME, ".env")
