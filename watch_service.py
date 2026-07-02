"""Watch identification — direct Google Lens pipeline (no Kizzum clothing re-rank)."""

from __future__ import annotations

import os
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import bootstrap  # noqa: F401 — env + paths
import identify_cache
from lib import lens
from lib.media import normalize_to_jpeg_bytes

DEFAULT_TPT_DOMAINS = ("timepiecetradingllc.com", "timepiecetrading.com")

# ---------------------------------------------------------------------------
# Watch-specific parsing + light filtering (Lens order preserved)
# ---------------------------------------------------------------------------

BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Richard Mille", re.compile(r"richard\s+mille", re.I)),
    ("Rolex", re.compile(r"\brolex\b", re.I)),
    ("Patek Philippe", re.compile(r"patek\s+philippe", re.I)),
    ("Audemars Piguet", re.compile(r"audemars\s+piguet", re.I)),
    ("Omega", re.compile(r"\bomega\b", re.I)),
    ("Cartier", re.compile(r"\bcartier\b", re.I)),
    ("IWC", re.compile(r"\biwc\b", re.I)),
    ("Panerai", re.compile(r"\bpanerai\b", re.I)),
    ("Breitling", re.compile(r"\bbreitling\b", re.I)),
    ("Hublot", re.compile(r"\bhublot\b", re.I)),
    ("Jaeger-LeCoultre", re.compile(r"jaeger[\s-]?le\s?coultre", re.I)),
    ("Vacheron Constantin", re.compile(r"vacheron\s+constantin", re.I)),
    ("A. Lange & Söhne", re.compile(r"lange\s+(?:&|and)\s+s[öo]hne", re.I)),
    ("FP Journe", re.compile(r"fp\s+journe|francois\s+paul\s+journe", re.I)),
    ("Tudor", re.compile(r"\btudor\b", re.I)),
    ("Tag Heuer", re.compile(r"tag\s+heuer", re.I)),
]

TRUSTED_WATCH_SOURCES = (
    "chrono24",
    "everywatch",
    "phillips",
    "christies",
    "sothebys",
    "getbezel",
    "watchbox",
    "hodinkee",
    "wristcheck",
    "bobswatches",
    "crownandcaliber",
    "millenarywatches",
    "jomashop",
    "wristaficionado",
    "grailzee",
    "farfetch",
)

JUNK_TITLE_RE = re.compile(
    r"invention of the wristwatch|wristwatch creation|blog|/journal/|"
    r"steampunk|gute men'?s watch|history of the watch",
    re.I,
)

JUNK_SOURCE_RE = re.compile(
    r"dhgate|aliexpress|wish\.com|replica|fake[\s-]?watch|rep[\s-]?watch|"
    r"replicawatch|superclone",
    re.I,
)

REF_PATTERNS = [
    re.compile(r"\bRM\s*(\d{2})\s*[- ]?\s*(\d{2})\b", re.I),
    re.compile(r"\bRM(\d{2})(\d{2})\b", re.I),
    re.compile(r"\bref\.?\s*#?\s*([A-Z0-9][\w./-]{2,})\b", re.I),
    # Patek / Rolex style: 5180/1G, 126610LN, 5711/1A-010
    re.compile(r"\b(\d{4,5}(?:/\d{1,2}[A-Z]?)?(?:-\d{3})?)\b", re.I),
    re.compile(r"\b(\d{4,6}[A-Z]{0,4})\b"),
]

TPT_PRODUCT_PATH_RE = re.compile(r"/products/|/collections/[^/]+/products/", re.I)
TPT_JUNK_PATH_RE = re.compile(r"/blogs/|/pages/|/policies/|/cart", re.I)

