from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import uuid

from .volume_ids import find_mount_point_for_volume_id, iter_mount_points, split_path_volume_relative


DEFAULT_FILENAME_REGEX = (
    # Default format: <streamer>_<site>_<YYYYMMDD>_<time>.<ext>
    # Streamer names may contain underscores, so streamer is greedy and we
    # anchor parsing with the date/time suffix.
    r"^(?P<streamer>[^/\\]+)_(?P<site>[^_/\\]+)_(?P<date>\d{8})_(?P<time>[^.]+)\.(?P<ext>[^.]+)$"
)


_FILENAME_INFO_GROUPS = {"streamer", "site", "date", "time"}

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 6969
DEFAULT_AUTH_REQUIRED = False
DEFAULT_AUTH_USERNAME = ""
DEFAULT_AUTH_PASSWORD = ""
DEFAULT_PERIODIC_SCAN_ENABLED = False
DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES = 30
DEFAULT_AUTO_TAG_AFTER_SCAN = False
DEFAULT_AUTO_TAG_CONFIDENCE = 0.6
DEFAULT_AUTO_TAG_SCENE_THRESHOLD = 0.35
DEFAULT_AUTO_TAG_FRAME_INTERVAL = "auto"


def _is_valid_filename_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


@dataclass(frozen=True)
class CatalogFolder:
    key: str
    path: str
    filename_regex: str = DEFAULT_FILENAME_REGEX
    filename_template: str | None = None
    match_path: bool = False


@dataclass(frozen=True)
class AppSettings:
    catalog_root: str | None = None
    filename_regex: str = DEFAULT_FILENAME_REGEX
    catalog_folders: tuple[CatalogFolder, ...] = ()
    generate_live_previews: bool = True
    live_preview_segments: int = 10
    periodic_scan_enabled: bool = DEFAULT_PERIODIC_SCAN_ENABLED
    periodic_scan_interval_minutes: int = DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES
    auto_tag_after_scan: bool = DEFAULT_AUTO_TAG_AFTER_SCAN
    auto_tag_confidence: float = DEFAULT_AUTO_TAG_CONFIDENCE
    auto_tag_scene_threshold: float = DEFAULT_AUTO_TAG_SCENE_THRESHOLD
    auto_tag_frame_interval: str = DEFAULT_AUTO_TAG_FRAME_INTERVAL
    backend_host: str = DEFAULT_BACKEND_HOST
    backend_port: int = DEFAULT_BACKEND_PORT
    auth_required: bool = DEFAULT_AUTH_REQUIRED
    auth_username: str = DEFAULT_AUTH_USERNAME
    auth_password: str = DEFAULT_AUTH_PASSWORD


_ALLOWED_LIVE_PREVIEW_SEGMENTS: set[int] = {5, 10, 15, 20}


def _coerce_live_preview_segments(value: object, *, default: int = 10) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except Exception:
        n = int(default)
    if n not in _ALLOWED_LIVE_PREVIEW_SEGMENTS:
        return int(default)
    return n


def _coerce_backend_host(value: object, *, default: str = DEFAULT_BACKEND_HOST) -> str:
    if not isinstance(value, str):
        return str(default)
    host = value.strip()
    return host or str(default)


def _coerce_backend_port(value: object, *, default: int = DEFAULT_BACKEND_PORT) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except Exception:
        n = int(default)
    if n < 1 or n > 65535:
        return int(default)
    return n


def _coerce_auth_required(value: object, *, default: bool = DEFAULT_AUTH_REQUIRED) -> bool:
    if isinstance(value, bool):
        return value
    return bool(default)


def _coerce_auth_credential(value: object, *, default: str = "") -> str:
    if not isinstance(value, str):
        return str(default)
    return value.strip()


def _coerce_periodic_scan_enabled(value: object, *, default: bool = DEFAULT_PERIODIC_SCAN_ENABLED) -> bool:
    if isinstance(value, bool):
        return value
    return bool(default)


def _coerce_periodic_scan_interval_minutes(
    value: object, *, default: int = DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES
) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except Exception:
        n = int(default)
    if n < 1 or n > 1440:
        return int(default)
    return n


