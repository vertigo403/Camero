from __future__ import annotations

import re
from dataclasses import dataclass
import os
from typing import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from .media import VIDEO_EXTENSIONS, ensure_thumbnail, probe_media, quick_file_hash
from .models import Recording, RecordingOverride, RecordingTag, Site, Streamer
from .site_normalization import normalize_all_sites, upsert_site_canonical
from .settings import CatalogFolder
from .catalog_paths import encode_recording_rel_path
from .catalog_paths import parse_recording_rel_path


@dataclass(frozen=True)
class ScanResult:
    scanned_files: int
    added: int
    updated: int
    skipped: int
    errors: int


@dataclass(frozen=True)
class ScanProgress:
    phase: str = "scan"  # scan | live_previews (animated previews)
    total_files: int | None = None
    scanned_files: int = 0
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    current_file: str | None = None
    # During the animated preview generation phase, this points to the source recording being processed.
    current_recording: str | None = None
    total_previews: int | None = None
    generated_previews: int = 0
    failed_previews: int = 0
    current_preview: str | None = None


def _iter_video_files(root: Path):
    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except Exception:
            return 0.0

    # Important: never descend into the metadata folder.
    # It contains generated thumbnails/previews and must not be scanned.
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune `.camero` from traversal.
        dirnames[:] = [d for d in dirnames if d != ".camero"]

        base = Path(dirpath)

        # Traverse newest folders first to discover recent recordings sooner.
        dirnames[:] = sorted(dirnames, key=lambda d: (-_safe_mtime(base / d), d.lower()))

        # Process newest files first within each folder.
        video_files: list[Path] = []
        for name in filenames:
            path = base / name
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            video_files.append(path)

        video_files.sort(key=lambda p: (-_safe_mtime(p), p.name.lower()))
        yield from video_files


def _parse_recorded_at(date_str: str | None, time_str: str | None) -> datetime | None:
    if not date_str:
        return None

    # Support common patterns:
    # - Date: YYYYMMDD, YYYY-MM-DD, YYYY_MM_DD, DDMMYYYY, DD-MM-YYYY, etc.
    # - Time: HHMM, HHMMSS, HH-MM-SS, HH_MM_SS, etc.
    try:
        ds = (date_str or "").strip()
        ts = (time_str or "").strip()

        # Extract digits only.
        date_digits = re.sub(r"\D", "", ds)

        # Support:
        # - YYYYMMDD / YYYY-MM-DD / YYYY_MM_DD
        # - DDMMYYYY / DD-MM-YYYY / DD_MM_YYYY
        # - YYMMDD (token {YYMMDD})
        if len(date_digits) == 6:
            yy = int(date_digits[0:2])
            m = int(date_digits[2:4])
            d = int(date_digits[4:6])
            # Pivot year: 00-69 => 2000-2069, 70-99 => 1970-1999
            y = 1900 + yy if yy >= 70 else 2000 + yy
        elif len(date_digits) == 8:
            # Infer whether digits are YYYYMMDD or DDMMYYYY.
            y1 = int(date_digits[0:4])
            y2 = int(date_digits[4:8])

            ymd_first = 1900 <= y1 <= 2100
            dmy_last = 1900 <= y2 <= 2100

            if ymd_first and not dmy_last:
                y, m, d = y1, int(date_digits[4:6]), int(date_digits[6:8])
            elif dmy_last and not ymd_first:
                d, m, y = int(date_digits[0:2]), int(date_digits[2:4]), y2
            else:
                # Ambiguous: try YYYYMMDD first, then DDMMYYYY.
                try:
                    y, m, d = y1, int(date_digits[4:6]), int(date_digits[6:8])
                    datetime(y, m, d)
                except Exception:
                    d, m, y = int(date_digits[0:2]), int(date_digits[2:4]), y2
        else:
            return None

        hour = 0
        minute = 0
        second = 0
        if ts:
            time_digits = re.sub(r"\D", "", ts)
            if len(time_digits) == 2:
                hour = int(time_digits)
            elif len(time_digits) == 4:
                hour = int(time_digits[0:2])
                minute = int(time_digits[2:4])
            elif len(time_digits) == 6:
                hour = int(time_digits[0:2])
                minute = int(time_digits[2:4])
                second = int(time_digits[4:6])
            else:
                # Unsupported time shape.
                hour = 0
                minute = 0
                second = 0

        return datetime(int(y), int(m), int(d), int(hour), int(minute), int(second))
    except Exception:
        return None