DESCRIPTOR_WORDS = (
    "rose gold",
    "white gold",
    "yellow gold",
    "platinum",
    "titanium",
    "ceramic",
    "diamond",
    "skeleton",
    "skeletonized",
    "openwork",
    "openworked",
    "extra flat",
    "extra-thin",
    "automatic",
    "manual",
    "chronograph",
    "perpetual",
    "tourbillon",
    "gmt",
    "daytona",
    "submariner",
    "datejust",
    "day-date",
    "nautilus",
    "aquanaut",
    "calatrava",
)


def identify_watch(jpg_bytes: bytes, *, hint: str = "", save_upload: bool = False) -> dict[str, Any]:
    """Single-shot identify — cached; coalesces with background warm."""
    crop = normalize_to_jpeg_bytes(jpg_bytes)
    hint = (hint or "").strip()
    return identify_cache.identify_cached(
        crop,
        hint,
        lambda: _identify_core(crop, hint=hint, save_upload=save_upload),
    )


def schedule_warm_identify(jpg_bytes: bytes, *, hint: str = "") -> bool:
    """Start identify in background while user finishes crop (no-op if cached)."""
    crop = normalize_to_jpeg_bytes(jpg_bytes)
    hint = (hint or "").strip()
    return identify_cache.schedule_warm(
        crop,
        hint,
        lambda: _identify_core(crop, hint=hint, save_upload=False),
    )


def _identify_core(crop: bytes, *, hint: str = "", save_upload: bool = False) -> dict[str, Any]:
    """Lens + identity + ranked matches in one pass."""
    import scan_cache

    upload_url = ""
    public_url = ""

    if save_upload:
        with ThreadPoolExecutor(max_workers=2) as pool:
            upload_fut = pool.submit(_save_tpt_upload, crop)
            host_fut = pool.submit(lens.upload_to_temp_host, crop, "watch_0.jpg")
            for fut in as_completed([upload_fut, host_fut]):
                try:
                    if fut is upload_fut:
                        upload_url = fut.result()
                    else:
                        public_url = fut.result()
                except Exception:
                    pass
    else:
        public_url = lens.upload_to_temp_host(crop, "watch_0.jpg")

    if not public_url:
        public_url = lens.upload_to_temp_host(crop, "watch_0.jpg")

    bundle = lens.lens_image_search(
        public_url,
        limit=12,
        hint=hint,
        language="en",
        country="us",
    )

    raw_matches = _filter_watch_matches(list(bundle.matches or []))
    if len(raw_matches) < 6 and bundle.organic_results:
        seen = {m.get("link") for m in raw_matches if m.get("link")}
        organic = lens.organic_results_to_matches(
            bundle.organic_results,
            limit=10,
            seen_links=seen,
        )
        raw_matches.extend(organic)

    identity = _extract_watch_identity(raw_matches, hint=hint)

    if len(raw_matches) < 8:
        try:
            tpt_rows = _fetch_tpt_inventory_matches(identity, hint=hint)
            if tpt_rows:
                raw_matches, _ = _merge_with_tpt(raw_matches, tpt_rows)
                identity = _extract_watch_identity(raw_matches, hint=hint)
        except Exception:
            pass

    ranked = _rank_lens_matches(raw_matches, identity, hint=hint)

    session = scan_cache.create_scan(
        hint=hint,
        identity=identity,
        raw_matches=ranked,
        related_searches=list(bundle.related_searches or [])[:6],
        ocr_text=(bundle.ocr_text or "").strip(),
        lens_image_url=public_url,
        upload_url=upload_url,
    )

    visual = _format_matches(ranked[:10])

    return {
        "ok": True,
        "phase": "complete",
        "scan_id": session.scan_id,
        "pipeline": "lens-direct-v3",
        "hint": hint,
        "upload_url": upload_url,
        "lens_image_url": public_url,
        "confidence_pct": identity["confidence_pct"],
        "confidence_tier": identity["confidence_tier"],
        "identity_line": identity["identity_line"],
        "reference": identity["reference"],
        "brand_guess": identity["brand"],
        "model_guess": identity["model"],
        "descriptors": identity["descriptors"],
        "related_searches": session.related_searches,
        "ocr_text": session.ocr_text,
        "matches": visual,
        "match_count": len(visual),
        "listings_pending": False,
        "cache_hit": False,
    }


