@echo off
setlocal EnableDelayedExpansion
title Dark Mode Map v1.1 - Compatibility Tool
color 0A

echo  Dark Mode Map v1.1 - Compatibility Tool
echo  Patches worldmapview.css inside any mod .zip with Dark Mode colors.
echo.

set "SD=%~dp0"
if "%SD:~-1%"=="\" set "SD=%SD:~0,-1%"

:: ── Zip check ─────────────────────────────────────────────────────
if not exist "%SD%\patch_css_zip.py" (
    echo   ERROR: Cannot find patch_css_zip.py.
    echo   Make sure patch.bat and patch_css_zip.py are in the same folder.
    echo.
    pause & exit /b 1
)

:: ── Python check ──────────────────────────────────────────────────
set "PYTHON="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"
if "!PYTHON!"=="" (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)
if "!PYTHON!"=="" (
    echo   ERROR: Python 3 not found.
    echo   Install from: https://www.python.org/downloads/
    echo   IMPORTANT: Check "Add python.exe to PATH" during install.
    echo.
    pause & exit /b 1
)

:: ── lz4 + cryptography dep check ──────────────────────────────────
!PYTHON! -m pip show lz4 >nul 2>&1
if errorlevel 1 (
    echo   Installing lz4...
    !PYTHON! -m pip install --disable-pip-version-check lz4
    if errorlevel 1 ( echo   ERROR: Failed to install lz4. & pause & exit /b 1 )
)
!PYTHON! -m pip show cryptography >nul 2>&1
if errorlevel 1 (
    echo   Installing cryptography...
    !PYTHON! -m pip install --disable-pip-version-check cryptography
    if errorlevel 1 ( echo   ERROR: Failed to install cryptography. & pause & exit /b 1 )
)

:: ── Auto-detect zip in script folder ─────────────────────────────
set "ZIP_PATH="
set "ZIP_COUNT=0"
set "FOUND_AUTO=0"
for %%F in ("%SD%\*.zip") do (
    set /a ZIP_COUNT+=1
    set "ZIP_PATH=%%~fF"
)

if !ZIP_COUNT! gtr 1 (
    echo   Multiple .zip files found in this folder:
    echo.
    for %%F in ("%SD%\*.zip") do echo     %%~nxF
    echo.
    echo   Please keep only one .zip file here, then run again.
    echo.
    pause & exit /b 1
)

if !ZIP_COUNT! equ 1 (
    set "FOUND_AUTO=1"
    echo   Found: !ZIP_PATH!
    echo.
    goto :run
)

:: ── No zip found - prompt ─────────────────────────────────────────
echo   No .zip found in this folder.
echo   Drag a mod .zip here or type its full path, then press Enter.
echo.
set /p "ZIP_PATH=  Zip path: "
set "ZIP_PATH=!ZIP_PATH:"=!"

if "!ZIP_PATH!"=="" (
    echo   No path entered. Cancelled.
    pause & exit /b 1
)
if not exist "!ZIP_PATH!" (
    echo.
    echo   ERROR: File not found:
    echo   !ZIP_PATH!
    echo.
    pause & exit /b 1
)

:run

:: ── Run patcher ───────────────────────────────────────────────────
echo.if "!FOUND_AUTO!"=="1" (
    echo   Mod zip found automatically.
) else (
    echo   Using manually entered path.
)
echo   Zip: !ZIP_PATH!
echo.!PYTHON! "%SD%\patch_css_zip.py" "!ZIP_PATH!"
if errorlevel 1 (
    echo.
    echo   Patching failed. See error above.
    pause & exit /b 1
)

echo.
echo   Restart the game to apply. The mod zip is now Dark Mode patched.
echo.
pause
