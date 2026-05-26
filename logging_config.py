import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"
ERROR_LOG_FILE = LOG_DIR / "error.log"
RCS_LOG_FILE = LOG_DIR / "rcs.log"
PROGRAMMING_LOG_FILE = LOG_DIR / "programming.log"
IO_LOG_FILE = LOG_DIR / "io.log"
LOG_FILES = (LOG_FILE, ERROR_LOG_FILE, RCS_LOG_FILE, PROGRAMMING_LOG_FILE, IO_LOG_FILE)
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_CONFIGURED = False
_DB_LOGGING_CONFIGURED = False
_LOCK = threading.Lock()
_SENSITIVE_KEYWORDS = ("password", "token", "secret", "auth", "key")


def ensure_log_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for log_file in LOG_FILES:
        if not log_file.exists():
            log_file.touch()
    return LOG_FILE


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(name)s.%(funcName)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class _LoggerNameFilter(logging.Filter):
    def __init__(self, *prefixes: str):
        super().__init__()
        self.prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        return any(record.name == prefix or record.name.startswith(prefix + ".") for prefix in self.prefixes)


def _has_handler(logger: logging.Logger, log_path: Path) -> bool:
    path = str(log_path)
    return any(
        isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None) == path
        for h in logger.handlers
    )


def _make_rotating_handler(log_path: Path, level: int, formatter: logging.Formatter) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _add_rotating_handler(logger: logging.Logger, log_path: Path, level: int, formatter: logging.Formatter, *filters: logging.Filter) -> None:
    if _has_handler(logger, log_path):
        return
    handler = _make_rotating_handler(log_path, level, formatter)
    for log_filter in filters:
        handler.addFilter(log_filter)
    logger.addHandler(handler)


def configure_logging() -> Path:
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return ensure_log_file()

        log_path = ensure_log_file()
        formatter = _build_formatter()

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        _add_rotating_handler(root_logger, log_path, logging.INFO, formatter)
        _add_rotating_handler(root_logger, ERROR_LOG_FILE, logging.WARNING, formatter)

        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.INFO)

        rcs_filter = _LoggerNameFilter("app.rcs_client", "app.rcs")
        programming_filter = _LoggerNameFilter("app.programming", "app.simulator", "app.simulation")
        io_filter = _LoggerNameFilter("app.io")
        _add_rotating_handler(root_logger, RCS_LOG_FILE, logging.INFO, formatter, rcs_filter)
        _add_rotating_handler(root_logger, PROGRAMMING_LOG_FILE, logging.INFO, formatter, programming_filter)
        _add_rotating_handler(root_logger, IO_LOG_FILE, logging.INFO, formatter, io_filter)

        def _log_unhandled_exception(exc_type, exc_value, exc_tb):
            if issubclass(exc_type, KeyboardInterrupt):
                return sys.__excepthook__(exc_type, exc_value, exc_tb)
            logging.getLogger("app.unhandled").error(
                "Unhandled exception: %s\n%s",
                exc_value,
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
            )

        def _log_thread_exception(args):
            logging.getLogger("app.thread").error(
                "Unhandled thread exception in %s: %s\n%s",
                getattr(args.thread, "name", "unknown"),
                args.exc_value,
                "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
            )

        sys.excepthook = _log_unhandled_exception
        if hasattr(threading, "excepthook"):
            threading.excepthook = _log_thread_exception

        _CONFIGURED = True
        return log_path


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def _is_sensitive(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return any(word in text for word in _SENSITIVE_KEYWORDS)


def _safe_value(column_name: str, value: Any) -> Any:
    if _is_sensitive(column_name):
        return "[REDACTED]"
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def _describe_instance(obj: Any) -> str:
    try:
        mapper = inspect(obj).mapper
    except Exception:
        return "unknown"

    parts = []
    for col in mapper.primary_key:
        value = getattr(obj, col.key, None)
        if value is not None:
            parts.append(f"{col.key}={value}")

    for fallback in ("code", "name", "order_code", "key", "status"):
        if fallback in mapper.columns and not _is_sensitive(fallback):
            value = _safe_value(fallback, getattr(obj, fallback, None))
            if value not in (None, ""):
                parts.append(f"{fallback}={value}")
                break

    return ", ".join(parts) if parts else "sin_identificador"


def _collect_changed_columns(obj: Any) -> list[str]:
    try:
        state = inspect(obj)
    except Exception:
        return []

    changed = []
    for attr in state.attrs:
        if not getattr(attr, "history", None) or not attr.history.has_changes():
            continue
        if _is_sensitive(attr.key):
            changed.append(f"{attr.key}=[REDACTED]")
            continue
        new_value = _safe_value(attr.key, getattr(obj, attr.key, None))
        changed.append(f"{attr.key}={new_value}")
    return changed[:8]


def configure_sqlalchemy_logging(session_factory) -> None:
    global _DB_LOGGING_CONFIGURED
    configure_logging()
    with _LOCK:
        if _DB_LOGGING_CONFIGURED:
            return

        db_logger = logging.getLogger("app.db")

        @event.listens_for(session_factory.class_, "after_flush")
        def _log_db_changes(session, flush_context):
            try:
                for obj in session.new:
                    table_name = getattr(obj, "__tablename__", obj.__class__.__name__)
                    db_logger.info("DB INSERT table=%s %s", table_name, _describe_instance(obj))

                for obj in session.dirty:
                    if obj in session.new or obj in session.deleted:
                        continue
                    if not session.is_modified(obj, include_collections=False):
                        continue
                    table_name = getattr(obj, "__tablename__", obj.__class__.__name__)
                    changed = _collect_changed_columns(obj)
                    suffix = f" changes={'; '.join(changed)}" if changed else ""
                    db_logger.info("DB UPDATE table=%s %s%s", table_name, _describe_instance(obj), suffix)

                for obj in session.deleted:
                    table_name = getattr(obj, "__tablename__", obj.__class__.__name__)
                    db_logger.info("DB DELETE table=%s %s", table_name, _describe_instance(obj))
            except Exception:
                db_logger.exception("Error logging database changes")

        _DB_LOGGING_CONFIGURED = True
