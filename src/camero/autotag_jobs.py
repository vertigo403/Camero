"""Background auto-tagging jobs.

Wraps NSFWAutoTagger to process catalog recordings and assign tags stored in
the SQLite database.  Tags that do not exist yet are created on the fly.
Processing is sequential (one video at a time) in a daemon thread to avoid
overwhelming the ONNX runtime and ffmpeg.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import get_session_for_catalog
from .models import Recording, Tag
from .settings import AppSettings, DEFAULT_AUTO_TAG_CONFIDENCE, DEFAULT_AUTO_TAG_FRAME_INTERVAL, DEFAULT_AUTO_TAG_SCENE_THRESHOLD
from .catalog_paths import resolve_recording_path
from .logging_utils import get_logger


logger = get_logger("autotag_jobs")


# ---------------------------------------------------------------------------
# Job data model
# ---------------------------------------------------------------------------

@dataclass
class AutoTagProgress:
    total: int = 0
    completed: int = 0
    failed: int = 0
    current_file: str | None = None
    frames_used: int | None = None
    candidate_count: int | None = None


@dataclass
class AutoTagJob:
    job_id: str
    status: str  # queued | running | done | cancelled | error
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    progress: AutoTagProgress = field(default_factory=AutoTagProgress)
    error: str | None = None
    cancel_requested: bool = False
    # Internal event set immediately when cancel is requested; propagates
    # cancellation into the running ffmpeg subprocess without waiting for the
    # current video to finish.
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)


_LOCK = threading.Lock()
_JOBS: dict[str, AutoTagJob] = {}
_TAGGER_LOCK = threading.Lock()
_TAGGER_RUN_LOCK = threading.Lock()
_CACHED_TAGGER: object | None = None
_CACHED_TAGGER_SIGNATURE: tuple[str, str, float] | None = None
_EXTERNAL_AUTOTAG_PHASES: set[str] = set()


# ---------------------------------------------------------------------------
# Public access helpers
# ---------------------------------------------------------------------------

def _build_external_autotag_job(phase_id: str) -> AutoTagJob:
    return AutoTagJob(
        job_id=str(phase_id),
        status="running",
        created_at=time.time(),
        started_at=time.time(),
        progress=AutoTagProgress(),
        error=None,
    )

def get_auto_tag_job(job_id: str) -> AutoTagJob | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            return job
        if job_id in _EXTERNAL_AUTOTAG_PHASES:
            return _build_external_autotag_job(job_id)
        return None


def get_active_auto_tag_job() -> AutoTagJob | None:
    """Return the most recently created auto-tag job that is still active."""
    with _LOCK:
        if _EXTERNAL_AUTOTAG_PHASES:
            phase_id = sorted(_EXTERNAL_AUTOTAG_PHASES)[-1]
            return _build_external_autotag_job(phase_id)
        active = [j for j in _JOBS.values() if j.status in {"queued", "running"}]
        if not active:
            return None
        return max(active, key=lambda j: float(j.created_at or 0.0))


def register_external_autotag_phase(phase_id: str) -> None:
    with _LOCK:
        _EXTERNAL_AUTOTAG_PHASES.add(str(phase_id))


def unregister_external_autotag_phase(phase_id: str) -> None:
    with _LOCK:
        _EXTERNAL_AUTOTAG_PHASES.discard(str(phase_id))


def has_active_autotag_work() -> bool:
    with _LOCK:
        return bool(_EXTERNAL_AUTOTAG_PHASES) or any(
            j.status in {"queued", "running"} for j in _JOBS.values()
        )


def cancel_auto_tag(job_id: str) -> bool:
    if str(job_id).startswith("scan:"):
        from .scan_jobs import cancel_scan

        scan_job_id = str(job_id).split(":", 1)[1]
        ok = cancel_scan(scan_job_id)
        if ok:
            logger.info("Cancel requested for auto-tag scan phase job=%s", scan_job_id)
        return ok

    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return False
        j.cancel_requested = True
        j._cancel_event.set()
        logger.info("Cancel requested for auto-tag job=%s", job_id)
        return True


# ---------------------------------------------------------------------------
# Core tagging logic (reusable from scan_jobs)
# ---------------------------------------------------------------------------

def _resolve_tagger_resource_paths() -> tuple[Path, Path]:
    # Resolve model paths regardless of cwd.
    # In dev: project root / models / ...
    # In PyInstaller: executable directory / models / ...
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        # Walk up from this file to find the project root containing /models/.
        here = Path(__file__).resolve()
        # src/camero/autotag_jobs.py → project root is 2 levels up
        base = here.parent.parent.parent

    return base / "models" / "model.onnx", base / "models" / "selected_tags.csv"


def _tagger_signature(settings: AppSettings) -> tuple[str, str, float]:
    model_path, csv_path = _resolve_tagger_resource_paths()
    confidence = float(
        getattr(settings, "auto_tag_confidence", DEFAULT_AUTO_TAG_CONFIDENCE)
        or DEFAULT_AUTO_TAG_CONFIDENCE
    )
    return str(model_path), str(csv_path), confidence


def _create_tagger(settings: AppSettings) -> object:
    """Instantiate NSFWAutoTagger from the external model files shipped with the app."""
    model_path, csv_path = _resolve_tagger_resource_paths()

    # Import here to avoid making onnxruntime a hard dependency at import time.
    from autotagger.autotagger import NSFWAutoTagger  # type: ignore[import]

    confidence = float(
        getattr(settings, "auto_tag_confidence", DEFAULT_AUTO_TAG_CONFIDENCE)
        or DEFAULT_AUTO_TAG_CONFIDENCE
    )
    tagger = NSFWAutoTagger(
        model_path=str(model_path),
        csv_path=str(csv_path),
        umbral_confianza=confidence,
    )
    logger.info(
        "Auto-tagger loaded provider=%s requested=%s model=%s confidence=%s",
        getattr(tagger, "active_provider", "unknown"),
        getattr(tagger, "requested_providers", []),
        getattr(tagger, "model_path", str(model_path)),
        confidence,
    )
    return tagger


def release_cached_tagger(*, reason: str | None = None) -> None:
    global _CACHED_TAGGER, _CACHED_TAGGER_SIGNATURE

    with _TAGGER_LOCK:
        tagger = _CACHED_TAGGER
        if tagger is None:
            _CACHED_TAGGER_SIGNATURE = None
            return

        provider = getattr(tagger, "active_provider", "unknown")
        _CACHED_TAGGER = None
        _CACHED_TAGGER_SIGNATURE = None

    logger.info(
        "Auto-tagger cache released provider=%s reason=%s",
        provider,
        reason or "unspecified",
    )


def sync_cached_tagger(settings: AppSettings) -> object | None:
    """Keep the process-wide tagger cache aligned with current settings."""
    global _CACHED_TAGGER, _CACHED_TAGGER_SIGNATURE

    if not bool(getattr(settings, "auto_tag_after_scan", False)):
        release_cached_tagger(reason="auto_tag_after_scan_disabled")
        return None

    signature = _tagger_signature(settings)

    with _TAGGER_LOCK:
        if _CACHED_TAGGER is not None and _CACHED_TAGGER_SIGNATURE == signature:
            return _CACHED_TAGGER

    tagger = _create_tagger(settings)

    with _TAGGER_LOCK:
        previous = _CACHED_TAGGER
        previous_signature = _CACHED_TAGGER_SIGNATURE
        _CACHED_TAGGER = tagger
        _CACHED_TAGGER_SIGNATURE = signature

    if previous is None:
        logger.info("Auto-tagger cache warmed provider=%s", getattr(tagger, "active_provider", "unknown"))
    elif previous_signature != signature:
        logger.info(
            "Auto-tagger cache refreshed provider=%s confidence=%s",
            getattr(tagger, "active_provider", "unknown"),
            signature[2],
        )

    return tagger

def _get_tagger(settings: AppSettings) -> object:
    cached = sync_cached_tagger(settings)
    if cached is not None:
        return cached
    return _create_tagger(settings)


def _untagged_autotag_filter():
    return (
        ~Recording.tags.any(),
        or_(
            Recording.auto_tag_attempted_at.is_(None),
            Recording.file_mtime.is_not(None) & (Recording.auto_tag_attempted_mtime.is_(None)),
            Recording.file_mtime.is_not(None) & (Recording.auto_tag_attempted_mtime != Recording.file_mtime),
        ),
    )


def auto_tag_recording_with_session(
    session: Session,
    recording: Recording,
    tagger: object,
    settings: AppSettings,
    *,
    progress_cb: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
    frame_count_cb: Callable | None = None,
) -> tuple[list[str], int]:
    """Tag a single recording and persist the results to the DB.

    Returns the list of newly assigned tag names (empty if nothing new).
    Raises on error so callers can decide whether to continue.
    """
    from autotagger.autotagger import NSFWAutoTagger  # type: ignore[import]
    assert isinstance(tagger, NSFWAutoTagger)

    scene_threshold = float(
        getattr(settings, "auto_tag_scene_threshold", DEFAULT_AUTO_TAG_SCENE_THRESHOLD)
        or DEFAULT_AUTO_TAG_SCENE_THRESHOLD
    )
    raw_interval = str(
        getattr(settings, "auto_tag_frame_interval", DEFAULT_AUTO_TAG_FRAME_INTERVAL)
        or DEFAULT_AUTO_TAG_FRAME_INTERVAL
    )
    frame_interval: float | str
    if raw_interval.strip().lower() == "auto":
        frame_interval = "auto"
    else:
        try:
            frame_interval = float(raw_interval)
        except Exception:
            frame_interval = float(DEFAULT_AUTO_TAG_FRAME_INTERVAL)

    input_path = resolve_recording_path(settings, str(recording.rel_path))
    if input_path is None or not input_path.exists():
        raise FileNotFoundError(f"Recording file not found: {recording.rel_path}")

    provider = getattr(tagger, "active_provider", "unknown")
    started_at = time.monotonic()
    logger.info(
        "Auto-tagging recording started id=%s rel_path=%s provider=%s",
        getattr(recording, "id", None),
        recording.rel_path,
        provider,
    )

    if progress_cb:
        progress_cb(str(input_path))

    with _TAGGER_RUN_LOCK:
        detail = tagger.tag_video_detailed(  # type: ignore[attr-defined]
            str(input_path),
            frame_interval=frame_interval,
            scene_threshold=scene_threshold,
            stop_event=stop_event,
            frame_count_cb=frame_count_cb,
        )
    tag_names: list[str] = detail["video_tags"]
    frames_used: int = int(detail.get("frame_count", 0))
    elapsed_seconds = round(time.monotonic() - started_at, 2)
    recording.auto_tag_attempted_at = datetime.utcnow()
    recording.auto_tag_attempted_mtime = recording.file_mtime

    if not tag_names:
        logger.info(
            "Auto-tagged recording id=%s rel_path=%s provider=%s frames=%s elapsed=%ss tags=[]",
            getattr(recording, "id", None),
            recording.rel_path,
            provider,
            frames_used,
            elapsed_seconds,
        )
        return [], frames_used

    existing_tag_names = {t.name for t in recording.tags}
    newly_added: list[str] = []

    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        if name in existing_tag_names:
            continue
        # Get-or-create the tag.
        tag = session.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()
        recording.tags.append(tag)
        newly_added.append(name)

    logger.info(
        "Auto-tagged recording id=%s rel_path=%s provider=%s frames=%s elapsed=%ss tags=%s new_tags=%s",
        getattr(recording, "id", None),
        recording.rel_path,
        provider,
        frames_used,
        elapsed_seconds,
        tag_names,
        newly_added,
    )

    return newly_added, frames_used


# ---------------------------------------------------------------------------
# Job launchers
# ---------------------------------------------------------------------------

def _make_job() -> tuple[str, AutoTagJob]:
    job_id = uuid.uuid4().hex
    job = AutoTagJob(job_id=job_id, status="queued", created_at=time.time())
    with _LOCK:
        _JOBS[job_id] = job
    return job_id, job


def _check_existing_active() -> AutoTagJob | None:
    with _LOCK:
        if _EXTERNAL_AUTOTAG_PHASES:
            phase_id = sorted(_EXTERNAL_AUTOTAG_PHASES)[-1]
            return _build_external_autotag_job(phase_id)
        active = [j for j in _JOBS.values() if j.status in {"queued", "running"}]
        if active:
            return max(active, key=lambda j: float(j.created_at or 0.0))
    return None


def start_auto_tag_untagged_job(settings: AppSettings) -> AutoTagJob:
    """Auto-tag all recordings that have no tags assigned.

    Returns an existing active job if one is already running.
    """
    if not settings.catalog_root:
        raise ValueError("Catalog root is not configured")

    existing = _check_existing_active()
    if existing:
        logger.info("Auto-tag job already active job=%s", existing.job_id)
        return existing
    if has_active_autotag_work():
        raise ValueError("Auto-tagging is already active from another job")

    job_id, job = _make_job()
    logger.info("Auto-tag untagged job created job=%s", job_id)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j.status = "running"
                j.started_at = time.time()

        session_factory = get_session_for_catalog(settings.catalog_root or "")
        session: Session = session_factory()
        try:
            tagger = _get_tagger(settings)

            # Grab the cancel event for this job so we can propagate it into ffmpeg.
            with _LOCK:
                j = _JOBS.get(job_id)
                cancel_event = j._cancel_event if j else threading.Event()

            # Query recordings that have no tags.
            rows = session.scalars(
                select(Recording).where(*_untagged_autotag_filter())
            ).all()

            total = len(rows)
            completed = 0
            failed = 0

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.progress = AutoTagProgress(total=total)

            def _on_frame_count_untagged(selected: int, candidates: int | None) -> None:
                with _LOCK:
                    _j = _JOBS.get(job_id)
                    if _j:
                        _j.progress.frames_used = selected
                        _j.progress.candidate_count = candidates

            for rec in rows:
                with _LOCK:
                    j = _JOBS.get(job_id)
                    if j and j.cancel_requested:
                        break

                with _LOCK:
                    j = _JOBS.get(job_id)
                    if j:
                        j.progress = AutoTagProgress(
                            total=total,
                            completed=completed,
                            failed=failed,
                            current_file=str(rec.rel_path),
                        )

                try:
                    _, frames_used = auto_tag_recording_with_session(
                        session, rec, tagger, settings, stop_event=cancel_event,
                        frame_count_cb=_on_frame_count_untagged,
                    )
                    session.commit()
                    completed += 1
                    with _LOCK:
                        j = _JOBS.get(job_id)
                        if j:
                            j.progress = AutoTagProgress(
                                total=total,
                                completed=completed,
                                failed=failed,
                                current_file=str(rec.rel_path),
                                frames_used=j.progress.frames_used,
                                candidate_count=j.progress.candidate_count,
                            )
                    logger.debug("Auto-tagged recording rel_path=%s frames=%s", rec.rel_path, frames_used)
                except InterruptedError:
                    # ffmpeg was killed by cancellation.
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    with _LOCK:
                        j = _JOBS.get(job_id)
                        if j:
                            j.cancel_requested = True
                    break
                except Exception as exc:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    failed += 1
                    logger.warning("Auto-tag failed for rel_path=%s error=%s", rec.rel_path, exc)

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.progress = AutoTagProgress(total=total, completed=completed, failed=failed)
                    j.status = "cancelled" if (j.cancel_requested and completed < total) else "done"
                    j.finished_at = time.time()

            logger.info(
                "Auto-tag untagged job finished job=%s total=%s completed=%s failed=%s",
                job_id, total, completed, failed,
            )
        except Exception as exc:
            try:
                session.rollback()
            except Exception:
                pass
            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "error"
                    j.error = str(exc)
                    j.finished_at = time.time()
            logger.warning("Auto-tag untagged job failed job=%s error=%s", job_id, exc)
        finally:
            try:
                session.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job


def start_auto_tag_recordings_job(settings: AppSettings, recording_ids: list[int]) -> AutoTagJob:
    """Auto-tag specific recordings by ID (always creates a new job)."""
    if not settings.catalog_root:
        raise ValueError("Catalog root is not configured")
    if not recording_ids:
        raise ValueError("No recording IDs provided")

    existing = _check_existing_active()
    if existing:
        logger.info("Auto-tag job already active job=%s", existing.job_id)
        return existing
    if has_active_autotag_work():
        raise ValueError("Auto-tagging is already active from another job")

    job_id, job = _make_job()
    logger.info("Auto-tag specific job created job=%s ids=%s", job_id, recording_ids)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j.status = "running"
                j.started_at = time.time()

        session_factory = get_session_for_catalog(settings.catalog_root or "")
        session: Session = session_factory()
        try:
            tagger = _get_tagger(settings)

            # Grab the cancel event for this job so we can propagate it into ffmpeg.
            with _LOCK:
                j = _JOBS.get(job_id)
                cancel_event = j._cancel_event if j else threading.Event()

            total = len(recording_ids)
            completed = 0
            failed = 0

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.progress = AutoTagProgress(total=total)

            def _on_frame_count_specific(selected: int, candidates: int | None) -> None:
                with _LOCK:
                    _j = _JOBS.get(job_id)
                    if _j:
                        _j.progress.frames_used = selected
                        _j.progress.candidate_count = candidates

            for rid in recording_ids:
                with _LOCK:
                    j = _JOBS.get(job_id)
                    if j and j.cancel_requested:
                        break

                rec = session.get(Recording, rid)
                if rec is None:
                    failed += 1
                    continue

                with _LOCK:
                    j = _JOBS.get(job_id)
                    if j:
                        j.progress = AutoTagProgress(
                            total=total,
                            completed=completed,
                            failed=failed,
                            current_file=str(rec.rel_path),
                        )

                try:
                    _, frames_used = auto_tag_recording_with_session(
                        session, rec, tagger, settings, stop_event=cancel_event,
                        frame_count_cb=_on_frame_count_specific,
                    )
                    session.commit()
                    completed += 1
                    with _LOCK:
                        j = _JOBS.get(job_id)
                        if j:
                            j.progress = AutoTagProgress(
                                total=total,
                                completed=completed,
                                failed=failed,
                                current_file=str(rec.rel_path),
                                frames_used=j.progress.frames_used,
                                candidate_count=j.progress.candidate_count,
                            )
                    logger.debug("Auto-tagged recording id=%s rel_path=%s frames=%s", rid, rec.rel_path, frames_used)
                except InterruptedError:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    with _LOCK:
                        j = _JOBS.get(job_id)
                        if j:
                            j.cancel_requested = True
                    break
                except Exception as exc:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    failed += 1
                    logger.warning("Auto-tag failed for id=%s rel_path=%s error=%s", rid, getattr(rec, "rel_path", "?"), exc)

            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.progress = AutoTagProgress(total=total, completed=completed, failed=failed)
                    j.status = "cancelled" if (j.cancel_requested and completed < total) else "done"
                    j.finished_at = time.time()

            logger.info(
                "Auto-tag specific job finished job=%s total=%s completed=%s failed=%s",
                job_id, total, completed, failed,
            )
        except Exception as exc:
            try:
                session.rollback()
            except Exception:
                pass
            with _LOCK:
                j = _JOBS.get(job_id)
                if j:
                    j.status = "error"
                    j.error = str(exc)
                    j.finished_at = time.time()
            logger.warning("Auto-tag specific job failed job=%s error=%s", job_id, exc)
        finally:
            try:
                session.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job
