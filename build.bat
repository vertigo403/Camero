@echo off
setlocal

REM Build Camero as a single-file .exe using PyInstaller.
REM Output: dist\Camero.exe

cd /d "%~dp0"

set "PYTHON_EXE=py"
if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
)

if not exist "src\assets\icon.ico" (
  echo [Camero] ERROR: missing src\assets\icon.ico
  exit /b 1
)

echo [Camero] Checking PyInstaller...
"%PYTHON_EXE%" -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
  echo [Camero] PyInstaller not found. Installing...
  "%PYTHON_EXE%" -m pip install -U pyinstaller
  if errorlevel 1 exit /b 1
)

echo [Camero] Cleaning previous build outputs...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

echo [Camero] Building (onefile) from camero.spec...
"%PYTHON_EXE%" -m pip install -U pyinstaller >nul 2>nul
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean "camero.spec"
if errorlevel 1 exit /b 1

echo.
echo [Camero] OK: dist\Camero.exe
echo.
endlocal
