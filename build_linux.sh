#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "[Camero] ERROR: run this script on Linux (or inside WSL)." >&2
    exit 1
fi

PYTHON_EXE=python3
if [[ -x ".venv/bin/python" ]]; then
    PYTHON_EXE=".venv/bin/python"
fi

if ! command -v "$PYTHON_EXE" >/dev/null 2>&1; then
    echo "[Camero] ERROR: Python 3 not found. Install Python and the project dependencies first." >&2
    exit 1
fi

echo "[Camero] Checking PyInstaller..."
if ! "$PYTHON_EXE" -m pip show pyinstaller >/dev/null 2>&1; then
    echo "[Camero] PyInstaller not found. Installing..."
    "$PYTHON_EXE" -m pip install pyinstaller
fi

echo "[Camero] Building (onefile) from camero.spec..."
"$PYTHON_EXE" -m PyInstaller --noconfirm --clean --workpath build/linux --distpath dist_linux camero.spec

if [[ ! -x "dist_linux/Camero" ]]; then
    echo "[Camero] ERROR: the build did not produce dist_linux/Camero." >&2
    exit 1
fi

echo "[Camero] OK: dist_linux/Camero"
