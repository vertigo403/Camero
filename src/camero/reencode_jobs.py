from __future__ import annotations

import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logging_utils import get_logger


logger = get_logger("reencode_jobs")


@dataclass(frozen=True)
class ReencodeOptions:
    container: str  # mp4 | mkv | avi | webm
    video_codec: str  # h264 | hevc | vp9 | av1 | theora | copy
    audio_codec: str  # aac | mp3 | opus | flac | copy | none
    quality: str  # small | balanced | high | lossless
    speed: str  # ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow
    audio_bitrate: str | None = None
    extra_ffmpeg_args: str | None = None
    delete_original: bool = False


@dataclass
class ReencodeItem:
    recording_id: int
    input_video: Path
    output_path: Path
    duration_seconds: float | None
    status: str  # queued | running | done | error | canceled
    temp_output_path: Path | None = None
    percent: float = 0.0
    error: str | None = None
    file_name: str | None = None


@dataclass
class ReencodeJob:
    job_id: str
    status: str  # queued | running | done | error | canceled
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    items: list[ReencodeItem] | None = None
    current_recording_id: int | None = None
    error: str | None = None


_LOCK = threading.Lock()
_JOBS: dict[str, ReencodeJob] = {}
_CANCEL: dict[str, bool] = {}
_PROCS: dict[str, subprocess.Popen[str]] = {}


def get_reencode_job(job_id: str) -> ReencodeJob | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        return j


def get_active_reencode_job() -> ReencodeJob | None:
    """Return the most recently created reencode job that is still active."""
    with _LOCK:
        active = [j for j in _JOBS.values() if (j.status in {"queued", "running"})]
        if not active:
            return None
        return max(active, key=lambda j: float(getattr(j, "created_at", 0.0) or 0.0))


def get_reencode_output_path(job_id: str, recording_id: int) -> Path | None:
    with _LOCK:
        j = _JOBS.get(job_id)
        items = (j.items or []) if j else []
        for it in items:
            if int(getattr(it, "recording_id", -1)) == int(recording_id) and getattr(it, "status", "") == "done":
                op = getattr(it, "output_path", None)
                return Path(str(op)) if op is not None else None
    return None


def cancel_reencode_job(job_id: str) -> bool:
    with _LOCK:
        if job_id not in _JOBS:
            return False
        _CANCEL[job_id] = True
        proc = _PROCS.get(job_id)

    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return True


def _parse_extra_args(raw: str | None) -> list[str]:
    if not raw:
        return []
    s = (raw or "").strip()
    if not s:
        return []
    try:
        return shlex.split(s, posix=False)
    except Exception:
        # Best-effort: split on whitespace.
        return [p for p in s.split() if p]


def _quality_to_crf(opts: ReencodeOptions) -> int | None:
    q = (opts.quality or "balanced").lower()
    if q == "lossless":
        return 0
    if q == "high":
        return 18
    if q == "small":
        return 28
    return 23


def _default_audio_bitrate(opts: ReencodeOptions) -> str | None:
    if opts.audio_codec in {"none", "copy", "flac"}:
        return None
    if opts.audio_codec == "opus":
        return "96k"
    if opts.audio_codec == "mp3":
        return "160k"
    return "160k"


def _map_nvenc_preset(speed: str | None) -> str:
    s = (speed or "veryfast").lower()
    # NVENC presets are commonly p1..p7 (fastest..best) in modern ffmpeg.
    return {
        "ultrafast": "p1",
        "superfast": "p2",
        "veryfast": "p3",
        "faster": "p3",
        "fast": "p4",
        "medium": "p4",
        "slow": "p6",
        "slower": "p6",
        "veryslow": "p7",
    }.get(s, "p3")


def _pick_hw_video_encoder(v: str) -> tuple[str | None, str | None]:
    """Return (encoder, flavor) where flavor is one of: nvenc, qsv, amf."""
    vv = (v or "").lower()
    if vv in {"h264"}:
        return ("h264_nvenc", "nvenc")
    if vv in {"hevc", "h265"}:
        return ("hevc_nvenc", "nvenc")
    if vv == "av1":
        return ("av1_nvenc", "nvenc")
    if vv == "vp9":
        # NVIDIA/AMD don't commonly expose VP9 encoders via ffmpeg on Windows.
        return ("vp9_qsv", "qsv")
    return (None, None)