def identify_identity(jpg_bytes: bytes, *, hint: str = "", save_upload: bool = False) -> dict[str, Any]:
    """Backward-compat alias for identify_watch."""
    return identify_watch(jpg_bytes, hint=hint, save_upload=save_upload)


def fetch_listings(scan_id: str) -> dict[str, Any]:
    """Phase 2 — finalize Lens visual matches (no TPT text search)."""
    import scan_cache

    session = scan_cache.get_scan(scan_id)
    if not session:
        raise ValueError("scan expired or not found — scan again")

    ranked = _rank_lens_matches(session.raw_matches, session.identity, hint=session.hint)
    matches = _format_matches(ranked[:12])
    top = matches[0] if matches else None

    return {
        "ok": True,
        "phase": "listings",
        "scan_id": scan_id,
        "pipeline": "lens-direct-v3",
        "top_match": top,
        "matches": matches,
        "match_count": len(matches),
        "listings_pending": False,
    }


def _merge_phases(phase1: dict[str, Any], phase2: dict[str, Any]) -> dict[str, Any]:
    out = {**phase1, **phase2}
    out["pipeline"] = "lens-direct-v3"
    out.pop("listings_pending", None)
    out.pop("phase", None)
    return out


def _tpt_boost_enabled() -> bool:
    v = (os.getenv("TPT_INVENTORY_BOOST") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def tpt_boost_enabled() -> bool:
    return _tpt_boost_enabled()


def _norm_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _rank_lens_matches(
    matches: list[dict],
    identity: dict[str, Any],
    *,
    hint: str = "",
) -> list[dict]:
    """Keep Lens order but drop junk and boost identity/hint hits."""
    brand = (identity.get("brand") or "").strip()
    ref = (identity.get("reference") or "").strip()
    brand_key = brand.lower()
    ref_key = _norm_alnum(ref)
    hint_words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]{3,}", hint) if len(w) >= 3]

    def blob(m: dict) -> str:
        return " ".join(
            [
                m.get("title") or "",
                m.get("link") or "",
                m.get("source") or "",
                m.get("source_name") or "",
            ]
        ).lower()

    def score(m: dict) -> int:
        text = blob(m)
        title = (m.get("title") or "").lower()
        link = (m.get("link") or "").lower()
        if JUNK_TITLE_RE.search(title) or JUNK_SOURCE_RE.search(text):
            return -100
        s = 0
        if brand_key and brand_key in text:
            s += 12
        if ref_key and ref_key in _norm_alnum(text):
            s += 18
        elif ref and ref.lower().replace(" ", "") in text.replace(" ", ""):
            s += 14
        for w in hint_words:
            if w in text:
                s += 4
        for d in TRUSTED_WATCH_SOURCES:
            if d in link:
                s += 3
                break
        pos = m.get("position") or 0
        if isinstance(pos, int):
            s += max(0, 6 - min(pos, 6))
        return s

    scored = [(score(m), i, m) for i, m in enumerate(matches)]
    kept = [m for sc, _, m in scored if sc > -50]
    if not kept:
        kept = list(matches)
    kept.sort(key=lambda m: (-score(m), matches.index(m) if m in matches else 999))
    return kept


def _tpt_domains() -> tuple[str, ...]:
    raw = (os.getenv("TPT_INVENTORY_DOMAINS") or "").strip()
    if raw:
        return tuple(d.strip().lower() for d in raw.split(",") if d.strip())
    return DEFAULT_TPT_DOMAINS


