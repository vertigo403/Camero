from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logging_utils import get_logger


logger = get_logger("remux_jobs")


@dataclass
class RemuxJob:
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
_JOBS: dict[str, RemuxJob] = {}


def get_remux_job(job_id: str) -> RemuxJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def get_active_remux_job() -> RemuxJob | None:
    """Return the most recently created remux job that is still active."""
    with _LOCK:
        active = [j for j in _JOBS.values() if (j.status in {"queued", "running"})]
        if not active:
            return None
        return max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))


def _set_job(job: RemuxJob) -> None:
    with _LOCK:
        _JOBS[job.job_id] = job


def start_remux_job(
    *,
    recording_id: int,
    catalog_root: str,
    input_video: Path,
    output_mp4: Path,
    duration_seconds: float | None,
    audio_codec: str | None = None,
    on_success: Callable[[], None] | None = None,
) -> RemuxJob:
    rel = str(output_mp4.relative_to(Path(catalog_root))).replace("\\", "/")
    recording_name = input_video.name

    if output_mp4.exists() and output_mp4.stat().st_size > 0:
        job = RemuxJob(
            job_id=uuid.uuid4().hex,
            recording_id=recording_id,
            status="done",
            created_at=time.time(),
            started_at=time.time(),
            finished_at=time.time(),
            percent=100.0,
            output_rel_path=rel,
        )
        _set_job(job)
        if on_success is not None:
            try:
                on_success()
            except Exception:
                pass
        logger.info("Remux skipped; output already exists job=%s recording=%s", job.job_id, recording_name)
        return job

    job = RemuxJob(
        job_id=uuid.uuid4().hex,
        recording_id=recording_id,
        status="queued",
        created_at=time.time(),
        percent=0.0,
        output_rel_path=rel,
    )
    _set_job(job)
    logger.info("Remux started job=%s recording=%s", job.job_id, recording_name)

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job.job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        # Remux into MP4 without transcoding.
        # We intentionally keep only the first video/audio streams to maximize compatibility.
        audio = (audio_codec or "").strip().lower()

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "+genpts",
            "-i",
            str(input_video),
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output_mp4),
        ]

        # For TS/AAC (ADTS) inputs. This bitstream filter can fail for non-AAC audio
        # (e.g. MP3 in FLV), so only apply it when we know audio is AAC.
        if audio == "aac":
            cmd.insert(-4, "-bsf:a")
            cmd.insert(-4, "aac_adtstoasc")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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
            logger.warning("Remux failed job=%s recording=%s error=%s", job.job_id, recording_name, str(e))
            return

        stderr_lines: list[str] = []
        try:
            # Read progress lines from stdout.
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

            # Capture stderr (errors only) for a better message.
            try:
                if proc.stderr is not None:
                    stderr_text = proc.stderr.read() or ""
                    stderr_lines = [l.strip() for l in stderr_text.splitlines() if l.strip()]
            except Exception:
                stderr_lines = []

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
                logger.info("Remux finished job=%s recording=%s output=%s", job.job_id, recording_name, rel)
            else:
                msg = f"ffmpeg remux failed (exit {rc})"
                if stderr_lines:
                    msg = msg + ": " + stderr_lines[-1]
                with _LOCK:
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "error"
                        j.error = msg
                        j.finished_at = time.time()
                logger.warning("Remux failed job=%s recording=%s error=%s", job.job_id, recording_name, msg)
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.stderr:
                    proc.stderr.close()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()
    return job
