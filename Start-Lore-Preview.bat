@echo off
REM ============================================================
REM  Lore — lore2 redesign preview launcher (double-click me)
REM  Starts a local, throwaway Lore instance with the new UI and
REM  sample data, then opens your browser. Close this window or
REM  press Ctrl+C to stop. Touches no real data.
REM ============================================================
setlocal
cd /d "%~dp0"

set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE (
  py -3 --version >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  python --version >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo.
  echo   Could not find Python. Install Python 3.12+ or create the project venv ^(.venv^).
  echo.
  pause
  exit /b 1
)

%PYEXE% "testing\lore2_preview.py"

echo.
echo   Lore preview stopped.
pause
