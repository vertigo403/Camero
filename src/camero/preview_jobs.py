from __future__ import annotations

import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import get_session_for_catalog, session_scope
from .logging_utils import get_logger
from .models import Recording


logger = get_logger("preview_jobs")


LIVE_PREVIEW_SEGMENT_SECONDS = 1.0

_ALLOWED_LIVE_PREVIEW_SEGMENTS: set[int] = {5, 10, 15, 20}
_DEFAULT_LIVE_PREVIEW_SEGMENTS = 10


def _coerce_live_preview_segments(value: object | None) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except Exception:
        n = _DEFAULT_LIVE_PREVIEW_SEGMENTS
    if n not in _ALLOWED_LIVE_PREVIEW_SEGMENTS:
        return _DEFAULT_LIVE_PREVIEW_SEGMENTS
    return n


@dataclass
class LivePreviewJob:
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
_JOBS: dict[str, LivePreviewJob] = {}


def get_live_preview_job(job_id: str) -> LivePreviewJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def _set_job(job: LivePreviewJob) -> None:
    with _LOCK:
        _JOBS[job.job_id] = job


def live_preview_output_path_for_hash(*, catalog_root: str, file_hash: str) -> Path:
    # New layout: previews keyed by a quick file fingerprint so they can be reused
    # across catalog DB rebuilds.
    safe = (file_hash or "").strip().lower()
    return Path(catalog_root) / ".camero" / "previews" / f"{safe}.mp4"


def live_preview_exists(*, catalog_root: str, file_hash: str | None) -> bool:
    if not isinstance(file_hash, str) or not file_hash.strip():
        return False

    p = live_preview_output_path_for_hash(catalog_root=catalog_root, file_hash=file_hash)
    try:
        return bool(p.exists() and p.stat().st_size > 0)
    except Exception:
        return False


def normalize_file_hash(file_hash: str | None) -> str | None:
    if not isinstance(file_hash, str):
        return None
    safe = file_hash.strip().lower()
    return safe or None


def recording_has_blocked_live_preview(recording: Recording | None) -> bool:
    if recording is None:
        return False

    current_hash = normalize_file_hash(getattr(recording, "file_hash", None))
    blocked_hash = normalize_file_hash(getattr(recording, "live_preview_failed_hash", None))
    return bool(current_hash and blocked_hash and current_hash == blocked_hash)


def clear_recording_live_preview_failure(recording: Recording | None) -> bool:
    if recording is None:
        return False

    changed = False
    if getattr(recording, "live_preview_failed_hash", None) is not None:
        recording.live_preview_failed_hash = None
        changed = True
    if getattr(recording, "live_preview_failure_reason", None) is not None:
        recording.live_preview_failure_reason = None
        changed = True
    return changed


def set_recording_live_preview_failure(recording: Recording | None, *, file_hash: str | None, error: str | None) -> bool:
    if recording is None:
        return False

    blocked_hash = normalize_file_hash(file_hash)
    if blocked_hash is None:
        return False

    current_hash = normalize_file_hash(getattr(recording, "file_hash", None))
    if current_hash is not None and current_hash != blocked_hash:
        return False

    changed = False
    if getattr(recording, "live_preview_failed_hash", None) != blocked_hash:
        recording.live_preview_failed_hash = blocked_hash
        changed = True

    reason = (error or "").strip()
    normalized_reason = reason[:512] if reason else None
    if getattr(recording, "live_preview_failure_reason", None) != normalized_reason:
        recording.live_preview_failure_reason = normalized_reason
        changed = True

    return changed


def clear_live_preview_failure_for_recording(*, catalog_root: str, recording_id: int) -> None:
    session_factory = get_session_for_catalog(catalog_root)
    with session_scope(session_factory) as session:
        rec = session.get(Recording, int(recording_id))
        if rec is None:
            return
        clear_recording_live_preview_failure(rec)


