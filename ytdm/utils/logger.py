"""Very small file logger.

The terminal interface stays quiet on success; this is what we fall back to
when something needs to be recorded for debugging.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .helpers import runtime_data_dir

_LOG_DIR = runtime_data_dir("data") / "logs"
_LOGGER_NAME = "ytdm"
_DEFAULT_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(
                _LOG_DIR / "app.log", encoding="utf-8"
            )
        except OSError:
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
