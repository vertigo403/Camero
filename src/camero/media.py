from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".ts", ".flv", ".webm", ".avi", ".m4v"}
CONTACT_SHEET_EXTENSIONS = (".png", ".jpg", ".jpeg")


def find_contact_sheet_path(video_path: Path) -> Path | None:
    try:
        if not video_path.exists() or not video_path.is_file():
            return None
    except Exception:
        return None

    for ext in CONTACT_SHEET_EXTENSIONS:
        candidate = video_path.with_suffix(ext)
        try:
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except Exception:
            continue

    return None


def quick_file_hash(path: Path, *, sample_bytes: int = 256 * 1024) -> str | None:
    """Compute a fast fingerprint for a file.

    - Intended for cache keys (thumbs/previews), not security.
    - Uses file size + sampled content (start/middle/end) to reduce collisions.
    """

    try:
        st = path.stat()
        size = int(st.st_size)
    except Exception:
        return None

    h = hashlib.blake2b(digest_size=16)
    try:
        h.update(size.to_bytes(8, "little", signed=False))
    except Exception:
        h.update(str(size).encode("utf-8", errors="ignore"))

    if size <= 0:
        return h.hexdigest()

    offsets = [0, max(0, size // 2), max(0, size - sample_bytes)]
    seen: set[int] = set()
    try:
        with path.open("rb") as f:
            for off in offsets:
                if off in seen:
                    continue
                seen.add(off)
                try:
                    f.seek(off)
                    chunk = f.read(sample_bytes)
                    if chunk:
                        h.update(chunk)
                except Exception:
                    continue
    except Exception:
        return None

    return h.hexdigest()


def check_ffmpeg_installed() -> tuple[bool, str]:
    """Check whether ffmpeg is available.

    Looks in PATH first, then next to the executable (frozen builds) or next to
    this module (dev runs). Returns (True, ffmpeg_path) or raises RuntimeError.
    """

    ffmpeg_exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    ffprobe_exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"

    ffmpeg_path = shutil.which(ffmpeg_exe)
    ffprobe_path = shutil.which(ffprobe_exe)

    if getattr(sys, "frozen", False):
        current_dir = os.path.dirname(sys.executable)
    else:
        current_dir = os.path.dirname(os.path.abspath(__file__))

    if not ffmpeg_path:
        local_path = os.path.join(current_dir, ffmpeg_exe)
        if os.path.isfile(local_path):
            ffmpeg_path = local_path

    if not ffprobe_path:
        local_path = os.path.join(current_dir, ffprobe_exe)
        if os.path.isfile(local_path):
            ffprobe_path = local_path

    if not ffmpeg_path:
        raise RuntimeError(
            f"FFmpeg not found. Please install FFmpeg and add it to your system's PATH, "
            f"or place '{ffmpeg_exe}' in the program's folder."
        )
    if not ffprobe_path:
        raise RuntimeError(
            f"FFprobe not found. Please install FFmpeg and add it to your system's PATH, "
            f"or place '{ffprobe_exe}' in the program's folder."
        )

    return True, str(ffmpeg_path)


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    # NOTE (Windows): using text=True relies on the system default encoding
    # (often cp1252) and can crash with UnicodeDecodeError when tools emit
    # bytes that are not valid in that encoding. We capture raw bytes and
    # decode explicitly where needed.
    return subprocess.run(cmd, capture_output=True, text=False, check=False)


def probe_media(path: Path) -> MediaInfo:
    size_bytes = None
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        pass

    ffprobe = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            str(path),
        ]
    )

    if ffprobe.returncode != 0:
        return MediaInfo(size_bytes=size_bytes)

    try:
        stdout = ffprobe.stdout.decode("utf-8", errors="replace") if ffprobe.stdout else ""
        data: dict[str, Any] = json.loads(stdout)
        duration = None
        width = None
        height = None
        video_codec = None
        audio_codec = None

        fmt = data.get("format") or {}
        if isinstance(fmt, dict) and fmt.get("duration"):
            try:
                duration = float(fmt["duration"])
            except Exception:
                duration = None

        streams = data.get("streams") or []
        if isinstance(streams, list):
            for s in streams:
                if not isinstance(s, dict):
                    continue
                st = (s.get("codec_type") or "").strip().lower()
                cn = (s.get("codec_name") or "").strip()

                if st == "video" and not video_codec and cn:
                    video_codec = cn
                if st == "audio" and not audio_codec and cn:
                    audio_codec = cn

                w = s.get("width")
                h = s.get("height")
                if width is None and height is None and w and h:
                    try:
                        width = int(w)
                        height = int(h)
                    except Exception:
                        pass

        return MediaInfo(
            duration_seconds=duration,
            width=width,
            height=height,
            size_bytes=size_bytes,
            video_codec=video_codec,
            audio_codec=audio_codec,
        )
    except Exception:
        return MediaInfo(size_bytes=size_bytes)