def _coerce_auto_tag_confidence(value: object, *, default: float = DEFAULT_AUTO_TAG_CONFIDENCE) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except Exception:
        return float(default)
    if f < 0.01 or f > 1.0:
        return float(default)
    return round(f, 4)


def _coerce_auto_tag_scene_threshold(value: object, *, default: float = DEFAULT_AUTO_TAG_SCENE_THRESHOLD) -> float:
    try:
        f = float(value)  # type: ignore[arg-type]
    except Exception:
        return float(default)
    if f < 0.0 or f > 1.0:
        return float(default)
    return round(f, 4)


def _coerce_auto_tag_frame_interval(value: object, *, default: str = DEFAULT_AUTO_TAG_FRAME_INTERVAL) -> str:
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto"
    try:
        f = float(value)  # type: ignore[arg-type]
        if f <= 0:
            return str(default)
        return str(round(f, 2))
    except Exception:
        return str(default)


@dataclass(frozen=True)
class CatalogRef:
    """Stable reference to a catalog location.

    On Windows removable drives, drive letters are not stable.
    We store a stable volume id (Volume GUID) plus a path relative
    to the volume root, so we can re-locate the catalog when the
    same disk appears under a different letter.
    """

    catalog_id: str | None = None
    volume_id: str | None = None
    relative_path: str | None = None
    fallback_path: str | None = None


def _new_folder_key() -> str:
    # Short, stable id for prefixing rel_path in DB.
    return uuid.uuid4().hex[:10]


def _coerce_catalog_folders(data: object) -> tuple[CatalogFolder, ...]:
    if not isinstance(data, list):
        return ()

    out: list[CatalogFolder] = []
    seen_keys: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_key = item.get("key")
        raw_path = item.get("path")
        raw_regex = item.get("filename_regex")
        raw_template = item.get("filename_template")
        if raw_template is None:
            # Back-compat: tolerate camelCase keys if any old writer used them.
            raw_template = item.get("filenameTemplate")
        raw_match_path = item.get("match_path")

        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = raw_path.strip()

        key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else _new_folder_key()
        if key in seen_keys:
            # Ensure uniqueness.
            key = _new_folder_key()
        seen_keys.add(key)

        filename_regex = raw_regex if isinstance(raw_regex, str) and raw_regex.strip() else DEFAULT_FILENAME_REGEX
        filename_template = raw_template.strip() if isinstance(raw_template, str) and raw_template.strip() else None
        match_path = bool(raw_match_path) if isinstance(raw_match_path, bool) else False
        if not _is_valid_filename_regex(filename_regex):
            filename_regex = DEFAULT_FILENAME_REGEX

        # Best-effort sanity check for stored values.
        try:
            p = Path(path)
            if not p.exists() or not p.is_dir():
                continue
        except Exception:
            continue

        out.append(
            CatalogFolder(
                key=key,
                path=str(Path(path)),
                filename_regex=filename_regex,
                filename_template=filename_template,
                match_path=match_path,
            )
        )

    return tuple(out)


def _settings_path() -> Path:
    """Return the path to the global app settings file.

    - In development: keep settings alongside the project (cwd).
    - In PyInstaller/"frozen" builds: store settings in a per-user writable
      location (AppData) to avoid permission and packaging constraints.

    Users can override this via the CAMERO_SETTINGS_PATH environment variable.
    """

    explicit = os.environ.get("CAMERO_SETTINGS_PATH")
    if explicit:
        return Path(explicit)

    is_frozen = bool(getattr(sys, "frozen", False))
    if not is_frozen:
        # Stored alongside the project by default.
        return Path.cwd() / "camero_settings.json"

    # Frozen (.exe): default to portable settings next to the executable.
    # This makes the app portable when the folder is writable.
    try:
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir / "camero_settings.json"
    except Exception:
        # Fall back to a per-user config directory.
        return _user_settings_path()


