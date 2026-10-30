@echo off
rem Super market - start the shop on this PC (Windows). No installation or packages needed.
rem Uses the bundled python\python.exe when present, otherwise an installed Python 3.
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python\python.exe"
if not exist "%PY%" (
  where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
)
set "MODE="
if /i "%~1"=="training" set "MODE=--training"
set "PORT=8765"
if defined MODE set "PORT=8766"
start "" /min cmd /c "%PY% -m server.app %MODE% %2 %3"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%PORT%/"
