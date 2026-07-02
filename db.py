"""Postgres connection + inventory schema (DATABASE_URL in .env)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

import bootstrap  # noqa: F401 — loads .env

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_watches (
    id TEXT PRIMARY KEY,
    brand TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    subtitle TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    case_material TEXT NOT NULL DEFAULT '',
    dial TEXT NOT NULL DEFAULT '',
    case_size_mm INTEGER,
    stones TEXT NOT NULL DEFAULT '',
    price_usd INTEGER,
    price_display TEXT NOT NULL DEFAULT '',
    in_stock BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL DEFAULT 'available',
    image_url TEXT NOT NULL,
    product_url TEXT NOT NULL DEFAULT '',
    inquire_url TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def postgres_enabled() -> bool:
    return bool(database_url())


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


@contextmanager
def get_connection():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(_normalize_url(database_url()), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    if not postgres_enabled():
        return
    with get_connection() as conn:
        conn.execute(_SCHEMA)
    logger.info("Postgres inventory schema ready")


def seed_inventory_if_empty(rows: list[dict[str, Any]]) -> int:
    if not postgres_enabled() or not rows:
        return 0
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM inventory_watches").fetchone()["n"]
        if count:
            return 0
        for row in rows:
            conn.execute(
                """
                INSERT INTO inventory_watches (
                    id, brand, model, title, subtitle, reference,
                    case_material, dial, case_size_mm, stones,
                    price_usd, price_display, in_stock, status,
                    image_url, product_url, inquire_url
                ) VALUES (
                    %(id)s, %(brand)s, %(model)s, %(title)s, %(subtitle)s, %(reference)s,
                    %(case_material)s, %(dial)s, %(case_size_mm)s, %(stones)s,
                    %(price_usd)s, %(price_display)s, %(in_stock)s, %(status)s,
                    %(image_url)s, %(product_url)s, %(inquire_url)s
                )
                ON CONFLICT (id) DO NOTHING
                """,
                row,
            )
    logger.info("Seeded %d inventory watches", len(rows))
    return len(rows)


def db_health() -> dict[str, Any]:
    if not postgres_enabled():
        return {"enabled": False, "ok": False}
    try:
        with get_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM inventory_watches").fetchone()["n"]
        return {"enabled": True, "ok": True, "count": int(n)}
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return {"enabled": True, "ok": False, "error": str(exc)}