def _user_settings_path() -> Path:
    # Per-user settings path (fallback when portable location is not writable).
    local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local_app_data:
        config_dir = Path(local_app_data) / "Camero"
    else:
        # Fallback for non-Windows / weird envs.
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        config_dir = Path(xdg_config_home) if xdg_config_home else (Path.home() / ".config")
        config_dir = config_dir / "camero"
    return config_dir / "settings.json"


def _catalog_settings_path(catalog_root: str | Path) -> Path:
    root = Path(catalog_root)
    return root / ".camero" / "settings.json"


def _catalog_id_path(catalog_root: str | Path) -> Path:
    root = Path(catalog_root)
    return root / ".camero" / "catalog_id"


def _read_catalog_id(catalog_root: str | Path) -> str | None:
    p = _catalog_id_path(catalog_root)
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        return raw or None
    except Exception:
        return None


def _ensure_catalog_id(catalog_root: str | Path) -> str:
    root = Path(catalog_root)
    meta_dir = root / ".camero"
    meta_dir.mkdir(parents=True, exist_ok=True)

    existing = _read_catalog_id(root)
    if existing:
        return existing

    cid = uuid.uuid4().hex
    _catalog_id_path(root).write_text(cid + "\n", encoding="utf-8")
    return cid


def _load_json_first_existing(paths: list[Path]) -> dict[str, Any] | None:
    data: dict[str, Any] | None = None
    for path in paths:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
                break
        except Exception:
            data = None
    return data


def _write_json_with_fallback(payload: dict[str, Any]) -> None:
    path = _settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return
    except OSError:
        if os.environ.get("CAMERO_SETTINGS_PATH"):
            raise

        fallback = _user_settings_path()
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return


_MISSING = object()


def _build_global_settings_payload(
    *,
    active_catalog: object = _MISSING,
    backend_host: object = _MISSING,
    backend_port: object = _MISSING,
    auth_required: object = _MISSING,
    auth_username: object = _MISSING,
    auth_password: object = _MISSING,
) -> dict[str, Any]:
    current = _load_json_first_existing([_settings_path(), _user_settings_path()])
    if not isinstance(current, dict):
        current = {}

    payload: dict[str, Any] = {
        "schema_version": 2,
        "active_catalog": current.get("active_catalog") if "active_catalog" in current else None,
        "backend_host": _coerce_backend_host(current.get("backend_host"), default=DEFAULT_BACKEND_HOST),
        "backend_port": _coerce_backend_port(current.get("backend_port"), default=DEFAULT_BACKEND_PORT),
        "auth_required": _coerce_auth_required(current.get("auth_required"), default=DEFAULT_AUTH_REQUIRED),
        "auth_username": _coerce_auth_credential(current.get("auth_username"), default=DEFAULT_AUTH_USERNAME),
        "auth_password": _coerce_auth_credential(current.get("auth_password"), default=DEFAULT_AUTH_PASSWORD),
    }

    if active_catalog is not _MISSING:
        payload["active_catalog"] = active_catalog
    if backend_host is not _MISSING:
        payload["backend_host"] = _coerce_backend_host(backend_host, default=DEFAULT_BACKEND_HOST)
    if backend_port is not _MISSING:
        payload["backend_port"] = _coerce_backend_port(backend_port, default=DEFAULT_BACKEND_PORT)
    if auth_required is not _MISSING:
        payload["auth_required"] = _coerce_auth_required(auth_required, default=DEFAULT_AUTH_REQUIRED)
    if auth_username is not _MISSING:
        payload["auth_username"] = _coerce_auth_credential(auth_username, default=DEFAULT_AUTH_USERNAME)
    if auth_password is not _MISSING:
        payload["auth_password"] = _coerce_auth_credential(auth_password, default=DEFAULT_AUTH_PASSWORD)

    return payload