def _upsert_site(session: Session, name: str) -> Site:
    # Normalize case variants (e.g. CAM4/cam4/Cam4) into a single canonical site.
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


def _has_cached_thumbnail(meta_root: Path, rec: Recording) -> bool:
    thumb_rel = getattr(rec, "thumbnail_rel_path", None)
    if not isinstance(thumb_rel, str) or not thumb_rel.strip():
        return False

    thumb_path = meta_root / thumb_rel
    try:
        return thumb_path.exists() and thumb_path.is_file() and thumb_path.stat().st_size > 0
    except Exception:
        return False


def _is_recording_unchanged(rec: Recording, *, size_bytes: int | None, file_mtime: float | None, meta_root: Path) -> bool:
    existing_size = getattr(rec, "size_bytes", None)
    existing_mtime = getattr(rec, "file_mtime", None)
    if size_bytes is None or file_mtime is None:
        return False
    if existing_size is None or not isinstance(existing_mtime, (int, float)):
        return False
    if int(existing_size) != int(size_bytes):
        return False
    if abs(float(existing_mtime) - float(file_mtime)) >= 0.0001:
        return False
    return _has_cached_thumbnail(meta_root, rec)


def _find_recording_with_matching_rel_path(
    session: Session, *, file_name: str, relative_path: str
) -> Recording | None:
    matches = session.scalars(select(Recording).where(Recording.file_name == file_name)).all()
    candidates = [rec for rec in matches if parse_recording_rel_path(str(rec.rel_path)).rel_path == relative_path]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_existing_file_under_active_bases(*, meta_root: Path, active_bases: list[Path], relative_path: str) -> Path | None:
    candidates: list[Path] = []
    seen: set[str] = set()

    for base in [meta_root, *active_bases]:
        key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(base)

    for base in candidates:
        abs_path = base / relative_path
        try:
            if abs_path.exists() and abs_path.is_file():
                return abs_path
        except Exception:
            continue

    return None


