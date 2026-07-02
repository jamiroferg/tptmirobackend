"""TPT Watch MVP — photo → identify → listing draft."""

from __future__ import annotations

import asyncio
from typing import Optional, Union

import bootstrap  # noqa: F401 — path + env setup

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lib.media import normalize_to_jpeg_bytes
from inventory import create_watch, get_watch, list_inventory
from inventory_draft import draft_inventory_from_image, draft_inventory_from_selection, identify_inventory_image
from inventory_store import storage_backend
from db import db_health, init_db, postgres_enabled, seed_inventory_if_empty
from inventory_seed import DEFAULT_INVENTORY
from listing import generate_listing_draft
from tryon_service import is_configured as tryon_configured
from tryon_service import prefetch_watch_image, run_try_on
from watch_service import fetch_listings, identify_identity, identify_watch, schedule_warm_identify, tpt_boost_enabled

app = FastAPI(title="TPT Watch MVP", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(bootstrap.UPLOAD_DIR)), name="tpt-uploads")


@app.on_event("startup")
def _startup_db() -> None:
    if not postgres_enabled():
        return
    init_db()
    seed_inventory_if_empty(DEFAULT_INVENTORY)


class ListingRequest(BaseModel):
    identification: dict
    notes: str = ""


class ListingsRequest(BaseModel):
    scan_id: str


class CreateInventoryRequest(BaseModel):
    title: str
    brand: str
    model: str = ""
    reference: str = ""
    case_material: str = ""
    dial: str = ""
    case_size_mm: Optional[int] = None
    stones: str = ""
    subtitle: str = ""
    price_usd: Optional[Union[int, float]] = None
    image_url: str
    product_url: str = ""
    inquire_url: str = ""
    in_stock: bool = True


class InventoryDraftRequest(BaseModel):
    identification: dict
    selected_match: Optional[dict] = None
    image_url: str = ""


@app.get("/api/health")
def health() -> dict:
    pg = db_health()
    return {
        "ok": True,
        "service": "tpt-watch-mvp",
        "pipeline": "lens-direct-v3",
        "phases": ["identify"],
        "tpt_boost": tpt_boost_enabled(),
        "identify_cache": True,
        "try_on": tryon_configured(),
        "inventory_storage": storage_backend(),
        "postgres": pg,
    }


@app.get("/api/inventory")
def inventory_list() -> dict:
    return {"ok": True, "items": list_inventory()}


@app.post("/api/inventory/identify")
async def inventory_identify(
    file: Optional[UploadFile] = File(None),
    image_url: str = Form(""),
    hint: str = Form(""),
) -> dict:
    """Upload or paste image → Lens matches only (dealer picks listing next)."""
    url = (image_url or "").strip()
    raw: bytes | None = None
    if file is not None:
        raw = await file.read()
    if not raw and not url:
        raise HTTPException(status_code=400, detail="Upload a photo or paste an image URL")
    if raw is not None and not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        return await asyncio.to_thread(
            identify_inventory_image,
            file_bytes=raw,
            image_url=url if not raw else "",
            hint=hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/inventory/draft")
async def inventory_draft(body: InventoryDraftRequest) -> dict:
    """Selected listing + Lens identity → AI-filled TPT spec fields."""
    if not body.identification:
        raise HTTPException(status_code=400, detail="identification required")
    try:
        return await asyncio.to_thread(
            draft_inventory_from_selection,
            identification=body.identification,
            selected_match=body.selected_match,
            image_url=body.image_url.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/inventory/draft/quick")
async def inventory_draft_quick(
    file: Optional[UploadFile] = File(None),
    image_url: str = Form(""),
    hint: str = Form(""),
) -> dict:
    """Legacy one-shot: identify + draft from first match."""
    url = (image_url or "").strip()
    raw: bytes | None = None
    if file is not None:
        raw = await file.read()
    if not raw and not url:
        raise HTTPException(status_code=400, detail="Upload a photo or paste an image URL")
    if raw is not None and not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        return await asyncio.to_thread(
            draft_inventory_from_image,
            file_bytes=raw,
            image_url=url if not raw else "",
            hint=hint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/inventory")
async def inventory_create(body: CreateInventoryRequest) -> dict:
    """Add a watch to inventory (dealer sets price manually)."""
    try:
        item = await asyncio.to_thread(
            create_watch,
            body.model_dump(exclude_none=False),
        )
        return {"ok": True, "item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/inventory/{watch_id}")
def inventory_item(watch_id: str) -> dict:
    row = get_watch(watch_id)
    if not row:
        raise HTTPException(status_code=404, detail="watch not found")
    return {"ok": True, "item": row}


@app.post("/api/try-on/prefetch")
async def try_on_prefetch(watch_image_url: str = Form(...)) -> dict:
    url = (watch_image_url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="watch_image_url required")
    try:
        cached, nbytes = await asyncio.to_thread(prefetch_watch_image, url)
        return {"ok": True, "cached": cached, "bytes": nbytes}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/try-on")
async def try_on(
    wrist_file: UploadFile = File(...),
    watch_id: str = Form(""),
    watch_image_url: str = Form(""),
) -> dict:
    """Wrist photo + inventory watch → try-before-you-inquire composite."""
    if not tryon_configured():
        raise HTTPException(
            status_code=503,
            detail="Try-on is not configured: set API4AI_API_KEY or RAPID_API_KEY",
        )
    raw = await wrist_file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty wrist photo")
    try:
        return await asyncio.to_thread(
            run_try_on,
            wrist_bytes=raw,
            watch_id=watch_id.strip() or None,
            watch_image_url=watch_image_url.strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/prepare-photo")
async def prepare_photo(file: UploadFile = File(...)) -> Response:
    """Convert any upload (HEIC/JPEG/PNG) → JPEG for crop preview."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        jpeg = await asyncio.to_thread(normalize_to_jpeg_bytes, raw)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=jpeg, media_type="image/jpeg")


@app.post("/api/identify")
async def identify(
    file: UploadFile = File(...),
    hint: str = Form(""),
) -> dict:
    """Phase 1 — Lens + identity. Returns scan_id; call /api/identify/listings next."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        return await asyncio.to_thread(identify_identity, raw, hint=hint)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/identify/listings")
async def identify_listings(body: ListingsRequest) -> dict:
    """Phase 2 — finalize ranked Lens listing matches."""
    if not body.scan_id.strip():
        raise HTTPException(status_code=400, detail="scan_id required")
    try:
        return await asyncio.to_thread(fetch_listings, body.scan_id.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/identify/warm")
async def identify_warm(
    file: UploadFile = File(...),
    hint: str = Form(""),
) -> dict:
    """Background identify while user finishes crop — same cache as /identify/full."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        scheduled = await asyncio.to_thread(schedule_warm_identify, raw, hint=hint)
        return {"ok": True, "scheduled": scheduled}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/identify/full")
async def identify_full(
    file: UploadFile = File(...),
    hint: str = Form(""),
) -> dict:
    """Single-shot identify (Phase 1 + 2 combined)."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    try:
        return await asyncio.to_thread(identify_watch, raw, hint=hint)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/listing-draft")
async def listing_draft(body: ListingRequest) -> dict:
    if not body.identification:
        raise HTTPException(status_code=400, detail="identification required")
    try:
        draft = generate_listing_draft(body.identification, extra_notes=body.notes)
        return {"ok": True, "listing": draft}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