def mark_live_preview_failure_for_recording(
    *,
    catalog_root: str,
    recording_id: int,
    file_hash: str | None,
    error: str | None,
) -> None:
    blocked_hash = normalize_file_hash(file_hash)
    if blocked_hash is None:
        return

    session_factory = get_session_for_catalog(catalog_root)
    with session_scope(session_factory) as session:
        rec = session.get(Recording, int(recording_id))
        if rec is None:
            return
        set_recording_live_preview_failure(rec, file_hash=blocked_hash, error=error)


def _should_pre_remux_for_preview(input_video: Path) -> bool:
    # FLV is frequently not browser-playable and may have poor seek behavior.
    # Our preview generator does many short seeks; remuxing to MP4 first usually
    # improves reliability and performance.
    return input_video.suffix.lower() == ".flv"


def _remux_for_preview_sync(*, input_video: Path, output_mp4: Path) -> tuple[bool, str | None]:
    """Best-effort remux to MP4 for preview generation (no transcoding)."""

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        if output_mp4.exists() and output_mp4.stat().st_size > 0:
            return True, None
    except Exception:
        pass

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
        str(output_mp4),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        if proc.returncode == 0 and output_mp4.exists() and output_mp4.stat().st_size > 0:
            return True, None
    except Exception:
        pass

    stderr = (proc.stderr or "").strip()
    if stderr:
        last = stderr.splitlines()[-1].strip()
        return False, last[:400]
    return False, f"ffmpeg remux failed (exit {proc.returncode})"


def _segment_start_times(duration_seconds: float | None, segments: int) -> list[float]:
    # N segments of 1 second. Each segment corresponds to a slice of the video.
    # Clamp starts so start+1s doesn't exceed duration.
    segments = _coerce_live_preview_segments(segments)
    if not duration_seconds or duration_seconds <= 0:
        return [0.0] * segments

    max_start = max(0.0, float(duration_seconds) - 1.0)
    starts: list[float] = []
    for i in range(segments):
        t = (float(duration_seconds) * i) / float(segments)
        if t > max_start:
            t = max_start
        if t < 0:
            t = 0.0
        starts.append(t)
    return starts


def _build_live_preview_cmd(
    *,
    input_video: Path,
    output_mp4: Path,
    duration_seconds: float | None,
    video_encoder: str,
    segments: int,
) -> list[str]:
    starts = _segment_start_times(duration_seconds, segments)
    n = len(starts)

    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "auto",
    ]

    # Use N inputs with per-input seek.
    for t in starts:
        cmd += [
            "-ss",
            f"{t:.3f}",
            "-t",
            "1",
            "-i",
            str(input_video),
        ]

    # concat filter
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")

    concat_inputs = "".join([f"[v{i}]" for i in range(n)])
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=0[vcat]")
    parts.append("[vcat]scale=320:-2,format=yuv420p[vout]")
    filter_complex = ";".join(parts)

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-an",
        "-sn",
        "-dn",
        "-movflags",
        "+faststart",
        "-r",
        "30",
    ]

    if video_encoder == "libx264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]
    else:
        # Hardware encoders typically don't support CRF; use bitrate.
        cmd += ["-c:v", video_encoder, "-b:v", "1200k"]

    cmd += [
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_mp4),
    ]

    return cmd


def _run_with_progress(
    *,
    cmd: list[str],
    job_id: str,
    total_seconds: float,
    timeout_seconds: float | None = None,
) -> tuple[int, list[str]]:
    """Run ffmpeg, parse -progress output and update job percent.

    Uses a reader thread so we can enforce timeouts without blocking on stdout.
    """

    def _on_progress(out_time_ms: int) -> None:
        pct = (out_time_ms / (total_seconds * 1_000_000.0)) * 100.0
        pct = max(0.0, min(99.9, float(pct)))
        with _LOCK:
            j = _JOBS.get(job_id)
            if j and j.status == "running":
                j.percent = pct

    return _run_ffmpeg_with_progress(cmd=cmd, on_progress=_on_progress, timeout_seconds=timeout_seconds)


