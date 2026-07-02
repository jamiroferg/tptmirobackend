"""Reverse-image-search via Google Lens (RapidAPI).

Flow:
  user phone pic
    -> normalize to JPEG (images.normalize_to_jpeg_bytes)
    -> upload to a public temp host so RapidAPI can fetch it (catbox.moe)
    -> POST /visual-matches on real-time-lens-data
    -> return clean list of matches (title, link, image, price, source)

Matches contain a `link` to the product page, which can then be fed into
the existing url_fetcher + ai.parse_item_from_url pipeline.
"""

import hashlib
import http.client
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from lib.ops_log import log_api_call, record_api

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE / ".env", _HERE.parent / ".env", _HERE.parent.parent / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)
load_dotenv(override=False)

logger = logging.getLogger(__name__)

RAPID_API_KEY = os.getenv("RAPID_API_KEY")


def _imgbb_key() -> Optional[str]:
    """Read imgbb API key dynamically so .env edits take effect without restart.
    Accepts either IMGBB_API_KEY or IMG_BB_API_KEY env var name."""
    for name in ("IMGBB_API_KEY", "IMG_BB_API_KEY"):
        key = (os.getenv(name) or "").strip()
        if key:
            return key
    return None
LENS_HOST = "real-time-lens-data.p.rapidapi.com"
SEARCH_HOST = "real-time-image-search.p.rapidapi.com"

# Back-compat alias for older imports
RAPID_API_HOST = LENS_HOST

CATBOX_URL = "https://catbox.moe/user/api.php"
LITTERBOX_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
ZEROXZERO_URL = "https://0x0.st"
FILEIO_URL = "https://file.io"
TRANSFERSH_URL = "https://transfer.sh"
IMGBB_URL = "https://api.imgbb.com/1/upload"
UPLOAD_TIMEOUT = 20.0
# Per-call ceiling. With hedging on, a stuck call is abandoned for a fresh one
# long before this, so we keep it tight instead of the old 45s.
LENS_TIMEOUT = float(os.getenv("LENS_TIMEOUT", "20"))
LENS_DETAIL_TIMEOUT = float(os.getenv("LENS_DETAIL_TIMEOUT", "18"))
# If a Lens call hasn't returned within this many seconds, fire a parallel
# hedge and take whichever finishes first. 0 disables hedging.
LENS_HEDGE_DELAY = float(os.getenv("LENS_HEDGE_DELAY", "6"))
# Back-compat
CATBOX_TIMEOUT = UPLOAD_TIMEOUT

# Browser-like UA — catbox started 412'ing default httpx UAs in 2026.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# --- RapidAPI rate limiter ---------------------------------------------------
# RapidAPI Pro plan: 2 requests per second across the account. When we run
# parallel per-piece Lens enrichment we'd burst past that and get 429s. This
# sliding-window limiter gates all _rapid_get() calls so threads naturally
# space themselves out without us having to coordinate at the call sites.

_RAPID_RPS = int(os.getenv("RAPID_API_RPS", "8"))
_rate_lock = threading.Lock()
_recent_calls: List[float] = []


def _throttle_rapid() -> None:
    """Sliding-window limiter: admit up to _RAPID_RPS calls per rolling 1s.

    Unlike a strict min-interval (which serialized every call ~0.6s apart and
    killed multi-piece/hedged concurrency), this lets calls burst up to the
    plan's per-second cap, so parallel pieces and latency hedges fire together
    and only block once the window is actually full.
    """
    window = 1.0
    while True:
        with _rate_lock:
            now = time.monotonic()
            cutoff = now - window
            while _recent_calls and _recent_calls[0] <= cutoff:
                _recent_calls.pop(0)
            if len(_recent_calls) < max(1, _RAPID_RPS):
                _recent_calls.append(now)
                return
            wait = _recent_calls[0] + window - now + 0.01
        time.sleep(max(0.01, wait))


