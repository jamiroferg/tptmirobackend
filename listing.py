"""Generate dealer-ready listing copy from watch identification."""

from __future__ import annotations

import json
from typing import Any, Optional

from lib.ai_json import call_json_llm

LISTING_PROMPT = """You are a luxury watch dealer writing inventory listings for Timepiece Trading.
Given identification signals from a photo search, produce accurate, professional listing copy.

Rules:
- Be conservative — if reference or model is uncertain, say "likely" or leave blank.
- Title format: Brand Model Reference — key descriptor (e.g. "Rolex Submariner Date 126610LN — Black Dial Oyster").
- Description: 2-4 sentences, dealer tone, mention dial, bezel, bracelet/strap if known.
- price_notes: market context only, no fake exact price unless clearly stated in sources.
- keywords: 5-10 search terms for Chrono24/eBay/dealer site SEO.
- Never invent box/papers unless mentioned in input.

Return ONLY valid JSON matching this schema:
{
  "title": "string",
  "brand": "string",
  "model": "string",
  "reference": "string",
  "case_size_mm": "string or empty",
  "dial": "string",
  "bezel": "string",
  "bracelet": "string",
  "movement": "string",
  "condition_notes": "string",
  "description": "string",
  "price_notes": "string",
  "keywords": ["string"]
}
"""


def generate_listing_draft(
    identification: dict[str, Any],
    *,
    extra_notes: str = "",
) -> dict[str, Any]:
    payload = {
        "user_hint": identification.get("hint") or "",
        "identity_line": identification.get("identity_line") or "",
        "reference": identification.get("reference") or "",
        "brand_guess": identification.get("brand_guess") or "",
        "model_guess": identification.get("model_guess") or "",
        "descriptors": identification.get("descriptors") or [],
        "market_prices": [
            m.get("price_display")
            for m in (identification.get("matches") or [])
            if m.get("price_display")
        ][:8],
        "confidence_pct": identification.get("confidence_pct"),
        "confidence_tier": identification.get("confidence_tier") or "",
        "top_match": _slim_match(identification.get("top_match")),
        "other_matches": [_slim_match(m) for m in (identification.get("matches") or [])[:5]],
        "dealer_notes": (extra_notes or "").strip(),
    }

    user_text = json.dumps(payload, indent=2)
    raw = call_json_llm(LISTING_PROMPT, user_text)

    return _normalize_listing(raw)


def _slim_match(match: Optional[dict]) -> Optional[dict]:
    if not match:
        return None
    return {
        "title": match.get("title"),
        "link": match.get("link"),
        "source": match.get("source"),
        "price": match.get("price"),
    }


def _normalize_listing(raw: dict) -> dict[str, Any]:
    keywords = raw.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    return {
        "title": (raw.get("title") or "").strip(),
        "brand": (raw.get("brand") or "").strip(),
        "model": (raw.get("model") or "").strip(),
        "reference": (raw.get("reference") or "").strip(),
        "case_size_mm": (raw.get("case_size_mm") or "").strip(),
        "dial": (raw.get("dial") or "").strip(),
        "bezel": (raw.get("bezel") or "").strip(),
        "bracelet": (raw.get("bracelet") or "").strip(),
        "movement": (raw.get("movement") or "").strip(),
        "condition_notes": (raw.get("condition_notes") or "").strip(),
        "description": (raw.get("description") or "").strip(),
        "price_notes": (raw.get("price_notes") or "").strip(),
        "keywords": [str(k).strip() for k in keywords if str(k).strip()],
    }
