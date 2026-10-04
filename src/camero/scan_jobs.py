from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session_for_catalog
from .scanner import ScanProgress, ScanResult, scan_catalog
from .settings import AppSettings
from .models import Recording
from .media import quick_file_hash
from .preview_jobs import (
    clear_recording_live_preview_failure,
    generate_live_preview_sync,
    live_preview_output_path_for_hash,
    normalize_file_hash,
    set_recording_live_preview_failure,
)
from .catalog_paths import resolve_recording_path
from .logging_utils import get_logger
from .autotag_jobs import (
    auto_tag_recording_with_session,
    _get_tagger,
    _check_existing_active as _check_existing_active_autotag,
    _untagged_autotag_filter,
    register_external_autotag_phase,
    unregister_external_autotag_phase,
)


logger = get_logger("scan_jobs")


@dataclass
class ScanJob:
    job_id: str
    status: str  # queued | running | done | cancelled | error
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    progress: ScanProgress = field(default_factory=ScanProgress)
    result: ScanResult | None = None
    error: str | None = None
    cancel_scan: bool = False
    cancel_live_previews: bool = False
    _auto_tag_cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )


_LOCK = threading.Lock()
_JOBS: dict[str, ScanJob] = {}


def get_scan_job(job_id: str) -> ScanJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_active_scan_job() -> ScanJob | None:
    """Return the most recently created scan job that is still active.

    Active means: queued or running.
    """
    with _LOCK:
        active = [j for j in _JOBS.values() if (j.status in {"queued", "running"})]
        if not active:
            return None
        return max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))


def cancel_scan_live_previews(job_id: str) -> bool:
    """Request cancellation of the animated preview generation phase for a scan job."""
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return False
        j.cancel_live_previews = True
        logger.info("Cancel requested for live previews job=%s", job_id)
        return True


def cancel_scan(job_id: str) -> bool:
    """Request cancellation of the scanning phase (recording discovery) for a scan job."""
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return False
        j.cancel_scan = True
        j._auto_tag_cancel_event.set()
        logger.info("Cancel requested for catalog scan job=%s", job_id)
        return True


