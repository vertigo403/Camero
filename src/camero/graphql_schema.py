from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
import re
import shutil
from typing import Annotated
from urllib.parse import quote
import uuid

import strawberry
from sqlalchemy import case, delete, exists, func, select
from sqlalchemy.orm import Session

from .models import Recording, RecordingOverride, RecordingTag, Site, Streamer, Tag
from .site_normalization import canonical_site_name, upsert_site_canonical
from .scanner import ScanResult, scan_catalog
from .settings import AppSettings, CatalogFolder, load_settings, save_global_network_settings, save_settings, set_active_catalog_root
from .db import get_session_for_catalog, session_scope
from .scan_jobs import (
    cancel_scan,
    cancel_scan_live_previews,
    get_scan_job,
    start_generate_missing_live_previews_job,
    start_scan_job,
    get_active_scan_job,
)
from .autotag_jobs import (
    AutoTagJob,
    AutoTagProgress,
    sync_cached_tagger,
    release_cached_tagger,
    get_auto_tag_job,
    get_active_auto_tag_job,
    cancel_auto_tag,
    start_auto_tag_untagged_job,
    start_auto_tag_recordings_job,
)
from .transcode_jobs import get_active_transcode_job, get_transcode_job, start_transcode_job
from .reencode_jobs import (
    ReencodeItem,
    ReencodeOptions,
    cancel_reencode_job,
    get_active_reencode_job,
    get_reencode_job,
    start_reencode_job,
)
from .remux_jobs import get_active_remux_job, get_remux_job, start_remux_job
from .preview_jobs import (
    clear_recording_live_preview_failure,
    clear_live_preview_failure_for_recording,
    get_live_preview_job,
    live_preview_exists,
    live_preview_output_path_for_hash,
    mark_live_preview_failure_for_recording,
    start_live_preview_job,
)
from .settings import (
    DEFAULT_AUTH_PASSWORD,
    DEFAULT_AUTH_REQUIRED,
    DEFAULT_AUTH_USERNAME,
    DEFAULT_BACKEND_HOST,
    DEFAULT_BACKEND_PORT,
    DEFAULT_FILENAME_REGEX,
    DEFAULT_PERIODIC_SCAN_ENABLED,
    DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
    DEFAULT_AUTO_TAG_AFTER_SCAN,
    DEFAULT_AUTO_TAG_CONFIDENCE,
    DEFAULT_AUTO_TAG_SCENE_THRESHOLD,
    DEFAULT_AUTO_TAG_FRAME_INTERVAL,
    _coerce_auto_tag_confidence,
    _coerce_auto_tag_scene_threshold,
    _coerce_auto_tag_frame_interval,
)
from .streamer_fetch import fetch_streamer_info
from .streamer_bulk_fetch_jobs import (
    get_active_bulk_fetch_streamer_info_job,
    get_bulk_fetch_streamer_info_job,
    start_bulk_fetch_missing_streamer_info_job,
)
from .assets import get_assets_dir
from .catalog_paths import (
    encode_recording_rel_path,
    parse_recording_rel_path,
    resolve_recording_path,
)
from .media import check_ffmpeg_installed, ensure_thumbnail, extract_frame_at_time, find_contact_sheet_path, probe_media, quick_file_hash
from .logging_utils import get_logger


ContextDict = dict[str, object]
logger = get_logger("graphql")


def _recording_log_name(rec: Recording | None) -> str:
    if rec is None:
        return "unknown"

    title = str(getattr(rec, "title", "") or "").strip()
    if title:
        return title

    file_name = str(getattr(rec, "file_name", "") or "").strip()
    if file_name:
        return file_name

    rel_path = str(getattr(rec, "rel_path", "") or "").strip()
    if rel_path:
        return Path(rel_path).name or rel_path

    return f"recording #{getattr(rec, 'id', '?')}"


def _recordings_log_target(session: Session, recording_ids: list[int] | None) -> str:
    ids = [int(rid) for rid in (recording_ids or []) if rid is not None]
    if len(ids) == 1:
        return _recording_log_name(session.get(Recording, ids[0]))
    return f"{len(ids)} recordings"


def _parse_iso_datetime(s: str) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    # Accept common ISO strings, including a trailing 'Z'.
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _upsert_site(session: Session, name: str) -> Site:
    return upsert_site_canonical(session, name)


def _upsert_streamer(session: Session, name: str, site: Site | None) -> Streamer:
    site_id = site.id if site else None
    existing = session.scalar(select(Streamer).where(Streamer.name == name, Streamer.site_id == site_id))
    if existing:
        return existing
    streamer = Streamer(name=name, site_id=site_id)
    session.add(streamer)
    session.flush()
    return streamer


def _ensure_streamer_site_from_recording(
    session: Session,
    rec: Recording,
    ov: RecordingOverride | None,
    site: Site | None,
) -> None:
    """When a recording's site is known but the streamer has unknown site,
    re-associate the recording to a streamer row bound to that site.

    This keeps the Streamers view consistent after assigning a site to recordings.
    We do not override explicit streamer overrides.
    """

    if site is None:
        return
    if rec.streamer_id is None:
        return
    if ov is not None and getattr(ov, "streamer_set", False):
        return

    streamer = rec.streamer or session.get(Streamer, int(rec.streamer_id))
    if not streamer:
        return

    # Only auto-fix when streamer has no site.
    if streamer.site_id is not None:
        return

    target = _upsert_streamer(session, streamer.name, site)
    if target.id != streamer.id:
        rec.streamer_id = target.id


@strawberry.enum
class SortDirection(Enum):
    ASC = "ASC"
    DESC = "DESC"


@strawberry.enum
class RecordingOrderBy(Enum):
    RECORDED_AT = "RECORDED_AT"
    TITLE = "TITLE"
    STREAMER_NAME = "STREAMER_NAME"
    DURATION = "DURATION"
    SIZE = "SIZE"
    RESOLUTION = "RESOLUTION"


@strawberry.enum
class StreamerOrderBy(Enum):
    NAME = "NAME"
    RECORDINGS_COUNT = "RECORDINGS_COUNT"
    TOTAL_SIZE = "TOTAL_SIZE"
    RECENT_ADDED = "RECENT_ADDED"


@strawberry.type
class CatalogFolderType:
    key: str
    path: str
    filename_regex: str
    filename_template: str | None
    match_path: bool


@strawberry.type
class AppConfig:
    catalog_root: str | None
    filename_regex: str
    catalog_folders: list[CatalogFolderType]
    generate_live_previews: bool
    live_preview_segments: int
    periodic_scan_enabled: bool
    periodic_scan_interval_minutes: int
    auto_tag_after_scan: bool
    auto_tag_confidence: float
    auto_tag_scene_threshold: float
    auto_tag_frame_interval: str
    backend_host: str
    backend_port: int
    auth_required: bool
    auth_username: str
    auth_password: str


@strawberry.type
class FfmpegStatus:
    ok: bool
    path: str | None
    error: str | None


@strawberry.input
class CatalogFolderInput:
    key: str | None = None
    path: str
    filename_regex: str
    filename_template: str | None = None
    match_path: bool = False


def _settings_to_app_config(s: AppSettings) -> AppConfig:
    folders = [
        CatalogFolderType(
            key=f.key,
            path=f.path,
            filename_regex=f.filename_regex,
            filename_template=getattr(f, "filename_template", None),
            match_path=bool(getattr(f, "match_path", False)),
        )
        for f in (s.catalog_folders or ())
    ]
    # Back-compat: expose a single filenameRegex for older clients.
    primary_regex = folders[0].filename_regex if folders else s.filename_regex
    return AppConfig(
        catalog_root=s.catalog_root,
        filename_regex=primary_regex,
        catalog_folders=folders,
        generate_live_previews=bool(getattr(s, "generate_live_previews", False)),
        live_preview_segments=int(getattr(s, "live_preview_segments", 10) or 10),
        periodic_scan_enabled=bool(getattr(s, "periodic_scan_enabled", DEFAULT_PERIODIC_SCAN_ENABLED)),
        periodic_scan_interval_minutes=int(
            getattr(s, "periodic_scan_interval_minutes", DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES)
            or DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES
        ),
        auto_tag_after_scan=bool(getattr(s, "auto_tag_after_scan", DEFAULT_AUTO_TAG_AFTER_SCAN)),
        auto_tag_confidence=float(getattr(s, "auto_tag_confidence", DEFAULT_AUTO_TAG_CONFIDENCE) or DEFAULT_AUTO_TAG_CONFIDENCE),
        auto_tag_scene_threshold=float(getattr(s, "auto_tag_scene_threshold", DEFAULT_AUTO_TAG_SCENE_THRESHOLD) or DEFAULT_AUTO_TAG_SCENE_THRESHOLD),
        auto_tag_frame_interval=str(getattr(s, "auto_tag_frame_interval", DEFAULT_AUTO_TAG_FRAME_INTERVAL) or DEFAULT_AUTO_TAG_FRAME_INTERVAL),
        backend_host=str(getattr(s, "backend_host", DEFAULT_BACKEND_HOST) or DEFAULT_BACKEND_HOST),
        backend_port=int(getattr(s, "backend_port", DEFAULT_BACKEND_PORT) or DEFAULT_BACKEND_PORT),
        auth_required=bool(getattr(s, "auth_required", DEFAULT_AUTH_REQUIRED)),
        auth_username=str(getattr(s, "auth_username", DEFAULT_AUTH_USERNAME) or DEFAULT_AUTH_USERNAME),
        auth_password=str(getattr(s, "auth_password", DEFAULT_AUTH_PASSWORD) or DEFAULT_AUTH_PASSWORD),
    )


def _clone_settings(s: AppSettings, **changes: object) -> AppSettings:
    data: dict[str, object] = {
        "catalog_root": s.catalog_root,
        "filename_regex": s.filename_regex,
        "catalog_folders": s.catalog_folders,
        "generate_live_previews": bool(getattr(s, "generate_live_previews", False)),
        "live_preview_segments": int(getattr(s, "live_preview_segments", 10) or 10),
        "periodic_scan_enabled": bool(getattr(s, "periodic_scan_enabled", DEFAULT_PERIODIC_SCAN_ENABLED)),
        "periodic_scan_interval_minutes": int(
            getattr(s, "periodic_scan_interval_minutes", DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES)
            or DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES
        ),
        "auto_tag_after_scan": bool(getattr(s, "auto_tag_after_scan", DEFAULT_AUTO_TAG_AFTER_SCAN)),
        "auto_tag_confidence": float(getattr(s, "auto_tag_confidence", DEFAULT_AUTO_TAG_CONFIDENCE) or DEFAULT_AUTO_TAG_CONFIDENCE),
        "auto_tag_scene_threshold": float(getattr(s, "auto_tag_scene_threshold", DEFAULT_AUTO_TAG_SCENE_THRESHOLD) or DEFAULT_AUTO_TAG_SCENE_THRESHOLD),
        "auto_tag_frame_interval": str(getattr(s, "auto_tag_frame_interval", DEFAULT_AUTO_TAG_FRAME_INTERVAL) or DEFAULT_AUTO_TAG_FRAME_INTERVAL),
        "backend_host": str(getattr(s, "backend_host", DEFAULT_BACKEND_HOST) or DEFAULT_BACKEND_HOST),
        "backend_port": int(getattr(s, "backend_port", DEFAULT_BACKEND_PORT) or DEFAULT_BACKEND_PORT),
        "auth_required": bool(getattr(s, "auth_required", DEFAULT_AUTH_REQUIRED)),
        "auth_username": str(getattr(s, "auth_username", DEFAULT_AUTH_USERNAME) or DEFAULT_AUTH_USERNAME),
        "auth_password": str(getattr(s, "auth_password", DEFAULT_AUTH_PASSWORD) or DEFAULT_AUTH_PASSWORD),
    }
    data.update(changes)
    return AppSettings(**data)


@strawberry.type
class TagType:
    id: int
    name: str


@strawberry.type
class SiteType:
    id: int
    name: str


@strawberry.type
class SiteOverviewType:
    id: int
    name: str
    logo_url: str
    recordings_count: int
    streamers_count: int


@strawberry.type
class StreamerType:
    id: int
    name: str
    site: SiteType | None
    avatar_url: str | None
    url: str | None = None
    gender: str | None = None
    age: str | None = None
    ethnicity: str | None = None
    country: str | None = None
    hair_color: str | None = None
    about: str | None = None
    recordings_count: int | None = None
    total_size_bytes: float | None = None


@strawberry.type
class RecordingType:
    id: int
    rel_path: str
    file_name: str
    title: str
    recorded_at: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    # GraphQL Int is limited to 32-bit signed; file sizes routinely exceed this.
    # Use Float so clients receive a JS number.
    size_bytes: float | None
    video_codec: str | None
    audio_codec: str | None
    streamer: StreamerType | None
    site: SiteType | None
    tags: list[TagType]
    thumbnail_url: str | None
    live_preview_url: str | None
    contact_sheet_url: str | None


