"""Lightweight API logging stubs for TPT (no Kizzum closet.ops)."""

from __future__ import annotations

import logging

logger = logging.getLogger("tpt.ops")


def record_api(service: str, n: int = 1) -> None:
    logger.debug("api %s +%s", service, n)


def log_api_call(service: str, *, ms: int = 0, ok: bool = True, detail: str = "") -> None:
    level = logging.INFO if ok else logging.WARNING
    logger.log(level, "api %s %s %sms | %s", service, "ok" if ok else "fail", ms, detail)
