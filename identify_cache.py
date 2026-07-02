"""Identify result cache + background warm while user adjusts crop (Kizzum lens_warm pattern)."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

_LOCK = threading.RLock()
_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_INFLIGHT: dict[str, Future] = {}
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tpt-warm")


def _enabled() -> bool:
    v = (os.getenv("TPT_IDENTIFY_CACHE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _ttl_s() -> int:
    try:
        return max(60, min(3600, int(os.getenv("TPT_IDENTIFY_CACHE_TTL_S", "900"))))
    except ValueError:
        return 900


def cache_key(crop_bytes: bytes, hint: str) -> str:
    digest = hashlib.sha256(crop_bytes).hexdigest()[:20]
    hint_digest = hashlib.sha256((hint or "").strip().encode()).hexdigest()[:8]
    return f"{digest}:{hint_digest}"


def _get_valid(key: str) -> Optional[dict[str, Any]]:
    row = _CACHE.get(key)
    if not row:
        return None
    if row[1] <= time.monotonic():
        _CACHE.pop(key, None)
        return None
    return row[0]


def store(crop_bytes: bytes, hint: str, result: dict[str, Any]) -> None:
    if not _enabled():
        return
    key = cache_key(crop_bytes, hint)
    with _LOCK:
        _CACHE[key] = (dict(result), time.monotonic() + _ttl_s())


def get_cached(crop_bytes: bytes, hint: str) -> Optional[dict[str, Any]]:
    if not _enabled():
        return None
    key = cache_key(crop_bytes, hint)
    with _LOCK:
        row = _get_valid(key)
        if row:
            out = dict(row)
            out["cache_hit"] = True
            return out
    return None


def warm_status(crop_bytes: bytes, hint: str) -> str:
    """ready | pending | miss"""
    if not _enabled():
        return "miss"
    key = cache_key(crop_bytes, hint)
    with _LOCK:
        if _get_valid(key):
            return "ready"
        fut = _INFLIGHT.get(key)
        if fut and not fut.done():
            return "pending"
    return "miss"


def _submit(key: str, crop_bytes: bytes, hint: str, compute: Callable[[], dict[str, Any]]) -> Future:
    def _run() -> dict[str, Any]:
        try:
            cached = get_cached(crop_bytes, hint)
            if cached:
                return cached
            result = compute()
            store(crop_bytes, hint, result)
            return result
        finally:
            with _LOCK:
                _INFLIGHT.pop(key, None)

    fut = _POOL.submit(_run)
    with _LOCK:
        _INFLIGHT[key] = fut
    return fut


def schedule_warm(crop_bytes: bytes, hint: str, compute: Callable[[], dict[str, Any]]) -> bool:
    """Fire background identify if not cached and not already running."""
    if not _enabled() or not crop_bytes:
        return False
    key = cache_key(crop_bytes, hint)

    with _LOCK:
        if _get_valid(key):
            return False
        fut = _INFLIGHT.get(key)
        if fut and not fut.done():
            return False
        _submit(key, crop_bytes, hint, compute)
    return True


def identify_cached(
    crop_bytes: bytes,
    hint: str,
    compute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Cache hit, wait for in-flight warm, or compute once."""
    if not _enabled():
        out = compute()
        out["cache_hit"] = False
        return out

    key = cache_key(crop_bytes, hint)

    with _LOCK:
        row = _get_valid(key)
        if row:
            out = dict(row)
            out["cache_hit"] = True
            return out

        fut = _INFLIGHT.get(key)
        if fut is None or fut.done():
            if fut and fut.done():
                try:
                    out = dict(fut.result())
                    out["cache_hit"] = True
                    return out
                except Exception:
                    _INFLIGHT.pop(key, None)
            fut = _submit(key, crop_bytes, hint, compute)

    result = fut.result(timeout=60)
    out = dict(result)
    out.setdefault("cache_hit", False)
    return out