def ensure_thumbnail(
    input_video: Path,
    output_jpg: Path,
    *,
    title: str = "",
    force: bool = False,
    allow_placeholder: bool = True,
) -> bool:
    output_jpg.parent.mkdir(parents=True, exist_ok=True)
    if not force:
        try:
            if output_jpg.exists() and output_jpg.stat().st_size > 0:
                return True
        except Exception:
            # Fall through to regeneration.
            pass

    # Write to a temporary file and then replace atomically.
    # This avoids leaving a corrupt/partial thumbnail on crashes or failures.
    # Keep the same extension so ffmpeg/PIL reliably produce a JPEG.
    # Using a .tmp extension can make ffmpeg pick a non-JPEG muxer (or fail silently).
    tmp_jpg = output_jpg.with_name(f"{output_jpg.stem}.tmp{output_jpg.suffix}")
    try:
        if tmp_jpg.exists():
            tmp_jpg.unlink(missing_ok=True)
    except Exception:
        pass

    def _try_ffmpeg(seek: str) -> bool:
        ffmpeg = _run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                seek,
                "-i",
                str(input_video),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-1",
                str(tmp_jpg),
            ]
        )
        try:
            if ffmpeg.returncode == 0 and tmp_jpg.exists() and tmp_jpg.stat().st_size > 0:
                tmp_jpg.replace(output_jpg)
                return True
        except Exception:
            return False
        return False

    # Try ffmpeg first (seek 1s, then 0s for very short clips).
    if _try_ffmpeg("00:00:01") or _try_ffmpeg("00:00:00"):
        return True

    if not allow_placeholder:
        return False

    # Fallback: generate a simple placeholder.
    try:
        img = Image.new("RGB", (480, 270), color=(27, 31, 38))
        draw = ImageDraw.Draw(img)
        text = title.strip() or "No thumbnail"

        # Use default font (portable) to avoid external dependencies.
        font = ImageFont.load_default()
        padding = 14
        draw.rectangle([(0, 0), (479, 269)], outline=(60, 70, 85), width=2)
        draw.text((padding, padding), text[:80], fill=(230, 235, 240), font=font)
        img.save(tmp_jpg, format="JPEG", quality=85)
        if tmp_jpg.exists() and tmp_jpg.stat().st_size > 0:
            tmp_jpg.replace(output_jpg)
            return True
        return False
    except Exception:
        return False
    finally:
        try:
            if tmp_jpg.exists():
                tmp_jpg.unlink(missing_ok=True)
        except Exception:
            pass


def extract_frame_at_time(
    input_video: Path,
    output_jpg: Path,
    *,
    time_seconds: float,
    width: int = 480,
) -> tuple[bool, str | None]:
    """Extract a single JPEG frame from a video at (approximately) the given time.

    We prefer accuracy over speed by seeking *after* opening the input.
    """

    try:
        t = float(time_seconds)
    except Exception:
        t = 0.0
    if t < 0:
        t = 0.0

    output_jpg.parent.mkdir(parents=True, exist_ok=True)

    # Seek after -i for better accuracy (slower, but correct for user-picked frames).
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_video),
        "-ss",
        f"{t:.3f}",
        "-frames:v",
        "1",
        "-vf",
        f"scale={int(width)}:-1",
        str(output_jpg),
    ]

    proc = _run(cmd)
    if proc.returncode == 0 and output_jpg.exists() and output_jpg.stat().st_size > 0:
        return True, None

    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    stderr = (stderr or "").strip()
    if stderr:
        # Keep it short for UI.
        last = stderr.splitlines()[-1].strip()
        return False, last[:400]

    return False, f"ffmpeg failed (exit {proc.returncode})"
