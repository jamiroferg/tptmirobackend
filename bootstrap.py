"""TPT Miro env + paths — standalone backend repo."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

TPT_BACKEND = Path(__file__).resolve().parent
TPT_ROOT = TPT_BACKEND

env_file = TPT_BACKEND / ".env"
if env_file.exists():
    load_dotenv(env_file, override=False)

load_dotenv(override=False)

import os

# Faster Lens tail latency + skip heavy /search bundle when unset.
os.environ.setdefault("LENS_HEDGE_DELAY", "3")
os.environ.setdefault("LENS_SEARCH_BUNDLE", "0")

UPLOAD_DIR = TPT_BACKEND / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