def _build_ffmpeg_cmd(*, input_video: Path, output_path: Path, opts: ReencodeOptions, prefer_hw: bool) -> list[str]:
    container = (opts.container or "mp4").lower()
    v = (opts.video_codec or "h264").lower()
    a = (opts.audio_codec or "aac").lower()

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]

    # Hardware-accelerated decode when possible.
    if prefer_hw and v != "copy":
        cmd += ["-hwaccel", "auto"]

    cmd += ["-i", str(input_video), "-sn", "-dn"]

    # Video
    if v == "copy":
        cmd += ["-c:v", "copy"]
    else:
        # Prefer hardware encoders when requested; fall back handled by caller.
        hw_enc, hw_flavor = (None, None)
        if prefer_hw:
            hw_enc, hw_flavor = _pick_hw_video_encoder(v)

        if hw_enc:
            cmd += ["-c:v", hw_enc]

            # Best-effort quality controls; if unsupported, caller will fall back.
            crf = _quality_to_crf(opts)
            if hw_flavor == "nvenc":
                cmd += ["-preset", _map_nvenc_preset(opts.speed)]
                if crf is not None:
                    if crf <= 0:
                        cmd += ["-qp", "0"]
                    else:
                        cmd += ["-cq", str(int(crf))]
            elif hw_flavor == "qsv":
                # QSV supports various rate control modes; global_quality is widely accepted.
                if crf is not None and crf > 0:
                    cmd += ["-global_quality", str(int(crf))]

            # Keep compatibility for typical players.
            cmd += ["-pix_fmt", "yuv420p"]

        else:
            vmap = {
                "h264": "libx264",
                "hevc": "libx265",
                "h265": "libx265",
                "vp9": "libvpx-vp9",
                "av1": "libaom-av1",
                "theora": "libtheora",
            }
            vcodec = vmap.get(v, "libx264")
            cmd += ["-c:v", vcodec]

            crf = _quality_to_crf(opts)
            if vcodec in {"libx264", "libx265"}:
                cmd += ["-preset", (opts.speed or "veryfast")]
                if crf is not None:
                    cmd += ["-crf", str(int(crf))]
                cmd += ["-pix_fmt", "yuv420p"]
            elif vcodec == "libvpx-vp9":
                # VP9 typical: CQ mode with b:v 0.
                cmd += ["-b:v", "0"]
                if crf is not None:
                    # VP9 CRF scale differs; map roughly.
                    vp9_crf = 30 if crf == 23 else (24 if crf <= 18 else (33 if crf >= 28 else 30))
                    cmd += ["-crf", str(int(vp9_crf))]
                cmd += ["-row-mt", "1"]
            elif vcodec == "libaom-av1":
                cmd += ["-b:v", "0"]
                if crf is not None:
                    av1_crf = 32 if crf == 23 else (24 if crf <= 18 else (40 if crf >= 28 else 32))
                    cmd += ["-crf", str(int(av1_crf))]
            elif vcodec == "libtheora":
                # Theora uses quality scale, not CRF.
                qv = 7
                if opts.quality == "small":
                    qv = 5
                elif opts.quality == "high":
                    qv = 9
                elif opts.quality == "lossless":
                    qv = 10
                cmd += ["-q:v", str(int(qv))]

    # Audio
    if a == "none":
        cmd += ["-an"]
    elif a == "copy":
        cmd += ["-c:a", "copy"]
    else:
        amap = {
            "aac": "aac",
            "mp3": "libmp3lame",
            "opus": "libopus",
            "flac": "flac",
        }
        acodec = amap.get(a, "aac")
        cmd += ["-c:a", acodec]
        br = (opts.audio_bitrate or "").strip() or _default_audio_bitrate(opts)
        if br and acodec not in {"flac"}:
            cmd += ["-b:a", br]

    # Container-specific
    if container == "mp4":
        cmd += ["-movflags", "+faststart"]

    # Progress
    cmd += ["-progress", "pipe:1", "-nostats"]

    # Extra args (last wins)
    cmd += _parse_extra_args(opts.extra_ffmpeg_args)

    cmd += [str(output_path)]
    return cmd


