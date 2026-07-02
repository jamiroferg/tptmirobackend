"""In-memory scan sessions — Phase 1 caches Lens results for Phase 2 listings."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

SCAN_TTL_SEC = 300

_lock = Lock()
_sessions: dict[str, "ScanSession"] = {}


@dataclass
class ScanSession:
    scan_id: str
    hint: str
    identity: dict[str, Any]
    raw_matches: list[dict]
    related_searches: list[Any]
    ocr_text: str
    lens_image_url: str
    upload_url: str
    created_at: float = field(default_factory=time.time)


def _purge_expired() -> None:
    cutoff = time.time() - SCAN_TTL_SEC
    stale = [sid for sid, s in _sessions.items() if s.created_at < cutoff]
    for sid in stale:
        _sessions.pop(sid, None)


def create_scan(
    *,
    hint: str,
    identity: dict[str, Any],
    raw_matches: list[dict],
    related_searches: list[Any],
    ocr_text: str,
    lens_image_url: str,
    upload_url: str,
) -> ScanSession:
    session = ScanSession(
        scan_id=uuid.uuid4().hex,
        hint=hint,
        identity=identity,
        raw_matches=raw_matches,
        related_searches=related_searches,
        ocr_text=ocr_text,
        lens_image_url=lens_image_url,
        upload_url=upload_url,
    )
    with _lock:
        _purge_expired()
        _sessions[session.scan_id] = session
    return session


def get_scan(scan_id: str) -> ScanSession | None:
    with _lock:
        _purge_expired()
        session = _sessions.get(scan_id)
        if not session:
            return None
        if time.time() - session.created_at > SCAN_TTL_SEC:
            _sessions.pop(scan_id, None)
            return None
        return session
