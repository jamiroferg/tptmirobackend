"""Download + save try-on images for TPT MVP."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

import httpx

import bootstrap

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 16 * 1024 * 1024
TIMEOUT = 30.0
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def download_image_bytes(image_url: str) -> Optional[bytes]:
    url = (image_url or "").strip()
    if not url:
        return None
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            resp = client.get(url)
            if resp.status_code != 200 or len(resp.content) > MAX_IMAGE_BYTES:
                return None
            ct = resp.headers.get("content-type", "").split(";")[0].strip()
            if ct and not ct.startswith("image/"):
                return None
            return resp.content
    except Exception as exc:
        logger.warning("download_image_bytes failed for %s: %s", url[:80], exc)
        return None


def save_image_bytes(raw: bytes, filename: Optional[str] = None) -> str:
    ext = "png"
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
        fname = filename
    else:
        fname = f"tryon-{uuid.uuid4().hex[:12]}.{ext}"
    path = bootstrap.UPLOAD_DIR / fname
    path.write_bytes(raw)
    return f"/uploads/{fname}"