@strawberry.type
class RecordingConnection:
    total: int
    total_size_bytes: float | None = None
    items: list[RecordingType]


@strawberry.type
class RecordingsFilterMeta:
    max_size_bytes: float
    max_duration_seconds: float
    max_short_side: int
    video_codecs: list[str]


@strawberry.type
class StreamerConnection:
    total: int
    items: list[StreamerType]


@strawberry.type
class SimpleResult:
    ok: bool
    message: str


@strawberry.type
class RegexTestResult:
    ok: bool
    matched: bool
    error: str | None = None
    groups_json: str | None = None


@strawberry.type
class BulkFetchStreamerInfoProgressType:
    total: int
    done: int
    ok: int
    failed: int
    skipped: int
    current: str | None
    percent: float


@strawberry.type
class BulkFetchStreamerInfoJobType:
    job_id: str
    status: str
    error: str | None
    progress: BulkFetchStreamerInfoProgressType


def _bulk_fetch_progress_to_type(p) -> BulkFetchStreamerInfoProgressType:
    return BulkFetchStreamerInfoProgressType(
        total=int(getattr(p, "total", 0) or 0),
        done=int(getattr(p, "done", 0) or 0),
        ok=int(getattr(p, "ok", 0) or 0),
        failed=int(getattr(p, "failed", 0) or 0),
        skipped=int(getattr(p, "skipped", 0) or 0),
        current=getattr(p, "current", None),
        percent=float(getattr(p, "percent", 0.0) or 0.0),
    )


def _bulk_fetch_job_to_type(j) -> BulkFetchStreamerInfoJobType:
    return BulkFetchStreamerInfoJobType(
        job_id=str(getattr(j, "job_id", "")),
        status=str(getattr(j, "status", "")),
        error=getattr(j, "error", None),
        progress=_bulk_fetch_progress_to_type(getattr(j, "progress", None)),
    )


@strawberry.type
class ScanResultType:
    scanned_files: int
    added: int
    updated: int
    skipped: int
    errors: int


@strawberry.type
class ScanProgressType:
    phase: str
    total_files: int | None
    scanned_files: int
    added: int
    updated: int
    skipped: int
    errors: int
    current_file: str | None
    current_recording: str | None
    total_previews: int | None
    generated_previews: int
    failed_previews: int
    current_preview: str | None
    percent: float


@strawberry.type
class ScanJobType:
    job_id: str
    status: str
    progress: ScanProgressType
    result: ScanResultType | None
    error: str | None


@strawberry.type
class AutoTagProgressType:
    total: int
    completed: int
    failed: int
    current_file: str | None
    percent: float
    frames_used: int | None
    candidate_count: int | None


@strawberry.type
class AutoTagJobType:
    job_id: str
    status: str
    progress: AutoTagProgressType
    error: str | None


@strawberry.type
class TranscodeJobType:
    job_id: str
    recording_id: int
    status: str
    percent: float
    output_url: str | None
    error: str | None


@strawberry.type
class RemuxJobType:
    job_id: str
    recording_id: int
    status: str
    percent: float
    output_url: str | None
    error: str | None


@strawberry.type
class LivePreviewJobType:
    job_id: str
    recording_id: int
    status: str
    percent: float
    output_url: str | None
    error: str | None


@strawberry.type
class ReencodeItemType:
    recording_id: int
    file_name: str | None
    output_file_name: str | None
    status: str
    percent: float
    output_url: str | None
    error: str | None


@strawberry.type
class ReencodeJobType:
    job_id: str
    status: str
    current_recording_id: int | None
    percent: float
    items: list[ReencodeItemType]
    error: str | None


@strawberry.type
class StartReencodeResult:
    ok: bool
    message: str
    job: ReencodeJobType | None


@strawberry.input
class ReencodeOptionsInput:
    container: str = "mp4"  # mp4 | mkv | avi | webm
    video_codec: str = "h264"  # h264 | hevc | vp9 | av1 | theora | copy
    audio_codec: str = "aac"  # aac | mp3 | opus | flac | copy | none
    quality: str = "balanced"  # small | balanced | high | lossless
    speed: str = "veryfast"  # ffmpeg preset-like speed
    audio_bitrate: str | None = None
    extra_ffmpeg_args: str | None = None
    delete_original: bool = False


def _remux_job_to_type(j) -> RemuxJobType:
    url = f"/remuxed/{j.recording_id}.mp4" if j.status == "done" else None
    return RemuxJobType(
        job_id=j.job_id,
        recording_id=j.recording_id,
        status=j.status,
        percent=float(j.percent or 0.0),
        output_url=url,
        error=j.error,
    )


def _transcode_job_to_type(j) -> TranscodeJobType:
    url = f"/transcoded/{j.recording_id}.mp4" if j.status == "done" else None
    return TranscodeJobType(
        job_id=j.job_id,
        recording_id=j.recording_id,
        status=j.status,
        percent=float(j.percent or 0.0),
        output_url=url,
        error=j.error,
    )


def _live_preview_job_to_type(j) -> LivePreviewJobType:
    url = f"/preview/{j.recording_id}.mp4" if j.status == "done" else None
    return LivePreviewJobType(
        job_id=j.job_id,
        recording_id=j.recording_id,
        status=j.status,
        percent=float(j.percent or 0.0),
        output_url=url,
        error=j.error,
    )


def _reencode_job_to_type(j: object) -> ReencodeJobType:
    job_id = getattr(j, "job_id")
    status = getattr(j, "status")
    current_recording_id = getattr(j, "current_recording_id", None)
    items = getattr(j, "items", None) or []
    err = getattr(j, "error", None)

    done_count = 0
    pct_sum = 0.0
    out_items: list[ReencodeItemType] = []
    for it in items:
        st = getattr(it, "status", "queued")
        rid = int(getattr(it, "recording_id"))
        pct = float(getattr(it, "percent", 0.0) or 0.0)
        if st == "done":
            done_count += 1
            pct_sum += 100.0
        elif st == "running":
            pct_sum += max(0.0, min(99.9, pct))

        output_path = getattr(it, "output_path", None)
        output_url = None
        output_file_name = None
        if output_path is not None and st == "done":
            try:
                output_file_name = Path(str(output_path)).name
            except Exception:
                output_file_name = None
            output_url = f"/reencoded/{job_id}/{rid}"

        file_name = getattr(it, "file_name", None)
        if not file_name:
            try:
                iv = getattr(it, "input_video", None)
                if iv is not None:
                    file_name = Path(str(iv)).name
            except Exception:
                file_name = None

        out_items.append(
            ReencodeItemType(
                recording_id=rid,
                file_name=file_name,
                output_file_name=output_file_name,
                status=st,
                percent=float(pct),
                output_url=output_url,
                error=getattr(it, "error", None),
            )
        )

    overall = 0.0
    if items:
        overall = max(0.0, min(100.0, pct_sum / float(len(items))))

    return ReencodeJobType(
        job_id=str(job_id),
        status=str(status),
        current_recording_id=int(current_recording_id) if current_recording_id is not None else None,
        percent=float(overall),
        items=out_items,
        error=err,
    )


def _ensure_unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem = p.stem
    suffix = p.suffix
    parent = p.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _update_recording_after_reencode(*, settings: AppSettings, recording_id: int, output_path: Path) -> None:
    if not settings.catalog_root:
        return

    session_factory = get_session_for_catalog(settings.catalog_root)
    with session_scope(session_factory) as session:
        rec = session.get(Recording, int(recording_id))
        if not rec:
            return

        old_hash = getattr(rec, "file_hash", None)
        old_hash = old_hash.strip().lower() if isinstance(old_hash, str) and old_hash.strip() else None

        parsed = parse_recording_rel_path(str(rec.rel_path))

        base = Path(settings.catalog_root)
        folder_key = parsed.folder_key
        if folder_key:
            folder = next((f for f in (settings.catalog_folders or ()) if f.key == folder_key), None)
            if folder and folder.path:
                base = Path(folder.path)

        rel = str(output_path.relative_to(base)).replace("\\", "/")
        if folder_key:
            rec.rel_path = encode_recording_rel_path(folder_key, rel)
        else:
            rec.rel_path = rel

        rec.file_name = output_path.name

        info = probe_media(output_path)
        rec.size_bytes = info.size_bytes
        rec.duration_seconds = info.duration_seconds
        rec.width = info.width
        rec.height = info.height
        rec.video_codec = info.video_codec
        rec.audio_codec = info.audio_codec

        # Update file hash/mtime so previews/thumbs are keyed to the new bytes.
        new_hash: str | None = None
        try:
            computed = quick_file_hash(output_path)
            if isinstance(computed, str) and computed.strip():
                new_hash = computed.strip().lower()
        except Exception:
            new_hash = None

        new_mtime: float | None = None
        try:
            new_mtime = float(output_path.stat().st_mtime)
        except Exception:
            new_mtime = None

        # Best-effort: if this recording was the only one using the old hash, delete its old preview.
        # (The new hash will cause the UI to look for a different preview filename.)
        if old_hash and new_hash and old_hash != new_hash:
            try:
                cnt_old = session.scalar(
                    select(func.count()).select_from(Recording).where(Recording.file_hash == old_hash)
                ) or 0
                if int(cnt_old) <= 1:
                    try:
                        (Path(settings.catalog_root) / ".camero" / "previews" / f"{old_hash}.mp4").unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass

        clear_recording_live_preview_failure(rec)

        if new_hash:
            rec.file_hash = new_hash
            if new_mtime is not None:
                rec.file_mtime = float(new_mtime)

            # Keep the thumbnail image, but re-key it to the new hash (copy-once).
            try:
                root = Path(settings.catalog_root)
                thumb_src = None
                if old_hash:
                    candidate = root / ".camero" / "thumbs" / f"{old_hash}.jpg"
                    if candidate.exists() and candidate.stat().st_size > 0:
                        thumb_src = candidate
                thumb_dst = root / ".camero" / "thumbs" / f"{new_hash}.jpg"
                if thumb_src is not None:
                    thumb_dst.parent.mkdir(parents=True, exist_ok=True)
                    if not thumb_dst.exists() or thumb_dst.stat().st_size <= 0:
                        shutil.copy2(thumb_src, thumb_dst)
                if thumb_dst.exists() and thumb_dst.stat().st_size > 0:
                    rec.thumbnail_rel_path = f".camero/thumbs/{new_hash}.jpg"
            except Exception:
                pass


def _progress_to_type(p) -> ScanProgressType:
    phase = getattr(p, "phase", "scan") or "scan"

    total = getattr(p, "total_files", None)
    scanned = getattr(p, "scanned_files", 0)

    percent = 0.0
    if phase in ("live_previews", "auto_tagging"):
        tp = getattr(p, "total_previews", None)
        gp = getattr(p, "generated_previews", 0)
        if tp and tp > 0:
            percent = min(100.0, max(0.0, (gp / tp) * 100.0))
        else:
            percent = 0.0
    else:
        if total and total > 0:
            percent = min(100.0, max(0.0, (scanned / total) * 100.0))
        else:
            percent = 0.0

    return ScanProgressType(
        phase=phase,
        total_files=total,
        scanned_files=scanned,
        added=p.added,
        updated=p.updated,
        skipped=p.skipped,
        errors=p.errors,
        current_file=p.current_file,
        current_recording=getattr(p, "current_recording", None),
        total_previews=getattr(p, "total_previews", None),
        generated_previews=int(getattr(p, "generated_previews", 0) or 0),
        failed_previews=int(getattr(p, "failed_previews", 0) or 0),
        current_preview=getattr(p, "current_preview", None),
        percent=percent,
    )


def _scan_job_to_type(j) -> ScanJobType:
    result = ScanResultType(**j.result.__dict__) if j.result else None
    return ScanJobType(
        job_id=j.job_id,
        status=j.status,
        progress=_progress_to_type(j.progress),
        result=result,
        error=j.error,
    )


def _auto_tag_job_to_type(j: AutoTagJob) -> AutoTagJobType:
    p = j.progress
    _total = int(p.total or 0)
    _done = int(p.completed or 0)
    _pct = round((_done / _total) * 100, 1) if _total > 0 else 0.0
    return AutoTagJobType(
        job_id=j.job_id,
        status=j.status,
        progress=AutoTagProgressType(
            total=_total,
            completed=_done,
            failed=int(p.failed or 0),
            current_file=p.current_file,
            percent=_pct,
            frames_used=p.frames_used,
            candidate_count=p.candidate_count,
        ),
        error=j.error,
    )


def _tag_to_type(tag: Tag) -> TagType:
    return TagType(id=tag.id, name=tag.name)


def _site_to_type(site: Site) -> SiteType:
    return SiteType(id=site.id, name=site.name)