def _resolve_catalog_ref(ref: CatalogRef | None) -> str | None:
    if ref is None:
        return None

    # Prefer stable resolution via volume id.
    if ref.volume_id and ref.relative_path is not None:
        mp = find_mount_point_for_volume_id(ref.volume_id)
        if mp:
            base = Path(mp)
            rel = (ref.relative_path or "").replace("\\", "/").lstrip("/")
            return str((base / Path(rel)).resolve()) if rel else str(base.resolve())

    # Cross-platform: if we have a catalog_id + relative_path, try all current mount points.
    if ref.catalog_id and ref.relative_path is not None:
        rel = (ref.relative_path or "").replace("\\", "/").lstrip("/")
        for mp in iter_mount_points():
            try:
                base = Path(mp)
                candidate = (base / Path(rel)) if rel else base
                if not candidate.exists() or not candidate.is_dir():
                    continue
                cid = _read_catalog_id(candidate)
                if cid and cid == ref.catalog_id:
                    return str(candidate.resolve())
            except Exception:
                continue

    # Fallback to last known absolute path.
    if ref.fallback_path:
        return str(Path(ref.fallback_path))

    return None


def _catalog_ref_from_path(path: str) -> CatalogRef:
    try:
        resolved = Path(path).resolve()
    except Exception:
        resolved = Path(path)

    p = str(resolved)
    # Ensure the catalog has a stable ID stored *inside* the catalog.
    catalog_id = None
    try:
        catalog_id = _ensure_catalog_id(resolved)
    except Exception:
        catalog_id = None

    volume_id, rel = split_path_volume_relative(p)
    return CatalogRef(catalog_id=catalog_id, volume_id=volume_id, relative_path=rel, fallback_path=p)


def _coerce_catalog_folders_for_catalog(data: object, *, catalog_root: str) -> tuple[CatalogFolder, ...]:
    if not isinstance(data, list):
        return ()

    root = Path(catalog_root)

    out: list[CatalogFolder] = []
    seen_keys: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_key = item.get("key")
        raw_path = item.get("path")
        raw_regex = item.get("filename_regex")
        raw_template = item.get("filename_template")
        if raw_template is None:
            raw_template = item.get("filenameTemplate")
        raw_match_path = item.get("match_path")

        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        raw_path_s = raw_path.strip()
        p = Path(raw_path_s)
        abs_path = (root / p) if not p.is_absolute() else p

        key = raw_key.strip() if isinstance(raw_key, str) and raw_key.strip() else _new_folder_key()
        if key in seen_keys:
            key = _new_folder_key()
        seen_keys.add(key)

        filename_regex = raw_regex if isinstance(raw_regex, str) and raw_regex.strip() else DEFAULT_FILENAME_REGEX
        filename_template = raw_template.strip() if isinstance(raw_template, str) and raw_template.strip() else None
        match_path = bool(raw_match_path) if isinstance(raw_match_path, bool) else False
        if not _is_valid_filename_regex(filename_regex):
            filename_regex = DEFAULT_FILENAME_REGEX

        try:
            if not abs_path.exists() or not abs_path.is_dir():
                continue
        except Exception:
            continue

        out.append(
            CatalogFolder(
                key=key,
                path=str(abs_path),
                filename_regex=filename_regex,
                filename_template=filename_template,
                match_path=match_path,
            )
        )

    return tuple(out)


def _encode_catalog_folder_path_for_storage(*, catalog_root: str, folder_path: str) -> str:
    root = Path(catalog_root)
    p = Path(folder_path)
    try:
        rel = p.relative_to(root)
        rel_str = str(rel).replace("\\", "/")
        return rel_str or "."
    except Exception:
        return str(p)


def _ensure_catalog_settings_file_exists(catalog_root: str) -> None:
    root = Path(catalog_root)
    meta_dir = root / ".camero"
    meta_dir.mkdir(parents=True, exist_ok=True)
    _ensure_catalog_id(root)
    cfg_path = _catalog_settings_path(root)
    if cfg_path.exists():
        return

    # Default: a single scan folder equal to the catalog root itself.
    payload: dict[str, Any] = {
        "schema_version": 1,
        "catalog_folders": [
            {
                "key": _new_folder_key(),
                "path": ".",
                "filename_regex": DEFAULT_FILENAME_REGEX,
                "match_path": False,
            }
        ],
        "generate_live_previews": True,
        "live_preview_segments": 10,
        "periodic_scan_enabled": DEFAULT_PERIODIC_SCAN_ENABLED,
        "periodic_scan_interval_minutes": DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
        "auto_tag_after_scan": DEFAULT_AUTO_TAG_AFTER_SCAN,
        "auto_tag_confidence": DEFAULT_AUTO_TAG_CONFIDENCE,
        "auto_tag_scene_threshold": DEFAULT_AUTO_TAG_SCENE_THRESHOLD,
        "auto_tag_frame_interval": DEFAULT_AUTO_TAG_FRAME_INTERVAL,
    }
    cfg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_catalog_settings_for_root(catalog_root: str) -> dict[str, Any] | None:
    cfg_path = _catalog_settings_path(catalog_root)
    return _load_json_first_existing([cfg_path])


