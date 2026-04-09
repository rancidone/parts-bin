"""
Logging configuration for Parts Bin.

Environment variables:
  LOG_LEVEL   — DEBUG / INFO / WARNING / ERROR  (default: INFO)
  LOG_FORMAT  — json / text                     (default: text)
  LOG_FILE    — path to log file                (default: none; stdout only)

File logs are always JSON regardless of LOG_FORMAT.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone


_STDLIB_KEYS = frozenset(logging.makeLogRecord({}).__dict__)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        # Merge extra fields passed via the `extra=` kwarg, excluding stdlib internals.
        for key, val in record.__dict__.items():
            if key not in _STDLIB_KEYS and not key.startswith("_"):
                obj[key] = val
        return json.dumps(obj)


def init() -> None:
    """Call once at startup (server.py). Idempotent."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return  # already initialised

    root.setLevel(level)

    # --- stdout handler ---
    fmt = os.environ.get("LOG_FORMAT", "text").lower()
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(
        _JsonFormatter() if fmt == "json" else logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(stdout_handler)

    # --- file handler (always JSON) ---
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(_JsonFormatter())
        root.addHandler(file_handler)

    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "python_multipart", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
