@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"

set "MODEL_SOURCE="
if exist "assets\models\model.onnx" set "MODEL_SOURCE=assets\models\model.onnx"
if not defined MODEL_SOURCE if exist "models\model.onnx" set "MODEL_SOURCE=models\model.onnx"

if not defined MODEL_SOURCE (
  echo.
  echo Build failed: model.onnx not found in assets\models\ or models\
  exit /b 1
)

set "CSV_SOURCE="
if exist "assets\selected_tags.csv" set "CSV_SOURCE=assets\selected_tags.csv"
if not defined CSV_SOURCE if exist "models\assets\selected_tags.csv" set "CSV_SOURCE=models\assets\selected_tags.csv"
if not defined CSV_SOURCE if exist "models\selected_tags.csv" set "CSV_SOURCE=models\selected_tags.csv"

if not defined CSV_SOURCE (
  echo.
  echo Build failed: selected_tags.csv not found in assets\, models\assets\ or models\
  exit /b 1
)

set "CSV_SOURCE=%CSV_SOURCE%"
set "MODEL_SOURCE=%MODEL_SOURCE%"

"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  autotagger.spec

if errorlevel 1 (
  echo.
  echo Build failed.
  exit /b 1
)

if exist "models\*.onnx" (
  if not exist "dist\models" mkdir "dist\models"
  copy /Y "models\*.onnx" "dist\models\" >nul
)

echo.
echo Build completed: dist\AITagger.exe
echo Python used: %PYTHON_EXE%
echo Embedded resource: %CSV_SOURCE%
echo Embedded model: %MODEL_SOURCE%
if exist "assets\icon.png" echo App icon: assets\icon.png
if exist "models\*.onnx" echo External resources copied to: dist\models
endlocal