from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logging_utils import get_logger


logger = get_logger("transcode_jobs")


@dataclass
class TranscodeJob:
    job_id: str
    recording_id: int
    status: str  # queued | running | done | error
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    percent: float = 0.0
    output_rel_path: str | None = None
    error: str | None = None


_LOCK = threading.Lock()
_JOBS: dict[str, TranscodeJob] = {}


def get_transcode_job(job_id: str) -> TranscodeJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_active_transcode_job() -> TranscodeJob | None:
    """Return the most recently created transcode job that is still active."""
    with _LOCK:
        active = [j for j in _JOBS.values() if (j.status in {"queued", "running"})]
        if not active:
            return None
        return max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))


def _set_job(job: TranscodeJob) -> None:
    with _LOCK:
        _JOBS[job.job_id] = job


def start_transcode_job(
    *,
    recording_id: int,
    catalog_root: str,
    input_video: Path,
    output_mp4: Path,
    duration_seconds: float | None,
    on_success: Callable[[], None] | None = None,
) -> TranscodeJob:
    # If already transcoded, return a completed job quickly.
    rel = str(output_mp4.relative_to(Path(catalog_root))).replace("\\", "/")
    recording_name = input_video.name
    if output_mp4.exists() and output_mp4.stat().st_size > 0:
        job = TranscodeJob(
            job_id=uuid.uuid4().hex,
            recording_id=recording_id,
            status="done",
            created_at=time.time(),
            started_at=time.time(),
            finished_at=time.time(),
            percent=100.0,
            output_rel_path=rel,
            error=None,
        )
        _set_job(job)
        if on_success is not None:
            try:
                on_success()
            except Exception:
                pass
        logger.info("Transcode skipped; output already exists job=%s recording=%s", job.job_id, recording_name)
        return job

    job = TranscodeJob(
        job_id=uuid.uuid4().hex,
        recording_id=recording_id,
        status="queued",
        created_at=time.time(),
        percent=0.0,
        output_rel_path=rel,
    )
    _set_job(job)
    logger.info("Transcode started job=%s recording=%s", job.job_id, recording_name)

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job.job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        # ffmpeg progress: writes key=value lines to stdout.
        # We keep logs minimal; only parse out_time_ms.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output_mp4),
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as e:
            with _LOCK:
                j = _JOBS.get(job.job_id)
                if j:
                    j.status = "error"
                    j.error = str(e)
                    j.finished_at = time.time()
            logger.warning("Transcode failed job=%s recording=%s error=%s", job.job_id, recording_name, str(e))
            return

        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line = (line or "").strip()
                    if not line:
                        continue

                    if line.startswith("out_time_ms="):
                        try:
                            out_time_ms = int(line.split("=", 1)[1])
                        except Exception:
                            out_time_ms = 0

                        pct = 0.0
                        if duration_seconds and duration_seconds > 0:
                            pct = (out_time_ms / (duration_seconds * 1_000_000.0)) * 100.0
                        pct = max(0.0, min(99.9, float(pct)))

                        with _LOCK:
                            j = _JOBS.get(job.job_id)
                            if j and j.status == "running":
                                j.percent = pct

                    if line == "progress=end":
                        break

            rc = proc.wait()

            if rc == 0 and output_mp4.exists() and output_mp4.stat().st_size > 0:
                with _LOCK:
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "done"
                        j.percent = 100.0
                        j.finished_at = time.time()
                if on_success is not None:
                    try:
                        on_success()
                    except Exception:
                        pass
                logger.info("Transcode finished job=%s recording=%s output=%s", job.job_id, recording_name, rel)
            else:
                with _LOCK:
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "error"
                        j.error = f"ffmpeg failed (exit {rc})"
                        j.finished_at = time.time()
                logger.warning("Transcode failed job=%s recording=%s error=ffmpeg failed (exit %s)", job.job_id, recording_name, rc)
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job
