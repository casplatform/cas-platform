"""password_resets: one definition instead of two

Revision ID: 0002_password_resets
Revises: 0001_baseline
Create Date: 2026-08-17

cas_engine created this table inline, in two places, with definitions that did
not agree:

  line 3416  token VARCHAR(128) UNIQUE, user_id ... ON DELETE CASCADE
  line 6464  token TEXT (no unique),    user_id ... (no cascade)

Both used CREATE TABLE IF NOT EXISTS, so the schema would have been decided by
whichever request arrived first -- a password-reset request from an admin panel
or one from the public endpoint. The table does not exist yet in any
environment, which is the only reason this is a clean fix rather than a
migration of divergent live data.

The stricter definition wins on both points. UNIQUE on token is a security
property, not a nicety: the reset flow looks a user up by token alone, so two
rows sharing one would be an account-takeover path. ON DELETE CASCADE means
deleting a user cannot fail on a dangling token, and leaves no reset link alive
for an account that no longer exists.

TEXT rather than VARCHAR(128): tokens are secrets.token_urlsafe(32), 43
characters, and a length cap adds nothing PostgreSQL does not already do.

No extra index. The only read is
  WHERE token = %s AND used = FALSE AND expires_at > NOW()
and the unique index on token already serves it.
"""
from alembic import op

revision = "0002_password_resets"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.password_resets (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            token       TEXT NOT NULL UNIQUE,
            expires_at  TIMESTAMPTZ NOT NULL,
            used        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.password_resets")
