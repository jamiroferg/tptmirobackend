"""Text-only JSON LLM helpers for TPT listing drafts (standalone — no Kizzum imports)."""

from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Optional

import anthropic
import google.generativeai as genai
from fastapi import HTTPException

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def gemini_api_key() -> Optional[str]:
    raw = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        raw = raw[1:-1]
    return raw or None


def anthropic_api_key() -> Optional[str]:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None


def _parse_first_json_object(text: str, start: int) -> dict:
    decoder = json.JSONDecoder()
    try:
        return decoder.decode(text[start:])
    except json.JSONDecodeError:
        pass
    try:
        obj, _ = decoder.raw_decode(text[start:])
        return obj
    except json.JSONDecodeError:
        pass
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("could not locate balanced JSON object in response")


def _coerce_text_to_dict(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("empty LLM response")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")
    return _parse_first_json_object(cleaned, start)


def call_json_llm(system: str, user_text: str, *, max_tokens: int = 2000) -> dict:
    """Prefer Gemini for text JSON; fall back to Claude."""
    gkey = gemini_api_key()
    if gkey:
        try:
            return _call_gemini_text_json(system, user_text, max_tokens, gkey)
        except Exception as e:
            logger.warning("Gemini listing failed, trying Claude: %s", e)
    akey = anthropic_api_key()
    if not akey:
        raise HTTPException(
            status_code=500,
            detail="Set GEMINI_API_KEY or ANTHROPIC_API_KEY in tpt-mvp/backend/.env",
        )
    return _call_claude_text_json(system, user_text, max_tokens, akey)


def _call_claude_text_json(
    system: str,
    user_text: str,
    max_tokens: int,
    api_key: str,
) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = response.content[0].text.strip()
        return _coerce_text_to_dict(raw)
    except Exception as e:
        logger.error("Claude call failed: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"AI call failed: {e}") from e


def _call_gemini_text_json(
    system: str,
    user_text: str,
    max_tokens: int,
    api_key: str,
) -> dict:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=system)
    out_cap = min(8192, max(int(max_tokens), 4096))
    cfg = genai.GenerationConfig(
        max_output_tokens=out_cap,
        temperature=0.2,
        response_mime_type="application/json",
    )
    response = model.generate_content(user_text, generation_config=cfg)
    if not response.candidates:
        raise ValueError("Gemini returned no candidates")
    text = (response.text or "").strip()
    try:
        return _coerce_text_to_dict(text)
    except (ValueError, json.JSONDecodeError):
        cfg = genai.GenerationConfig(max_output_tokens=out_cap, temperature=0.2)
        response = model.generate_content(user_text, generation_config=cfg)
        text = (response.text or "").strip()
        return _coerce_text_to_dict(text)
