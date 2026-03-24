"""Ensure middleware log records appear on stderr (uvicorn does not configure third-party loggers by default)."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_middleware_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    if root.level == logging.NOTSET:
        root.setLevel(level)

    mw = logging.getLogger("middleware")
    mw.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(fmt)
    mw.addHandler(handler)
    mw.propagate = False
