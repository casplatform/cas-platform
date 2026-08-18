"""Alembic environment for CAS.

Two things differ from the generated default.

The database URL is not in alembic.ini. It is read here from the instance's
.env, resolved through CAS_HOME, so the checked-in config carries no credential
and the same file drives production and staging against different databases --
the migration you rehearse in staging is run by identical configuration.

target_metadata is None because CAS has no ORM models; every table is created
with explicit SQL. That means --autogenerate cannot work, and migrations are
written by hand. This is deliberate: autogenerate infers intent from a diff,
and a diff cannot tell a rename from a drop-and-add.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _db_url() -> str:
    """DB_URL from the environment, falling back to the instance .env.

    Environment first: systemd injects it per instance, and a staging process
    must never pick up production's file. The .env fallback is for running
    alembic from a shell, where nothing is injected.

    There is deliberately NO default for CAS_HOME here, unlike core.paths and
    cas_engine.py. Those two are loaded by systemd units that always set it,
    and their /opt/cas default is what preserves production behaviour when it
    is absent. Alembic is the opposite case: it is run by hand, from whatever
    directory the operator happens to be standing in, and it writes DDL. An
    operator in /opt/cas_staging who forgot to export CAS_HOME would have
    silently migrated production -- the default would pick the more dangerous
    of the two instances precisely when the operator had shown they were not
    thinking about which one they meant. Being unable to run without saying
    which database you mean is the point.
    """
    url = os.environ.get("DB_URL")
    if url:
        return url

    cas_home = (os.environ.get("CAS_HOME") or "").rstrip("/")
    if not cas_home:
        raise RuntimeError(
            "Neither DB_URL nor CAS_HOME is set, and alembic will not guess "
            "which instance to migrate. Name the target explicitly:\n"
            "  CAS_HOME=/opt/cas_staging python3 -m alembic current   # staging\n"
            "  CAS_HOME=/opt/cas         python3 -m alembic current   # PRODUCTION\n"
            "or set DB_URL directly."
        )
    env_file = os.path.join(cas_home, ".env")
    try:
        for line in open(env_file):
            line = line.strip()
            if line.startswith("DB_URL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass

    raise RuntimeError(
        f"CAS_HOME={cas_home} but DB_URL was not found in {env_file}. "
        f"Set DB_URL, or point CAS_HOME at the instance you mean to migrate."
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
