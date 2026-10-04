from __future__ import annotations

import logging
import sys


_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATEFMT = "%H:%M:%S"
_LOGGER_NAME = "camero"


def configure_app_logging() -> logging.Logger:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.WARNING, format=_FORMAT, datefmt=_DATEFMT, stream=sys.stdout)
    else:
        root.setLevel(logging.WARNING)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(getattr(handler, "_camero_handler", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        setattr(handler, "_camero_handler", True)
        logger.handlers.clear()
        logger.addHandler(handler)

    for name in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    base = configure_app_logging()
    if not name:
        return base
    return base.getChild(name)