def start_scan_job(settings: AppSettings) -> ScanJob:
    if not settings.catalog_root or not settings.catalog_folders:
        raise ValueError("Catalog folders are not configured")

    with _LOCK:
        active = [j for j in _JOBS.values() if j.status in {"queued", "running"}]
        if active:
            existing = max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))
            logger.info("Catalog scan already active job=%s", existing.job_id)
            return existing

        job_id = uuid.uuid4().hex
        job = ScanJob(job_id=job_id, status="queued", created_at=time.time())
        _JOBS[job_id] = job

    logger.info(
        "Catalog scan started job=%s folders=%s previews=%s",
        job_id,
        len(settings.catalog_folders or ()),
        bool(getattr(settings, "generate_live_previews", False)),
    )

    last_progress: ScanProgress = ScanProgress()

    def _update_progress(p: ScanProgress) -> None:
        nonlocal last_progress
        last_progress = p
        with _LOCK:
            j = _JOBS.get(job_id)
            if not j:
                return
            j.progress = p

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        session_factory = get_session_for_catalog(settings.catalog_root or "")
        session: Session = session_factory()
        try:
            # Make scan results visible to the UI incrementally.
            # Without intermediate commits, other connections won't see new rows until the job ends
            # (which can be delayed by the animated previews phase).
            last_commit_scanned = 0
            last_commit_t = time.monotonic()
            last_preview_commit_done = 0
            last_preview_commit_t = time.monotonic()

            def _maybe_commit(progress: ScanProgress) -> None:
                nonlocal last_commit_scanned, last_commit_t
                try:
                    phase = getattr(progress, "phase", "scan") or "scan"
                    if phase != "scan":
                        return
                    scanned = int(getattr(progress, "scanned_files", 0) or 0)
                    now = time.monotonic()
                    if (scanned - last_commit_scanned) < 25 and (now - last_commit_t) < 2.0:
                        return
                    session.commit()
                    last_commit_scanned = scanned
                    last_commit_t = now
                except Exception:
                    # Bubble up as a job error; continuing would lead to inconsistent UI/DB.
                    raise

            def _scan_cancelled() -> bool:
                with _LOCK:
                    j = _JOBS.get(job_id)
                    return bool(getattr(j, "cancel_scan", False)) if j else True

            def _progress_cb(p: ScanProgress) -> None:
                _update_progress(p)
                _maybe_commit(p)

            def _maybe_commit_previews(*, force: bool = False, done_previews: int = 0) -> None:
                nonlocal last_preview_commit_done, last_preview_commit_t
                now = time.monotonic()
                if not force and (done_previews - last_preview_commit_done) < 5 and (now - last_preview_commit_t) < 2.0:
                    return
                session.commit()
                last_preview_commit_done = done_previews
                last_preview_commit_t = now

            result = scan_catalog(
                catalog_root=settings.catalog_root or "",
                catalog_folders=list(settings.catalog_folders),
                session=session,
                progress_cb=_progress_cb,
                cancel_cb=_scan_cancelled,
            )

            if _scan_cancelled():
                try:
                    session.commit()
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                with _LOCK:
                    j = _JOBS.get(job_id)
                    if j:
                        j.status = "cancelled"
                        j.finished_at = time.time()
                        j.result = result
                logger.warning(
                    "Catalog scan cancelled job=%s scanned=%s added=%s updated=%s errors=%s",
                    job_id,
                    result.scanned_files,
                    result.added,
                    result.updated,
                    result.errors,
                )
                return

            # Ensure discovered recordings are visible before any long-running second phase.
            session.commit()

            # Optional: generate animated previews as a second phase.
            def _previews_cancelled() -> bool:
                with _LOCK:
                    j = _JOBS.get(job_id)
                    return bool(getattr(j, "cancel_live_previews", False)) if j else True

            if getattr(settings, "generate_live_previews", False) and not _previews_cancelled():
                root = Path(settings.catalog_root or "")
                rows = session.execute(
                    select(
                        Recording.id,
                        Recording.rel_path,
                        Recording.duration_seconds,
                        Recording.file_hash,
                        Recording.file_mtime,
                        Recording.live_preview_failed_hash,
                    )
                ).all()

                todo: list[tuple[int, str, Path, float | None, Path, str]] = []
                for rid, rel_path, dur, fh, fm, failed_hash in rows:
                    rid_i = int(rid)
                    input_path = resolve_recording_path(settings, str(rel_path))
                    if input_path is None:
                        continue

                    # Ensure we have a hash for stable preview caching.
                    file_hash: str | None = fh.strip().lower() if isinstance(fh, str) and fh.strip() else None
                    st_mtime: float | None = None
                    try:
                        st_mtime = float(input_path.stat().st_mtime)
                    except Exception:
                        st_mtime = None

                    if file_hash is None or (
                        st_mtime is not None
                        and isinstance(fm, (int, float))
                        and abs(float(fm) - float(st_mtime)) >= 0.0001
                    ):
                        computed = quick_file_hash(input_path)
                        if isinstance(computed, str) and computed.strip():
                            file_hash = computed.strip().lower()
                            try:
                                rec = session.get(Recording, rid_i)
                                if rec is not None:
                                    rec.file_hash = file_hash
                                    if st_mtime is not None:
                                        rec.file_mtime = float(st_mtime)
                                    if normalize_file_hash(getattr(rec, "live_preview_failed_hash", None)) != file_hash:
                                        clear_recording_live_preview_failure(rec)
                            except Exception:
                                pass

                    if not file_hash:
                        continue

                    if normalize_file_hash(failed_hash) == file_hash:
                        continue

                    out_path = live_preview_output_path_for_hash(
                        catalog_root=settings.catalog_root or "",
                        file_hash=file_hash,
                    )
                    rel_out = str(out_path.relative_to(root)).replace("\\", "/")

                    try:
                        if out_path.exists() and out_path.stat().st_size > 0:
                            continue
                    except Exception:
                        pass

                    if not input_path.exists():
                        continue

                    todo.append((rid_i, file_hash, input_path, dur, out_path, rel_out))

                total_previews = len(todo)
                done_previews = 0
                preview_errors = 0

                if total_previews:
                    logger.info("Live previews started job=%s pending=%s", job_id, total_previews)

                # Emit an initial progress state for the preview phase.
                _update_progress(
                    ScanProgress(
                        phase="live_previews",
                        total_files=last_progress.total_files,
                        scanned_files=last_progress.scanned_files,
                        added=last_progress.added,
                        updated=last_progress.updated,
                        skipped=last_progress.skipped,
                        errors=0,
                        current_file=None,
                        current_recording=None,
                        total_previews=total_previews,
                        generated_previews=0,
                        failed_previews=0,
                        current_preview=None,
                    )
                )

                for rid_i, file_hash, input_path, dur, out_path, rel_out in todo:
                    if _previews_cancelled():
                        try:
                            _maybe_commit_previews(force=True, done_previews=done_previews)
                        except Exception:
                            try:
                                session.rollback()
                            except Exception:
                                pass
                        _update_progress(
                            ScanProgress(
                                phase="live_previews",
                                total_files=last_progress.total_files,
                                scanned_files=last_progress.scanned_files,
                                added=last_progress.added,
                                updated=last_progress.updated,
                                skipped=last_progress.skipped,
                                errors=preview_errors,
                                current_file=None,
                                current_recording=None,
                                total_previews=total_previews,
                                generated_previews=done_previews,
                                failed_previews=preview_errors,
                                current_preview=None,
                            )
                        )
                        break

                    _update_progress(
                        ScanProgress(
                            phase="live_previews",
                            total_files=last_progress.total_files,
                            scanned_files=last_progress.scanned_files,
                            added=last_progress.added,
                            updated=last_progress.updated,
                            skipped=last_progress.skipped,
                            errors=preview_errors,
                            current_file=None,
                            current_recording=str(input_path),
                            total_previews=total_previews,
                            generated_previews=done_previews,
                            failed_previews=preview_errors,
                            current_preview=rel_out,
                        )
                    )

                    try:
                        ok, _err = generate_live_preview_sync(
                            input_video=input_path,
                            output_mp4=out_path,
                            duration_seconds=dur,
                            segments=int(getattr(settings, "live_preview_segments", 10) or 10),
                            progress_cb=None,
                            timeout_seconds=180.0,
                        )
                        if not ok:
                            set_recording_live_preview_failure(
                                session.get(Recording, rid_i),
                                file_hash=file_hash,
                                error=_err,
                            )
                            preview_errors += 1
                        else:
                            clear_recording_live_preview_failure(session.get(Recording, rid_i))
                    except Exception:
                        set_recording_live_preview_failure(
                            session.get(Recording, rid_i),
                            file_hash=file_hash,
                            error="Failed to generate animated preview",
                        )
                        preview_errors += 1
                    done_previews += 1

                    _maybe_commit_previews(force=True, done_previews=done_previews)

                    _update_progress(
                        ScanProgress(
                            phase="live_previews",
                            total_files=last_progress.total_files,
                            scanned_files=last_progress.scanned_files,
                            added=last_progress.added,
                            updated=last_progress.updated,
                            skipped=last_progress.skipped,
                            errors=preview_errors,
                            current_file=None,
                            current_recording=str(input_path),
                            total_previews=total_previews,
                            generated_previews=done_previews,
                            failed_previews=preview_errors,
                            current_preview=rel_out,
                        )
                    )

            # Final commit (normally a no-op if the scan already committed).
            session.commit()

            # Optional: auto-tag recordings that have no tags after scan.
            _active_autotag = _check_existing_active_autotag()
            if _active_autotag:
                logger.info(
                    "Auto-tagging phase skipped: AutoTagJob already active job=%s scan_job=%s",
                    _active_autotag.job_id, job_id,
                )
            if getattr(settings, "auto_tag_after_scan", False) and not _scan_cancelled() and not _active_autotag:
                # Collect IDs of recordings added/updated in this scan that have no tags.
                from sqlalchemy import select as _select
                from .models import Tag as _Tag
                untagged_rows = session.execute(
                    _select(Recording.id, Recording.rel_path).where(
                        *_untagged_autotag_filter()
                    )
                ).all()

                total_auto = len(untagged_rows)
                done_auto = 0
                failed_auto = 0

                if total_auto:
                    logger.info("Auto-tagging phase started job=%s untagged=%s", job_id, total_auto)
                    register_external_autotag_phase(f"scan:{job_id}")
                    try:
                        try:
                            tagger = _get_tagger(settings)
                        except Exception as _tagger_exc:
                            logger.warning("Auto-tag skipped: could not load tagger job=%s error=%s", job_id, _tagger_exc)
                            tagger = None

                        if tagger is not None:
                            with _LOCK:
                                _j = _JOBS.get(job_id)
                                _auto_tag_stop = _j._auto_tag_cancel_event if _j else threading.Event()

                            _update_progress(
                                ScanProgress(
                                    phase="auto_tagging",
                                    total_files=last_progress.total_files,
                                    scanned_files=last_progress.scanned_files,
                                    added=last_progress.added,
                                    updated=last_progress.updated,
                                    skipped=last_progress.skipped,
                                    errors=0,
                                    current_file=None,
                                    current_recording=None,
                                    total_previews=total_auto,
                                    generated_previews=0,
                                    failed_previews=0,
                                    current_preview=None,
                                )
                            )

                            for _rid, _rel in untagged_rows:
                                if _scan_cancelled():
                                    break
                                rec = session.get(Recording, int(_rid))
                                if rec is None:
                                    failed_auto += 1
                                    continue

                                _update_progress(
                                    ScanProgress(
                                        phase="auto_tagging",
                                        total_files=last_progress.total_files,
                                        scanned_files=last_progress.scanned_files,
                                        added=last_progress.added,
                                        updated=last_progress.updated,
                                        skipped=last_progress.skipped,
                                        errors=failed_auto,
                                        current_file=None,
                                        current_recording=str(_rel),
                                        total_previews=total_auto,
                                        generated_previews=done_auto,
                                        failed_previews=failed_auto,
                                        current_preview=None,
                                    )
                                )

                                try:
                                    auto_tag_recording_with_session(
                                        session, rec, tagger, settings,
                                        stop_event=_auto_tag_stop,
                                    )
                                    session.commit()
                                    done_auto += 1
                                    logger.debug("Auto-tagged during scan rel_path=%s", _rel)
                                except InterruptedError:
                                    logger.info(
                                        "Auto-tagging phase interrupted by cancel job=%s", job_id
                                    )
                                    break
                                except Exception as _exc:
                                    try:
                                        session.rollback()
                                    except Exception:
                                        pass
                                    failed_auto += 1
                                    logger.warning(
                                        "Auto-tag failed during scan rel_path=%s error=%s", _rel, _exc
                                    )

                            logger.info(
                                "Auto-tagging phase finished job=%s total=%s done=%s failed=%s",
                                job_id, total_auto, done_auto, failed_auto,
                            )
                    finally:
                        unregister_external_autotag_phase(f"scan:{job_id}")

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "done"
                    j.result = result
                    j.finished_at = time.time()
            logger.info(
                "Catalog scan finished job=%s scanned=%s added=%s updated=%s skipped=%s errors=%s",
                job_id,
                result.scanned_files,
                result.added,
                result.updated,
                result.skipped,
                result.errors,
            )
        except Exception as e:
            try:
                session.rollback()
            except Exception:
                pass
            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "error"
                    j.error = str(e)
                    j.finished_at = time.time()
            logger.warning("Catalog scan failed job=%s error=%s", job_id, str(e))
        finally:
            try:
                session.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job


