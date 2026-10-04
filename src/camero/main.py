from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from .app import create_app
from .logging_utils import configure_app_logging
from .settings import DEFAULT_BACKEND_HOST, DEFAULT_BACKEND_PORT, load_settings


# Windows: enforce selector loop policy as early as possible.
# This avoids noisy ConnectionResetError tracebacks when clients cancel Range requests.
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    logger = configure_app_logging()
    app = create_app()
    settings = load_settings()

    host = (os.environ.get("CAMERO_HOST") or getattr(settings, "backend_host", DEFAULT_BACKEND_HOST) or DEFAULT_BACKEND_HOST).strip()
    if not host:
        host = DEFAULT_BACKEND_HOST

    try:
        port = int(os.environ.get("CAMERO_PORT", str(getattr(settings, "backend_port", DEFAULT_BACKEND_PORT))))
    except Exception:
        port = DEFAULT_BACKEND_PORT
    if port < 1 or port > 65535:
        port = DEFAULT_BACKEND_PORT

    def _browser_host(bind_host: str) -> str:
        value = (bind_host or "").strip()
        if not value or value in {"0.0.0.0", "::", "[::]"}:
            return "localhost"
        if ":" in value and not value.startswith("["):
            return f"[{value}]"
        return value

    def _can_bind(bind_host: str, bind_port: int) -> bool:
        family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((bind_host, bind_port))
            return True
        except OSError:
            return False

    def _find_free_port(bind_host: str, preferred: int, max_attempts: int = 20) -> int:
        """Return *preferred* if it is available, otherwise try nearby ports."""
        if _can_bind(bind_host, preferred):
            return preferred
        logger.warning(
            "El puerto %d no está disponible (puede estar ocupado o reservado por Windows). "
            "Buscando un puerto alternativo…",
            preferred,
        )
        for offset in range(1, max_attempts + 1):
            candidate = preferred + offset
            if 1 <= candidate <= 65535 and _can_bind(bind_host, candidate):
                return candidate
        # Last resort: let the OS pick one.
        family = socket.AF_INET6 if ":" in bind_host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((bind_host, 0))
            return s.getsockname()[1]

    port = _find_free_port(host, port)

    browser_host = _browser_host(host)
    url = f"http://{browser_host}:{port}"

    def _open_browser() -> None:
        # Give the server a moment to bind.
        time.sleep(0.8)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open_browser, daemon=True).start()

    logger.info("Servidor iniciado en %s", url)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
