"""Resize try-on inputs before sending to API4AI."""

from __future__ import annotations

import io
import os

from PIL import Image, ImageOps

VTON_MAX_EDGE = int(os.getenv("VTON_MAX_EDGE", "768"))
VTON_JPEG_QUALITY = int(os.getenv("VTON_JPEG_QUALITY", "85"))


def prepare_tryon_image(raw: bytes, max_edge: int | None = None) -> bytes:
    if not raw:
        raise ValueError("empty image")
    edge = max_edge or VTON_MAX_EDGE
    img = Image.open(io.BytesIO(raw))
    img.load()
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > edge:
        scale = edge / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=VTON_JPEG_QUALITY, optimize=True)
    return buf.getvalue()
