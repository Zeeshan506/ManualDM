import contextvars
import logging
import os
import sys
from typing import Any
from loguru import logger

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def set_request_id(request_id: str | None) -> contextvars.Token[str | None]:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str | None:
    return _request_id_var.get()


def _resolve_level(level: int | str) -> str:
    if isinstance(level, str):
        return level.upper()

    level_mapping = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }
    return level_mapping.get(level, "INFO")


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level_name = logger.level(record.levelname).name
        except Exception:
            level_name = _resolve_level(record.levelno)

        frame = logging.currentframe()
        depth = 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        request_id = get_request_id() or "-"
        log = logger.bind(
            request_id=request_id,
            logger_name=record.name,
            event=record.name,
            fields="",
        )
        log.opt(depth=depth, exception=record.exc_info).log(level_name, record.getMessage())


def _format_fields(fields: dict[str, Any] | None) -> str:
    if not fields:
        return ""
    items = [f"{key}={value}" for key, value in fields.items()]
    return " | " + " ".join(items)


def _attach_stdlib_intercept(level_name: str) -> None:
    intercept_handler = InterceptHandler()
    logging.basicConfig(handlers=[intercept_handler], level=0, force=True)
    logging.root.setLevel(level_name)

    forwarded_loggers = (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "celery",
        "celery.app.trace",
        "kombu",
    )

    for logger_name in forwarded_loggers:
        std_logger = logging.getLogger(logger_name)
        std_logger.handlers = [intercept_handler]
        std_logger.propagate = False
        std_logger.setLevel(level_name)


def configure_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL") or "INFO").upper()
    logger.remove()
    logger.configure(extra={"request_id": "-", "event": "", "fields": "", "logger_name": "-"})
    logger.add(
        sink=sys.stdout,
        level=level_name,
        colorize=True,
        backtrace=False,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[request_id]}</cyan> | "
            "<blue>{extra[logger_name]}</blue> | "
            "<magenta>{extra[event]}</magenta> | {message}{extra[fields]}"
        ),
    )
    _attach_stdlib_intercept(level_name)


def get_logger(name: str) -> Any:
    return logger.bind(logger_name=name)


def log_event(logger_instance: Any, level: int | str, event: str, **fields: Any) -> None:
    request_id = get_request_id() or "-"
    bound_logger = logger_instance.bind(
        request_id=request_id,
        event=event,
        fields=_format_fields(fields),
    )
    bound_logger.log(_resolve_level(level), event)