def scan_catalog(
    *,
    catalog_folders: list[CatalogFolder],
    catalog_root: str,
    session: Session,
    progress_cb: Callable[[ScanProgress], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> ScanResult:
    """Scan all configured catalog folders.

    `catalog_root` is the primary folder used for `.camero/*` metadata (thumbs, previews, DB).
    Each scan folder can provide its own filename regex.
    """

    if not catalog_folders:
        raise ValueError("No catalog folders configured")

    meta_root = Path(catalog_root)
    if not meta_root.exists() or not meta_root.is_dir():
        raise ValueError("Catalog root does not exist or is not a directory")

    # Validate and compile regex per folder.
    compiled: list[tuple[CatalogFolder, Path, re.Pattern[str]]] = []
    for f in catalog_folders:
        root = Path(f.path)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Catalog folder does not exist or is not a directory: {f.path}")
        try:
            pattern = re.compile(f.filename_regex)
        except re.error as e:
            raise ValueError(f"Invalid filename regex for folder {f.path}: {e}")
        compiled.append((f, root, pattern))

    # If a folder is removed from settings, purge recordings that belonged to it.
    # This avoids stale DB entries pointing to folders that are no longer part of the catalog.
    active_keys = {f.key for f in catalog_folders if getattr(f, "key", None)}
    folder_roots_by_key = {f.key: Path(f.path) for f in (catalog_folders or ())}
    active_folder_roots = list(folder_roots_by_key.values())

    # Also purge recordings whose underlying video file was deleted outside the app.
    # This keeps DB/UI consistent with the filesystem.
    rows = session.execute(select(Recording.id, Recording.rel_path)).all()
    stale_ids: list[int] = []
    for rid, rel_path in rows:
        rid_i = int(rid)
        parsed = parse_recording_rel_path(str(rel_path))

        # Removed folder.
        if parsed.folder_key and parsed.folder_key not in active_keys:
            migrated_path = _find_existing_file_under_active_bases(
                meta_root=meta_root,
                active_bases=active_folder_roots,
                relative_path=parsed.rel_path,
            )
            if migrated_path is None:
                stale_ids.append(rid_i)
            continue

        # Missing file on disk.
        base: Path | None
        if parsed.folder_key:
            base = folder_roots_by_key.get(parsed.folder_key)
        else:
            base = meta_root

        # Be conservative if the base folder itself is unavailable.
        if base is None or not base.exists():
            continue

        abs_path = base / parsed.rel_path
        try:
            if not abs_path.exists() or not abs_path.is_file():
                stale_ids.append(rid_i)
        except Exception:
            # If we can't stat the file, treat it as missing.
            stale_ids.append(rid_i)

    if stale_ids:
        # Delete tag links first to keep FK constraints happy (when enabled).
        session.execute(delete(RecordingTag).where(RecordingTag.recording_id.in_(stale_ids)))
        session.execute(delete(Recording).where(Recording.id.in_(stale_ids)))
        session.flush()

    # Normalize/merge site case variants so the UI treats them as one site.
    # (e.g. CAM4/cam4/Cam4 -> Cam4)
    try:
        normalize_all_sites(session)
        session.flush()
    except Exception:
        # Best-effort: scan should still proceed.
        pass

        # Clean up orphaned streamers/sites. Tags are kept (users may want to reuse them).
        session.execute(delete(Streamer).where(~exists(select(1).where(Recording.streamer_id == Streamer.id))))
        session.execute(
            delete(Site).where(
                ~exists(select(1).where(Recording.site_id == Site.id))
                & ~exists(select(1).where(Streamer.site_id == Site.id))
            )
        )
        session.flush()

    total_files: int | None = None
    if progress_cb is not None:
        # Two-pass scan so we can provide a real progress %.
        total_files = 0
        for _f, root, _pat in compiled:
            for _ in _iter_video_files(root):
                if cancel_cb is not None and cancel_cb():
                    return ScanResult(scanned_files=0, added=0, updated=0, skipped=0, errors=0)
                total_files += 1

        progress_cb(
            ScanProgress(
                total_files=total_files,
                scanned_files=0,
                added=0,
                updated=0,
                skipped=0,
                errors=0,
                current_file=None,
            )
        )

    scanned_files = 0
    added = 0
    updated = 0
    skipped = 0
    errors = 0

    for folder, root, pattern in compiled:
        for path in _iter_video_files(root):

            if cancel_cb is not None and cancel_cb():
                return ScanResult(
                    scanned_files=scanned_files,
                    added=added,
                    updated=updated,
                    skipped=skipped,
                    errors=errors,
                )

            scanned_files += 1
            try:
                candidate = path.name
                if bool(getattr(folder, "match_path", False)):
                    candidate = str(path.relative_to(root))

                m = pattern.match(candidate)
                if not m:
                    skipped += 1
                    continue

                streamer_name = (m.groupdict().get("streamer") or "").strip()
                site_name = (m.groupdict().get("site") or "").strip()
                date_str = (m.groupdict().get("date") or "").strip()
                time_str = (m.groupdict().get("time") or "").strip()

                # If filenames follow the common pattern:
                #   <streamer>_<site>_<date>_<time>.<ext>
                # then streamer may contain underscores. Some user-configured
                # regex patterns use non-greedy groups and will split at the
                # first '_' producing a wrong site.
                #
                # When we're matching the filename only (not a relative path),
                # we can recover streamer/site reliably by anchoring on where
                # the date group starts.
                #
                # Also support the common pattern without site:
                #   <streamer>_<YYYY-MM-DD>_<HH-MM-SS>_<wildcard>.<ext>
                # In that case, everything before the date is streamer.
                if not bool(getattr(folder, "match_path", False)) and date_str and time_str:
                    try:
                        start_date = m.start("date")
                        if start_date > 0 and candidate[start_date - 1] == "_":
                            prefix = candidate[: start_date - 1]
                            # Pattern without site uses hyphenated date/time.
                            if "-" in date_str and "-" in time_str:
                                recovered_streamer = prefix.strip()
                                if recovered_streamer:
                                    streamer_name = recovered_streamer
                                    # Only clear site if the regex does not define a `site` group.
                                    # Some patterns include site *after* the time:
                                    #   <streamer>_<YYYY-MM-DD>_<HH-MM-SS>_<site>.<ext>
                                    # In that case, `site` is correctly captured by the regex and
                                    # must not be overwritten.
                                    if "site" not in getattr(m.re, "groupindex", {}):
                                        site_name = ""
                            else:
                                # Pattern with site: split prefix on last '_' only.
                                if "_" in prefix:
                                    recovered_streamer, recovered_site = prefix.rsplit("_", 1)
                                    recovered_streamer = recovered_streamer.strip()
                                    recovered_site = recovered_site.strip()
                                    if recovered_streamer and recovered_site:
                                        streamer_name = recovered_streamer
                                        site_name = recovered_site
                    except Exception:
                        pass

                legacy_rel = str(path.relative_to(root)).replace("\\", "/")
                rel_path = encode_recording_rel_path(folder.key, legacy_rel)
                rec = session.scalar(select(Recording).where(Recording.rel_path == rel_path))
                rel_path_changed = False
                # Back-compat migration: if this is the primary folder and a legacy row exists,
                # update it in-place to the new rel_path format.
                if rec is None and str(root) == str(meta_root):
                    legacy = session.scalar(select(Recording).where(Recording.rel_path == legacy_rel))
                    if legacy is not None:
                        legacy.rel_path = rel_path
                        rec = legacy
                        rel_path_changed = True

                if rec is None:
                    keyed_match = _find_recording_with_matching_rel_path(
                        session,
                        file_name=path.name,
                        relative_path=legacy_rel,
                    )
                    if keyed_match is not None:
                        keyed_match.rel_path = rel_path
                        rec = keyed_match
                        rel_path_changed = True

                st_mtime: float | None = None
                st_size: int | None = None
                try:
                    stat_result = path.stat()
                    st_mtime = float(stat_result.st_mtime)
                    st_size = int(stat_result.st_size)
                except Exception:
                    st_mtime = None
                    st_size = None

                if rec is None:
                    same_name_matches = session.scalars(
                        select(Recording).where(Recording.file_name == path.name)
                    ).all()
                    if len(same_name_matches) == 1:
                        same_name_rec = same_name_matches[0]
                        existing_size = getattr(same_name_rec, "size_bytes", None)
                        scanned_size = st_size
                        if (
                            existing_size is not None
                            and scanned_size is not None
                            and int(existing_size) != int(scanned_size)
                        ):
                            same_name_rec.rel_path = rel_path
                            rec = same_name_rec
                            rel_path_changed = True

                if rec is not None and not rel_path_changed and _is_recording_unchanged(
                    rec,
                    size_bytes=st_size,
                    file_mtime=st_mtime,
                    meta_root=meta_root,
                ):
                    skipped += 1
                    if progress_cb is not None:
                        current_rel = str(path.relative_to(root)).replace("\\", "/")
                        progress_cb(
                            ScanProgress(
                                total_files=total_files,
                                scanned_files=scanned_files,
                                added=added,
                                updated=updated,
                                skipped=skipped,
                                errors=errors,
                                current_file=f"[{folder.key}] {current_rel}",
                            )
                        )
                    continue

                site = _upsert_site(session, site_name) if site_name else None

                info = probe_media(path)

                recorded_at = _parse_recorded_at(date_str, time_str)

                # Hash/mtime: used to key artifacts (thumbs/previews) so they can be reused
                # across DB rebuilds.
                file_hash: str | None = None
                try:
                    existing_hash = getattr(rec, "file_hash", None) if rec is not None else None
                    existing_mtime = getattr(rec, "file_mtime", None) if rec is not None else None
                    existing_size = getattr(rec, "size_bytes", None) if rec is not None else None
                    if (
                        isinstance(existing_hash, str)
                        and existing_hash.strip()
                        and existing_size is not None
                        and info.size_bytes is not None
                        and int(existing_size) == int(info.size_bytes)
                        and st_mtime is not None
                        and isinstance(existing_mtime, (int, float))
                        and abs(float(existing_mtime) - float(st_mtime)) < 0.0001
                    ):
                        file_hash = existing_hash.strip().lower()
                    else:
                        computed = quick_file_hash(path)
                        if isinstance(computed, str) and computed.strip():
                            file_hash = computed.strip().lower()
                except Exception:
                    file_hash = None

                is_new = rec is None

                if rec is None:
                    site_id_for_rec = site.id if site else None
                    site_for_streamer = site
                    streamer = _upsert_streamer(session, streamer_name, site_for_streamer) if streamer_name else None
                    rec = Recording(
                        rel_path=rel_path,
                        file_name=path.name,
                        title=path.stem,
                        streamer_id=streamer.id if streamer else None,
                        site_id=site_id_for_rec,
                        recorded_at=recorded_at,
                        duration_seconds=info.duration_seconds,
                        width=info.width,
                        height=info.height,
                        size_bytes=info.size_bytes,
                        video_codec=info.video_codec,
                        audio_codec=info.audio_codec,
                    )
                    session.add(rec)
                    session.flush()
                    added += 1

                    if file_hash:
                        try:
                            rec.file_hash = file_hash
                            if st_mtime is not None:
                                rec.file_mtime = float(st_mtime)
                        except Exception:
                            pass

                    # No-op: thumbs/previews are keyed by file hash, so DB id reuse is not an issue.
                else:
                    ov = session.get(RecordingOverride, rec.id)
                    rec.file_name = path.name
                    rec.title = path.stem

                    # Site: prefer override, otherwise parsed value (may be None for patterns without site).
                    if ov is not None and getattr(ov, "site_set", False):
                        site_id_for_rec = ov.site_id
                    else:
                        site_id_for_rec = site.id if site else None
                    rec.site_id = site_id_for_rec

                    # Streamer: prefer override, otherwise bind to the effective site.
                    if ov is not None and getattr(ov, "streamer_set", False):
                        rec.streamer_id = ov.streamer_id
                    else:
                        site_for_streamer = session.get(Site, int(site_id_for_rec)) if site_id_for_rec is not None else None
                        streamer = _upsert_streamer(session, streamer_name, site_for_streamer) if streamer_name else None
                        rec.streamer_id = streamer.id if streamer else None

                    if ov is not None and getattr(ov, "recorded_at_set", False):
                        rec.recorded_at = ov.recorded_at
                    else:
                        rec.recorded_at = recorded_at
                    rec.duration_seconds = info.duration_seconds
                    rec.width = info.width
                    rec.height = info.height
                    rec.size_bytes = info.size_bytes
                    rec.video_codec = info.video_codec
                    rec.audio_codec = info.audio_codec
                    updated += 1

                    if file_hash:
                        try:
                            rec.file_hash = file_hash
                            if st_mtime is not None:
                                rec.file_mtime = float(st_mtime)
                        except Exception:
                            pass

                # Thumbnail (always stored under primary catalog root).
                # IMPORTANT: if we can't extract a real thumbnail for a newly discovered recording
                # (e.g. file is corrupt or locked by another program), do not add it to the catalog.
                if not file_hash:
                    if is_new:
                        # Roll back this new recording entry.
                        try:
                            session.delete(rec)
                            session.flush()
                        except Exception:
                            pass
                        added = max(0, added - 1)
                        errors += 1
                        continue
                    # Existing row: keep metadata but do not touch thumbnail.
                    continue

                thumb_name = f"{file_hash}.jpg"
                thumb_path = (meta_root / ".camero" / "thumbs" / thumb_name)
                thumb_rel = f".camero/thumbs/{thumb_name}"

                force_thumb = bool(is_new)
                if file_hash:
                    # If we're rebuilding the DB, we may already have a thumb for this file.
                    # Avoid overwriting/regenerating it.
                    try:
                        if thumb_path.exists() and thumb_path.stat().st_size > 0:
                            force_thumb = False
                    except Exception:
                        pass

                ok_thumb = ensure_thumbnail(
                    path,
                    thumb_path,
                    title=rec.title,
                    force=force_thumb,
                    allow_placeholder=not bool(is_new),
                )

                if ok_thumb:
                    rec.thumbnail_rel_path = thumb_rel
                elif is_new:
                    # Roll back this new recording entry.
                    try:
                        session.delete(rec)
                        session.flush()
                    except Exception:
                        # If deletion fails, keep scan resilient.
                        pass

                    added = max(0, added - 1)
                    errors += 1
                    continue

            except Exception:
                errors += 1

            if progress_cb is not None:
                current_rel = str(path.relative_to(root)).replace("\\", "/")
                progress_cb(
                    ScanProgress(
                        total_files=total_files,
                        scanned_files=scanned_files,
                        added=added,
                        updated=updated,
                        skipped=skipped,
                        errors=errors,
                        current_file=f"[{folder.key}] {current_rel}",
                    )
                )

    return ScanResult(
        scanned_files=scanned_files,
        added=added,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )
