"""
Logging configuration for Parts Bin.

Environment variables:
  LOG_LEVEL   — DEBUG / INFO / WARNING / ERROR  (default: INFO)
  LOG_FORMAT  — json / text                     (default: text)
  LOG_FILE    — path to log file                (default: none; stdout only)
  TELEMETRY_LOG_FILE — path to telemetry JSONL  (default: telemetry.jsonl)

File logs are always JSON regardless of LOG_FORMAT.
"""

import contextlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


_STDLIB_KEYS = frozenset(logging.makeLogRecord({}).__dict__)
_STDOUT_HANDLER_NAME = "parts_bin_stdout"
_FILE_HANDLER_NAME = "parts_bin_file"
_TELEMETRY_HANDLER_NAME = "parts_bin_telemetry_file"
_TELEMETRY_LOGGER_NAME = "parts_bin.telemetry"
_TELEMETRY_SCHEMA_VERSION = 1
_DEFAULT_TELEMETRY_LOG_FILE = "telemetry.jsonl"


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


class _TelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "telemetry_version": _TELEMETRY_SCHEMA_VERSION,
            "event": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _STDLIB_KEYS and not key.startswith("_"):
                obj[key] = val
        return json.dumps(obj)


def _find_handler(logger: logging.Logger, handler_name: str) -> logging.Handler | None:
    for handler in logger.handlers:
        if getattr(handler, "_parts_bin_handler_name", None) == handler_name:
            return handler
    return None


def _set_handler_name(handler: logging.Handler, handler_name: str) -> logging.Handler:
    handler._parts_bin_handler_name = handler_name  # type: ignore[attr-defined]
    return handler


def _normalise_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _replace_handler(
    logger: logging.Logger,
    existing: logging.Handler | None,
    handler_name: str,
    new_handler: logging.Handler | None,
) -> None:
    if existing is not None:
        logger.removeHandler(existing)
        with contextlib.suppress(Exception):
            existing.close()
    if new_handler is not None:
        logger.addHandler(_set_handler_name(new_handler, handler_name))


def init() -> None:
    """Call once at startup (server.py). Idempotent."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # --- stdout handler ---
    fmt = os.environ.get("LOG_FORMAT", "text").lower()
    stdout_handler = _find_handler(root, _STDOUT_HANDLER_NAME)
    formatter = (
        _JsonFormatter() if fmt == "json" else logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    if stdout_handler is None:
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        root.addHandler(_set_handler_name(stdout_handler, _STDOUT_HANDLER_NAME))
    else:
        stdout_handler.setFormatter(formatter)

    # --- file handler (always JSON) ---
    log_file = os.environ.get("LOG_FILE")
    file_handler = _find_handler(root, _FILE_HANDLER_NAME)
    if log_file:
        desired_path = _normalise_path(log_file)
        current_path = getattr(file_handler, "baseFilename", None) if file_handler else None
        if current_path != desired_path:
            replacement = logging.FileHandler(desired_path)
            replacement.setFormatter(_JsonFormatter())
            _replace_handler(root, file_handler, _FILE_HANDLER_NAME, replacement)
        elif file_handler is not None:
            file_handler.setFormatter(_JsonFormatter())
    elif file_handler is not None:
        _replace_handler(root, file_handler, _FILE_HANDLER_NAME, None)

    telemetry_logger = logging.getLogger(_TELEMETRY_LOGGER_NAME)
    telemetry_logger.setLevel(logging.INFO)
    telemetry_logger.propagate = False
    telemetry_path = _normalise_path(os.environ.get("TELEMETRY_LOG_FILE", _DEFAULT_TELEMETRY_LOG_FILE))
    telemetry_handler = _find_handler(telemetry_logger, _TELEMETRY_HANDLER_NAME)
    current_telemetry_path = getattr(telemetry_handler, "baseFilename", None) if telemetry_handler else None
    if current_telemetry_path != telemetry_path:
        replacement = logging.FileHandler(telemetry_path)
        replacement.setFormatter(_TelemetryFormatter())
        _replace_handler(telemetry_logger, telemetry_handler, _TELEMETRY_HANDLER_NAME, replacement)
    elif telemetry_handler is not None:
        telemetry_handler.setFormatter(_TelemetryFormatter())

    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "uvicorn.access", "python_multipart", "PIL", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def emit_telemetry(event: str, **fields) -> None:
    logging.getLogger(_TELEMETRY_LOGGER_NAME).info(event, extra=fields)