def _rapid_get(host: str, path: str, timeout: float = LENS_TIMEOUT) -> dict:
    """Single-shot GET to a RapidAPI host. Returns parsed JSON dict.
    Throttled to _RAPID_RPS requests per second across the whole process."""
    if not RAPID_API_KEY:
        raise RuntimeError("RAPID_API_KEY is not set")
    service = "rapidapi_lens" if host == LENS_HOST else "rapidapi_search"
    t0 = time.monotonic()
    _throttle_rapid()
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request("GET", path, headers={
            "x-rapidapi-key": RAPID_API_KEY,
            "x-rapidapi-host": host,
        })
        res = conn.getresponse()
        body = res.read().decode("utf-8")
    finally:
        conn.close()
    ms = int((time.monotonic() - t0) * 1000)
    if res.status != 200:
        record_api(service)
        log_api_call(service, ms=ms, ok=False, detail=f"HTTP {res.status}: {body[:120]}")
        raise RuntimeError(f"{host} returned {res.status}: {body[:300]}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        record_api(service)
        log_api_call(service, ms=ms, ok=False, detail="non-JSON response")
        raise RuntimeError(f"{host} returned non-JSON")
    n_results = 0
    data = payload.get("data")
    if isinstance(data, list):
        n_results = len(data)
    elif isinstance(data, dict):
        n_results = len(data.get("visual_matches") or [])
    record_api(service)
    endpoint = path.split("?")[0].lstrip("/") or "search"
    if host != LENS_HOST:
        endpoint = path.split("?")[0]
    log_api_call(
        service,
        ms=ms,
        ok=True,
        detail=f"{endpoint} results={n_results}",
    )
    return payload


def _rapid_get_hedged(host: str, path: str, timeout: float = LENS_TIMEOUT) -> dict:
    """Latency-hedged GET for Lens visual search.

    Issues the request; if it hasn't returned within ``LENS_HEDGE_DELAY``s,
    fires a second identical request in parallel and returns whichever succeeds
    first. Same host/path → identical results, so quality is unchanged; this
    only trims the upstream latency tail (p90/p99). Costs one extra API call,
    and only on the slow scans. ``LENS_HEDGE_DELAY=0`` disables it.
    """
    if LENS_HEDGE_DELAY <= 0:
        return _rapid_get(host, path, timeout=timeout)

    from concurrent.futures import FIRST_COMPLETED
    from concurrent.futures import wait as _fwait

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        primary = pool.submit(_rapid_get, host, path, timeout)
        done, _ = _fwait([primary], timeout=LENS_HEDGE_DELAY)
        if primary in done:
            try:
                return primary.result()
            except Exception:
                pass  # primary failed fast — let the hedge below cover it
        pending = {primary, pool.submit(_rapid_get, host, path, timeout)}
        last_err: Optional[Exception] = None
        while pending:
            done, pending = _fwait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                try:
                    return fut.result()
                except Exception as e:  # noqa: BLE001 — keep trying the other future
                    last_err = e
        raise last_err or RuntimeError("lens hedged request failed")
    finally:
        pool.shutdown(wait=False)


class LensMatch(TypedDict, total=False):
    position: int
    title: str
    link: str           # product page URL — feed this back into parse-url
    source: str         # hostname e.g. "www.ebay.com"
    source_name: str    # display e.g. "eBay"
    source_icon: str
    thumbnail: str      # small preview
    image: str          # full image URL
    price: Optional[str]
    availability: Optional[str]


@dataclass
class LensSearchBundle:
    """Full Lens /search?url= response — visual hits plus discovery metadata."""

    matches: List[LensMatch] = field(default_factory=list)
    related_searches: List[Any] = field(default_factory=list)
    organic_results: List[Any] = field(default_factory=list)
    knowledge_graph: List[Any] = field(default_factory=list)
    ocr_text: str = ""

    def extras_dict(self) -> Dict[str, Any]:
        return {
            "related_searches": list(self.related_searches),
            "organic_results": list(self.organic_results),
            "knowledge_graph": list(self.knowledge_graph),
            "ocr_text": self.ocr_text,
        }


_UPLOAD_CACHE: "OrderedDict[str, str]" = OrderedDict()
_UPLOAD_CACHE_MAX = int(os.getenv("LENS_UPLOAD_CACHE_MAX", "96"))


def upload_to_temp_host(jpg_bytes: bytes, filename: str = "img.jpg") -> str:
    """Upload bytes to a public host. Returns the public URL.

    Tries multiple hosts in order — public free hosts get rate-limited or
    change auth requirements unpredictably. First host to succeed wins.

    If IMGBB_API_KEY is set in env, imgbb is tried first (most reliable,
    free tier covers thousands of uploads). Get one at https://api.imgbb.com.

    Identical crop bytes reuse the cached public URL (saves ~1s/imgbb per piece).
    """
    digest = hashlib.sha256(jpg_bytes).hexdigest()
    cached = _UPLOAD_CACHE.get(digest)
    if cached:
        record_api("imgbb_cache")
        return cached

    chain = []
    if _imgbb_key():
        chain.append(("imgbb", _upload_imgbb))
    chain.extend([
        ("catbox", _upload_catbox),
        ("litterbox", _upload_litterbox),
        ("0x0.st", _upload_0x0),
        ("transfer.sh", _upload_transfer_sh),
        ("file.io", _upload_fileio),
    ])

    last_err: Optional[Exception] = None
    kb = max(1, len(jpg_bytes) // 1024)
    for host_name, uploader in chain:
        try:
            t0 = time.monotonic()
            url = uploader(jpg_bytes, filename)
            ms = int((time.monotonic() - t0) * 1000)
            if url and url.startswith("http"):
                svc = "imgbb" if host_name == "imgbb" else "temp_host"
                record_api(svc if host_name == "imgbb" else "temp_host")
                log_api_call(
                    svc if host_name == "imgbb" else "temp_host",
                    ms=ms,
                    ok=True,
                    detail=f"{host_name} {kb}KB -> {url[:80]}",
                )
                _UPLOAD_CACHE[digest] = url
                while len(_UPLOAD_CACHE) > _UPLOAD_CACHE_MAX:
                    _UPLOAD_CACHE.popitem(last=False)
                return url
        except Exception as e:
            last_err = e
            logger.warning("[ops] temp host %s failed (%dKB): %s", host_name, kb, e)
            continue
    raise RuntimeError(
        f"all {len(chain)} temp hosts failed. last error: {last_err}. "
        f"set IMGBB_API_KEY env var for a stable upload host."
    )


def _upload_catbox(jpg_bytes: bytes, filename: str) -> str:
    files = {"fileToUpload": (filename, jpg_bytes, "image/jpeg")}
    data = {"reqtype": "fileupload"}
    headers = {"User-Agent": _BROWSER_UA}
    r = httpx.post(CATBOX_URL, data=data, files=files, headers=headers, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox returned unexpected response: {url[:200]}")
    return url


def _upload_litterbox(jpg_bytes: bytes, filename: str) -> str:
    """Catbox's temporary file host (1h retention). Same API, often less locked-down."""
    files = {"fileToUpload": (filename, jpg_bytes, "image/jpeg")}
    data = {"reqtype": "fileupload", "time": "1h"}
    headers = {"User-Agent": _BROWSER_UA}
    r = httpx.post(LITTERBOX_URL, data=data, files=files, headers=headers, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"litterbox returned unexpected response: {url[:200]}")
    return url


def _upload_0x0(jpg_bytes: bytes, filename: str) -> str:
    """0x0.st — anonymous file host. Requires a custom User-Agent."""
    files = {"file": (filename, jpg_bytes, "image/jpeg")}
    headers = {"User-Agent": "ClosetAI/0.1 (+https://github.com/local)"}
    r = httpx.post(ZEROXZERO_URL, files=files, headers=headers, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"0x0.st returned unexpected response: {url[:200]}")
    return url


def _upload_transfer_sh(jpg_bytes: bytes, filename: str) -> str:
    """transfer.sh — PUT-style upload. Returns plain text URL."""
    headers = {"User-Agent": _BROWSER_UA}
    r = httpx.put(
        f"{TRANSFERSH_URL}/{filename or 'img.jpg'}",
        content=jpg_bytes,
        headers=headers,
        timeout=UPLOAD_TIMEOUT,
    )
    r.raise_for_status()
    url = (r.text or "").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"transfer.sh returned unexpected response: {url[:200]}")
    return url


def _upload_fileio(jpg_bytes: bytes, filename: str) -> str:
    """file.io — JSON response with `link` field. Files auto-delete after 1 view."""
    files = {"file": (filename, jpg_bytes, "image/jpeg")}
    headers = {"User-Agent": _BROWSER_UA}
    # `expires=1d` lets the file live longer than the default
    data = {"expires": "1d", "maxDownloads": "100"}
    r = httpx.post(FILEIO_URL, data=data, files=files, headers=headers, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    url = payload.get("link") or ""
    if not url.startswith("http"):
        raise RuntimeError(f"file.io returned: {payload}")
    return url


def _upload_imgbb(jpg_bytes: bytes, filename: str) -> str:
    """imgbb.com — most reliable option (paid + free keys both work).
    Get one at https://api.imgbb.com (instant, no email confirmation)."""
    key = _imgbb_key()
    if not key:
        raise RuntimeError("IMGBB_API_KEY not set")
    import base64 as _b64
    data = {
        "key": key,
        "image": _b64.b64encode(jpg_bytes).decode("ascii"),
        "name": (filename or "img").rsplit(".", 1)[0],  # imgbb wants no extension
    }
    r = httpx.post(IMGBB_URL, data=data, timeout=UPLOAD_TIMEOUT)
    if r.status_code != 200:
        # Surface auth/quota issues clearly since user is paying for this
        raise RuntimeError(
            f"imgbb HTTP {r.status_code}: {r.text[:200]}"
        )
    payload = r.json()
    if not payload.get("success"):
        raise RuntimeError(f"imgbb error: {payload.get('error') or payload}")
    url = ((payload.get("data") or {}).get("url")) or ""
    if not url.startswith("http"):
        raise RuntimeError(f"imgbb returned no url: {payload}")
    return url


def _normalize_visual_match(raw: dict) -> Optional[LensMatch]:
    if not isinstance(raw, dict):
        return None
    link = (raw.get("link") or raw.get("url") or "").strip()
    if not link:
        return None
    return {
        "position": raw.get("position") or raw.get("rank"),
        "title": raw.get("title", ""),
        "link": link,
        "source": raw.get("source") or raw.get("domain") or "",
        "source_name": raw.get("source_name") or raw.get("source") or "",
        "source_icon": raw.get("source_icon", ""),
        "thumbnail": raw.get("thumbnail", ""),
        "image": raw.get("image", ""),
        "price": raw.get("price"),
        "availability": raw.get("availability"),
    }


def _clean_visual_matches(
    raw_matches: List[Any],
    *,
    limit: int,
    hint: str,
) -> List[LensMatch]:
    cleaned: List[LensMatch] = []
    seen_links: set = set()
    for raw in raw_matches or []:
        row = _normalize_visual_match(raw)
        if not row:
            continue
        link = row.get("link") or ""
        if link in seen_links:
            continue
        seen_links.add(link)
        if row.get("position") is None:
            row["position"] = len(cleaned) + 1
        cleaned.append(row)
        if len(cleaned) >= limit * 2:
            break
    # When query= was sent to Lens, preserve the API's visual order — do not re-sort locally.
    if hint and hint.strip():
        return cleaned[:limit]
    cleaned = _rerank_by_hint(cleaned, hint)
    return cleaned[:limit]


def _extract_bundle_block(payload: dict) -> Tuple[List[Any], Dict[str, Any]]:
    """Return (visual_match_rows, bundle_metadata) from either API shape."""
    meta: Dict[str, Any] = {
        "related_searches": [],
        "organic_results": [],
        "knowledge_graph": [],
        "ocr_text": "",
    }
    # RapidAPI may return visual_matches at top level or under data.
    top_visual = payload.get("visual_matches")
    if isinstance(top_visual, list) and top_visual:
        meta["related_searches"] = list(payload.get("related_searches") or [])
        meta["organic_results"] = list(payload.get("organic_results") or [])
        meta["knowledge_graph"] = list(payload.get("knowledge_graph") or [])
        return top_visual, meta

    data = payload.get("data")
    if isinstance(data, list):
        return data, meta
    if not isinstance(data, dict):
        return [], meta
    visual = data.get("visual_matches") or []
    meta["related_searches"] = list(data.get("related_searches") or [])
    meta["organic_results"] = list(data.get("organic_results") or [])
    meta["knowledge_graph"] = list(data.get("knowledge_graph") or [])
    ocr = data.get("ocr") or data.get("ocr_text") or data.get("image_to_text") or ""
    if isinstance(ocr, dict):
        ocr = ocr.get("text") or ocr.get("full_text") or ""
    meta["ocr_text"] = str(ocr or "").strip()
    return visual, meta


def parse_lens_search_payload(
    payload: dict,
    *,
    limit: int = 12,
    hint: str = "",
) -> LensSearchBundle:
    """Parse /search?url= or legacy /visual-matches JSON into a bundle."""
    visual, meta = _extract_bundle_block(payload)
    matches = _clean_visual_matches(visual, limit=limit, hint=hint)
    return LensSearchBundle(
        matches=matches,
        related_searches=meta["related_searches"],
        organic_results=meta["organic_results"],
        knowledge_graph=meta["knowledge_graph"],
        ocr_text=meta["ocr_text"],
    )


def organic_results_to_matches(
    organic_results: List[Any],
    *,
    limit: int = 12,
    seen_links: Optional[set] = None,
) -> List[LensMatch]:
    """Convert Lens organic web results into match rows (lower-priority pool)."""
    out: List[LensMatch] = []
    seen = set(seen_links) if seen_links is not None else set()
    for raw in organic_results or []:
        if not isinstance(raw, dict):
            continue
        link = (raw.get("url") or raw.get("link") or "").strip()
        if not link or link in seen:
            continue
        seen.add(link)
        out.append({
            "position": raw.get("position") or raw.get("rank"),
            "title": raw.get("title", ""),
            "link": link,
            "source": raw.get("domain") or raw.get("source") or "",
            "source_name": raw.get("source") or raw.get("domain") or "",
            "source_icon": "",
            "thumbnail": "",
            "image": "",
            "price": None,
            "availability": None,
        })
        if len(out) >= limit:
            break
    return out


def _lens_search_bundle_enabled() -> bool:
    v = (os.getenv("LENS_SEARCH_BUNDLE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def lens_image_search(
    public_image_url: str,
    limit: int = 12,
    hint: str = "",
    language: str = "en",
    country: str = "us",
    timeout: Optional[float] = None,
) -> LensSearchBundle:
    """Call Real-Time Lens Data /search?url= — full bundle (visual + related + organic)."""
    encoded = urllib.parse.quote(public_image_url, safe="")
    query = f"url={encoded}&language={language}&country={country}"
    if hint:
        query += f"&query={urllib.parse.quote(hint)}"
    path = f"/search?{query}"
    try:
        payload = _rapid_get_hedged(LENS_HOST, path, timeout=timeout or LENS_TIMEOUT)
    except Exception as e:
        if not _lens_search_bundle_enabled():
            raise
        logger.warning("[ops] Lens /search failed (%s); falling back to /visual-matches", e)
        return _visual_matches_legacy(public_image_url, limit=limit, hint=hint, language=language, country=country, timeout=timeout)
    bundle = parse_lens_search_payload(payload, limit=limit, hint=hint)
    if bundle.matches:
        return bundle
    logger.warning("[ops] Lens /search returned no visual_matches; trying /visual-matches")
    return _visual_matches_legacy(public_image_url, limit=limit, hint=hint, language=language, country=country, timeout=timeout)


def _visual_matches_legacy(
    public_image_url: str,
    *,
    limit: int,
    hint: str,
    language: str,
    country: str,
    timeout: Optional[float],
) -> LensSearchBundle:
    encoded = urllib.parse.quote(public_image_url, safe="")
    path = f"/visual-matches?url={encoded}&language={language}&country={country}"
    if hint and hint.strip():
        path += f"&query={urllib.parse.quote(hint.strip())}"
    payload = _rapid_get_hedged(LENS_HOST, path, timeout=timeout or LENS_TIMEOUT)
    return parse_lens_search_payload(payload, limit=limit, hint=hint)


def visual_matches(
    public_image_url: str,
    limit: int = 12,
    hint: str = "",
    language: str = "en",
    country: str = "us",
    timeout: Optional[float] = None,
) -> List[LensMatch]:
    """Lens visual product hits for a hosted image URL.

    Uses /search?url= (full bundle) by default; returns visual_matches only.
    """
    if _lens_search_bundle_enabled():
        return lens_image_search(
            public_image_url,
            limit=limit,
            hint=hint,
            language=language,
            country=country,
            timeout=timeout,
        ).matches
    return _visual_matches_legacy(
        public_image_url,
        limit=limit,
        hint=hint,
        language=language,
        country=country,
        timeout=timeout,
    ).matches


def _rerank_by_hint(matches: List[LensMatch], hint: str) -> List[LensMatch]:
    """Stable sort: matches whose title/source contains more hint words go first."""
    if not hint or not matches:
        return matches
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", hint)]
    if not words:
        return matches

    def score(m: LensMatch) -> int:
        hay = ((m.get("title") or "") + " " + (m.get("source_name") or "")).lower()
        return sum(1 for w in words if w in hay)

    return sorted(matches, key=score, reverse=True)


def text_image_search(
    query: str,
    limit: int = 12,
    region: str = "us",
) -> List[LensMatch]:
    """Google-style image search by text. Returns the same Match shape as visual_matches.

    Uses real-time-image-search.p.rapidapi.com. Each result includes a
    `source_url` (the actual product page) which we use as `link` so the
    existing parse-url pipeline can ingest it.
    """
    encoded = urllib.parse.quote(query)
    path = (
        f"/search?query={encoded}&limit={max(1, min(limit, 50))}"
        f"&size=any&color=any&type=any&time=any"
        f"&usage_rights=any&file_type=any&aspect_ratio=any"
        f"&safe_search=off&region={region}"
    )
    payload = _rapid_get(SEARCH_HOST, path)

    items = payload.get("data") or []
    cleaned: List[LensMatch] = []
    seen_links = set()
    for m in items:
        link = m.get("source_url")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        # Price is a number + currency in this API; flatten to a display string.
        price_display: Optional[str] = None
        if m.get("price") is not None:
            cur = m.get("currency") or "USD"
            symbol = "$" if cur == "USD" else f"{cur} "
            price_display = f"{symbol}{m['price']}"
        cleaned.append({
            "position": m.get("position"),
            "title": m.get("title", ""),
            "link": link,
            "source": m.get("source_domain", ""),
            "source_name": m.get("source", ""),
            "source_icon": "",  # not provided by this API
            "thumbnail": m.get("url", ""),          # Google-cached small
            "image": m.get("thumbnail_url", ""),    # full from source CDN
            "price": price_display,
            "availability": None,
        })
        if len(cleaned) >= limit:
            break
    return cleaned


def query_from_url_slug(page_url: str) -> str:
    """Turn a product page URL into a search query string.

    e.g. https://www.neimanmarcus.com/p/burberry-mens-check-collar-cotton-polo-shirt-prod287890092
         -> 'burberry mens check collar cotton polo shirt'
    """
    try:
        path = urlparse(page_url).path.strip("/")
    except Exception:
        return ""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    slug = parts[-1].split("?")[0]
    # Drop trailing product id segments like "prod287890092", "id12345", "p287890092"
    slug = re.sub(r"\b(prod|p|id|item|sku)[\d_]+\b", "", slug, flags=re.I)
    # Underscore/dash → space
    q = re.sub(r"[-_]+", " ", slug)
    # Drop pure number tokens
    q = " ".join(t for t in q.split() if not t.isdigit())
    q = re.sub(r"\s+", " ", q).strip()
    return q


def image_url_for_blocked_page(page_url: str) -> Optional[str]:
    """Last-resort: when a product page is bot-walled and we have no image,
    derive a search query from its URL slug and return the top result's image URL.

    Returns None if the slug is too generic (< 2 alphabetic words) or the
    search returns nothing.
    """
    query = query_from_url_slug(page_url)
    words = [w for w in query.split() if any(c.isalpha() for c in w)]
    if len(words) < 2:
        return None
    try:
        results = text_image_search(query, limit=3)
    except Exception as e:
        logger.warning("slug image search failed for %s: %s", page_url, e)
        return None
    for r in results:
        candidate = r.get("image") or r.get("thumbnail")
        if candidate:
            return candidate
    return None