def _run_ffmpeg_with_progress(
    *,
    cmd: list[str],
    on_progress: Callable[[int], None] | None,
    timeout_seconds: float | None,
) -> tuple[int, list[str]]:
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

    stderr_lines: list[str] = []
    stdout_done = threading.Event()
    stderr_done = threading.Event()
    progress_end = threading.Event()
    last_out_time_ms = 0

    def _stdout_reader() -> None:
        nonlocal last_out_time_ms
        try:
            if proc.stdout is None:
                return
            for raw in proc.stdout:
                line = (raw or "").strip()
                if not line:
                    continue

                if line.startswith("out_time_ms="):
                    try:
                        last_out_time_ms = int(line.split("=", 1)[1])
                    except Exception:
                        last_out_time_ms = 0
                    if on_progress is not None:
                        try:
                            on_progress(last_out_time_ms)
                        except Exception:
                            pass

                if line == "progress=end":
                    progress_end.set()
                    continue
        finally:
            stdout_done.set()

    def _stderr_reader() -> None:
        try:
            if proc.stderr is None:
                return
            for raw in proc.stderr:
                line = (raw or "").strip()
                if not line:
                    continue
                stderr_lines.append(line)
                # Avoid unbounded growth if ffmpeg is very chatty.
                if len(stderr_lines) > 200:
                    del stderr_lines[:100]
        finally:
            stderr_done.set()

    t = threading.Thread(target=_stdout_reader, daemon=True)
    t.start()

    t_err = threading.Thread(target=_stderr_reader, daemon=True)
    t_err.start()

    start = time.monotonic()
    timed_out = False
    rc: int
    try:
        while True:
            try:
                rc = proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if timeout_seconds is not None and (time.monotonic() - start) > float(timeout_seconds):
                    timed_out = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    # After kill, wait without timeout.
                    rc = proc.wait()
                    break
                continue

        # Best-effort: let reader threads drain remaining output.
        stdout_done.wait(timeout=1.0)
        stderr_done.wait(timeout=1.0)

        if timed_out:
            stderr_lines = stderr_lines or ["ffmpeg timeout"]
            return 124, stderr_lines

        return rc, stderr_lines
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


def generate_live_preview_sync(
    *,
    input_video: Path,
    output_mp4: Path,
    duration_seconds: float | None,
    segments: int | None = None,
    progress_cb: Callable[[float], None] | None = None,
    timeout_seconds: float | None = 180.0,
) -> tuple[bool, str | None]:
    """Generate an animated preview synchronously.

    Returns (ok, error_message).
    """

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    # For FLV inputs, try remuxing first for better seeking.
    if _should_pre_remux_for_preview(input_video):
        remuxed = output_mp4.parent.parent / "remux" / f"{int(output_mp4.stem)}.mp4" if output_mp4.stem.isdigit() else None
        if remuxed is not None:
            ok, _err = _remux_for_preview_sync(input_video=input_video, output_mp4=remuxed)
            if ok:
                input_video = remuxed

    encoders = ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
    segs = _coerce_live_preview_segments(segments)
    total_seconds = float(segs) * float(LIVE_PREVIEW_SEGMENT_SECONDS)

    def _run(cmd: list[str]) -> tuple[int, list[str]]:
        last_pct = -1.0

        def _on_progress(out_time_ms: int) -> None:
            nonlocal last_pct
            pct = (out_time_ms / (total_seconds * 1_000_000.0)) * 100.0
            pct = max(0.0, min(99.9, float(pct)))
            if progress_cb is not None and pct - last_pct >= 1.0:
                last_pct = pct
                try:
                    progress_cb(pct)
                except Exception:
                    pass

        return _run_ffmpeg_with_progress(cmd=cmd, on_progress=_on_progress if progress_cb is not None else None, timeout_seconds=timeout_seconds)

    last_err: str | None = None
    for enc in encoders:
        cmd = _build_live_preview_cmd(
            input_video=input_video,
            output_mp4=output_mp4,
            duration_seconds=duration_seconds,
            video_encoder=enc,
            segments=segs,
        )

        try:
            rc, stderr_lines = _run(cmd)
        except Exception as e:
            last_err = str(e)
            continue

        if rc == 0 and output_mp4.exists() and output_mp4.stat().st_size > 0:
            if progress_cb is not None:
                try:
                    progress_cb(100.0)
                except Exception:
                    pass
            return True, None

        msg = f"ffmpeg failed (exit {rc})"
        if stderr_lines:
            msg = msg + ": " + stderr_lines[-1]
        last_err = f"{enc}: {msg}"

        try:
            if output_mp4.exists():
                output_mp4.unlink()
        except Exception:
            pass

    return False, last_err or "Failed to generate animated preview"


