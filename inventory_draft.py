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
Given Google Lens identification results and the dealer's SELECTED listing, extract Shopify spec fields.

Ground rules:
- selected_match is the dealer's chosen source — prioritize its title, link domain, and any specs implied there.
- Use lens_identity (brand, reference, identity_line, confidence) to confirm or correct the selection.
- Be conservative — if uncertain, use empty string or null for case_size_mm.
- Never invent box, papers, or condition unless explicitly stated in the selected listing.
- Title should read like a TPT product name: Brand Model Ref — key descriptors (you may refine selected_match.title).
- Dial color: TPT-style labels (e.g. "Mother Of Pearl", "Black", "Openworked", "Brown").
- Case material: e.g. "Rose Gold", "White Gold", "Steel", "Platinum", "Ceramic", "NTPT Carbon", "Titanium".
- Stones: prefer "Diamonds", "No Diamonds", "Sapphires", "Mixed Gemstones", or brief accurate description.
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


def _match_image_url(match: Optional[dict[str, Any]]) -> str:
    if not match:
        return ""
    for key in ("image", "thumbnail"):
        val = (match.get(key) or "").strip()
        if val:
            return val
    return ""


def identify_inventory_image(
    *,
    file_bytes: Optional[bytes] = None,
    image_url: str = "",
    hint: str = "",
) -> dict[str, Any]:
    """Lens identify only — returns matches for dealer to choose from."""
    jpg, resolved_image_url = resolve_image_bytes(file_bytes=file_bytes, image_url=image_url)
    identification = identify_watch(jpg, hint=(hint or "").strip())
    identification["image_url"] = resolved_image_url
    return {
        "ok": True,
        "phase": "identify",
        "image_url": resolved_image_url,
        "lens_image_url": identification.get("lens_image_url") or "",
        "identification": identification,
        "matches": identification.get("matches") or [],
        "match_count": identification.get("match_count") or 0,
    }


def draft_inventory_from_selection(
    *,
    identification: dict[str, Any],
    selected_match: Optional[dict[str, Any]] = None,
    image_url: str = "",
) -> dict[str, Any]:
    """Dealer picked a listing → LLM fills TPT spec fields."""
    if not identification:
        raise ValueError("identification required")

    id_copy = dict(identification)
    if selected_match:
        id_copy["selected_match"] = selected_match

    draft = generate_inventory_spec_draft(id_copy)
    resolved = (image_url or "").strip()
    if not resolved:
        resolved = _match_image_url(selected_match)
    if not resolved:
        resolved = (identification.get("lens_image_url") or identification.get("image_url") or "").strip()
    if not resolved:
        resolved = (draft.get("image_url") or "").strip()

    draft["image_url"] = resolved
    if selected_match:
        link = (selected_match.get("link") or "").strip()
        if link:
            draft["product_url"] = link

    return {
        "ok": True,
        "phase": "draft",
        "draft": draft,
        "image_url": resolved,
        "selected_match": selected_match,
    }


def draft_inventory_from_image(
    *,
    file_bytes: Optional[bytes] = None,
    image_url: str = "",
    hint: str = "",
) -> dict[str, Any]:
    """One-shot: identify + draft from top match (legacy)."""
    result = identify_inventory_image(
        file_bytes=file_bytes,
        image_url=image_url,
        hint=hint,
    )
    identification = result["identification"]
    matches = result.get("matches") or []
    selected = matches[0] if matches else None
    return draft_inventory_from_selection(
        identification=identification,
        selected_match=selected,
        image_url=result.get("image_url") or "",
    )


def generate_inventory_spec_draft(identification: dict[str, Any]) -> dict[str, Any]:
    selected = identification.get("selected_match")
    payload = {
        "selected_match": {
            "title": selected.get("title") if selected else "",
            "link": selected.get("link") if selected else "",
            "source": (selected.get("source") or selected.get("source_name") if selected else ""),
            "price_display": selected.get("price_display") if selected else "",
            "tpt_inventory": bool(selected.get("tpt_inventory")) if selected else False,
        } if selected else None,
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
