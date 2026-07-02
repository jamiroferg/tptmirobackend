"""Wrist + watch virtual try-on — wraps API4AI like Kizzum apparel try-on."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from inventory import get_watch
from lib.tryon_api4ai import run_virtual_tryon, tryon_configured
from lib.tryon_cache import cache_get, cache_put
from lib.tryon_media import download_image_bytes, save_image_bytes
from lib.tryon_prepare import prepare_tryon_image

logger = logging.getLogger(__name__)

MAX_UPLOAD = 16 * 1024 * 1024


def is_configured() -> bool:
    return tryon_configured()


def prefetch_watch_image(watch_image_url: str) -> tuple[bool, int]:
    url = (watch_image_url or "").strip()
    if not url:
        return False, 0
    cached = cache_get(url)
    if cached:
        return True, len(cached)
    data = _fetch_watch_bytes(url)
    if not data:
        return False, 0
    return True, len(data)


def run_try_on(
    *,
    wrist_bytes: bytes,
    watch_id: Optional[str] = None,
    watch_image_url: Optional[str] = None,
) -> dict:
    if not is_configured():
        raise RuntimeError(
            "Try-on is not configured: set API4AI_API_KEY or RAPID_API_KEY in tpt-mvp/backend/.env"
        )
    if not wrist_bytes:
        raise ValueError("wrist photo required")
    if len(wrist_bytes) > MAX_UPLOAD:
        raise ValueError("Wrist photo must be under 16MB")

    watch_url = (watch_image_url or "").strip()
    if watch_id and not watch_url:
        row = get_watch(watch_id)
        if not row:
            raise ValueError("watch not found in inventory")
        watch_url = row.get("image_url") or ""
    if not watch_url:
        raise ValueError("watch image required")

    wrist_prepared = prepare_tryon_image(wrist_bytes)
    watch_bytes = _fetch_watch_bytes(watch_url)
    watch_prepared = prepare_tryon_image(watch_bytes) if watch_bytes else None

    out_bytes, ext, meta = run_virtual_tryon(
        person_bytes=wrist_prepared,
        apparel_bytes=watch_prepared,
        apparel_url=watch_url if not watch_prepared else None,
    )
    result_url = save_image_bytes(out_bytes, filename=f"tryon-{uuid.uuid4().hex[:12]}.{ext}")
    return {
        "ok": True,
        "result_url": result_url,
        "status_message": meta.get("status_message") or "Success",
        "format": ext,
        "provider": meta.get("provider"),
        "total_ms": meta.get("total_ms"),
    }


def _fetch_watch_bytes(watch_url: str) -> Optional[bytes]:
    url = (watch_url or "").strip()
    if not url:
        return None
    cached = cache_get(url)
    if cached:
        return cached
    raw = download_image_bytes(url)
    if not raw:
        return None
    try:
        prepared = prepare_tryon_image(raw)
    except Exception as exc:
        logger.warning("[try-on] watch image prepare failed: %s", exc)
        return None
    cache_put(url, prepared)
    return prepared
