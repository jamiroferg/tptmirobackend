"""AI-assisted TPT inventory spec draft — Lens identify + structured LLM extract."""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from lib.ai_json import call_json_llm
from lib.media import normalize_to_jpeg_bytes
from lib.tryon_media import download_image_bytes, save_image_bytes
from watch_service import identify_watch

INVENTORY_SPEC_PROMPT = """You are a luxury watch inventory specialist at Timepiece Trading (TPT).
Given Google Lens identification results from a product photo, extract Shopify listing spec fields.

Ground rules:
- Treat lens_identity (brand, reference, identity_line, confidence) as primary truth.
- Use match titles and descriptors to fill case material, dial, stones, and case size.
- Be conservative — if uncertain, use empty string or null for case_size_mm.
- Never invent box, papers, or condition unless explicitly in dealer_notes.
- Title should read like a TPT product name: Brand Model Ref — key descriptors.
- Dial color: use TPT-style labels (e.g. "Mother Of Pearl", "Black", "Openworked", "Brown").
- Case material: e.g. "Rose Gold", "White Gold", "Steel", "Platinum", "Ceramic", "NTPT Carbon", "Titanium".
- Stones: prefer exactly one of — "Diamonds", "No Diamonds", "Sapphires", "Mixed Gemstones", or describe briefly if obvious (e.g. "Diamonds and Sapphires" for rainbow bezels).
- case_size_mm: integer millimeters only, or null if unknown.

Return ONLY valid JSON:
{
  "title": "string — suggested listing title",
  "brand": "string",
  "model": "string",
  "reference": "string",
  "case_material": "string",
  "dial": "string",
  "case_size_mm": number or null,
  "stones": "string",
  "confidence_notes": "string — one sentence on what was clear vs uncertain"
}
"""


def resolve_image_bytes(
    *,
    file_bytes: Optional[bytes] = None,
    image_url: str = "",
) -> tuple[bytes, str]:
    """Return JPEG bytes and the image URL to store (CDN URL or /uploads/ path)."""
    url = (image_url or "").strip()
    if file_bytes:
        jpg = normalize_to_jpeg_bytes(file_bytes)
        saved = save_image_bytes(jpg, filename=f"inv-{uuid.uuid4().hex[:12]}.jpg")
        return jpg, saved
    if url:
        raw = download_image_bytes(url)
        if not raw:
            raise ValueError("Could not download image from URL — check the link is a direct product photo")
        jpg = normalize_to_jpeg_bytes(raw)
        return jpg, url
    raise ValueError("Upload a photo or paste an image URL")


def draft_inventory_from_image(
    *,
    file_bytes: Optional[bytes] = None,
    image_url: str = "",
    hint: str = "",
) -> dict[str, Any]:
    """Lens identify → LLM TPT spec fields. Price is omitted (dealer sets manually)."""
    jpg, resolved_image_url = resolve_image_bytes(file_bytes=file_bytes, image_url=image_url)
    identification = identify_watch(jpg, hint=(hint or "").strip())

    draft = generate_inventory_spec_draft(identification)
    draft["image_url"] = resolved_image_url

    return {
        "ok": True,
        "draft": draft,
        "image_url": resolved_image_url,
        "identification": {
            "identity_line": identification.get("identity_line") or "",
            "brand_guess": identification.get("brand_guess") or "",
            "model_guess": identification.get("model_guess") or "",
            "reference": identification.get("reference") or "",
            "confidence_pct": identification.get("confidence_pct"),
            "confidence_tier": identification.get("confidence_tier") or "",
            "match_count": identification.get("match_count") or 0,
        },
    }


def generate_inventory_spec_draft(identification: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "lens_identity": {
            "hint": identification.get("hint") or "",
            "identity_line": identification.get("identity_line") or "",
            "brand_guess": identification.get("brand_guess") or "",
            "model_guess": identification.get("model_guess") or "",
            "reference": identification.get("reference") or "",
            "descriptors": identification.get("descriptors") or [],
            "confidence_pct": identification.get("confidence_pct"),
            "confidence_tier": identification.get("confidence_tier") or "",
            "ocr_text": identification.get("ocr_text") or "",
        },
        "match_titles": [
            m.get("title")
            for m in (identification.get("matches") or [])
            if m.get("title")
        ][:10],
        "match_sources": [
            {
                "title": m.get("title"),
                "source": m.get("source") or m.get("source_name"),
                "link": m.get("link"),
            }
            for m in (identification.get("matches") or [])[:6]
        ],
    }

    raw = call_json_llm(INVENTORY_SPEC_PROMPT, json.dumps(payload, indent=2))
    return _normalize_draft(raw)


def _normalize_draft(raw: dict[str, Any]) -> dict[str, Any]:
    size = raw.get("case_size_mm")
    if size in ("", None):
        case_size_mm = None
    else:
        try:
            case_size_mm = int(round(float(size)))
            if case_size_mm <= 0:
                case_size_mm = None
        except (TypeError, ValueError):
            case_size_mm = None

    row = {
        "title": (raw.get("title") or "").strip(),
        "brand": (raw.get("brand") or "").strip(),
        "model": (raw.get("model") or "").strip(),
        "reference": (raw.get("reference") or "").strip(),
        "case_material": (raw.get("case_material") or "").strip(),
        "dial": (raw.get("dial") or "").strip(),
        "case_size_mm": case_size_mm,
        "stones": (raw.get("stones") or "").strip(),
        "confidence_notes": (raw.get("confidence_notes") or "").strip(),
        "image_url": "",
        "product_url": "",
        "in_stock": True,
        "price_usd": None,
    }
    row["subtitle"] = _build_subtitle(row)
    return row


def _build_subtitle(row: dict[str, Any]) -> str:
    parts: list[str] = []
    if row.get("case_material"):
        parts.append(row["case_material"])
    dial = (row.get("dial") or "").strip()
    if dial:
        parts.append(dial if "dial" in dial.lower() else f"{dial} dial")
    if row.get("case_size_mm"):
        parts.append(f"{row['case_size_mm']}mm")
    return " · ".join(parts)