def start_reencode_job(
    *,
    items: list[ReencodeItem],
    opts: ReencodeOptions,
    catalog_root: str,
    on_item_success: Callable[[ReencodeItem], None] | None = None,
) -> ReencodeJob:
    job = ReencodeJob(job_id=uuid.uuid4().hex, status="queued", created_at=time.time(), items=items)

    with _LOCK:
        _JOBS[job.job_id] = job
        _CANCEL[job.job_id] = False

    if len(items) == 1:
        logger.info(
            "Reencode started job=%s recording=%s delete_original=%s",
            job.job_id,
            items[0].file_name or items[0].input_video.name,
            opts.delete_original,
        )
    else:
        logger.info("Reencode started job=%s recordings=%s delete_original=%s", job.job_id, len(items), opts.delete_original)

    def _runner() -> None:
        with _LOCK:
            j = _JOBS.get(job.job_id)
            if not j:
                return
            j.status = "running"
            j.started_at = time.time()

        had_errors = False
        error_count = 0

        for it in items:
            with _LOCK:
                if _CANCEL.get(job.job_id):
                    it.status = "canceled"
                    it.error = "Canceled"
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "canceled"
                        j.finished_at = time.time()
                    logger.warning("Reencode cancelled job=%s", job.job_id)
                    return

                it.status = "running"
                it.percent = 0.0
                j = _JOBS.get(job.job_id)
                if j:
                    j.current_recording_id = it.recording_id

            target_path = it.output_path
            tmp_path = it.temp_output_path or it.output_path
            tmp_path.parent.mkdir(parents=True, exist_ok=True)

            def _run_one(prefer_hw: bool) -> tuple[bool, int, list[str]]:
                cmd = _build_ffmpeg_cmd(input_video=it.input_video, output_path=tmp_path, opts=opts, prefer_hw=prefer_hw)

                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
                except Exception as e:
                    return (False, 1, [str(e)])

                with _LOCK:
                    _PROCS[job.job_id] = proc

                # NOTE: we merge stderr into stdout to avoid deadlocks when ffmpeg is noisy.
                stderr_lines: list[str] = []
                try:
                    if proc.stdout is not None:
                        for line in proc.stdout:
                            if _CANCEL.get(job.job_id):
                                try:
                                    proc.terminate()
                                except Exception:
                                    pass
                                break

                            line = (line or "").strip()
                            if not line:
                                continue

                            if line.startswith("out_time_ms="):
                                try:
                                    out_time_ms = int(line.split("=", 1)[1])
                                except Exception:
                                    out_time_ms = 0

                                pct = 0.0
                                if it.duration_seconds and it.duration_seconds > 0:
                                    pct = (out_time_ms / (it.duration_seconds * 1_000_000.0)) * 100.0
                                pct = max(0.0, min(99.9, float(pct)))

                                with _LOCK:
                                    if it.status == "running":
                                        it.percent = pct

                            # Collect non-progress lines as error context.
                            if not (line.startswith("progress=") or line.startswith("out_time_ms=") or line.startswith("speed=") or line.startswith("frame=") or line.startswith("fps=") or line.startswith("bitrate=") or line.startswith("total_size=") or line.startswith("out_time=") or line.startswith("dup_frames=") or line.startswith("drop_frames=")):
                                stderr_lines.append(line)

                            if line == "progress=end":
                                # ffmpeg may still print some trailing lines; we'll just keep draining until EOF.
                                continue

                    rc = proc.wait()

                    ok = rc == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0
                    return (ok, int(rc), stderr_lines)
                finally:
                    with _LOCK:
                        _PROCS.pop(job.job_id, None)

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

            # First try: hardware-accelerated decode/encode when possible.
            ok, rc, stderr_lines = _run_one(prefer_hw=True)
            if not ok:
                # Retry with software encoding for common hardware-related failures.
                hw_related_markers = (
                    "unknown encoder",
                    "error selecting an encoder",
                    "no device",
                    "no capable devices",
                    "device not found",
                    "cannot load",
                    "initializ",
                    "openencodesessionex",
                    "nvenc",
                    "qsv",
                    "amf",
                    "error parsing options",
                    "unrecognized option",
                )
                joined = "\n".join(stderr_lines).lower()
                if any(m in joined for m in hw_related_markers):
                    try:
                        if tmp_path.exists():
                            tmp_path.unlink()
                    except Exception:
                        pass
                    ok, rc, stderr_lines = _run_one(prefer_hw=False)

            with _LOCK:
                if _CANCEL.get(job.job_id):
                    it.status = "canceled"
                    it.error = "Canceled"
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.status = "canceled"
                        j.finished_at = time.time()
                    logger.warning("Reencode cancelled job=%s", job.job_id)
                    return

                if ok:
                    # Move temp to final destination if needed.
                    if tmp_path != target_path:
                        try:
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            if target_path.exists() and target_path != it.input_video:
                                # Avoid silent overwrite of unrelated files.
                                raise FileExistsError(str(target_path))
                            tmp_path.replace(target_path)
                        except Exception as e:
                            msg = f"Failed to move output to final path: {e}"
                            it.status = "error"
                            it.error = msg
                            had_errors = True
                            error_count += 1
                            j = _JOBS.get(job.job_id)
                            if j:
                                j.error = msg
                            continue

                    if opts.delete_original:
                        # If we are not replacing in-place, prefer updating app state/DB first,
                        # then removing the original file.
                        if target_path != it.input_video and on_item_success is not None:
                            try:
                                on_item_success(it)
                            except Exception as e:
                                msg = f"Transcode succeeded but failed to update database: {e}"
                                it.status = "error"
                                it.error = msg
                                had_errors = True
                                error_count += 1
                                j = _JOBS.get(job.job_id)
                                if j:
                                    j.error = msg
                                continue

                            try:
                                if it.input_video.exists():
                                    it.input_video.unlink()
                            except Exception as e:
                                msg = f"Transcode succeeded but failed to delete original: {e}"
                                it.status = "error"
                                it.error = msg
                                had_errors = True
                                error_count += 1
                                j = _JOBS.get(job.job_id)
                                if j:
                                    j.error = msg
                                continue

                        # If replacing in-place, the original path is overwritten by the move above.
                        it.file_name = target_path.name

                    # If we didn't run an item callback yet, do it now (e.g. delete_original=False
                    # or in-place replacement).
                    if on_item_success is not None and not (opts.delete_original and target_path != it.input_video):
                        try:
                            on_item_success(it)
                        except Exception as e:
                            msg = f"Transcode succeeded but failed to update database: {e}"
                            it.status = "error"
                            it.error = msg
                            had_errors = True
                            error_count += 1
                            j = _JOBS.get(job.job_id)
                            if j:
                                j.error = msg
                            continue

                    it.status = "done"
                    it.percent = 100.0
                else:
                    msg = f"ffmpeg transcode failed (exit {rc})"
                    if stderr_lines:
                        msg = msg + ": " + stderr_lines[-1]
                    it.status = "error"
                    it.error = msg
                    had_errors = True
                    error_count += 1
                    j = _JOBS.get(job.job_id)
                    if j:
                        j.error = msg
                    continue

        with _LOCK:
            j = _JOBS.get(job.job_id)
            if j and j.status == "running":
                if had_errors:
                    j.status = "error"
                    j.error = j.error or f"{error_count} item(s) failed"
                else:
                    j.status = "done"
                j.finished_at = time.time()
                j.current_recording_id = None

        if had_errors:
            if len(items) == 1:
                logger.warning(
                    "Reencode finished with errors job=%s recording=%s failed_items=%s",
                    job.job_id,
                    items[0].file_name or items[0].input_video.name,
                    error_count,
                )
            else:
                logger.warning("Reencode finished with errors job=%s recordings=%s failed_items=%s", job.job_id, len(items), error_count)
        else:
            if len(items) == 1:
                logger.info("Reencode finished job=%s recording=%s", job.job_id, items[0].file_name or items[0].input_video.name)
            else:
                logger.info("Reencode finished job=%s recordings=%s", job.job_id, len(items))

    threading.Thread(target=_runner, daemon=True).start()
    return job
