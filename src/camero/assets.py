from __future__ import annotations

from pathlib import Path
import sys


def get_assets_dir() -> Path:
    """Return the assets folder path for both dev and PyInstaller-frozen layouts."""

    # Development layout: src/camero/* and src/assets/*
    # Frozen (PyInstaller) layout: <_MEIPASS>/camero/* and <_MEIPASS>/assets/*
    base_dir = Path(__file__).resolve().parent

    candidate = base_dir.parent / "assets"
    if candidate.exists():
        return candidate

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(str(meipass)) / "assets"
        if candidate.exists():
            return candidate

    return base_dir.parent / "assets"