def set_active_catalog_root(catalog_root: str) -> AppSettings:
    """Set the active catalog and ensure local catalog settings exist."""

    p = Path(catalog_root)
    if not p.exists() or not p.is_dir():
        raise ValueError("Catalog root must be an existing directory")

    ref = _catalog_ref_from_path(str(p))
    payload = _build_global_settings_payload(
        active_catalog={
            "catalog_id": ref.catalog_id,
            "volume_id": ref.volume_id,
            "relative_path": ref.relative_path,
            "fallback_path": ref.fallback_path,
        }
    )
    _write_json_with_fallback(payload)

    _ensure_catalog_settings_file_exists(str(p))
    return load_settings()


def clear_active_catalog() -> None:
    payload = _build_global_settings_payload(active_catalog=None)
    _write_json_with_fallback(payload)


def load_settings() -> AppSettings:
    primary_path = _settings_path()
    fallback_path = _user_settings_path()
    data = _load_json_first_existing([primary_path, fallback_path])
    if not isinstance(data, dict):
        return AppSettings()

    backend_host = _coerce_backend_host(data.get("backend_host"), default=DEFAULT_BACKEND_HOST)
    backend_port = _coerce_backend_port(data.get("backend_port"), default=DEFAULT_BACKEND_PORT)
    auth_required = _coerce_auth_required(data.get("auth_required"), default=DEFAULT_AUTH_REQUIRED)
    auth_username = _coerce_auth_credential(data.get("auth_username"), default=DEFAULT_AUTH_USERNAME)
    auth_password = _coerce_auth_credential(data.get("auth_password"), default=DEFAULT_AUTH_PASSWORD)

    # New schema (v2): app settings only contain a stable reference to active catalog.
    if "active_catalog" in data:
        raw_ref = data.get("active_catalog")
        ref: CatalogRef | None = None
        if isinstance(raw_ref, dict):
            ref = CatalogRef(
                catalog_id=raw_ref.get("catalog_id") if isinstance(raw_ref.get("catalog_id"), str) else None,
                volume_id=raw_ref.get("volume_id") if isinstance(raw_ref.get("volume_id"), str) else None,
                relative_path=raw_ref.get("relative_path") if isinstance(raw_ref.get("relative_path"), str) else None,
                fallback_path=raw_ref.get("fallback_path") if isinstance(raw_ref.get("fallback_path"), str) else None,
            )
        catalog_root = _resolve_catalog_ref(ref)
        if not catalog_root:
            return AppSettings(
                backend_host=backend_host,
                backend_port=backend_port,
                auth_required=auth_required,
                auth_username=auth_username,
                auth_password=auth_password,
            )

        try:
            root = Path(catalog_root)
            if not root.exists() or not root.is_dir():
                return AppSettings(
                    backend_host=backend_host,
                    backend_port=backend_port,
                    auth_required=auth_required,
                    auth_username=auth_username,
                    auth_password=auth_password,
                )
        except Exception:
            return AppSettings(
                backend_host=backend_host,
                backend_port=backend_port,
                auth_required=auth_required,
                auth_username=auth_username,
                auth_password=auth_password,
            )

        # Load per-catalog settings from <catalog_root>/.camero/settings.json
        _ensure_catalog_settings_file_exists(str(root))
        cdata = _load_catalog_settings_for_root(str(root)) or {}

        generate_live_previews = cdata.get("generate_live_previews")
        if not isinstance(generate_live_previews, bool):
            generate_live_previews = False

        live_preview_segments = _coerce_live_preview_segments(cdata.get("live_preview_segments"), default=10)
        periodic_scan_enabled = _coerce_periodic_scan_enabled(
            cdata.get("periodic_scan_enabled"), default=DEFAULT_PERIODIC_SCAN_ENABLED
        )
        periodic_scan_interval_minutes = _coerce_periodic_scan_interval_minutes(
            cdata.get("periodic_scan_interval_minutes"),
            default=DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
        )
        auto_tag_after_scan = bool(cdata.get("auto_tag_after_scan", DEFAULT_AUTO_TAG_AFTER_SCAN))
        auto_tag_confidence = _coerce_auto_tag_confidence(
            cdata.get("auto_tag_confidence"), default=DEFAULT_AUTO_TAG_CONFIDENCE
        )
        auto_tag_scene_threshold = _coerce_auto_tag_scene_threshold(
            cdata.get("auto_tag_scene_threshold"), default=DEFAULT_AUTO_TAG_SCENE_THRESHOLD
        )
        auto_tag_frame_interval = _coerce_auto_tag_frame_interval(
            cdata.get("auto_tag_frame_interval"), default=DEFAULT_AUTO_TAG_FRAME_INTERVAL
        )

        catalog_folders = _coerce_catalog_folders_for_catalog(cdata.get("catalog_folders"), catalog_root=str(root))
        if not catalog_folders:
            # Default to catalog root itself.
            catalog_folders = (
                CatalogFolder(
                    key=_new_folder_key(),
                    path=str(root),
                    filename_regex=DEFAULT_FILENAME_REGEX,
                    filename_template=None,
                    match_path=False,
                ),
            )

        # Back-compat for UI: expose a single filename_regex as the primary folder regex.
        primary_regex = catalog_folders[0].filename_regex if catalog_folders else DEFAULT_FILENAME_REGEX
        if not _is_valid_filename_regex(primary_regex):
            primary_regex = DEFAULT_FILENAME_REGEX

        return AppSettings(
            catalog_root=str(root),
            filename_regex=primary_regex,
            catalog_folders=catalog_folders,
            generate_live_previews=generate_live_previews,
            live_preview_segments=live_preview_segments,
            periodic_scan_enabled=periodic_scan_enabled,
            periodic_scan_interval_minutes=periodic_scan_interval_minutes,
            auto_tag_after_scan=auto_tag_after_scan,
            auto_tag_confidence=auto_tag_confidence,
            auto_tag_scene_threshold=auto_tag_scene_threshold,
            auto_tag_frame_interval=auto_tag_frame_interval,
            backend_host=backend_host,
            backend_port=backend_port,
            auth_required=auth_required,
            auth_username=auth_username,
            auth_password=auth_password,
        )

    # Legacy schema (v1): migrate best-effort.
    legacy_catalog_folders = _coerce_catalog_folders(data.get("catalog_folders"))
    legacy_catalog_root = data.get("catalog_root")
    legacy_filename_regex = data.get("filename_regex") or DEFAULT_FILENAME_REGEX
    legacy_generate_live_previews = data.get("generate_live_previews")
    legacy_live_preview_segments = data.get("live_preview_segments")
    legacy_periodic_scan_enabled = data.get("periodic_scan_enabled")
    legacy_periodic_scan_interval_minutes = data.get("periodic_scan_interval_minutes")

    if legacy_catalog_root is not None and not isinstance(legacy_catalog_root, str):
        legacy_catalog_root = None
    if not isinstance(legacy_filename_regex, str):
        legacy_filename_regex = DEFAULT_FILENAME_REGEX
    if not isinstance(legacy_generate_live_previews, bool):
        legacy_generate_live_previews = False
    if not isinstance(legacy_periodic_scan_enabled, bool):
        legacy_periodic_scan_enabled = DEFAULT_PERIODIC_SCAN_ENABLED

    legacy_live_preview_segments_i = _coerce_live_preview_segments(legacy_live_preview_segments, default=10)
    legacy_periodic_scan_interval_minutes_i = _coerce_periodic_scan_interval_minutes(
        legacy_periodic_scan_interval_minutes,
        default=DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
    )

    # Back-compat: if no folder list, infer from catalog_root.
    if not legacy_catalog_folders and legacy_catalog_root:
        try:
            p = Path(legacy_catalog_root)
            if p.exists() and p.is_dir():
                legacy_catalog_folders = (
                    CatalogFolder(
                        key=_new_folder_key(),
                        path=str(p),
                        filename_regex=legacy_filename_regex,
                        filename_template=None,
                        match_path=False,
                    ),
                )
        except Exception:
            legacy_catalog_folders = ()

    if legacy_catalog_root:
        try:
            # Write new global schema.
            ref = _catalog_ref_from_path(legacy_catalog_root)
            payload: dict[str, Any] = {
                "schema_version": 2,
                "active_catalog": {
                    "catalog_id": ref.catalog_id,
                    "volume_id": ref.volume_id,
                    "relative_path": ref.relative_path,
                    "fallback_path": ref.fallback_path,
                },
                "backend_host": backend_host,
                "backend_port": backend_port,
                "auth_required": auth_required,
                "auth_username": auth_username,
                "auth_password": auth_password,
            }
            _write_json_with_fallback(payload)

            # Seed catalog-local settings when the catalog root is accessible.
            p = Path(legacy_catalog_root)
            if p.exists() and p.is_dir():
                _ensure_catalog_settings_file_exists(str(p))
                # If the local config is still default, upgrade it to include the legacy folders.
                cfg_path = _catalog_settings_path(str(p))
                try:
                    existing = _load_json_first_existing([cfg_path]) or {}
                except Exception:
                    existing = {}

                if isinstance(existing, dict) and ("catalog_folders" not in existing or not existing.get("catalog_folders")):
                    folders_payload: list[dict[str, Any]] = []
                    for f in (legacy_catalog_folders or ()): 
                        folders_payload.append(
                            {
                                "key": f.key,
                                "path": _encode_catalog_folder_path_for_storage(catalog_root=str(p), folder_path=f.path),
                                "filename_regex": f.filename_regex,
                                "match_path": bool(getattr(f, "match_path", False)),
                            }
                        )
                    new_local: dict[str, Any] = {
                        "schema_version": 1,
                        "catalog_folders": folders_payload,
                        "generate_live_previews": legacy_generate_live_previews,
                        "live_preview_segments": legacy_live_preview_segments_i,
                        "periodic_scan_enabled": legacy_periodic_scan_enabled,
                        "periodic_scan_interval_minutes": legacy_periodic_scan_interval_minutes_i,
                    }
                    cfg_path.write_text(json.dumps(new_local, indent=2), encoding="utf-8")
        except Exception:
            # If migration fails, fall back to legacy behavior without blocking startup.
            pass

    # Finally, load again (now using v2 if migration happened).
    data2 = _load_json_first_existing([primary_path, fallback_path])
    if isinstance(data2, dict) and "active_catalog" in data2:
        return load_settings()

    # Worst-case fallback.
    legacy_root = legacy_catalog_folders[0].path if legacy_catalog_folders else None
    return AppSettings(
        catalog_root=legacy_root,
        filename_regex=legacy_filename_regex if _is_valid_filename_regex(legacy_filename_regex) else DEFAULT_FILENAME_REGEX,
        catalog_folders=legacy_catalog_folders,
        generate_live_previews=legacy_generate_live_previews,
        live_preview_segments=legacy_live_preview_segments_i,
        periodic_scan_enabled=legacy_periodic_scan_enabled,
        periodic_scan_interval_minutes=legacy_periodic_scan_interval_minutes_i,
        backend_host=backend_host,
        backend_port=backend_port,
        auth_required=auth_required,
        auth_username=auth_username,
        auth_password=auth_password,
    )


