"""Image normalize helpers for TPT — HEIC/JPEG only (copied from Kizzum media.py)."""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False
    logger.warning("pillow-heif not installed — HEIC uploads will not be supported")

MAX_EDGE = 1600
JPEG_QUALITY = 85
CLAUDE_MAX_BYTES = 4_500_000
FAST_PATH_MAX_BYTES = 900_000


def _looks_like_heic(raw: bytes) -> bool:
    if len(raw) < 16:
        return False
    if raw[4:8] != b"ftyp":
        return False
    brand = raw[8:16]
    return any(tag in brand for tag in (b"heic", b"heif", b"mif1", b"msf1"))


def normalize_to_jpeg_bytes(raw: bytes, max_bytes: int = CLAUDE_MAX_BYTES) -> bytes:
    """Decode any supported format -> downscaled JPEG bytes."""
    if not raw or len(raw) < 12:
        raise ValueError(f"empty or too-short upload ({len(raw)} bytes)")

    # Client already sends a small JPEG — skip re-encode (~200–400ms saved).
    if raw[:3] == b"\xff\xd8\xff" and len(raw) <= FAST_PATH_MAX_BYTES:
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
            w, h = img.size
            if max(w, h) <= MAX_EDGE and img.format == "JPEG":
                return raw
        except Exception:
            pass

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        head = raw[:16].hex()
        if _looks_like_heic(raw) and not HEIF_SUPPORTED:
            raise ValueError(
                "HEIC photo received but pillow-heif is not installed on the TPT backend"
            ) from e
        raise ValueError(
            f"unsupported image ({len(raw)} bytes, head={head}): {e}"
        ) from e

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    attempts = [
        (MAX_EDGE, JPEG_QUALITY),
        (1400, 80),
        (1200, 78),
        (1000, 75),
        (800, 70),
        (640, 65),
    ]
    last_data: bytes = b""
    for max_edge, quality in attempts:
        candidate = img
        w, h = candidate.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / longest
            candidate = candidate.resize(
                (int(w * scale), int(h * scale)), Image.LANCZOS
            )
        buf = io.BytesIO()
        candidate.save(buf, format="JPEG", quality=quality, optimize=True)
        last_data = buf.getvalue()
        if len(last_data) <= max_bytes:
            return last_data
    return last_data