def _is_tpt_product_link(link: str) -> bool:
    low = (link or "").lower()
    if not any(domain in low for domain in _tpt_domains()):
        return False
    if TPT_JUNK_PATH_RE.search(low):
        return False
    return bool(TPT_PRODUCT_PATH_RE.search(low))


def _visual_tpt_product_count(matches: list[dict]) -> int:
    return sum(
        1
        for m in matches
        if _is_tpt_match(m) and _is_tpt_product_link(m.get("link") or "")
    )


def _is_tpt_match(match: dict) -> bool:
    blob = " ".join(
        [
            match.get("link") or "",
            match.get("source") or "",
            match.get("source_name") or "",
            match.get("title") or "",
        ]
    ).lower()
    return any(domain in blob for domain in _tpt_domains())


def _fetch_tpt_inventory_matches(identity: dict[str, Any], hint: str) -> list[dict]:
    """Text search Timepiece Trading inventory — surfaces TPT listings Lens visual may miss."""
    brand = (identity.get("brand") or "").strip()
    ref = (identity.get("reference") or "").strip()
    model = (identity.get("model") or "").strip()
    descriptors = identity.get("descriptors") or []
    identity_line = (identity.get("identity_line") or "").strip()

    queries = _build_tpt_search_queries(
        brand=brand,
        ref=ref,
        model=model,
        descriptors=descriptors,
        identity_line=identity_line,
        hint=hint,
    )
    if not queries:
        return []

    collected: list[dict] = []
    seen: set[str] = set()

    def _add(rows: list[dict], *, products_only: bool) -> None:
        for row in rows:
            link = (row.get("link") or "").strip()
            if not link or link in seen or not _is_tpt_match(row):
                continue
            if products_only and not _is_tpt_product_link(link):
                continue
            seen.add(link)
            collected.append(dict(row))

    with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
        futures = [
            pool.submit(lens.text_image_search, q, 8 if "site:" in q else 10)
            for q in queries
        ]
        for fut in as_completed(futures):
            try:
                _add(fut.result(), products_only=True)
            except Exception:
                pass

    # If we still have nothing, allow collection pages (but never blogs).
    if not collected:
        with ThreadPoolExecutor(max_workers=min(4, len(queries[:3]))) as pool:
            futures = [
                pool.submit(lens.text_image_search, q, 8 if "site:" in q else 10)
                for q in queries[:3]
            ]
            for fut in as_completed(futures):
                try:
                    rows = fut.result()
                    for row in rows:
                        link = (row.get("link") or "").strip()
                        if not link or link in seen or not _is_tpt_match(row):
                            continue
                        if TPT_JUNK_PATH_RE.search(link.lower()):
                            continue
                        seen.add(link)
                        collected.append(dict(row))
                except Exception:
                    pass

    return _rank_tpt_products(collected, brand=brand, ref=ref, descriptors=descriptors)[:6]


def _build_tpt_search_queries(
    *,
    brand: str,
    ref: str,
    model: str,
    descriptors: list[str],
    identity_line: str,
    hint: str,
) -> list[str]:
    """Build several Google queries — ref alone is often missing from Lens titles."""
    seeds: list[str] = []
    if hint.strip():
        seeds.append(hint.strip())
    if brand and ref:
        seeds.append(f"{brand} {ref}")
    if brand:
        seeds.append(brand)
        if ref:
            seeds.append(f"{brand} ref {ref}")
        desc = " ".join(descriptors[:2])
        if desc:
            seeds.append(f"{brand} {desc}")
        if "calatrava" in model.lower() or "calatrava" in identity_line.lower():
            seeds.append(f"{brand} Calatrava {ref or 'skeleton'}")
        if ref:
            seeds.append(ref)
            # Slash refs: also search compact form 5180/1G → 5180 1G
            if "/" in ref:
                seeds.append(ref.replace("/", " "))
    elif identity_line and len(identity_line) >= 8:
        seeds.append(identity_line.split("·")[0].strip())

    seeds = [s for s in dict.fromkeys(s.strip() for s in seeds if s and len(s.strip()) >= 4)]
    if not seeds:
        return []

    queries: list[str] = []
    for seed in seeds[:5]:
        for domain in _tpt_domains():
            queries.append(f"{seed} site:{domain}")
        queries.append(f"{seed} Timepiece Trading")
    return queries