def save_settings(settings: AppSettings) -> None:
    # New behavior:
    # - Global app settings only store the active catalog reference.
    # - Per-catalog settings live at <catalog_root>/.camero/settings.json.
    if not settings.catalog_root:
        payload = _build_global_settings_payload(
            active_catalog=None,
            backend_host=getattr(settings, "backend_host", DEFAULT_BACKEND_HOST),
            backend_port=getattr(settings, "backend_port", DEFAULT_BACKEND_PORT),
            auth_required=getattr(settings, "auth_required", DEFAULT_AUTH_REQUIRED),
            auth_username=getattr(settings, "auth_username", DEFAULT_AUTH_USERNAME),
            auth_password=getattr(settings, "auth_password", DEFAULT_AUTH_PASSWORD),
        )
        _write_json_with_fallback(payload)
        return

    payload = _build_global_settings_payload(
        backend_host=getattr(settings, "backend_host", DEFAULT_BACKEND_HOST),
        backend_port=getattr(settings, "backend_port", DEFAULT_BACKEND_PORT),
        auth_required=getattr(settings, "auth_required", DEFAULT_AUTH_REQUIRED),
        auth_username=getattr(settings, "auth_username", DEFAULT_AUTH_USERNAME),
        auth_password=getattr(settings, "auth_password", DEFAULT_AUTH_PASSWORD),
    )
    _write_json_with_fallback(payload)

    # Update active catalog reference.
    set_active_catalog_root(settings.catalog_root)

    # Persist catalog-local settings.
    root = Path(settings.catalog_root)
    (root / ".camero").mkdir(parents=True, exist_ok=True)
    cfg_path = _catalog_settings_path(root)

    folders_payload: list[dict[str, Any]] = []
    for f in (settings.catalog_folders or ()):
        item: dict[str, Any] = {
            "key": f.key,
            "path": _encode_catalog_folder_path_for_storage(catalog_root=str(root), folder_path=f.path),
            "filename_regex": f.filename_regex,
            "match_path": bool(getattr(f, "match_path", False)),
        }
        if getattr(f, "filename_template", None):
            item["filename_template"] = f.filename_template
        folders_payload.append(item)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "catalog_folders": folders_payload,
        "generate_live_previews": bool(settings.generate_live_previews),
        "live_preview_segments": _coerce_live_preview_segments(getattr(settings, "live_preview_segments", 10), default=10),
        "periodic_scan_enabled": _coerce_periodic_scan_enabled(
            getattr(settings, "periodic_scan_enabled", DEFAULT_PERIODIC_SCAN_ENABLED),
            default=DEFAULT_PERIODIC_SCAN_ENABLED,
        ),
        "periodic_scan_interval_minutes": _coerce_periodic_scan_interval_minutes(
            getattr(settings, "periodic_scan_interval_minutes", DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES),
            default=DEFAULT_PERIODIC_SCAN_INTERVAL_MINUTES,
        ),
        "auto_tag_after_scan": bool(getattr(settings, "auto_tag_after_scan", DEFAULT_AUTO_TAG_AFTER_SCAN)),
        "auto_tag_confidence": _coerce_auto_tag_confidence(
            getattr(settings, "auto_tag_confidence", DEFAULT_AUTO_TAG_CONFIDENCE),
            default=DEFAULT_AUTO_TAG_CONFIDENCE,
        ),
        "auto_tag_scene_threshold": _coerce_auto_tag_scene_threshold(
            getattr(settings, "auto_tag_scene_threshold", DEFAULT_AUTO_TAG_SCENE_THRESHOLD),
            default=DEFAULT_AUTO_TAG_SCENE_THRESHOLD,
        ),
        "auto_tag_frame_interval": _coerce_auto_tag_frame_interval(
            getattr(settings, "auto_tag_frame_interval", DEFAULT_AUTO_TAG_FRAME_INTERVAL),
            default=DEFAULT_AUTO_TAG_FRAME_INTERVAL,
        ),
    }

    cfg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return


def save_global_network_settings(
    *,
    backend_host: object = _MISSING,
    backend_port: object = _MISSING,
    auth_required: object = _MISSING,
    auth_username: object = _MISSING,
    auth_password: object = _MISSING,
) -> None:
    payload = _build_global_settings_payload(
        backend_host=backend_host,
        backend_port=backend_port,
        auth_required=auth_required,
        auth_username=auth_username,
        auth_password=auth_password,
    )
    _write_json_with_fallback(payload)
