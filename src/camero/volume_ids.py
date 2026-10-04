from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import ctypes
from ctypes import wintypes

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


@dataclass(frozen=True)
class VolumeRef:
    volume_id: str
    mount_point: str


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _normalize_mount_point(p: str) -> str:
    # Expected: 'E:\\'
    mp = (p or "").strip()
    if not mp:
        return mp
    # Ensure trailing backslash for WinAPI calls.
    if not mp.endswith("\\"):
        mp += "\\"
    return mp


def get_volume_id_for_mount_point(mount_point: str) -> str | None:
    """Return a stable volume ID for a Windows mount point (drive root).

    On Windows, this is the Volume GUID path like: \\?\\Volume{GUID}\\
    Returns None on non-Windows platforms or when unavailable.
    """

    if not _is_windows():
        return None

    mp = _normalize_mount_point(mount_point)
    if not mp:
        return None

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None

    GetVolumeNameForVolumeMountPointW = kernel32.GetVolumeNameForVolumeMountPointW
    GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL

    buf = ctypes.create_unicode_buffer(1024)
    ok = GetVolumeNameForVolumeMountPointW(mp, buf, len(buf))
    if not ok:
        return None

    vol = (buf.value or "").strip()
    return vol or None


def iter_windows_drive_roots() -> list[str]:
    """List existing Windows drive roots like ['C:\\', 'D:\\']."""

    if not _is_windows():
        return []

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mask = kernel32.GetLogicalDrives()
    except Exception:
        # Fallback: brute-force check.
        out: list[str] = []
        for code in range(ord("A"), ord("Z") + 1):
            root = f"{chr(code)}:\\"
            try:
                if Path(root).exists():
                    out.append(root)
            except Exception:
                pass
        return out

    out: list[str] = []
    for i in range(26):
        if mask & (1 << i):
            root = f"{chr(ord('A') + i)}:\\"
            out.append(root)
    return out


def iter_mount_points() -> list[str]:
    """Return a list of mount points (filesystem roots) for the current OS.

    Uses `psutil` when available (recommended). Falls back to a minimal list.
    """

    if psutil is not None:
        out: list[str] = []
        try:
            for p in psutil.disk_partitions(all=False):
                mp = (getattr(p, "mountpoint", "") or "").strip()
                if not mp:
                    continue
                out.append(_normalize_mount_point(mp) if _is_windows() else mp)
        except Exception:
            out = []

        # De-dupe while preserving order.
        seen: set[str] = set()
        uniq: list[str] = []
        for mp in out:
            key = mp.lower() if _is_windows() else mp
            if key in seen:
                continue
            seen.add(key)
            uniq.append(mp)
        return uniq

    # Fallbacks when psutil isn't installed.
    if _is_windows():
        return iter_windows_drive_roots()
    return ["/"]


def find_mount_point_for_path(path: str | Path) -> str | None:
    """Find the mount point that contains `path` (best-effort).

    Returns the longest matching mount point prefix.
    """

    try:
        target = Path(path).resolve()
    except Exception:
        target = Path(path)

    target_s = str(target)
    candidates = iter_mount_points()
    # Prefer the most specific mount point.
    candidates.sort(key=lambda x: len(x), reverse=True)

    for mp in candidates:
        if not mp:
            continue
        if _is_windows():
            mp_cmp = mp.lower()
            t_cmp = target_s.lower()
            if t_cmp.startswith(mp_cmp):
                return mp
        else:
            # Ensure prefix ends with / except root.
            mp_norm = mp if mp == "/" else mp.rstrip("/") + "/"
            t_norm = target_s if target_s == "/" else target_s.rstrip("/") + "/"
            if t_norm.startswith(mp_norm):
                return mp.rstrip("/") or "/"

    return None


def find_mount_point_for_volume_id(volume_id: str) -> str | None:
    """Find the current drive root for a given volume GUID ID."""

    if not _is_windows():
        return None

    vid = (volume_id or "").strip()
    if not vid:
        return None

    for root in iter_windows_drive_roots():
        try:
            rid = get_volume_id_for_mount_point(root)
        except Exception:
            rid = None
        if rid and rid.lower() == vid.lower():
            return root

    return None


def split_path_volume_relative(path: str | Path) -> tuple[str | None, str | None]:
    """Return (volume_id, relative_path_from_volume_root) for a path.

    - On Windows drive-letter paths, relative_path uses '/' separators.
    - Returns (None, None) if it cannot be derived.
    """

    p = Path(path)

    # Windows: use stable Volume GUID when possible.
    if _is_windows():
        anchor = p.anchor  # e.g. 'E:\\'
        if not anchor or ":\\" not in anchor:
            return (None, None)

        root = _normalize_mount_point(anchor)
        vid = get_volume_id_for_mount_point(root)

        try:
            rel = p.resolve().relative_to(Path(root))
        except Exception:
            rel = None

        rel_str = None
        if rel is not None:
            rel_str = str(rel).replace("\\", "/").lstrip("/")

        return (vid, rel_str)

    # Linux/macOS: no stable volume id without OS-specific APIs.
    # We still return a path relative to the mount root, which remains stable
    # even if the mount point changes.
    mp = find_mount_point_for_path(p)
    if not mp:
        return (None, None)

    try:
        rel = p.resolve().relative_to(Path(mp))
    except Exception:
        return (None, None)

    rel_str = str(rel).replace("\\", "/").lstrip("/")
    return (None, rel_str)
