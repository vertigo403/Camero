from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import AppSettings, CatalogFolder


REL_PATH_SEP = "::"


@dataclass(frozen=True)
class ParsedRelPath:
    folder_key: str | None
    rel_path: str


def parse_recording_rel_path(rel_path: str) -> ParsedRelPath:
    raw = (rel_path or "").strip()
    if REL_PATH_SEP in raw:
        key, rest = raw.split(REL_PATH_SEP, 1)
        key = key.strip()
        rest = rest.lstrip("/")
        if key and rest:
            return ParsedRelPath(folder_key=key, rel_path=rest)
    return ParsedRelPath(folder_key=None, rel_path=raw)


def encode_recording_rel_path(folder_key: str, relative_path: str) -> str:
    rel = (relative_path or "").replace("\\", "/").lstrip("/")
    return f"{folder_key}{REL_PATH_SEP}{rel}"


def get_folder_by_key(settings: AppSettings, key: str) -> CatalogFolder | None:
    for f in settings.catalog_folders or ():
        if f.key == key:
            return f
    return None


def _iter_candidate_bases(settings: AppSettings, parsed: ParsedRelPath) -> list[Path]:
    bases: list[Path] = []
    seen: set[str] = set()

    def _add(path: str | Path | None) -> None:
        if not path:
            return
        candidate = Path(path)
        key = str(candidate).lower()
        if key in seen:
            return
        seen.add(key)
        bases.append(candidate)

    if parsed.folder_key:
        folder = get_folder_by_key(settings, parsed.folder_key)
        if folder and folder.path:
            _add(folder.path)

    for folder in settings.catalog_folders or ():
        _add(folder.path)

    _add(settings.catalog_root)
    return bases


def _remap_absolute_legacy_path(base: Path, raw_path: str) -> Path | None:
    legacy_path = Path(raw_path)
    if not legacy_path.is_absolute():
        return None

    try:
        if legacy_path.exists():
            return legacy_path
    except Exception:
        pass

    base_parts = [part.lower() for part in base.parts[1:]]
    legacy_parts = list(legacy_path.parts[1:])
    legacy_parts_lower = [part.lower() for part in legacy_parts]
    if not base_parts or len(legacy_parts_lower) < len(base_parts):
        return None

    for idx in range(len(legacy_parts_lower) - len(base_parts) + 1):
        if legacy_parts_lower[idx : idx + len(base_parts)] != base_parts:
            continue
        suffix = legacy_parts[idx + len(base_parts) :]
        return base / Path(*suffix) if suffix else base

    return None


def resolve_recording_path(settings: AppSettings, recording_rel_path: str) -> Path | None:
    """Resolve a Recording.rel_path to an absolute path on disk.

    Back-compat:
    - Legacy records store a rel_path relative to settings.catalog_root.
    - New records store: <folderKey>::<relPath>
    """

    parsed = parse_recording_rel_path(recording_rel_path)

    raw_rel = parsed.rel_path
    raw_path = Path(raw_rel)
    fallback_candidate: Path | None = None

    if raw_path.is_absolute():
        remapped_bases = _iter_candidate_bases(settings, parsed)
        for base in remapped_bases:
            remapped = _remap_absolute_legacy_path(base, raw_rel)
            if remapped is None:
                continue
            if fallback_candidate is None:
                fallback_candidate = remapped
            try:
                if remapped.exists():
                    return remapped
            except Exception:
                continue

    for base in _iter_candidate_bases(settings, parsed):
        candidate = base / parsed.rel_path
        if fallback_candidate is None:
            fallback_candidate = candidate
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue

    if fallback_candidate is not None:
        return fallback_candidate

    return None