def _streamer_to_type(
    streamer: Streamer,
    *,
    recordings_count: int | None = None,
    total_size_bytes: float | None = None,
    avatar_fallback_recording_id: int | None = None,
) -> StreamerType:
    def _to_str(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            s = str(v)
            return s.strip() or None
        if isinstance(v, str):
            return v.strip() or None
        return None

    url: str | None = None
    gender: str | None = None
    age: str | None = None
    ethnicity: str | None = None
    country: str | None = None
    hair_color: str | None = None
    about: str | None = None
    try:
        if streamer.info_json:
            parsed = json.loads(streamer.info_json)
            if isinstance(parsed, dict):
                url = _to_str(parsed.get("url"))
                gender = _to_str(parsed.get("gender"))
                age = _to_str(parsed.get("age"))
                ethnicity = _to_str(parsed.get("ethnicity"))
                country = _to_str(parsed.get("country"))
                hair_color = _to_str(parsed.get("hair_color"))
                about = _to_str(parsed.get("about"))
    except Exception:
        url = None

    avatar_url = (streamer.avatar_url or "").strip() or None
    # Fallback: when no fetched/stored avatar exists, use a thumbnail from any recording
    # the streamer participates in. This way, assigning a site does not change the
    # displayed image; only a successful Fetch info (which stores avatar_url) will.
    if (not avatar_url) and avatar_fallback_recording_id:
        avatar_url = f"/thumb/{int(avatar_fallback_recording_id)}"

    return StreamerType(
        id=streamer.id,
        name=streamer.name,
        site=_site_to_type(streamer.site) if streamer.site else None,
        avatar_url=avatar_url,
        url=url,
        gender=gender,
        age=age,
        ethnicity=ethnicity,
        country=country,
        hair_color=hair_color,
        about=about,
        recordings_count=recordings_count,
        total_size_bytes=total_size_bytes,
    )


def _recording_to_type(recording: Recording, *, settings: AppSettings | None) -> RecordingType:
    recorded_at = recording.recorded_at.isoformat() if recording.recorded_at else None
    catalog_root = settings.catalog_root if isinstance(settings, AppSettings) else None
    thumb_url = None
    if recording.thumbnail_rel_path and catalog_root:
        thumb_url = f"/thumb/{recording.id}"

    live_preview_url = None
    if catalog_root and live_preview_exists(catalog_root=catalog_root, file_hash=getattr(recording, "file_hash", None)):
        live_preview_url = f"/preview/{recording.id}.mp4"

    contact_sheet_url = None
    if isinstance(settings, AppSettings) and catalog_root:
        recording_path = resolve_recording_path(settings, recording.rel_path)
        if recording_path is not None and find_contact_sheet_path(recording_path) is not None:
            contact_sheet_url = f"/contact-sheet/{recording.id}"

    return RecordingType(
        id=recording.id,
        rel_path=recording.rel_path,
        file_name=recording.file_name,
        title=recording.title,
        recorded_at=recorded_at,
        duration_seconds=recording.duration_seconds,
        width=recording.width,
        height=recording.height,
        size_bytes=float(recording.size_bytes) if recording.size_bytes is not None else None,
        video_codec=getattr(recording, "video_codec", None),
        audio_codec=getattr(recording, "audio_codec", None),
        streamer=_streamer_to_type(recording.streamer) if recording.streamer else None,
        site=_site_to_type(recording.site) if recording.site else None,
        tags=[_tag_to_type(t) for t in (recording.tags or [])],
        thumbnail_url=thumb_url,
        live_preview_url=live_preview_url,
        contact_sheet_url=contact_sheet_url,
    )


def _cleanup_recording_artifacts(
    *,
    catalog_root: str,
    recording_id: int,
    session: Session | None = None,
    file_hash: str | None = None,
) -> None:
    root = Path(catalog_root)
    rid_i = int(recording_id)

    # Thumbs/previews are keyed by file hash. Only delete when this hash is not referenced
    # by any other recording (best-effort).
    safe_hash = file_hash.strip().lower() if isinstance(file_hash, str) and file_hash.strip() else None
    if safe_hash and session is not None:
        try:
            cnt = session.scalar(select(func.count()).select_from(Recording).where(Recording.file_hash == safe_hash)) or 0
            if int(cnt) <= 1:
                try:
                    (root / ".camero" / "thumbs" / f"{safe_hash}.jpg").unlink(missing_ok=True)
                except Exception:
                    pass
                try:
                    (root / ".camero" / "previews" / f"{safe_hash}.mp4").unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            pass
    try:
        (root / ".camero" / "transcodes" / f"{rid_i}.mp4").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        (root / ".camero" / "remux" / f"{rid_i}.mp4").unlink(missing_ok=True)
    except Exception:
        pass
    for ext in ("mp4", "mkv", "avi", "webm"):
        try:
            (root / ".camero" / "reencodes" / f"{rid_i}.{ext}").unlink(missing_ok=True)
        except Exception:
            pass


@strawberry.type
class Query:
    @strawberry.field
    def app_config(self, info: strawberry.Info[ContextDict, None]) -> AppConfig:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        return _settings_to_app_config(s)

    @strawberry.field
    def ffmpeg_status(self, info: strawberry.Info[ContextDict, None]) -> FfmpegStatus:
        try:
            _, path = check_ffmpeg_installed()
            return FfmpegStatus(ok=True, path=path, error=None)
        except Exception as e:
            return FfmpegStatus(ok=False, path=None, error=str(e))

    @strawberry.field
    def recordings(
        self,
        info: strawberry.Info[ContextDict, None],
        limit: int = 20,
        offset: int = 0,
        streamer_name: str | None = None,
        site_name: str | None = None,
        tag_name: str | None = None,
        search: str | None = None,
        name_contains: str | None = None,
        negate_name_contains: bool | None = None,
        negate_filters_legacy: Annotated[bool | None, strawberry.argument(name="negateFilters")] = None,
        name_contains_regex: bool | None = None,
        missing_live_preview: bool | None = None,
        min_size_bytes: float | None = None,
        max_size_bytes: float | None = None,
        min_duration_seconds: float | None = None,
        max_duration_seconds: float | None = None,
        min_short_side: int | None = None,
        max_short_side: int | None = None,
        video_codecs: list[str] | None = None,
        order_by: RecordingOrderBy | None = None,
        order_dir: SortDirection | None = None,
    ) -> RecordingConnection:
        session = info.context["session"]
        assert isinstance(session, Session)

        s = info.context["settings"]
        catalog_root = s.catalog_root if isinstance(s, AppSettings) else None

        if site_name:
            site_name = canonical_site_name(site_name)

        ob = order_by or RecordingOrderBy.RECORDED_AT
        od = order_dir
        if od is None:
            od = SortDirection.DESC if ob == RecordingOrderBy.RECORDED_AT else SortDirection.ASC

        stmt = select(Recording).outerjoin(Recording.streamer).outerjoin(Recording.site)

        if streamer_name:
            stmt = stmt.where(Streamer.name == streamer_name)
        if site_name:
            stmt = stmt.where(Site.name == site_name)
        if tag_name:
            stmt = stmt.join(Recording.tags).where(Tag.name == tag_name)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(Recording.file_name.like(like) | Recording.title.like(like))

        name_regex: re.Pattern[str] | None = None
        negate_text = bool(
            negate_name_contains if negate_name_contains is not None else negate_filters_legacy
        )
        if name_contains:
            needle = str(name_contains).strip().lower()
            if needle:
                if name_contains_regex:
                    try:
                        name_regex = re.compile(str(name_contains).strip(), re.IGNORECASE)
                    except re.error as exc:
                        raise ValueError(f"Invalid regex for nameContains: {exc}") from exc
                else:
                    like = f"%{needle}%"
                    clause = func.lower(Recording.file_name).like(like) | func.lower(Recording.title).like(like)
                    stmt = stmt.where(~clause if negate_text else clause)

        # Advanced filters (optional)
        if min_size_bytes is not None:
            stmt = stmt.where(Recording.size_bytes >= float(min_size_bytes))
        if max_size_bytes is not None:
            stmt = stmt.where(Recording.size_bytes <= float(max_size_bytes))

        if min_duration_seconds is not None:
            stmt = stmt.where(Recording.duration_seconds >= float(min_duration_seconds))
        if max_duration_seconds is not None:
            stmt = stmt.where(Recording.duration_seconds <= float(max_duration_seconds))

        if min_short_side is not None or max_short_side is not None:
            w = func.coalesce(Recording.width, 0)
            h = func.coalesce(Recording.height, 0)
            short_side = case((w <= h, w), else_=h)
            if min_short_side is not None:
                stmt = stmt.where(short_side >= int(min_short_side))
            if max_short_side is not None:
                stmt = stmt.where(short_side <= int(max_short_side))

        if video_codecs:
            cleaned = [str(x).strip() for x in video_codecs if str(x).strip()]
            if cleaned:
                stmt = stmt.where(Recording.video_codec.in_(cleaned))

        # Order
        def _nullslast(col):
            if od == SortDirection.ASC:
                return col.asc().nullslast()
            return col.desc().nullslast()

        if ob == RecordingOrderBy.TITLE:
            stmt = stmt.order_by(_nullslast(func.lower(Recording.title)), Recording.id.desc())
        elif ob == RecordingOrderBy.STREAMER_NAME:
            # Put null streamers last, then order by name.
            is_null = Streamer.name.is_(None)
            if od == SortDirection.ASC:
                stmt = stmt.order_by(is_null.asc(), func.lower(Streamer.name).asc(), Recording.id.desc())
            else:
                stmt = stmt.order_by(is_null.asc(), func.lower(Streamer.name).desc(), Recording.id.desc())
        elif ob == RecordingOrderBy.DURATION:
            stmt = stmt.order_by(_nullslast(Recording.duration_seconds), Recording.id.desc())
        elif ob == RecordingOrderBy.SIZE:
            stmt = stmt.order_by(_nullslast(Recording.size_bytes), Recording.id.desc())
        elif ob == RecordingOrderBy.RESOLUTION:
            pixels = (func.coalesce(Recording.width, 0) * func.coalesce(Recording.height, 0))
            stmt = stmt.order_by(_nullslast(pixels), Recording.id.desc())
        else:
            stmt = stmt.order_by(_nullslast(Recording.recorded_at), Recording.id.desc())

        # Some filters require Python-side evaluation while preserving sort/paging.
        needs_python_filter = bool(name_regex) or (missing_live_preview is not None and catalog_root)
        if needs_python_filter:
            want_missing = bool(missing_live_preview) if missing_live_preview is not None else None
            start_idx = max(0, int(offset or 0))
            end_idx = start_idx + max(0, int(limit or 0))

            selected_ids: list[int] = []
            matched = 0
            total_size = 0.0

            filtered_stmt = stmt.with_only_columns(
                Recording.id,
                Recording.file_hash,
                Recording.size_bytes,
                Recording.file_name,
                Recording.title,
            )
            for rid, fh, size_bytes, file_name, title in session.execute(filtered_stmt):
                if name_regex is not None:
                    haystack_file = str(file_name or "")
                    haystack_title = str(title or "")
                    matched_name = bool(name_regex.search(haystack_file) or name_regex.search(haystack_title))
                    if negate_text:
                        matched_name = not matched_name
                    if not matched_name:
                        continue

                if want_missing is not None:
                    has_prev = live_preview_exists(catalog_root=catalog_root, file_hash=fh)
                    is_missing = not has_prev
                    if is_missing != want_missing:
                        continue

                if matched >= start_idx and matched < end_idx:
                    selected_ids.append(int(rid))
                if size_bytes is not None:
                    total_size += float(size_bytes)
                matched += 1

            total = int(matched)
            if not selected_ids:
                return RecordingConnection(total=total, total_size_bytes=float(total_size), items=[])

            page_items = session.scalars(select(Recording).where(Recording.id.in_(selected_ids))).all()
            by_id = {int(getattr(r, "id")): r for r in page_items}
            ordered_items = [by_id[i] for i in selected_ids if i in by_id]

            return RecordingConnection(
                total=total,
                total_size_bytes=float(total_size),
                items=[_recording_to_type(r, settings=s) for r in ordered_items],
            )

        # Default path: SQL-only filters.
        # Total count (distinct IDs to avoid double-counts when joins are involved)
        id_subq = stmt.order_by(None).with_only_columns(Recording.id).distinct().subquery()
        total = session.scalar(select(func.count()).select_from(id_subq)) or 0
        total_size = session.scalar(select(func.sum(Recording.size_bytes)).where(Recording.id.in_(select(id_subq.c.id))))

        items = session.scalars(stmt.limit(limit).offset(offset)).all()

        return RecordingConnection(
            total=int(total),
            total_size_bytes=float(total_size or 0.0),
            items=[_recording_to_type(r, settings=s) for r in items],
        )

    @strawberry.field
    def recordings_filter_meta(
        self,
        info: strawberry.Info[ContextDict, None],
        streamer_name: str | None = None,
        site_name: str | None = None,
        tag_name: str | None = None,
        search: str | None = None,
    ) -> RecordingsFilterMeta:
        """Returns maxima and available codecs for building the recordings filter UI.

        Note: this intentionally does NOT take the advanced filter args; otherwise the UI can't
        easily widen the range after applying a restrictive filter.
        """

        session = info.context["session"]
        assert isinstance(session, Session)

        if site_name:
            site_name = canonical_site_name(site_name)

        stmt = select(Recording).outerjoin(Recording.streamer).outerjoin(Recording.site)

        if streamer_name:
            stmt = stmt.where(Streamer.name == streamer_name)
        if site_name:
            stmt = stmt.where(Site.name == site_name)
        if tag_name:
            stmt = stmt.join(Recording.tags).where(Tag.name == tag_name)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(Recording.file_name.like(like) | Recording.title.like(like))

        id_subq = stmt.order_by(None).with_only_columns(Recording.id).distinct().subquery()
        id_sel = select(id_subq.c.id)

        max_size = session.scalar(select(func.max(Recording.size_bytes)).where(Recording.id.in_(id_sel)))
        max_dur = session.scalar(select(func.max(Recording.duration_seconds)).where(Recording.id.in_(id_sel)))

        w = func.coalesce(Recording.width, 0)
        h = func.coalesce(Recording.height, 0)
        short_side = case((w <= h, w), else_=h)
        max_short = session.scalar(select(func.max(short_side)).where(Recording.id.in_(id_sel)))

        codecs = session.scalars(
            select(Recording.video_codec)
            .where(Recording.id.in_(id_sel))
            .where(Recording.video_codec.is_not(None))
            .distinct()
            .order_by(func.lower(Recording.video_codec).asc())
        ).all()
        codecs_clean = [str(c).strip() for c in codecs if str(c or '').strip()]

        return RecordingsFilterMeta(
            max_size_bytes=float(max_size or 0.0),
            max_duration_seconds=float(max_dur or 0.0),
            max_short_side=int(max_short or 0),
            video_codecs=codecs_clean,
        )

    @strawberry.field
    def recording(self, info: strawberry.Info[ContextDict, None], id: int) -> RecordingType | None:
        session = info.context["session"]
        assert isinstance(session, Session)
        rec = session.get(Recording, id)
        if not rec:
            return None
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        return _recording_to_type(rec, settings=s)

    @strawberry.field
    def streamers(self, info: strawberry.Info[ContextDict, None], site_name: str | None = None) -> list[StreamerType]:
        session = info.context["session"]
        assert isinstance(session, Session)

        if site_name:
            site_name = canonical_site_name(site_name)
        stmt = (
            select(Streamer)
            .where(exists(select(1).where(Recording.streamer_id == Streamer.id)))
            .order_by(Streamer.name.asc())
        )
        if site_name:
            stmt = stmt.join(Streamer.site).where(Site.name == site_name)
        items = session.scalars(stmt).all()
        return [_streamer_to_type(s) for s in items]

    @strawberry.field
    def streamers_connection(
        self,
        info: strawberry.Info[ContextDict, None],
        limit: int = 20,
        offset: int = 0,
        site_name: str | None = None,
        search: str | None = None,
        order_by: StreamerOrderBy | None = None,
        order_dir: SortDirection | None = None,
        starts_with: str | None = None,
    ) -> StreamerConnection:
        session = info.context["session"]
        assert isinstance(session, Session)

        if site_name:
            site_name = canonical_site_name(site_name)

        ob = order_by or StreamerOrderBy.NAME
        od = order_dir or SortDirection.ASC

        starts = (starts_with or "").strip() or None
        if starts is not None:
            starts = starts[:1]

        first_char = func.substr(func.lower(Streamer.name), 1, 1)

        base = select(Streamer.id).where(exists(select(1).where(Recording.streamer_id == Streamer.id)))
        if site_name:
            base = base.join(Streamer.site).where(Site.name == site_name)
        if search:
            term = search.strip()
            if term:
                like = f"{term}%" if len(term) < 3 else f"%{term}%"
                base = base.where(Streamer.name.like(like))
        if starts:
            if starts == "#":
                base = base.where((first_char == "") | (first_char < "a") | (first_char > "z"))
            else:
                base = base.where(first_char == starts.lower())

        total = session.scalar(select(func.count()).select_from(base.subquery())) or 0

        thumb_rec_id = func.max(
            case(
                (Recording.thumbnail_rel_path.is_not(None), Recording.id),
                else_=None,
            )
        ).label("thumb_recording_id")

        stmt = (
            select(
                Streamer,
                func.count(Recording.id).label("recordings_count"),
                thumb_rec_id,
                func.coalesce(func.sum(Recording.size_bytes), 0).label("total_size_bytes"),
            )
            .join(Recording, Recording.streamer_id == Streamer.id)
            .group_by(Streamer.id, Streamer.name, Streamer.site_id, Streamer.avatar_url, Streamer.info_json)
        )
        if site_name:
            stmt = stmt.join(Streamer.site).where(Site.name == site_name)
        if search:
            term = search.strip()
            if term:
                like = f"{term}%" if len(term) < 3 else f"%{term}%"
                stmt = stmt.where(Streamer.name.like(like))
        if starts:
            if starts == "#":
                stmt = stmt.where((first_char == "") | (first_char < "a") | (first_char > "z"))
            else:
                stmt = stmt.where(first_char == starts.lower())

        # Ordering
        if ob == StreamerOrderBy.RECORDINGS_COUNT:
            cnt = func.count(Recording.id)
            if od == SortDirection.ASC:
                stmt = stmt.order_by(cnt.asc(), func.lower(Streamer.name).asc())
            else:
                stmt = stmt.order_by(cnt.desc(), func.lower(Streamer.name).asc())
        elif ob == StreamerOrderBy.TOTAL_SIZE:
            total_size = func.coalesce(func.sum(Recording.size_bytes), 0)
            if od == SortDirection.ASC:
                stmt = stmt.order_by(total_size.asc(), func.lower(Streamer.name).asc())
            else:
                stmt = stmt.order_by(total_size.desc(), func.lower(Streamer.name).asc())
        elif ob == StreamerOrderBy.RECENT_ADDED:
            last_added_id = func.max(Recording.id)
            if od == SortDirection.ASC:
                stmt = stmt.order_by(last_added_id.asc(), func.lower(Streamer.name).asc())
            else:
                stmt = stmt.order_by(last_added_id.desc(), func.lower(Streamer.name).asc())
        else:
            if od == SortDirection.ASC:
                stmt = stmt.order_by(func.lower(Streamer.name).asc())
            else:
                stmt = stmt.order_by(func.lower(Streamer.name).desc())

        rows = session.execute(stmt.limit(limit).offset(offset)).all()
        items: list[StreamerType] = []
        for streamer, cnt, thumb_id, total_size_bytes in rows:
            fallback_id = int(thumb_id) if thumb_id is not None else None
            items.append(
                _streamer_to_type(
                    streamer,
                    recordings_count=int(cnt or 0),
                    total_size_bytes=float(total_size_bytes or 0),
                    avatar_fallback_recording_id=fallback_id,
                )
            )

        return StreamerConnection(total=int(total), items=items)

    @strawberry.field
    def streamer(
        self,
        info: strawberry.Info[ContextDict, None],
        name: str,
        site_name: str | None = None,
    ) -> StreamerType | None:
        session = info.context["session"]
        assert isinstance(session, Session)

        if site_name:
            site_name = canonical_site_name(site_name)

        stmt = select(Streamer).where(
            Streamer.name == name,
            exists(select(1).where(Recording.streamer_id == Streamer.id)),
        )
        if site_name:
            stmt = stmt.join(Streamer.site).where(Site.name == site_name)

        streamer = session.scalar(stmt)
        if not streamer:
            return None
        fallback_id = session.scalar(
            select(Recording.id)
            .where(Recording.streamer_id == streamer.id, Recording.thumbnail_rel_path.is_not(None))
            .order_by(Recording.id.desc())
            .limit(1)
        )
        return _streamer_to_type(streamer, avatar_fallback_recording_id=int(fallback_id) if fallback_id else None)

    @strawberry.field
    def sites(self, info: strawberry.Info[ContextDict, None]) -> list[SiteType]:
        session = info.context["session"]
        assert isinstance(session, Session)
        items = session.scalars(select(Site).order_by(Site.name.asc())).all()
        return [_site_to_type(s) for s in items]

    @strawberry.field
    def site_overviews(self, info: strawberry.Info[ContextDict, None]) -> list[SiteOverviewType]:
        session = info.context["session"]
        assert isinstance(session, Session)

        # Aggregate counts per site without N+1 queries.
        stmt = (
            select(
                Site.id,
                Site.name,
                func.count(func.distinct(Recording.id)).label("recordings_count"),
                func.count(func.distinct(Streamer.id)).label("streamers_count"),
            )
            .select_from(Site)
            .outerjoin(Recording, Recording.site_id == Site.id)
            .outerjoin(Streamer, Streamer.site_id == Site.id)
            .group_by(Site.id, Site.name)
            .order_by(Site.name.asc())
        )

        rows = session.execute(stmt).all()
        out: list[SiteOverviewType] = []
        assets_sites_dir = get_assets_dir() / "sites"
        for site_id, site_name, rec_cnt, streamer_cnt in rows:
            name = str(site_name or "")
            # Prefer a real logo if present under assets/sites/<site>.png.
            # The lookup uses a normalized lowercase key (alnum only) so that
            # "Amateur.TV" -> "amateurtv" etc.
            site_key = re.sub(r"[^a-z0-9]+", "", name.strip().lower())
            png_path = assets_sites_dir / f"{site_key}.png"
            if site_key and png_path.exists():
                logo_url = f"/assets/sites/{quote(site_key, safe='')}.png"
            else:
                logo_url = f"/site-logo/{quote(name, safe='')}.svg"
            out.append(
                SiteOverviewType(
                    id=int(site_id),
                    name=name,
                    logo_url=logo_url,
                    recordings_count=int(rec_cnt or 0),
                    streamers_count=int(streamer_cnt or 0),
                )
            )
        return out

    @strawberry.field
    def tags(self, info: strawberry.Info[ContextDict, None]) -> list[TagType]:
        session = info.context["session"]
        assert isinstance(session, Session)
        items = session.scalars(select(Tag).order_by(Tag.name.asc())).all()
        return [_tag_to_type(t) for t in items]

    @strawberry.field
    def scan_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> ScanJobType | None:
        _ = info
        j = get_scan_job((job_id or "").strip())
        if not j:
            return None
        return _scan_job_to_type(j)

    @strawberry.field
    def active_scan_job(self, info: strawberry.Info[ContextDict, None]) -> ScanJobType | None:
        _ = info
        j = get_active_scan_job()
        return _scan_job_to_type(j) if j else None

    @strawberry.field
    def auto_tag_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> AutoTagJobType | None:
        _ = info
        j = get_auto_tag_job((job_id or "").strip())
        if not j:
            return None
        return _auto_tag_job_to_type(j)

    @strawberry.field
    def active_auto_tag_job(self, info: strawberry.Info[ContextDict, None]) -> AutoTagJobType | None:
        _ = info
        j = get_active_auto_tag_job()
        return _auto_tag_job_to_type(j) if j else None

    @strawberry.field
    def transcode_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> TranscodeJobType | None:
        _ = info
        j = get_transcode_job((job_id or "").strip())
        if not j:
            return None
        return _transcode_job_to_type(j)

    @strawberry.field
    def active_transcode_job(self, info: strawberry.Info[ContextDict, None]) -> TranscodeJobType | None:
        _ = info
        j = get_active_transcode_job()
        return _transcode_job_to_type(j) if j else None

    @strawberry.field
    def remux_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> RemuxJobType | None:
        _ = info
        j = get_remux_job((job_id or "").strip())
        if not j:
            return None
        return _remux_job_to_type(j)

    @strawberry.field
    def active_remux_job(self, info: strawberry.Info[ContextDict, None]) -> RemuxJobType | None:
        _ = info
        j = get_active_remux_job()
        return _remux_job_to_type(j) if j else None

    @strawberry.field
    def reencode_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> ReencodeJobType | None:
        _ = info
        j = get_reencode_job((job_id or "").strip())
        if not j:
            return None
        return _reencode_job_to_type(j)

    @strawberry.field
    def active_reencode_job(self, info: strawberry.Info[ContextDict, None]) -> ReencodeJobType | None:
        _ = info
        j = get_active_reencode_job()
        return _reencode_job_to_type(j) if j else None

    @strawberry.field
    def live_preview_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> LivePreviewJobType | None:
        _ = info
        j = get_live_preview_job((job_id or "").strip())
        if not j:
            return None
        return _live_preview_job_to_type(j)

    @strawberry.field
    def bulk_fetch_streamer_info_job(
        self, info: strawberry.Info[ContextDict, None], job_id: str
    ) -> BulkFetchStreamerInfoJobType | None:
        _ = info
        j = get_bulk_fetch_streamer_info_job((job_id or "").strip())
        if not j:
            return None
        return _bulk_fetch_job_to_type(j)

    @strawberry.field
    def active_bulk_fetch_streamer_info_job(
        self, info: strawberry.Info[ContextDict, None]
    ) -> BulkFetchStreamerInfoJobType | None:
        _ = info
        j = get_active_bulk_fetch_streamer_info_job()
        return _bulk_fetch_job_to_type(j) if j else None


def _delete_recordings_impl(
    info: strawberry.Info[ContextDict, None],
    ids: list[int],
) -> SimpleResult:
    session = info.context["session"]
    assert isinstance(session, Session)
    s = info.context["settings"]
    assert isinstance(s, AppSettings)
    if not s.catalog_root:
        return SimpleResult(ok=False, message="Catalog root is not configured")

    deleted = 0
    for rid in ids:
        rec = session.get(Recording, rid)
        if not rec:
            continue

        # Delete file (best-effort) then DB row.
        try:
            p = resolve_recording_path(s, rec.rel_path)
            if p is not None:
                p.unlink(missing_ok=True)
        except Exception:
            pass

        _cleanup_recording_artifacts(
            catalog_root=s.catalog_root,
            recording_id=int(rec.id),
            session=session,
            file_hash=getattr(rec, "file_hash", None),
        )

        session.delete(rec)
        deleted += 1

    return SimpleResult(ok=True, message=f"Deleted {deleted} recording(s)")


@strawberry.type
class Mutation:
    @strawberry.mutation
    def test_filename_regex(self, info: strawberry.Info[ContextDict, None], filename_regex: str, candidate: str) -> RegexTestResult:
        _ = info
        try:
            pat = re.compile(filename_regex)
        except re.error as e:
            return RegexTestResult(ok=False, matched=False, error=f"Invalid filename regex: {e}")

        try:
            m = pat.match(candidate)
            if not m:
                return RegexTestResult(ok=True, matched=False, groups_json=None)
            gd = m.groupdict() if hasattr(m, "groupdict") else {}
            # Ensure JSON-serializable and friendly for the UI.
            safe: dict[str, str] = {}
            if isinstance(gd, dict):
                for k, v in gd.items():
                    if v is None:
                        continue
                    safe[str(k)] = str(v)
            return RegexTestResult(ok=True, matched=True, groups_json=json.dumps(safe, ensure_ascii=False))
        except Exception as e:
            return RegexTestResult(ok=False, matched=False, error=str(e) or "Regex match failed")

    @strawberry.mutation
    def set_catalog_folders(self, info: strawberry.Info[ContextDict, None], folders: list[CatalogFolderInput]) -> AppConfig:
        # Validate and normalize early.
        normalized: list[CatalogFolder] = []
        seen_keys: set[str] = set()

        if not folders:
            raise ValueError("At least one catalog folder is required")

        for i, f in enumerate(folders):
            path = (f.path or "").strip()
            if not path:
                raise ValueError(f"Folder path is required (index {i})")
            # Normalize to an absolute path so the persisted catalog works
            # even if the process working directory changes between runs.
            try:
                p = Path(path).resolve()
            except Exception:
                p = Path(path)
            if not p.exists() or not p.is_dir():
                raise ValueError(f"Folder must be an existing directory: {path}")

            try:
                compiled = re.compile(f.filename_regex)
            except re.error as e:
                raise ValueError(f"Invalid filename regex: {e}")
            # Note: streamer/site/date/time groups are recommended but optional.
            # If omitted, the scan can still import files but metadata will be unknown.

            key = (f.key or "").strip() or None
            if not key:
                key = uuid.uuid4().hex[:10]
            if "::" in key or "/" in key or "\\" in key:
                raise ValueError("Folder key contains invalid characters")
            if key in seen_keys:
                raise ValueError("Duplicate folder key")
            seen_keys.add(key)

            normalized.append(
                CatalogFolder(
                    key=key,
                    path=str(p),
                    filename_regex=f.filename_regex,
                    filename_template=(f.filename_template or None),
                    match_path=bool(getattr(f, "match_path", False)),
                )
            )

        s = info.context["settings"]
        assert isinstance(s, AppSettings)

        # Keep the active catalog_root stable when it is already set.
        # A catalog can include multiple scan entries (even multiple patterns for the same folder).
        # For a fresh setup (no catalog_root yet), fall back to the first provided folder.
        catalog_root = s.catalog_root or normalized[0].path
        try:
            p_root = Path(catalog_root).resolve()
        except Exception:
            p_root = Path(catalog_root)
        if not p_root.exists() or not p_root.is_dir():
            raise ValueError("Catalog root is not configured")

        new_settings = _clone_settings(
            s,
            catalog_root=str(p_root),
            catalog_folders=tuple(normalized),
        )
        save_settings(new_settings)
        logger.info("Catalog folders updated count=%s root=%s", len(normalized), str(p_root))
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def start_scan_catalog(self, info: strawberry.Info[ContextDict, None]) -> ScanJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        job = start_scan_job(s)
        logger.info("User started catalog scan job=%s", job.job_id)
        return _scan_job_to_type(job)

    @strawberry.mutation
    def start_generate_missing_live_previews(self, info: strawberry.Info[ContextDict, None]) -> ScanJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        job = start_generate_missing_live_previews_job(s)
        logger.info("User started missing live previews job=%s", job.job_id)
        return _scan_job_to_type(job)

    @strawberry.mutation
    def start_auto_tag_untagged(self, info: strawberry.Info[ContextDict, None]) -> AutoTagJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        job = start_auto_tag_untagged_job(s)
        logger.info("User started auto-tag untagged job=%s", job.job_id)
        return _auto_tag_job_to_type(job)

    @strawberry.mutation
    def start_auto_tag_recordings(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_ids: list[int],
    ) -> AutoTagJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        ids = [int(i) for i in recording_ids if i is not None]
        if not ids:
            raise ValueError("No recording IDs provided")
        job = start_auto_tag_recordings_job(s, ids)
        logger.info("User started auto-tag recordings job=%s ids=%s", job.job_id, ids)
        return _auto_tag_job_to_type(job)

    @strawberry.mutation
    def cancel_auto_tag_job(self, info: strawberry.Info[ContextDict, None], job_id: str) -> SimpleResult:
        _ = info
        jid = (job_id or "").strip()
        if not jid:
            return SimpleResult(ok=False, message="Missing jobId")
        ok = cancel_auto_tag(jid)
        if not ok:
            return SimpleResult(ok=False, message="Job not found")
        logger.info("User requested auto-tag cancellation job=%s", jid)
        return SimpleResult(ok=True, message="Auto-tagging cancellation requested")

    @strawberry.mutation
    def cancel_live_previews(self, info: strawberry.Info[ContextDict, None], job_id: str) -> SimpleResult:
        _ = info
        jid = (job_id or "").strip()
        if not jid:
            return SimpleResult(ok=False, message="Missing jobId")

        ok = cancel_scan_live_previews(jid)
        if not ok:
            return SimpleResult(ok=False, message="Job not found")

        logger.info("User requested live previews cancellation job=%s", jid)
        return SimpleResult(ok=True, message="Animated preview generation cancellation requested")

    @strawberry.mutation
    def cancel_scan_catalog(self, info: strawberry.Info[ContextDict, None], job_id: str) -> SimpleResult:
        _ = info
        jid = (job_id or "").strip()
        if not jid:
            return SimpleResult(ok=False, message="Missing jobId")

        ok = cancel_scan(jid)
        if not ok:
            return SimpleResult(ok=False, message="Job not found")

        logger.info("User requested catalog scan cancellation job=%s", jid)
        return SimpleResult(ok=True, message="Scan cancellation requested")

    @strawberry.mutation
    def start_transcode_recording(self, info: strawberry.Info[ContextDict, None], recording_id: int) -> TranscodeJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, recording_id)
        if not rec:
            raise ValueError("Recording not found")

        root = Path(s.catalog_root)
        input_path = resolve_recording_path(s, rec.rel_path)
        if input_path is None:
            raise ValueError("File not found on disk")
        if not input_path.exists():
            raise ValueError("File not found on disk")

        out_path = root / ".camero" / "transcodes" / f"{recording_id}.mp4"
        job = start_transcode_job(
            recording_id=recording_id,
            catalog_root=s.catalog_root,
            input_video=input_path,
            output_mp4=out_path,
            duration_seconds=rec.duration_seconds,
            on_success=lambda: clear_live_preview_failure_for_recording(
                catalog_root=s.catalog_root or "",
                recording_id=recording_id,
            ),
        )
        logger.info("User started transcode job=%s recording=%s", job.job_id, _recording_log_name(rec))
        return _transcode_job_to_type(job)

    @strawberry.mutation
    def set_recording_metadata(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_id: int,
        streamer_name: str | None = None,
        site_name: str | None = None,
        recorded_at: str | None = None,
    ) -> RecordingType:
        """Persist manual metadata overrides for a recording.

        Any argument that is not provided is left unchanged.
        Passing an empty string clears that field override.
        """

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, recording_id)
        if not rec:
            raise ValueError("Recording not found")

        ov = session.get(RecordingOverride, recording_id)
        if ov is None:
            ov = RecordingOverride(recording_id=recording_id)
            session.add(ov)
            session.flush()

        # Site (can be set independently).
        if site_name is not None:
            name = (site_name or "").strip()
            ov.site_set = True
            if not name:
                ov.site_id = None
            else:
                site = _upsert_site(session, name)
                ov.site_id = site.id
            rec.site_id = ov.site_id
            # If streamer has unknown site and no explicit streamer override,
            # re-associate it to a streamer row for this site.
            site_obj = session.get(Site, int(ov.site_id)) if ov.site_id is not None else None
            _ensure_streamer_site_from_recording(session, rec, ov, site_obj)

        # Streamer (optionally associated to current/overridden site).
        if streamer_name is not None:
            name = (streamer_name or "").strip()
            ov.streamer_set = True
            if not name:
                ov.streamer_id = None
            else:
                # If site override is set, prefer it; otherwise use current rec site.
                site_for_streamer: Site | None = None
                site_id = ov.site_id if getattr(ov, "site_set", False) else rec.site_id
                if site_id is not None:
                    site_for_streamer = session.get(Site, int(site_id))
                streamer = _upsert_streamer(session, name, site_for_streamer)
                ov.streamer_id = streamer.id
            rec.streamer_id = ov.streamer_id

        # Recorded at
        if recorded_at is not None:
            raw = (recorded_at or "").strip()
            ov.recorded_at_set = True
            if not raw:
                ov.recorded_at = None
            else:
                dt = _parse_iso_datetime(raw)
                if dt is None:
                    raise ValueError("Invalid recordedAt (expected ISO datetime)")
                ov.recorded_at = dt
            rec.recorded_at = ov.recorded_at

        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        logger.info(
            "Recording metadata updated recording=%s streamer=%s site=%s recorded_at=%s",
            _recording_log_name(rec),
            streamer_name is not None,
            site_name is not None,
            recorded_at is not None,
        )
        return _recording_to_type(rec, settings=s)

    @strawberry.mutation
    def start_remux_recording(self, info: strawberry.Info[ContextDict, None], recording_id: int) -> RemuxJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, recording_id)
        if not rec:
            raise ValueError("Recording not found")

        root = Path(s.catalog_root)
        input_path = resolve_recording_path(s, rec.rel_path)
        if input_path is None:
            raise ValueError("File not found on disk")
        if not input_path.exists():
            raise ValueError("File not found on disk")

        out_path = root / ".camero" / "remux" / f"{recording_id}.mp4"

        # Remux robustness: only apply AAC ADTS bitstream filter when audio is AAC.
        audio_codec = (rec.audio_codec or "").strip().lower() or None
        if audio_codec is None:
            try:
                audio_codec = (probe_media(input_path).audio_codec or "").strip().lower() or None
            except Exception:
                audio_codec = None

        job = start_remux_job(
            recording_id=recording_id,
            catalog_root=s.catalog_root,
            input_video=input_path,
            output_mp4=out_path,
            duration_seconds=rec.duration_seconds,
            audio_codec=audio_codec,
            on_success=lambda: clear_live_preview_failure_for_recording(
                catalog_root=s.catalog_root or "",
                recording_id=recording_id,
            ),
        )
        logger.info("User started remux job=%s recording=%s", job.job_id, _recording_log_name(rec))
        return _remux_job_to_type(job)

    @strawberry.mutation
    def start_live_preview_recording(
        self, info: strawberry.Info[ContextDict, None], recording_id: int
    ) -> LivePreviewJobType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, recording_id)
        if not rec:
            raise ValueError("Recording not found")

        root = Path(s.catalog_root)
        input_path = resolve_recording_path(s, rec.rel_path)
        if input_path is None:
            raise ValueError("File not found on disk")
        if not input_path.exists():
            raise ValueError("File not found on disk")

        fh = getattr(rec, "file_hash", None)
        if not isinstance(fh, str) or not fh.strip():
            try:
                fh = quick_file_hash(input_path)
            except Exception:
                fh = None

        if isinstance(fh, str) and fh.strip():
            rec.file_hash = fh
            try:
                rec.file_mtime = float(input_path.stat().st_mtime)
            except Exception:
                pass
            session.flush()

        if not isinstance(fh, str) or not fh.strip():
            raise ValueError("Unable to compute file hash for preview")

        out_path = live_preview_output_path_for_hash(catalog_root=s.catalog_root, file_hash=fh)

        # If duration isn't available in the DB (can happen for some containers like FLV),
        # probe it on-demand so preview segments are spread across the video.
        duration_seconds = rec.duration_seconds
        if not duration_seconds or duration_seconds <= 0:
            try:
                duration_seconds = probe_media(input_path).duration_seconds
            except Exception:
                duration_seconds = rec.duration_seconds

        job = start_live_preview_job(
            recording_id=recording_id,
            catalog_root=s.catalog_root,
            input_video=input_path,
            output_mp4=out_path,
            duration_seconds=duration_seconds,
            segments=int(getattr(s, "live_preview_segments", 10) or 10),
            on_success=lambda: clear_live_preview_failure_for_recording(
                catalog_root=s.catalog_root or "",
                recording_id=recording_id,
            ),
            on_failure=lambda error: mark_live_preview_failure_for_recording(
                catalog_root=s.catalog_root or "",
                recording_id=recording_id,
                file_hash=fh,
                error=error,
            ),
        )
        logger.info("User started live preview job=%s recording=%s", job.job_id, _recording_log_name(rec))
        return _live_preview_job_to_type(job)

    @strawberry.mutation
    def set_recording_thumbnail_from_frame(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_id: int,
        time_seconds: float,
    ) -> SimpleResult:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, int(recording_id))
        if not rec:
            return SimpleResult(ok=False, message="Recording not found")

        input_path = resolve_recording_path(s, rec.rel_path)
        if input_path is None or not input_path.exists():
            return SimpleResult(ok=False, message="File not found on disk")

        root = Path(s.catalog_root)
        fh = getattr(rec, "file_hash", None)
        if not isinstance(fh, str) or not fh.strip():
            try:
                fh = quick_file_hash(input_path)
            except Exception:
                fh = None

        if not isinstance(fh, str) or not fh.strip():
            return SimpleResult(ok=False, message="Unable to compute file hash for thumbnail")

        rec.file_hash = fh
        try:
            rec.file_mtime = float(input_path.stat().st_mtime)
        except Exception:
            pass
        thumb_name = f"{fh.strip().lower()}.jpg"
        out_path = root / ".camero" / "thumbs" / thumb_name

        ok, err = extract_frame_at_time(input_video=input_path, output_jpg=out_path, time_seconds=float(time_seconds), width=480)
        if not ok:
            msg = (err or "Unknown error").strip() or "Unknown error"
            return SimpleResult(ok=False, message=f"Failed to extract frame: {msg}")

        rec.thumbnail_rel_path = f".camero/thumbs/{thumb_name}"
        session.commit()
        logger.info("Recording thumbnail updated recording=%s", _recording_log_name(rec))
        return SimpleResult(ok=True, message="Thumbnail updated")

    @strawberry.mutation
    def set_streamer_avatar_from_frame(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_id: int,
        time_seconds: float,
    ) -> SimpleResult:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        session = info.context["session"]
        assert isinstance(session, Session)

        rec = session.get(Recording, int(recording_id))
        if not rec:
            return SimpleResult(ok=False, message="Recording not found")

        if rec.streamer_id is None:
            return SimpleResult(ok=False, message="Recording has no streamer")

        streamer = rec.streamer or session.get(Streamer, int(rec.streamer_id))
        if not streamer:
            return SimpleResult(ok=False, message="Streamer not found")

        input_path = resolve_recording_path(s, rec.rel_path)
        if input_path is None or not input_path.exists():
            return SimpleResult(ok=False, message="File not found on disk")

        root = Path(s.catalog_root)
        out_path = root / ".camero" / "avatars" / f"{int(streamer.id)}.jpg"

        ok, err = extract_frame_at_time(input_video=input_path, output_jpg=out_path, time_seconds=float(time_seconds), width=480)
        if not ok:
            msg = (err or "Unknown error").strip() or "Unknown error"
            return SimpleResult(ok=False, message=f"Failed to extract frame: {msg}")

        streamer.avatar_url = f"/streamer-avatar/{int(streamer.id)}"
        session.commit()
        logger.info("Streamer avatar updated streamer=%s from recording=%s", int(streamer.id), _recording_log_name(rec))
        return SimpleResult(ok=True, message="Streamer avatar updated")

    @strawberry.mutation
    def rescan_recordings_metadata(self, info: strawberry.Info[ContextDict, None], ids: list[int]) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        rec_ids = [int(x) for x in (ids or [])]
        if not rec_ids:
            return SimpleResult(ok=False, message="No recordings selected")

        log_target = _recordings_log_target(session, rec_ids)

        root = Path(s.catalog_root)
        thumbs_dir = root / ".camero" / "thumbs"

        updated = 0
        missing = 0
        failed = 0

        for rid in rec_ids:
            try:
                rec = session.get(Recording, int(rid))
                if not rec:
                    missing += 1
                    continue

                input_path = resolve_recording_path(s, rec.rel_path)
                if input_path is None or not input_path.exists():
                    failed += 1
                    continue

                info = probe_media(input_path)
                rec.duration_seconds = info.duration_seconds
                rec.width = info.width
                rec.height = info.height
                rec.size_bytes = info.size_bytes
                rec.video_codec = info.video_codec
                rec.audio_codec = info.audio_codec

                try:
                    rec.file_mtime = float(input_path.stat().st_mtime)
                except Exception:
                    pass

                fh = getattr(rec, "file_hash", None)
                if not isinstance(fh, str) or not fh.strip():
                    try:
                        fh = quick_file_hash(input_path)
                    except Exception:
                        fh = None
                if isinstance(fh, str) and fh.strip():
                    rec.file_hash = fh
                else:
                    fh = None

                # Force-regenerate thumbnail when re-scanning (hash-keyed layout only).
                if fh:
                    thumb_name = f"{fh}.jpg"
                    thumb_path = thumbs_dir / thumb_name
                    if ensure_thumbnail(input_path, thumb_path, title=rec.title, force=True):
                        rec.thumbnail_rel_path = f".camero/thumbs/{thumb_name}"

                updated += 1
            except Exception:
                failed += 1

        parts: list[str] = [f"Updated {updated} recording(s)" if updated else "No recordings updated"]
        if missing:
            parts.append(f"missing {missing}")
        if failed:
            parts.append(f"failed {failed}")

        logger.info("Recording metadata rescan finished target=%s updated=%s missing=%s failed=%s", log_target, updated, missing, failed)

        return SimpleResult(ok=(failed == 0), message="; ".join(parts))

    @strawberry.mutation
    def start_reencode_recordings(
        self,
        info: strawberry.Info[ContextDict, None],
        ids: list[int],
        options: ReencodeOptionsInput | None = None,
    ) -> StartReencodeResult:
        try:
            s = info.context["settings"]
            assert isinstance(s, AppSettings)
            if not s.catalog_root:
                raise ValueError("Catalog root is not configured")

            session = info.context["session"]
            assert isinstance(session, Session)

            rec_ids = [int(x) for x in (ids or [])]
            if not rec_ids:
                raise ValueError("No recordings selected")

            opt_in = options or ReencodeOptionsInput()
            opts = ReencodeOptions(
                container=(opt_in.container or "mp4").lower(),
                video_codec=(opt_in.video_codec or "h264").lower(),
                audio_codec=(opt_in.audio_codec or "aac").lower(),
                quality=(opt_in.quality or "balanced").lower(),
                speed=(opt_in.speed or "veryfast").lower(),
                audio_bitrate=(opt_in.audio_bitrate or None),
                extra_ffmpeg_args=(opt_in.extra_ffmpeg_args or None),
                delete_original=bool(getattr(opt_in, "delete_original", False)),
            )

            # Normalize container and build output paths.
            ext = (opts.container or "mp4").strip().lower().lstrip(".")
            if ext not in {"mp4", "mkv", "avi", "webm"}:
                raise ValueError("Unsupported container")

            vcodec_label = (opts.video_codec or "").strip().lower() or "h264"
            acodec_label = (opts.audio_codec or "").strip().lower() or "aac"

            items: list[ReencodeItem] = []
            for rid in rec_ids:
                rec = session.get(Recording, rid)
                if not rec:
                    raise ValueError(f"Recording not found: {rid}")

                input_path = resolve_recording_path(s, rec.rel_path)
                if input_path is None or not input_path.exists():
                    raise ValueError(f"File not found on disk: {rid}")

                # Outputs are saved next to the original file.
                if opts.delete_original:
                    # Replace the original (same name) when container matches; otherwise keep stem and change extension.
                    out_path = input_path if input_path.suffix.lower() == f".{ext}" else input_path.with_suffix(f".{ext}")
                    if out_path != input_path:
                        out_path = _ensure_unique_path(out_path)
                else:
                    out_path = input_path.with_name(f"{input_path.stem}.transcoded_{vcodec_label}_{acodec_label}.{ext}")
                    out_path = _ensure_unique_path(out_path)

                tmp_path = input_path.with_name(f"{input_path.stem}.camero_reencode_tmp_{uuid.uuid4().hex[:8]}.{ext}")
                items.append(
                    ReencodeItem(
                        recording_id=rid,
                        input_video=input_path,
                        output_path=out_path,
                        temp_output_path=tmp_path,
                        duration_seconds=rec.duration_seconds,
                        status="queued",
                        file_name=getattr(rec, "file_name", None) or input_path.name,
                    )
                )

            if opts.delete_original:
                cb = lambda it: _update_recording_after_reencode(
                    settings=s,
                    recording_id=it.recording_id,
                    output_path=it.output_path,
                )
            else:
                cb = lambda it: clear_live_preview_failure_for_recording(
                    catalog_root=s.catalog_root or "",
                    recording_id=it.recording_id,
                )

            job = start_reencode_job(items=items, opts=opts, catalog_root=s.catalog_root, on_item_success=cb)
            jt = _reencode_job_to_type(job)
            if len(items) == 1:
                logger.info("User started reencode job=%s recording=%s", jt.job_id, items[0].file_name or items[0].input_video.name)
            else:
                logger.info("User started reencode job=%s recordings=%s", jt.job_id, len(items))
            return StartReencodeResult(ok=True, message=f"Started transcode job {jt.job_id}", job=jt)
        except Exception as e:
            # Surface a real error message to the UI.
            msg = str(e) or "Failed to start transcode job"
            return StartReencodeResult(ok=False, message=msg, job=None)

    @strawberry.mutation
    def cancel_reencode(self, info: strawberry.Info[ContextDict, None], job_id: str) -> SimpleResult:
        _ = info
        jid = (job_id or "").strip()
        if not jid:
            return SimpleResult(ok=False, message="Missing jobId")

        ok = cancel_reencode_job(jid)
        if not ok:
            return SimpleResult(ok=False, message="Job not found")

        logger.info("User requested reencode cancellation job=%s", jid)
        return SimpleResult(ok=True, message="Transcoding cancellation requested")

    @strawberry.mutation
    def create_tag(self, info: strawberry.Info[ContextDict, None], name: str) -> TagType:
        session = info.context["session"]
        assert isinstance(session, Session)

        tag_name = (name or "").strip()
        if not tag_name:
            raise ValueError("Tag name is required")

        existing = session.scalar(select(Tag).where(Tag.name == tag_name))
        if existing:
            return _tag_to_type(existing)

        tag = Tag(name=tag_name)
        session.add(tag)
        session.flush()
        logger.info("Tag created name=%s", tag_name)
        return _tag_to_type(tag)

    @strawberry.mutation
    def rename_tag(self, info: strawberry.Info[ContextDict, None], tag_id: int, new_name: str) -> TagType:
        session = info.context["session"]
        assert isinstance(session, Session)

        name = (new_name or "").strip()
        if not name:
            raise ValueError("New tag name is required")

        tag = session.get(Tag, tag_id)
        if not tag:
            raise ValueError("Tag not found")

        existing = session.scalar(select(Tag).where(Tag.name == name))
        if existing and existing.id != tag.id:
            raise ValueError("A tag with that name already exists")

        tag.name = name
        session.flush()
        logger.info("Tag renamed id=%s new_name=%s", tag_id, name)
        return _tag_to_type(tag)

    @strawberry.mutation
    def delete_tag(self, info: strawberry.Info[ContextDict, None], tag_id: int) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        tag = session.get(Tag, tag_id)
        if not tag:
            return SimpleResult(ok=False, message="Tag not found")

        # Remove associations first to avoid FK constraint issues.
        session.execute(delete(RecordingTag).where(RecordingTag.tag_id == tag.id))
        session.delete(tag)
        logger.info("Tag deleted id=%s name=%s", tag_id, getattr(tag, "name", ""))
        return SimpleResult(ok=True, message="Tag deleted")

    @strawberry.mutation
    def set_catalog_root(self, info: strawberry.Info[ContextDict, None], catalog_root: str) -> AppConfig:
        # Basic validation only (server runs locally).
        try:
            p = Path(catalog_root).resolve()
        except Exception:
            p = Path(catalog_root)
        if not p.exists() or not p.is_dir():
            raise ValueError("Catalog root must be an existing directory")

        # Important: do NOT carry over catalog folder settings from a previous catalog.
        # Catalog-specific settings live under <catalog_root>/.camero/settings.json.
        new_settings = set_active_catalog_root(str(p))
        logger.info("Active catalog root changed root=%s", str(p))
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_filename_regex(self, info: strawberry.Info[ContextDict, None], filename_regex: str) -> AppConfig:
        # Validate early so we don't end up with an unusable configuration.
        try:
            compiled = re.compile(filename_regex)
        except re.error as e:
            raise ValueError(f"Invalid filename regex: {e}")

        # streamer/site/date/time groups are recommended but optional.

        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        folders = list(s.catalog_folders or ())
        if not folders:
            folders = [
                CatalogFolder(
                    key=uuid.uuid4().hex[:10],
                    path=s.catalog_root,
                    filename_regex=filename_regex,
                    filename_template=None,
                )
            ]
        else:
            folders[0] = CatalogFolder(
                key=folders[0].key,
                path=folders[0].path,
                filename_regex=filename_regex,
                filename_template=getattr(folders[0], "filename_template", None),
                match_path=bool(getattr(folders[0], "match_path", False)),
            )

        new_settings = _clone_settings(
            s,
            filename_regex=filename_regex,
            catalog_folders=tuple(folders),
        )
        save_settings(new_settings)
        logger.info("Filename regex updated")
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def reset_app_config(self, info: strawberry.Info[ContextDict, None]) -> AppConfig:
        _ = info
        new_settings = AppSettings(
            catalog_root=None,
            filename_regex=DEFAULT_FILENAME_REGEX,
            catalog_folders=(),
            generate_live_previews=True,
            live_preview_segments=10,
            periodic_scan_enabled=DEFAULT_PERIODIC_SCAN_ENABLED,
            periodic_scan_interval_minutes=DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
            backend_host=DEFAULT_BACKEND_HOST,
            backend_port=DEFAULT_BACKEND_PORT,
            auth_required=DEFAULT_AUTH_REQUIRED,
            auth_username=DEFAULT_AUTH_USERNAME,
            auth_password=DEFAULT_AUTH_PASSWORD,
        )
        save_settings(new_settings)
        logger.info("Application settings reset")
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_backend_bind(
        self,
        info: strawberry.Info[ContextDict, None],
        backend_host: str,
        backend_port: int,
    ) -> AppConfig:
        _ = info
        host = (backend_host or "").strip()
        if not host:
            raise ValueError("backendHost is required")

        port = int(backend_port)
        if port < 1 or port > 65535:
            raise ValueError("backendPort must be between 1 and 65535")

        save_global_network_settings(
            backend_host=host,
            backend_port=port,
        )
        logger.info("Backend bind updated host=%s port=%s", host, port)
        return _settings_to_app_config(load_settings())

    @strawberry.mutation
    def set_network_auth(
        self,
        info: strawberry.Info[ContextDict, None],
        auth_required: bool,
        auth_username: str,
        auth_password: str,
    ) -> AppConfig:
        _ = info
        username = (auth_username or "").strip()
        password = (auth_password or "").strip()

        if auth_required:
            if not username:
                raise ValueError("Username is required when authentication is enabled")
            if not password:
                raise ValueError("Password is required when authentication is enabled")

        save_global_network_settings(
            auth_required=bool(auth_required),
            auth_username=username,
            auth_password=password,
        )
        logger.info("Network auth updated enabled=%s username_set=%s", bool(auth_required), bool(username))
        return _settings_to_app_config(load_settings())

    @strawberry.mutation
    def set_generate_live_previews(
        self, info: strawberry.Info[ContextDict, None], generate_live_previews: bool
    ) -> AppConfig:
        # Important: do not trust info.context["settings"] to be current.
        # The UI may call multiple mutations back-to-back (e.g. setCatalogFolders then
        # setGenerateLivePreviews). If the context settings are stale, we'd overwrite
        # the freshly-saved catalog_folders with an older snapshot.
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        new_settings = _clone_settings(s, generate_live_previews=bool(generate_live_previews))
        save_settings(new_settings)
        logger.info("Live preview generation setting updated enabled=%s", bool(generate_live_previews))
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_live_preview_segments(
        self, info: strawberry.Info[ContextDict, None], live_preview_segments: int
    ) -> AppConfig:
        _ = info
        allowed = {5, 10, 15, 20}
        n = int(live_preview_segments)
        if n not in allowed:
            raise ValueError("livePreviewSegments must be one of: 5, 10, 15, 20")

        # Same stale-context guard as set_generate_live_previews.
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        new_settings = _clone_settings(s, live_preview_segments=n)
        save_settings(new_settings)
        logger.info("Live preview segments updated segments=%s", n)
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_periodic_scan_enabled(
        self, info: strawberry.Info[ContextDict, None], periodic_scan_enabled: bool
    ) -> AppConfig:
        _ = info
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        new_settings = _clone_settings(s, periodic_scan_enabled=bool(periodic_scan_enabled))
        save_settings(new_settings)
        logger.info("Periodic scan setting updated enabled=%s", bool(periodic_scan_enabled))
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_periodic_scan_interval_minutes(
        self, info: strawberry.Info[ContextDict, None], periodic_scan_interval_minutes: int
    ) -> AppConfig:
        _ = info
        n = int(periodic_scan_interval_minutes)
        if n < 1 or n > 1440:
            raise ValueError("periodicScanIntervalMinutes must be between 1 and 1440")

        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")

        new_settings = _clone_settings(s, periodic_scan_interval_minutes=n)
        save_settings(new_settings)
        logger.info("Periodic scan interval updated minutes=%s", n)
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_auto_tag_after_scan(
        self, info: strawberry.Info[ContextDict, None], auto_tag_after_scan: bool
    ) -> AppConfig:
        _ = info
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        new_settings = _clone_settings(s, auto_tag_after_scan=bool(auto_tag_after_scan))
        save_settings(new_settings)
        if bool(auto_tag_after_scan):
            sync_cached_tagger(new_settings)
        else:
            release_cached_tagger(reason="settings_toggle_disabled")
        logger.info("Auto-tag after scan updated enabled=%s", bool(auto_tag_after_scan))
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_auto_tag_confidence(
        self, info: strawberry.Info[ContextDict, None], auto_tag_confidence: float
    ) -> AppConfig:
        _ = info
        v = _coerce_auto_tag_confidence(auto_tag_confidence, default=DEFAULT_AUTO_TAG_CONFIDENCE)
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        new_settings = _clone_settings(s, auto_tag_confidence=v)
        save_settings(new_settings)
        sync_cached_tagger(new_settings)
        logger.info("Auto-tag confidence updated confidence=%s", v)
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_auto_tag_scene_threshold(
        self, info: strawberry.Info[ContextDict, None], auto_tag_scene_threshold: float
    ) -> AppConfig:
        _ = info
        v = _coerce_auto_tag_scene_threshold(auto_tag_scene_threshold, default=DEFAULT_AUTO_TAG_SCENE_THRESHOLD)
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        new_settings = _clone_settings(s, auto_tag_scene_threshold=v)
        save_settings(new_settings)
        sync_cached_tagger(new_settings)
        logger.info("Auto-tag scene threshold updated threshold=%s", v)
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    def set_auto_tag_frame_interval(
        self, info: strawberry.Info[ContextDict, None], auto_tag_frame_interval: str
    ) -> AppConfig:
        _ = info
        v = _coerce_auto_tag_frame_interval(auto_tag_frame_interval, default=DEFAULT_AUTO_TAG_FRAME_INTERVAL)
        s = load_settings()
        if not s.catalog_root:
            raise ValueError("Catalog root is not configured")
        new_settings = _clone_settings(s, auto_tag_frame_interval=v)
        save_settings(new_settings)
        sync_cached_tagger(new_settings)
        logger.info("Auto-tag frame interval updated interval=%s", v)
        return _settings_to_app_config(new_settings)

    @strawberry.mutation
    async def scan_catalog(self, info: strawberry.Info[ContextDict, None]) -> ScanResultType:
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root or not s.catalog_folders:
            raise ValueError("Catalog folders are not configured")

        # Run the scan in a background thread so the server can continue
        # serving other requests (navigation, queries, etc.) while scanning.
        session_factory = get_session_for_catalog(s.catalog_root)

        def _do_scan() -> ScanResult:
            session = session_factory()
            try:
                result: ScanResult = scan_catalog(
                    catalog_root=s.catalog_root or "",
                    catalog_folders=list(s.catalog_folders),
                    session=session,
                )
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        result = await asyncio.to_thread(_do_scan)
        return ScanResultType(**result.__dict__)

    @strawberry.mutation
    def assign_tags(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_ids: list[int],
        tag_names: list[str],
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        names = [n.strip() for n in tag_names if n and n.strip()]
        if not names:
            return SimpleResult(ok=False, message="No tags provided")

        tags: list[Tag] = []
        for name in names:
            tag = session.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(name=name)
                session.add(tag)
                session.flush()
            tags.append(tag)

        updated = 0
        for rid in recording_ids:
            rec = session.get(Recording, rid)
            if not rec:
                continue
            existing = {t.name for t in rec.tags}
            for t in tags:
                if t.name not in existing:
                    rec.tags.append(t)
            updated += 1

        logger.info("Tags assigned target=%s tags=%s updated=%s", _recordings_log_target(session, recording_ids), len(tags), updated)
        return SimpleResult(ok=True, message=f"Updated {updated} recording(s)")

    @strawberry.mutation
    def set_tags(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_ids: list[int],
        tag_names: list[str],
    ) -> SimpleResult:
        """Set the tags for the given recordings.

        Unlike assignTags (which only adds), this overwrites the tag set and can remove tags.
        Passing an empty list clears tags.
        """

        session = info.context["session"]
        assert isinstance(session, Session)

        # Normalize + de-duplicate while preserving order.
        seen: set[str] = set()
        names: list[str] = []
        for n in tag_names or []:
            name = (n or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)

        tags: list[Tag] = []
        for name in names:
            tag = session.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(name=name)
                session.add(tag)
                session.flush()
            tags.append(tag)

        updated = 0
        for rid in recording_ids:
            rec = session.get(Recording, rid)
            if not rec:
                continue
            rec.tags = list(tags)
            updated += 1

        logger.info("Tags set target=%s tags=%s updated=%s", _recordings_log_target(session, recording_ids), len(tags), updated)
        return SimpleResult(ok=True, message=f"Updated {updated} recording(s)")

    @strawberry.mutation
    def assign_site(
        self,
        info: strawberry.Info[ContextDict, None],
        recording_ids: list[int],
        site_name: str,
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        name = (site_name or "").strip()
        if not name:
            return SimpleResult(ok=False, message="Site name is required")

        site = _upsert_site(session, name)

        updated = 0
        for rid in recording_ids:
            rec = session.get(Recording, rid)
            if not rec:
                continue

            ov = session.get(RecordingOverride, rid)
            if ov is None:
                ov = RecordingOverride(recording_id=rid)
                session.add(ov)
                session.flush()

            ov.site_set = True
            ov.site_id = site.id
            rec.site_id = site.id
            _ensure_streamer_site_from_recording(session, rec, ov, site)
            updated += 1

        return SimpleResult(ok=True, message=f"Updated {updated} recording(s)")

    @strawberry.mutation
    def assign_streamer_site(
        self,
        info: strawberry.Info[ContextDict, None],
        streamer_name: str,
        site_name: str,
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        sname = (streamer_name or "").strip()
        if not sname:
            return SimpleResult(ok=False, message="Streamer name is required")

        name = (site_name or "").strip()
        if not name:
            return SimpleResult(ok=False, message="Site name is required")
        if name.lower() == "unknown":
            return SimpleResult(ok=False, message="Site is unknown")

        site = _upsert_site(session, name)

        # We only allow assigning when the current streamer row has unknown site.
        unknown_streamer = session.scalar(select(Streamer).where(Streamer.name == sname, Streamer.site_id.is_(None)))
        if not unknown_streamer:
            return SimpleResult(ok=False, message="Streamer not found (or already has a site)")

        # Ensure we have a streamer row bound to the chosen site.
        _ = _upsert_streamer(session, sname, site)

        recs = session.scalars(select(Recording).where(Recording.streamer_id == unknown_streamer.id)).all()
        updated = 0
        for rec in recs:
            # Persist as an explicit override, same as per-recording assignment.
            ov = session.get(RecordingOverride, int(rec.id))
            if ov is None:
                ov = RecordingOverride(recording_id=int(rec.id))
                session.add(ov)
                session.flush()

            ov.site_set = True
            ov.site_id = site.id
            rec.site_id = site.id
            _ensure_streamer_site_from_recording(session, rec, ov, site)
            updated += 1

        return SimpleResult(ok=True, message=f"Updated {updated} recording(s)")

    @strawberry.mutation
    def set_streamer_site(
        self,
        info: strawberry.Info[ContextDict, None],
        streamer_id: int,
        site_name: str,
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        if not streamer_id:
            return SimpleResult(ok=False, message="Streamer id is required")

        name = (site_name or "").strip()
        if not name:
            return SimpleResult(ok=False, message="Site name is required")
        if name.lower() == "unknown":
            return SimpleResult(ok=False, message="Site is unknown")

        streamer = session.get(Streamer, int(streamer_id))
        if not streamer:
            return SimpleResult(ok=False, message="Streamer not found")

        site = _upsert_site(session, name)

        # Ensure a site-bound streamer row exists for the selected site.
        target_streamer = _upsert_streamer(session, streamer.name, site)

        recs = session.scalars(select(Recording).where(Recording.streamer_id == streamer.id)).all()
        updated = 0
        for rec in recs:
            ov = session.get(RecordingOverride, int(rec.id))
            if ov is None:
                ov = RecordingOverride(recording_id=int(rec.id))
                session.add(ov)
                session.flush()

            ov.site_set = True
            ov.site_id = site.id
            rec.site_id = site.id

            # Do not override explicit streamer overrides.
            if not getattr(ov, "streamer_set", False):
                rec.streamer_id = target_streamer.id

            updated += 1

        return SimpleResult(ok=True, message=f"Updated {updated} recording(s)")

    @strawberry.mutation
    def delete_recordings(self, info: strawberry.Info[ContextDict, None], ids: list[int]) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)
        log_target = _recordings_log_target(session, ids)
        result = _delete_recordings_impl(info, ids)
        logger.info("Recordings deleted target=%s result=%s", log_target, result.message)
        return result

    @strawberry.mutation
    def delete_streamer(self, info: strawberry.Info[ContextDict, None], streamer_id: int) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        if not streamer_id:
            return SimpleResult(ok=False, message="Streamer id is required")

        streamer = session.get(Streamer, int(streamer_id))
        if not streamer:
            return SimpleResult(ok=False, message="Streamer not found")

        rec_ids = session.scalars(select(Recording.id).where(Recording.streamer_id == streamer.id)).all()
        ids = [int(rid) for rid in rec_ids if rid is not None]
        if ids:
            res = _delete_recordings_impl(info, ids)
            if not res.ok:
                return res

        session.delete(streamer)
        return SimpleResult(ok=True, message=f"Deleted streamer and {len(ids)} recording(s)")

    @strawberry.mutation
    def move_or_copy_recordings(
        self,
        info: strawberry.Info[ContextDict, None],
        ids: list[int],
        destination_subfolder: str,
        mode: str = "move",  # "move" | "copy"
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        dest_rel = destination_subfolder.strip().replace("\\", "/").strip("/")
        if not dest_rel:
            return SimpleResult(ok=False, message="Destination subfolder is required")

        mode_norm = mode.strip().lower()
        if mode_norm not in {"move", "copy"}:
            return SimpleResult(ok=False, message="Mode must be 'move' or 'copy'")

        changed = 0
        for rid in ids:
            rec = session.get(Recording, rid)
            if not rec:
                continue

            src_path = resolve_recording_path(s, rec.rel_path)
            if src_path is None:
                continue
            if not src_path.exists():
                continue

            parsed = parse_recording_rel_path(rec.rel_path)
            folder_key = parsed.folder_key or (s.catalog_folders[0].key if s.catalog_folders else None)
            folder = next((f for f in (s.catalog_folders or ()) if f.key == folder_key), None) if folder_key else None
            source_root = Path(folder.path) if folder is not None else Path(s.catalog_root)

            dest_dir = (source_root / dest_rel).resolve()
            if source_root.resolve() not in dest_dir.parents and dest_dir != source_root.resolve():
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)

            dest_path = dest_dir / src_path.name
            # Avoid overwriting existing files.
            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                i = 1
                while True:
                    candidate = dest_dir / f"{stem} ({i}){suffix}"
                    if not candidate.exists():
                        dest_path = candidate
                        break
                    i += 1

            if mode_norm == "copy":
                shutil.copy2(src_path, dest_path)
                new_rel = str(dest_path.relative_to(source_root)).replace("\\", "/")
                if folder_key:
                    new_rel = encode_recording_rel_path(folder_key, new_rel)
                cloned = Recording(
                    rel_path=new_rel,
                    file_name=dest_path.name,
                    title=rec.title,
                    streamer_id=rec.streamer_id,
                    site_id=rec.site_id,
                    recorded_at=rec.recorded_at,
                    duration_seconds=rec.duration_seconds,
                    width=rec.width,
                    height=rec.height,
                    size_bytes=rec.size_bytes,
                    thumbnail_rel_path=rec.thumbnail_rel_path,
                )
                cloned.tags = list(rec.tags)
                session.add(cloned)
                changed += 1
            else:
                shutil.move(str(src_path), str(dest_path))
                new_rel = str(dest_path.relative_to(source_root)).replace("\\", "/")
                if folder_key:
                    new_rel = encode_recording_rel_path(folder_key, new_rel)
                rec.rel_path = new_rel
                rec.file_name = dest_path.name
                changed += 1

        return SimpleResult(ok=True, message=f"{mode_norm.title()}d {changed} recording(s)")

    @strawberry.mutation
    def move_or_copy_recordings_to_path(
        self,
        info: strawberry.Info[ContextDict, None],
        ids: list[int],
        destination_path: str,
        preserve_ancestors: bool = False,
        mode: str = "move",  # "move" | "copy"
    ) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)
        s = info.context["settings"]
        assert isinstance(s, AppSettings)
        if not s.catalog_root:
            return SimpleResult(ok=False, message="Catalog root is not configured")

        dest_raw = (destination_path or "").strip().strip('"')
        if not dest_raw:
            return SimpleResult(ok=False, message="Destination path is required")

        mode_norm = (mode or "").strip().lower()
        if mode_norm not in {"move", "copy"}:
            return SimpleResult(ok=False, message="Mode must be 'move' or 'copy'")

        try:
            dest_dir = Path(dest_raw).expanduser().resolve()
        except Exception:
            return SimpleResult(ok=False, message="Invalid destination path")

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return SimpleResult(ok=False, message=f"Failed to create destination folder: {e}")

        if not dest_dir.exists() or not dest_dir.is_dir():
            return SimpleResult(ok=False, message="Destination must be a folder")

        changed = 0
        removed = 0
        relocated = 0

        for rid in (ids or []):
            rec = session.get(Recording, int(rid))
            if not rec:
                continue

            src_path = resolve_recording_path(s, rec.rel_path)
            if src_path is None or not src_path.exists():
                continue

            parsed = parse_recording_rel_path(rec.rel_path)
            folder_key = parsed.folder_key or (s.catalog_folders[0].key if s.catalog_folders else None)
            folder = next((f for f in (s.catalog_folders or ()) if f.key == folder_key), None) if folder_key else None
            source_root = Path(folder.path) if folder is not None else Path(s.catalog_root)
            try:
                source_root_resolved = source_root.resolve()
            except Exception:
                source_root_resolved = source_root

            dest_path = dest_dir / src_path.name
            if preserve_ancestors:
                try:
                    rel_from_root = src_path.resolve().relative_to(source_root_resolved)
                    dest_path = dest_dir / rel_from_root
                except Exception:
                    dest_path = dest_dir / src_path.name

            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                # If we can't create the nested folders, fall back to copying/moving into the destination root.
                dest_path = dest_dir / src_path.name

            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                parent_dir = dest_path.parent
                i = 1
                while True:
                    candidate = parent_dir / f"{stem} ({i}){suffix}"
                    if not candidate.exists():
                        dest_path = candidate
                        break
                    i += 1

            try:
                if mode_norm == "copy":
                    shutil.copy2(src_path, dest_path)
                    changed += 1
                else:
                    shutil.move(str(src_path), str(dest_path))
                    # If destination is still inside this recording's catalog root folder,
                    # treat as relocation: update rel_path and keep DB row.
                    is_inside = False
                    try:
                        is_inside = (dest_dir in [source_root_resolved] or source_root_resolved in dest_dir.parents)
                    except Exception:
                        is_inside = False

                    if is_inside:
                        new_rel = str(dest_path.relative_to(source_root_resolved)).replace("\\", "/")
                        if folder_key:
                            new_rel = encode_recording_rel_path(folder_key, new_rel)
                        rec.rel_path = new_rel
                        rec.file_name = dest_path.name
                        relocated += 1
                        changed += 1
                    else:
                        # Outside-catalog move: treat as export -> remove from DB.
                        _cleanup_recording_artifacts(
                            catalog_root=s.catalog_root,
                            recording_id=int(rec.id),
                            session=session,
                            file_hash=getattr(rec, "file_hash", None),
                        )
                        session.delete(rec)
                        removed += 1
                        changed += 1
            except Exception:
                # Best-effort: continue with remaining items.
                continue

        if mode_norm == "move":
            return SimpleResult(
                ok=True,
                message=f"Moved {changed} recording(s) to '{dest_dir}'. Relocated {relocated} within catalog. Removed {removed} from catalog.",
            )
        return SimpleResult(ok=True, message=f"Copied {changed} recording(s) to '{dest_dir}'")

    @strawberry.mutation
    def reencode_recordings(self, info: strawberry.Info[ContextDict, None], ids: list[int]) -> SimpleResult:
        # Compatibility wrapper for older clients. Prefer `startReencodeRecordings` (transcode).
        res = self.start_reencode_recordings(info, ids, ReencodeOptionsInput())
        if not res.ok or not res.job:
            return SimpleResult(ok=False, message=res.message)
        return SimpleResult(ok=True, message=f"Started transcode job {res.job.job_id} for {len(ids or [])} recording(s)")

    @strawberry.mutation
    def fetch_streamer_info(self, info: strawberry.Info[ContextDict, None], streamer_name: str, site_name: str) -> SimpleResult:
        session = info.context["session"]
        assert isinstance(session, Session)

        streamer_name = (streamer_name or "").strip()
        site_name = (site_name or "").strip()
        site_name = canonical_site_name(site_name) or ""
        if not streamer_name:
            return SimpleResult(ok=False, message="Streamer name is required")
        if not site_name or site_name.lower() == "unknown":
            return SimpleResult(ok=False, message="Site is unknown")

        # Ensure we have a site row.
        site = _upsert_site(session, site_name)

        # Ensure we have a streamer row bound to this site.
        streamer = session.scalar(select(Streamer).where(Streamer.name == streamer_name, Streamer.site_id == site.id))
        if not streamer:
            streamer = _upsert_streamer(session, streamer_name, site)

        fetched = fetch_streamer_info(site_name, streamer_name)
        if not fetched:
            return SimpleResult(ok=False, message=f"Fetch not supported or no info found for site '{site_name}'")

        payload = fetched.normalized()

        # Debug/trace: show retrieved info in server console.
        """
        try:
            if (site_name or "").strip().lower() == "stripchat":
                print(
                    f"[fetch_streamer_info] site={site_name} streamer={streamer_name} payload={json.dumps(payload, ensure_ascii=False)}",
                    flush=True,
                )
        except Exception:
            pass
        """
        streamer.info_json = json.dumps(payload, ensure_ascii=False)
        avatar = payload.get("avatar_url")
        if isinstance(avatar, str) and avatar.strip():
            streamer.avatar_url = avatar.strip()

        session.flush()
        logger.info("Streamer info fetched site=%s streamer=%s", site_name, streamer_name)
        return SimpleResult(ok=True, message="Streamer info fetched")

    @strawberry.mutation
    def start_bulk_fetch_missing_streamer_info(
        self,
        info: strawberry.Info[ContextDict, None],
        site_name: str | None = None,
    ) -> BulkFetchStreamerInfoJobType:
        s = info.context.get("settings")
        assert isinstance(s, AppSettings)

        canon = canonical_site_name(site_name) if site_name else None
        job = start_bulk_fetch_missing_streamer_info_job(s, site_name=(canon or "").strip() or None, concurrency=10)
        logger.info("User started bulk streamer info fetch job=%s site=%s", job.job_id, (canon or "all"))
        return _bulk_fetch_job_to_type(job)


schema = strawberry.Schema(query=Query, mutation=Mutation)