def _rank_tpt_products(
    rows: list[dict],
    *,
    brand: str,
    ref: str,
    descriptors: list[str],
) -> list[dict]:
    ref_key = re.sub(r"[^a-z0-9]", "", (ref or "").lower())
    brand_key = (brand or "").lower()

    def score(row: dict) -> int:
        blob = " ".join(
            [
                row.get("link") or "",
                row.get("title") or "",
                row.get("source") or "",
            ]
        ).lower()
        s = 0
        if _is_tpt_product_link(row.get("link") or ""):
            s += 20
        if brand_key and brand_key in blob:
            s += 8
        if ref_key and ref_key in re.sub(r"[^a-z0-9]", "", blob):
            s += 15
        elif ref and ref.lower().replace("/", "") in blob.replace("/", ""):
            s += 10
        for d in descriptors[:3]:
            if d.lower() in blob:
                s += 3
        if "skeleton" in blob or "openwork" in blob:
            s += 2
        if TPT_JUNK_PATH_RE.search(blob):
            s -= 50
        return s

    return sorted(rows, key=score, reverse=True)


def _merge_with_tpt(
    visual_matches: list[dict],
    tpt_rows: list[dict],
) -> tuple[list[dict], bool]:
    seen: set[str] = set()
    tpt_out: list[dict] = []
    rest: list[dict] = []

    for row in tpt_rows:
        link = (row.get("link") or "").strip()
        if not link or link in seen:
            continue
        seen.add(link)
        tagged = dict(row)
        tagged["tpt_inventory"] = True
        tpt_out.append(tagged)

    for row in visual_matches:
        link = (row.get("link") or "").strip()
        if link in seen:
            continue
        if link:
            seen.add(link)
        tagged = dict(row)
        if _is_tpt_match(tagged):
            tagged["tpt_inventory"] = True
            tpt_out.append(tagged)
        else:
            rest.append(tagged)

    merged = tpt_out + rest
    return merged, len(tpt_out) > 0


def _save_tpt_upload(jpg_bytes: bytes) -> str:
    fname = f"{uuid.uuid4().hex}.jpg"
    out = bootstrap.UPLOAD_DIR / fname
    out.write_bytes(jpg_bytes)
    return f"/uploads/{fname}"


def _filter_watch_matches(matches: list[dict]) -> list[dict]:
    if not matches:
        return []
    kept = [m for m in matches if not JUNK_TITLE_RE.search(m.get("title") or "")]
    return kept or matches


