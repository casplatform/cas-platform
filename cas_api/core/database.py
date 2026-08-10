"""PostgreSQL connection pool — psycopg2 ile."""
import os
from contextlib import contextmanager
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor

from core.config import settings


_pool: ThreadedConnectionPool | None = None


def init_pool(min_size: int = 2, max_size: int = 10) -> None:
    global _pool
    if _pool is not None:
        return
    if not settings.db_url:
        raise RuntimeError("DB_URL config'de ayarlı değil")
    _pool = ThreadedConnectionPool(
        minconn=min_size,
        maxconn=max_size,
        dsn=settings.db_url,
    )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn():
    if _pool is None:
        raise RuntimeError("DB pool init edilmemiş")
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


@contextmanager
def get_dict_cursor():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur


def health_check() -> bool:
    try:
        with get_dict_cursor() as cur:
            cur.execute("SELECT 1 as ping")
            row = cur.fetchone()
            return row is not None and row.get("ping") == 1
    except Exception:
        return False