def start_generate_missing_live_previews_job(settings: AppSettings) -> ScanJob:
    """Generate animated previews for recordings that don't have one yet.

    This runs as a ScanJob so the existing UI polling/progress UI can be reused,
    but it does *not* scan folders.
    """

    if not settings.catalog_root or not settings.catalog_folders:
        raise ValueError("Catalog folders are not configured")

    job_id = uuid.uuid4().hex
    job = ScanJob(job_id=job_id, status="queued", created_at=time.time())

    with _LOCK:
        _JOBS[job_id] = job

    logger.info("Missing live previews job started job=%s", job_id)

    def _update_progress(p: ScanProgress) -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if not j:
                return
            j.progress = p

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        session_factory = get_session_for_catalog(settings.catalog_root or "")
        session: Session = session_factory()
        try:
            def _previews_cancelled() -> bool:
                with _LOCK:
                    j = _JOBS.get(job_id)
                    return bool(getattr(j, "cancel_live_previews", False)) if j else True

            last_preview_commit_done = 0
            last_preview_commit_t = time.monotonic()

            def _maybe_commit_previews(*, force: bool = False, done_previews: int = 0) -> None:
                nonlocal last_preview_commit_done, last_preview_commit_t
                now = time.monotonic()
                if not force and (done_previews - last_preview_commit_done) < 5 and (now - last_preview_commit_t) < 2.0:
                    return
                session.commit()
                last_preview_commit_done = done_previews
                last_preview_commit_t = now

            root = Path(settings.catalog_root or "")
            rows = session.execute(
                select(
                    Recording.id,
                    Recording.rel_path,
                    Recording.duration_seconds,
                    Recording.file_hash,
                    Recording.file_mtime,
                    Recording.live_preview_failed_hash,
                )
            ).all()

            todo: list[tuple[int, str, Path, float | None, Path, str]] = []
            for rid, rel_path, dur, fh, fm, failed_hash in rows:
                rid_i = int(rid)
                input_path = resolve_recording_path(settings, str(rel_path))
                if input_path is None:
                    continue

                file_hash: str | None = fh.strip().lower() if isinstance(fh, str) and fh.strip() else None
                st_mtime: float | None = None
                try:
                    st_mtime = float(input_path.stat().st_mtime)
                except Exception:
                    st_mtime = None

                if file_hash is None or (
                    st_mtime is not None
                    and isinstance(fm, (int, float))
                    and abs(float(fm) - float(st_mtime)) >= 0.0001
                ):
                    computed = quick_file_hash(input_path)
                    if isinstance(computed, str) and computed.strip():
                        file_hash = computed.strip().lower()
                        try:
                            rec = session.get(Recording, rid_i)
                            if rec is not None:
                                rec.file_hash = file_hash
                                if st_mtime is not None:
                                    rec.file_mtime = float(st_mtime)
                                if normalize_file_hash(getattr(rec, "live_preview_failed_hash", None)) != file_hash:
                                    clear_recording_live_preview_failure(rec)
                        except Exception:
                            pass

                if not file_hash:
                    continue

                if normalize_file_hash(failed_hash) == file_hash:
                    continue

                out_path = live_preview_output_path_for_hash(
                    catalog_root=settings.catalog_root or "",
                    file_hash=file_hash,
                )
                rel_out = str(out_path.relative_to(root)).replace("\\", "/")

                try:
                    if out_path.exists() and out_path.stat().st_size > 0:
                        continue
                except Exception:
                    pass

                if not input_path.exists():
                    continue

                todo.append((rid_i, file_hash, input_path, dur, out_path, rel_out))

            total_previews = len(todo)
            done_previews = 0
            preview_errors = 0

            logger.info("Missing live previews queued job=%s pending=%s", job_id, total_previews)

            _update_progress(
                ScanProgress(
                    phase="live_previews",
                    total_files=None,
                    scanned_files=0,
                    added=0,
                    updated=0,
                    skipped=0,
                    errors=0,
                    current_file=None,
                    current_recording=None,
                    total_previews=total_previews,
                    generated_previews=0,
                    failed_previews=0,
                    current_preview=None,
                )
            )

            for rid_i, file_hash, input_path, dur, out_path, rel_out in todo:
                if _previews_cancelled():
                    try:
                        _maybe_commit_previews(force=True, done_previews=done_previews)
                    except Exception:
                        try:
                            session.rollback()
                        except Exception:
                            pass
                    _update_progress(
                        ScanProgress(
                            phase="live_previews",
                            total_files=None,
                            scanned_files=0,
                            added=0,
                            updated=0,
                            skipped=0,
                            errors=preview_errors,
                            current_file=None,
                            current_recording=None,
                            total_previews=total_previews,
                            generated_previews=done_previews,
                            failed_previews=preview_errors,
                            current_preview=None,
                        )
                    )

                    with _LOCK:
                        j = _JOBS.get(job_id)
                        if j:
                            j.status = "cancelled"
                            j.finished_at = time.time()
                            j.result = ScanResult(scanned_files=0, added=0, updated=0, skipped=0, errors=preview_errors)
                    logger.warning(
                        "Missing live previews cancelled job=%s generated=%s errors=%s",
                        job_id,
                        done_previews,
                        preview_errors,
                    )
                    return

                _update_progress(
                    ScanProgress(
                        phase="live_previews",
                        total_files=None,
                        scanned_files=0,
                        added=0,
                        updated=0,
                        skipped=0,
                        errors=preview_errors,
                        current_file=None,
                        current_recording=str(input_path),
                        total_previews=total_previews,
                        generated_previews=done_previews,
                        failed_previews=preview_errors,
                        current_preview=rel_out,
                    )
                )

                try:
                    ok, _err = generate_live_preview_sync(
                        input_video=input_path,
                        output_mp4=out_path,
                        duration_seconds=dur,
                        segments=int(getattr(settings, "live_preview_segments", 10) or 10),
                        progress_cb=None,
                        timeout_seconds=180.0,
                    )
                    if not ok:
                        set_recording_live_preview_failure(
                            session.get(Recording, rid_i),
                            file_hash=file_hash,
                            error=_err,
                        )
                        preview_errors += 1
                    else:
                        clear_recording_live_preview_failure(session.get(Recording, rid_i))
                except Exception:
                    set_recording_live_preview_failure(
                        session.get(Recording, rid_i),
                        file_hash=file_hash,
                        error="Failed to generate animated preview",
                    )
                    preview_errors += 1
                done_previews += 1

                _maybe_commit_previews(force=True, done_previews=done_previews)

                _update_progress(
                    ScanProgress(
                        phase="live_previews",
                        total_files=None,
                        scanned_files=0,
                        added=0,
                        updated=0,
                        skipped=0,
                        errors=preview_errors,
                        current_file=None,
                        current_recording=str(input_path),
                        total_previews=total_previews,
                        generated_previews=done_previews,
                        failed_previews=preview_errors,
                        current_preview=rel_out,
                    )
                )

            session.commit()

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "done"
                    j.finished_at = time.time()
                    j.result = ScanResult(scanned_files=0, added=0, updated=0, skipped=0, errors=preview_errors)
            logger.info(
                "Missing live previews finished job=%s generated=%s errors=%s",
                job_id,
                done_previews,
                preview_errors,
            )
        except Exception as e:
            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "error"
                    j.error = str(e) or "Failed to generate animated previews"
                    j.finished_at = time.time()
            logger.warning("Missing live previews failed job=%s error=%s", job_id, str(e) or "Failed to generate animated previews")
        finally:
            try:
                session.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job
