from __future__ import annotations

import threading
import time

from .scan_jobs import get_active_scan_job, start_scan_job
from .settings import AppSettings, load_settings


_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_STOP_EVENT: threading.Event | None = None


def _settings_signature(settings: AppSettings) -> tuple[object, ...]:
    folders = tuple((f.key, f.path, f.filename_regex) for f in (settings.catalog_folders or ()))
    return (
        bool(getattr(settings, "periodic_scan_enabled", False)),
        int(getattr(settings, "periodic_scan_interval_minutes", 0) or 0),
        settings.catalog_root or "",
        folders,
    )


def _run_scheduler(stop_event: threading.Event) -> None:
    current_signature: tuple[object, ...] | None = None
    next_run_at: float | None = None
    last_seen_active_job_id: str | None = None

    while not stop_event.wait(5.0):
        try:
            settings = load_settings()
            enabled = bool(getattr(settings, "periodic_scan_enabled", False))
            interval_minutes = int(getattr(settings, "periodic_scan_interval_minutes", 0) or 0)
            signature = _settings_signature(settings)
            now = time.monotonic()

            if signature != current_signature:
                current_signature = signature
                next_run_at = (now + (interval_minutes * 60.0)) if enabled and interval_minutes > 0 else None
                last_seen_active_job_id = None

            if not enabled or interval_minutes <= 0 or not settings.catalog_root or not settings.catalog_folders:
                next_run_at = None
                last_seen_active_job_id = None
                continue

            active_job = get_active_scan_job()
            if active_job is not None:
                last_seen_active_job_id = active_job.job_id
                next_run_at = None
                continue

            if last_seen_active_job_id is not None:
                last_seen_active_job_id = None
                next_run_at = now + (interval_minutes * 60.0)
                continue

            if next_run_at is None:
                next_run_at = now + (interval_minutes * 60.0)
                continue
            if now < next_run_at:
                continue

            fresh_settings = load_settings()
            if (
                not getattr(fresh_settings, "periodic_scan_enabled", False)
                or not fresh_settings.catalog_root
                or not fresh_settings.catalog_folders
            ):
                next_run_at = None
                continue

            try:
                job = start_scan_job(fresh_settings)
            except Exception:
                next_run_at = now + (interval_minutes * 60.0)
                continue

            last_seen_active_job_id = job.job_id
            next_run_at = None
        except Exception:
            continue


def start_auto_scan_scheduler() -> None:
    global _THREAD, _STOP_EVENT
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_run_scheduler,
            args=(stop_event,),
            name="camero-auto-scan",
            daemon=True,
        )
        _STOP_EVENT = stop_event
        _THREAD = thread
        thread.start()


def stop_auto_scan_scheduler(*, timeout: float = 5.0) -> None:
    global _THREAD, _STOP_EVENT
    with _LOCK:
        thread = _THREAD
        stop_event = _STOP_EVENT
        _THREAD = None
        _STOP_EVENT = None

    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)