from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .db import get_session_for_catalog
from .models import Site, Streamer
from .settings import AppSettings
from .streamer_fetch import fetch_streamer_info, is_fetch_supported
from .logging_utils import get_logger


logger = get_logger("streamer_bulk_fetch_jobs")


@dataclass
class BulkFetchStreamerInfoProgress:
    total: int = 0
    done: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    current: str | None = None
    percent: float = 0.0


@dataclass
class BulkFetchStreamerInfoJob:
    job_id: str
    status: str  # queued | running | done | error
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    progress: BulkFetchStreamerInfoProgress = field(default_factory=BulkFetchStreamerInfoProgress)
    error: str | None = None


_LOCK = threading.Lock()
_JOBS: dict[str, BulkFetchStreamerInfoJob] = {}


def get_bulk_fetch_streamer_info_job(job_id: str) -> BulkFetchStreamerInfoJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_active_bulk_fetch_streamer_info_job() -> BulkFetchStreamerInfoJob | None:
    """Return the most recently created bulk-fetch job that is still active."""
    with _LOCK:
        active = [j for j in _JOBS.values() if (j.status in {"queued", "running"})]
        if not active:
            return None
        return max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))


def _set_job(job: BulkFetchStreamerInfoJob) -> None:
    with _LOCK:
        _JOBS[job.job_id] = job


def _update_progress(job_id: str, *, progress: BulkFetchStreamerInfoProgress) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return
        j.progress = progress


def start_bulk_fetch_missing_streamer_info_job(
    settings: AppSettings,
    *,
    site_name: str | None = None,
    concurrency: int = 10,
) -> BulkFetchStreamerInfoJob:
    if not settings.catalog_root:
        raise ValueError("Catalog root is not configured")

    conc = int(concurrency or 0)
    if conc <= 0:
        conc = 10
    conc = max(1, min(64, conc))

    site_filter = (site_name or "").strip() or None

    job = BulkFetchStreamerInfoJob(job_id=uuid.uuid4().hex, status="queued", created_at=time.time())
    _set_job(job)
    logger.info("Bulk streamer info fetch started job=%s site=%s concurrency=%s", job.job_id, site_filter or "all", conc)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job.job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        session_factory = get_session_for_catalog(settings.catalog_root or "")

        # Identify candidates using a single DB connection.
        try:
            with session_factory() as session:
                stmt = (
                    select(Streamer.id, Streamer.name, Site.name)
                    .select_from(Streamer)
                    .join(Site, Streamer.site_id == Site.id)
                    .where(Streamer.site_id.is_not(None))
                    .where((Streamer.info_json.is_(None)) | (Streamer.info_json == ""))
                )
                if site_filter:
                    stmt = stmt.where(Site.name == site_filter)

                rows = session.execute(stmt).all()
        except Exception as e:
            with _LOCK:
                j = _JOBS.get(job.job_id)
                if j:
                    j.status = "error"
                    j.error = str(e)
                    j.finished_at = time.time()
            logger.warning("Bulk streamer info fetch failed job=%s error=%s", job.job_id, str(e))
            return

        todo: list[tuple[int, str, str]] = []
        for sid, sname, site in rows:
            streamer_name = str(sname or "").strip()
            site_name_row = str(site or "").strip()
            if not streamer_name or not site_name_row:
                continue
            if site_name_row.lower() == "unknown":
                continue
            if not is_fetch_supported(site_name_row):
                continue
            todo.append((int(sid), streamer_name, site_name_row))

        progress = BulkFetchStreamerInfoProgress(total=len(todo), done=0, ok=0, failed=0, skipped=0, current=None, percent=0.0)
        _update_progress(job.job_id, progress=progress)

        if not todo:
            with _LOCK:
                j = _JOBS.get(job.job_id)
                if j:
                    j.status = "done"
                    j.finished_at = time.time()
            logger.info("Bulk streamer info fetch finished job=%s total=0", job.job_id)
            return

        # SQLite allows only a single writer at a time.
        # Keep network fetch concurrent, but serialize DB writes.
        _db_write_lock = threading.Lock()

        def _friendly_error(e: Exception) -> str:
            msg = str(e) or "Unknown error"
            if "database is locked" in msg.lower():
                return "Database busy (retrying)"
            # Avoid dumping raw SQL into the UI.
            if "[SQL:" in msg:
                return "Database error"
            return msg

        def _commit_with_retry(s2, *, max_attempts: int = 5) -> None:
            delay = 0.15
            for attempt in range(max_attempts):
                try:
                    s2.commit()
                    return
                except OperationalError as e:
                    if "database is locked" not in str(e).lower():
                        raise
                    # Backoff and retry.
                    try:
                        s2.rollback()
                    except Exception:
                        pass
                    time.sleep(delay)
                    delay = min(1.5, delay * 2)
            # One last raise to signal failure.
            raise OperationalError("database is locked", None, None)

        # Worker: fetch + persist in its own DB session.
        def _worker(streamer_id: int, streamer_name: str, site_name_row: str) -> tuple[str, str | None]:
            fetched = fetch_streamer_info(site_name_row, streamer_name)
            if not fetched:
                return ("failed", None)

            payload = fetched.normalized()

            try:
                with _db_write_lock:
                    with session_factory() as s2:
                        st = s2.get(Streamer, int(streamer_id))
                        if not st:
                            return ("skipped", None)

                        st.info_json = json.dumps(payload, ensure_ascii=False)
                        avatar = payload.get("avatar_url")
                        if isinstance(avatar, str) and avatar.strip():
                            st.avatar_url = avatar.strip()

                        _commit_with_retry(s2)
            except Exception as e:
                return ("failed", _friendly_error(e))

            return ("ok", None)

        # Run tasks concurrently.
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
                futs: dict[concurrent.futures.Future, tuple[int, str, str]] = {}
                for sid, sname, site in todo:
                    fut = ex.submit(_worker, sid, sname, site)
                    futs[fut] = (sid, sname, site)

                done = 0
                ok = 0
                failed = 0
                skipped = 0
                last_err: str | None = None

                for fut in concurrent.futures.as_completed(futs):
                    _sid, sname, _site = futs.get(fut, (0, "", ""))
                    status = "failed"
                    err: str | None = None
                    try:
                        status, err = fut.result()
                    except Exception as e:
                        status, err = ("failed", str(e))

                    done += 1
                    if status == "ok":
                        ok += 1
                    elif status == "skipped":
                        skipped += 1
                    else:
                        failed += 1
                        if err:
                            last_err = err

                    pct = (done / len(todo)) * 100.0 if todo else 100.0
                    progress = BulkFetchStreamerInfoProgress(
                        total=len(todo),
                        done=done,
                        ok=ok,
                        failed=failed,
                        skipped=skipped,
                        current=sname or None,
                        percent=float(max(0.0, min(100.0, pct))),
                    )
                    _update_progress(job.job_id, progress=progress)

                with _LOCK:
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "done" if failed == 0 else "done"
                        j.finished_at = time.time()
                        j.error = last_err
                logger.info(
                    "Bulk streamer info fetch finished job=%s total=%s ok=%s failed=%s skipped=%s",
                    job.job_id,
                    len(todo),
                    ok,
                    failed,
                    skipped,
                )
        except Exception as e:
            with _LOCK:
                j = _JOBS.get(job.job_id)
                if j:
                    j.status = "error"
                    j.error = str(e)
                    j.finished_at = time.time()
            logger.warning("Bulk streamer info fetch failed job=%s error=%s", job.job_id, str(e))

    threading.Thread(target=_runner, daemon=True).start()
    return job