def start_live_preview_job(
    *,
    recording_id: int,
    catalog_root: str,
    input_video: Path,
    output_mp4: Path,
    duration_seconds: float | None,
    segments: int | None = None,
    on_success: Callable[[], None] | None = None,
    on_failure: Callable[[str | None], None] | None = None,
) -> LivePreviewJob:
    rel = str(output_mp4.relative_to(Path(catalog_root))).replace("\\", "/")
    recording_name = input_video.name

    if output_mp4.exists() and output_mp4.stat().st_size > 0:
        job = LivePreviewJob(
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
        logger.info("Live preview skipped; output already exists job=%s recording=%s", job.job_id, recording_name)
        return job

    job = LivePreviewJob(
        job_id=uuid.uuid4().hex,
        recording_id=recording_id,
        status="queued",
        created_at=time.time(),
        percent=0.0,
        output_rel_path=rel,
    )
    _set_job(job)
    logger.info("Live preview started job=%s recording=%s segments=%s", job.job_id, recording_name, _coerce_live_preview_segments(segments))

    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job.job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        last_err: str | None = None

        # For FLV inputs, remux to MP4 first (better seeking).
        input_for_preview = input_video
        if _should_pre_remux_for_preview(input_video):
            remux_path = Path(catalog_root) / ".camero" / "remux" / f"{recording_id}.mp4"
            ok, err = _remux_for_preview_sync(input_video=input_video, output_mp4=remux_path)
            if ok:
                input_for_preview = remux_path
            else:
                # Keep original input, but remember the remux error for diagnostics.
                last_err = f"remux: {err}" if err else "remux: failed"

        # Prefer hardware encoders but fall back to libx264.
        encoders = ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"]
        segs = _coerce_live_preview_segments(segments)
        total_seconds = float(segs) * float(LIVE_PREVIEW_SEGMENT_SECONDS)
        timeout_seconds = 180.0

        for enc in encoders:
            cmd = _build_live_preview_cmd(
                input_video=input_for_preview,
                output_mp4=output_mp4,
                duration_seconds=duration_seconds,
                video_encoder=enc,
                segments=segs,
            )

            try:
                rc, stderr_lines = _run_with_progress(
                    cmd=cmd,
                    job_id=job.job_id,
                    total_seconds=total_seconds,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as e:
                last_err = str(e)
                continue

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
                logger.info("Live preview finished job=%s recording=%s output=%s", job.job_id, recording_name, rel)
                return

            msg = f"ffmpeg failed (exit {rc})"
            if stderr_lines:
                msg = msg + ": " + stderr_lines[-1]
            last_err = f"{enc}: {msg}"

            # Clean partial output before retrying another encoder.
            try:
                if output_mp4.exists():
                    output_mp4.unlink()
            except Exception:
                pass

        with _LOCK:
            j = _JOBS.get(job.job_id)
            if j:
                j.status = "error"
                j.error = last_err or "Failed to generate animated preview"
                j.finished_at = time.time()
        if on_failure is not None:
            try:
                on_failure(last_err)
            except Exception:
                pass
        logger.warning(
            "Live preview failed job=%s recording=%s error=%s",
            job.job_id,
            recording_name,
            last_err or "Failed to generate animated preview",
        )

    threading.Thread(target=_runner, daemon=True).start()
    return job
