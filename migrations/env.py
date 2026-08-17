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
    """
    url = os.environ.get("DB_URL")
    if url:
        return url

    cas_home = os.environ.get("CAS_HOME", "/opt/cas").rstrip("/") or "/opt/cas"
    env_file = os.path.join(cas_home, ".env")
    try:
        for line in open(env_file):
            line = line.strip()
            if line.startswith("DB_URL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass

    raise RuntimeError(
        f"DB_URL not set and not found in {env_file}. "
        f"Set DB_URL, or CAS_HOME to the instance you mean to migrate."
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
