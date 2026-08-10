-- CAS Database Bootstrap
-- Run as postgres superuser:
--   sudo -u postgres psql -f schema.sql
--
-- Safe to re-run: all statements use IF NOT EXISTS / DO $$ blocks.

-- ── 1. Database & role ──────────────────────────────────────────────────────
SELECT 'CREATE DATABASE cas_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'cas_db')\gexec

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cas_user') THEN
    CREATE ROLE cas_user LOGIN PASSWORD 'CHANGE_ME';
  END IF;
END$$;

GRANT ALL PRIVILEGES ON DATABASE cas_db TO cas_user;

-- ── 2. Connect to the new database ─────────────────────────────────────────
\connect cas_db

-- Allow cas_user to use the public schema
GRANT USAGE  ON SCHEMA public TO cas_user;
GRANT CREATE ON SCHEMA public TO cas_user;

-- ── 3. conjunction_events — append-only CDM history ────────────────────────
CREATE TABLE IF NOT EXISTS conjunction_events (
    id          SERIAL PRIMARY KEY,

    -- When this row was fetched from Space-Track
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Identifiers
    cdm_id      TEXT,
    norad1      TEXT,
    norad2      TEXT,
    sat1        TEXT,
    sat2        TEXT,

    -- Time of Closest Approach (UTC)
    tca         TIMESTAMPTZ,

    -- Conjunction metrics
    miss_dist_m REAL,           -- metres
    pc          DOUBLE PRECISION,  -- collision probability (0–1)
    risk        TEXT CHECK (risk IN ('RED', 'YELLOW', 'GREEN', 'UNKNOWN')),

    -- Full CDM payload for forward-compatibility
    raw_json    JSONB NOT NULL DEFAULT '{}'
);

-- ── 4. Indexes ──────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ce_fetched_at ON conjunction_events (fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_ce_risk       ON conjunction_events (risk);
CREATE INDEX IF NOT EXISTS idx_ce_tca        ON conjunction_events (tca);
CREATE INDEX IF NOT EXISTS idx_ce_norad_pair ON conjunction_events (norad1, norad2);

-- Partial index: fast lookup of RED conjunctions only
CREATE INDEX IF NOT EXISTS idx_ce_red
    ON conjunction_events (fetched_at DESC)
    WHERE risk = 'RED';

-- ── 5. Grant table privileges to cas_user ──────────────────────────────────
GRANT SELECT, INSERT ON conjunction_events       TO cas_user;
GRANT USAGE, SELECT  ON SEQUENCE conjunction_events_id_seq TO cas_user;

-- ── 6. Verify ───────────────────────────────────────────────────────────────
\echo '--- Schema created. Tables in cas_db.public: ---'
\dt public.*


-- ───────────────────────────────────────────────────
-- Contact form submissions (v1.0 — landing page)
-- ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contact_submissions (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(200) NOT NULL,
    email           VARCHAR(320) NOT NULL,
    organization    VARCHAR(200),
    subject         VARCHAR(50) NOT NULL,
    message         TEXT NOT NULL,
    ip_address      VARCHAR(64),
    user_agent      TEXT,
    status          VARCHAR(20) DEFAULT 'new',
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    replied_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_contact_submitted ON contact_submissions(submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_contact_status ON contact_submissions(status);
