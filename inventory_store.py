"""TPT inventory persistence — Postgres when DATABASE_URL is set, else JSON file."""

from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

import bootstrap

from db import init_db, postgres_enabled, seed_inventory_if_empty
from inventory_seed import DEFAULT_INVENTORY

DATA_DIR = bootstrap.TPT_BACKEND / "data"
INVENTORY_PATH = DATA_DIR / "inventory.json"
_LOCK = threading.RLock()
_DB_READY = False


def _ensure_db() -> None:
    global _DB_READY
    if _DB_READY or not postgres_enabled():
        return
    init_db()
    seed_inventory_if_empty(DEFAULT_INVENTORY)
    _DB_READY = True


def _ensure_json_store() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not INVENTORY_PATH.exists():
        INVENTORY_PATH.write_text(
            json.dumps(DEFAULT_INVENTORY, indent=2),
            encoding="utf-8",
        )


def _load_json() -> list[dict[str, Any]]:
    _ensure_json_store()
    data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("inventory.json must be a JSON array")
    return data


def _save_json(items: list[dict[str, Any]]) -> None:
    _ensure_json_store()
    INVENTORY_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def format_price_display(price_usd: int | float | None) -> str:
    if price_usd is None:
        return ""
    try:
        val = int(round(float(price_usd)))
    except (TypeError, ValueError):
        return ""
    return f"${val:,}"


def build_subtitle(row: dict[str, Any]) -> str:
    parts: list[str] = []
    material = (row.get("case_material") or "").strip()
    dial = (row.get("dial") or "").strip()
    size = row.get("case_size_mm")
    if material:
        parts.append(material)
    if dial:
        parts.append(dial if "dial" in dial.lower() else f"{dial} dial")
    if size not in (None, "", 0):
        parts.append(f"{int(size)}mm")
    return " · ".join(parts)


def _slug_id(title: str, brand: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (title or brand or "watch").lower()).strip("-")
    base = base[:56] or uuid.uuid4().hex[:12]
    candidate = base
    n = 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _normalize_case_size(val: Any) -> int | None:
    if val in (None, ""):
        return None
    try:
        n = int(round(float(val)))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def normalize_item(row: dict[str, Any], *, existing_ids: set[str] | None = None) -> dict[str, Any]:
    """Normalize a watch row for API responses and storage."""
    title = (row.get("title") or "").strip()
    brand = (row.get("brand") or "").strip()
    in_stock = bool(row.get("in_stock", True))
    price_usd = row.get("price_usd")
    if price_usd not in (None, ""):
        try:
            price_usd = int(round(float(price_usd)))
        except (TypeError, ValueError):
            price_usd = None

    item_id = (row.get("id") or "").strip()
    if not item_id:
        item_id = _slug_id(title, brand, existing_ids or set())

    out = {
        "id": item_id,
        "brand": brand,
        "model": (row.get("model") or "").strip(),
        "title": title,
        "reference": (row.get("reference") or "").strip(),
        "case_material": (row.get("case_material") or "").strip(),
        "dial": (row.get("dial") or "").strip(),
        "case_size_mm": _normalize_case_size(row.get("case_size_mm")),
        "stones": (row.get("stones") or "").strip(),
        "price_usd": price_usd,
        "price_display": (row.get("price_display") or "").strip() or format_price_display(price_usd),
        "in_stock": in_stock,
        "status": "available" if in_stock else "sold_out",
        "image_url": (row.get("image_url") or "").strip(),
        "product_url": (row.get("product_url") or "").strip(),
        "inquire_url": (row.get("inquire_url") or row.get("product_url") or "").strip(),
    }
    out["subtitle"] = (row.get("subtitle") or "").strip() or build_subtitle(out)
    return out


def storage_backend() -> str:
    return "postgres" if postgres_enabled() else "json"


def list_inventory() -> list[dict[str, Any]]:
    if postgres_enabled():
        _ensure_db()
        from inventory_db import list_watches

        return list_watches()
    with _LOCK:
        return [dict(row) for row in _load_json()]


def get_watch(watch_id: str) -> dict[str, Any] | None:
    key = (watch_id or "").strip()
    if not key:
        return None
    if postgres_enabled():
        _ensure_db()
        from inventory_db import get_watch as db_get_watch

        return db_get_watch(key)
    with _LOCK:
        for row in _load_json():
            if row.get("id") == key:
                return dict(row)
    return None


def create_watch(row: dict[str, Any]) -> dict[str, Any]:
    if postgres_enabled():
        _ensure_db()
        from inventory_db import existing_ids, insert_watch

        ids = existing_ids()
        item = normalize_item(row, existing_ids=ids)
        if not item["title"]:
            raise ValueError("title is required")
        if not item["brand"]:
            raise ValueError("brand is required")
        if not item["image_url"]:
            raise ValueError("image_url is required")
        if item["id"] in ids:
            raise ValueError(f"watch id already exists: {item['id']}")
        return insert_watch(item)

    with _LOCK:
        items = _load_json()
        existing_ids_set = {str(r.get("id") or "") for r in items}
        item = normalize_item(row, existing_ids=existing_ids_set)
        if not item["title"]:
            raise ValueError("title is required")
        if not item["brand"]:
            raise ValueError("brand is required")
        if not item["image_url"]:
            raise ValueError("image_url is required")
        if item["id"] in existing_ids_set:
            raise ValueError(f"watch id already exists: {item['id']}")
        items.append(item)
        _save_json(items)
        return dict(item)