def _format_matches(matches: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, m in enumerate(matches, 1):
        row = dict(m)
        row["position"] = i
        row["price_display"] = _price_display(m.get("price"))
        link = (row.get("link") or "").lower()
        row["trusted_dealer"] = row.get("tpt_inventory") or any(d in link for d in TRUSTED_WATCH_SOURCES)
        if row.get("tpt_inventory"):
            row["source"] = row.get("source") or "timepiecetradingllc.com"
        out.append(row)
    return out


def _price_display(price: Any) -> str:
    if not price:
        return ""
    if isinstance(price, dict):
        for key in ("extracted_value", "value", "raw"):
            val = price.get(key)
            if val:
                return str(val).strip()
        currency = price.get("currency") or ""
        val = price.get("value")
        if val and currency:
            return f"{currency} {val}".strip()
    return str(price).strip()


def _extract_watch_identity(matches: list[dict], *, hint: str = "") -> dict[str, Any]:
    titles = [(m.get("title") or "").strip() for m in matches[:10] if (m.get("title") or "").strip()]
    if hint:
        titles.insert(0, hint)

    brand = _vote_brand(titles)
    reference = _vote_reference(titles)
    descriptors = _collect_descriptors(titles)

    model = _build_model_name(brand, reference, titles)
    identity_line = _build_identity_line(brand, model, reference, descriptors)

    brand_hits = sum(1 for t in titles if brand and brand.lower() in t.lower()) if brand else 0
    ref_hits = sum(1 for t in titles if reference and reference.replace(" ", "").lower() in t.replace(" ", "").lower()) if reference else 0

    confidence_pct = 55
    if brand_hits >= 5 and ref_hits >= 3:
        confidence_pct = 96
        tier = "exact"
    elif brand_hits >= 3 and ref_hits >= 2:
        confidence_pct = 88
        tier = "exact"
    elif brand_hits >= 2:
        confidence_pct = 78
        tier = "likely"
    elif brand:
        confidence_pct = 65
        tier = "likely"
    else:
        tier = "possible"

    return {
        "brand": brand,
        "model": model,
        "reference": reference,
        "descriptors": descriptors,
        "identity_line": identity_line,
        "confidence_pct": confidence_pct,
        "confidence_tier": tier,
    }


def _vote_brand(titles: list[str]) -> str:
    counts: Counter[str] = Counter()
    for brand, pat in BRAND_PATTERNS:
        hits = sum(1 for t in titles if pat.search(t))
        if hits:
            counts[brand] = hits
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _normalize_rm_ref(a: str, b: str) -> str:
    return f"RM {a}-{b}"


def _vote_reference(titles: list[str]) -> str:
    refs: list[str] = []
    for title in titles:
        for pat in REF_PATTERNS:
            m = pat.search(title)
            if not m:
                continue
            groups = m.groups()
            if len(groups) == 2 and groups[0].isdigit() and groups[1].isdigit():
                refs.append(_normalize_rm_ref(groups[0], groups[1]))
            elif groups:
                raw = groups[0].strip().upper()
                if raw.startswith("RM"):
                    raw = re.sub(r"RM\s*", "RM ", raw)
                refs.append(_normalize_watch_ref(raw))
    if not refs:
        return ""
    # Prefer longer refs (5180/1G beats bare 5180).
    refs.sort(key=len, reverse=True)
    return Counter(refs).most_common(1)[0][0]


def _normalize_watch_ref(raw: str) -> str:
    """Normalize refs like 5180/1G-001 → 5180/1G when possible."""
    val = raw.strip().upper()
    m = re.match(r"^(\d{4,5}/\d{1,2}[A-Z]?)", val)
    if m:
        return m.group(1)
    m = re.match(r"^(\d{4,6}[A-Z]{0,4})", val)
    if m:
        return m.group(1)
    return val


def _collect_descriptors(titles: list[str]) -> list[str]:
    blob = " ".join(titles).lower()
    found: list[str] = []
    for phrase in DESCRIPTOR_WORDS:
        if phrase in blob and phrase not in found:
            found.append(phrase.title() if phrase != "gmt" else "GMT")
    return found[:4]


def _build_model_name(brand: str, reference: str, titles: list[str]) -> str:
    if brand and reference:
        return f"{brand} {reference}".strip()
    if brand and titles:
        t = titles[0]
        t = re.sub(re.escape(brand), "", t, flags=re.I).strip(" -–|,")
        return (brand + " " + t[:70]).strip()
    return titles[0][:90] if titles else ""


def _build_identity_line(
    brand: str,
    model: str,
    reference: str,
    descriptors: list[str],
) -> str:
    parts: list[str] = []
    if model:
        parts.append(model)
    elif brand:
        parts.append(brand)
        if reference:
            parts.append(reference)
    desc = " · ".join(descriptors[:3])
    if desc:
        if parts:
            return f"{parts[0]} · {desc}"
        return desc
    return parts[0] if parts else "Unknown watch"
