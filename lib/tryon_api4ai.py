"""API4AI Virtual Try-On — same provider stack as Kizzum (portal or RapidAPI)."""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Dict, Literal, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

VTON_TIMEOUT = float(os.getenv("VTON_TIMEOUT_SEC", "120"))
PORTAL_URL = (
    os.getenv("VTON_PORTAL_URL") or "https://api4ai.cloud/virtual-try-on/v1/results"
).strip()
RAPIDAPI_HOST = "virtual-try-on7.p.rapidapi.com"
RAPIDAPI_URL = (
    os.getenv("VTON_RAPIDAPI_URL") or f"https://{RAPIDAPI_HOST}/results"
).strip()

Provider = Literal["portal", "rapidapi"]


def _api4ai_key() -> str:
    return (os.getenv("API4AI_API_KEY") or "").strip()


def _rapidapi_key() -> str:
    return (os.getenv("RAPID_API_KEY") or os.getenv("RAPIDAPI_KEY") or "").strip()


def tryon_configured() -> bool:
    try:
        _resolve_provider()
        return True
    except RuntimeError:
        return False


def _resolve_provider() -> Provider:
    explicit = (os.getenv("VTON_PROVIDER") or "").strip().lower()
    if explicit in ("portal", "direct", "api4ai", "normal"):
        if not _api4ai_key():
            raise RuntimeError("VTON_PROVIDER=portal but API4AI_API_KEY is not configured")
        return "portal"
    if explicit in ("rapidapi", "rapid"):
        if not _rapidapi_key():
            raise RuntimeError("VTON_PROVIDER=rapidapi but RAPID_API_KEY is not configured")
        return "rapidapi"
    if _api4ai_key():
        return "portal"
    if _rapidapi_key():
        return "rapidapi"
    raise RuntimeError(
        "Try-on is not configured: set API4AI_API_KEY or RAPID_API_KEY in tpt-mvp/backend/.env"
    )


def _request_config(provider: Provider) -> Tuple[str, Dict[str, str]]:
    if provider == "portal":
        return PORTAL_URL, {"X-API-KEY": _api4ai_key()}
    return RAPIDAPI_URL, {
        "x-rapidapi-key": _rapidapi_key(),
        "x-rapidapi-host": RAPIDAPI_HOST,
    }


def run_virtual_tryon(
    *,
    person_bytes: Optional[bytes] = None,
    person_url: Optional[str] = None,
    apparel_bytes: Optional[bytes] = None,
    apparel_url: Optional[str] = None,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """Wrist photo + watch product image → composite result."""
    if not person_bytes and not (person_url or "").strip():
        raise ValueError("wrist photo required")
    if not apparel_bytes and not (apparel_url or "").strip():
        raise ValueError("watch image required")

    provider = _resolve_provider()
    url, headers = _request_config(provider)

    files: Dict[str, Tuple[str, bytes, str]] = {}
    data: Dict[str, str] = {}

    if person_bytes:
        files["image"] = ("wrist.jpg", person_bytes, "image/jpeg")
    else:
        data["url"] = person_url.strip()

    if apparel_bytes:
        files["image-apparel"] = ("watch.jpg", apparel_bytes, "image/jpeg")
    else:
        data["url-apparel"] = apparel_url.strip()

    logger.info("watch try-on via %s", provider)
    t_start = time.monotonic()
    with httpx.Client(timeout=VTON_TIMEOUT) as client:
        resp = client.post(url, headers=headers, data=data, files=files or None)

    total_ms = int((time.monotonic() - t_start) * 1000)

    if resp.status_code == 413:
        raise ValueError("Image too large (max 16MB per photo)")
    if resp.status_code == 422:
        raise ValueError("Missing wrist or watch image")
    if resp.status_code != 200:
        detail = resp.text[:300] if resp.text else f"HTTP {resp.status_code}"
        raise RuntimeError(
            f"Try-on service error ({provider}, HTTP {resp.status_code}): {detail}"
        )

    payload = resp.json()
    results = payload.get("results") or []
    if not results:
        raise RuntimeError("Try-on returned no results")

    row = results[0]
    status = row.get("status") or {}
    code = (status.get("code") or "").lower()
    message = status.get("message") or "Try-on failed"

    if code == "failure":
        raise RuntimeError(message)

    entities = row.get("entities") or []
    image_entity = next(
        (e for e in entities if (e.get("kind") or "") == "image" and e.get("image")),
        None,
    )
    if not image_entity:
        raise RuntimeError(message if code != "ok" else "Try-on succeeded but returned no image")

    raw_b64 = image_entity.get("image") or ""
    fmt = (image_entity.get("format") or "PNG").upper()
    try:
        out_bytes = base64.b64decode(raw_b64)
    except Exception as exc:
        raise RuntimeError("Try-on returned invalid image data") from exc

    meta = {
        "provider": provider,
        "status_code": code,
        "status_message": message,
        "total_ms": total_ms,
        "format": fmt,
    }
    ext = "jpg" if fmt == "JPEG" else "png"
    return out_bytes, ext, meta
