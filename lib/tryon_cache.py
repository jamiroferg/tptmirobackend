"""Short-lived cache for prefetched watch product images."""

from __future__ import annotations

import os
import time
from threading import Lock
from typing import Optional

_TTL = float(os.getenv("VTON_APPAREL_CACHE_TTL", "600"))
_MAX = int(os.getenv("VTON_APPAREL_CACHE_MAX", "40"))
_LOCK = Lock()
_CACHE: dict[str, tuple[float, bytes]] = {}


def cache_put(url: str, data: bytes) -> None:
    key = (url or "").strip()
    if not key or not data:
        return
    now = time.time()
    with _LOCK:
        _CACHE[key] = (now, data)
        if len(_CACHE) <= _MAX:
            return
        stale = [k for k, (ts, _) in _CACHE.items() if now - ts > _TTL]
        for k in stale:
            _CACHE.pop(k, None)
        while len(_CACHE) > _MAX:
            oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _CACHE.pop(oldest, None)


def cache_get(url: str) -> Optional[bytes]:
    key = (url or "").strip()
    if not key:
        return None
    now = time.time()
    with _LOCK:
        row = _CACHE.get(key)
        if not row:
            return None
        ts, data = row
        if now - ts > _TTL:
            _CACHE.pop(key, None)
            return None
        return data
