"""Postgres-backed inventory CRUD."""

from __future__ import annotations

from typing import Any

from db import get_connection


def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "brand": row["brand"],
        "model": row["model"] or "",
        "title": row["title"],
        "subtitle": row["subtitle"] or "",
        "reference": row["reference"] or "",
        "case_material": row["case_material"] or "",
        "dial": row["dial"] or "",
        "case_size_mm": row["case_size_mm"],
        "stones": row["stones"] or "",
        "price_usd": row["price_usd"],
        "price_display": row["price_display"] or "",
        "in_stock": bool(row["in_stock"]),
        "status": row["status"] or ("available" if row["in_stock"] else "sold_out"),
        "image_url": row["image_url"],
        "product_url": row["product_url"] or "",
        "inquire_url": row["inquire_url"] or row["product_url"] or "",
    }


def list_watches() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM inventory_watches
            ORDER BY price_usd DESC NULLS LAST, title ASC
            """
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def get_watch(watch_id: str) -> dict[str, Any] | None:
    key = (watch_id or "").strip()
    if not key:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM inventory_watches WHERE id = %s",
            (key,),
        ).fetchone()
    return _row_to_item(row) if row else None


def existing_ids() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT id FROM inventory_watches").fetchall()
    return {str(r["id"]) for r in rows}


def insert_watch(item: dict[str, Any]) -> dict[str, Any]:
    with get_connection() as conn:
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
            """,
            item,
        )
    return dict(item)
