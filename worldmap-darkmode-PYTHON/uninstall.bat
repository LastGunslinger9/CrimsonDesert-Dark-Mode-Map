@echo off
setlocal EnableDelayedExpansion
title Dark Mode Map v1.1.2 - Uninstall
color 0C

echo  Dark Mode Map v1.1.2 - Uninstall
echo.

set "SD=%~dp0"
if "%SD:~-1%"=="\" set "SD=%SD:~0,-1%"

:: ── Zip check ─────────────────────────────────────────────────────
if not exist "%SD%\patch.py" (
    echo.
    echo   ERROR: Cannot find patch.py.
    echo   Right-click the zip and choose "Extract All" first.
    echo   Do NOT run from inside the zip file.
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
    echo.
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
    if errorlevel 1 (
        echo.
        echo   ERROR: Failed to install lz4.
        echo   Try manually: pip install lz4
        echo.
        pause & exit /b 1
    )
)
!PYTHON! -m pip show cryptography >nul 2>&1
if errorlevel 1 (
    echo   Installing cryptography...
    !PYTHON! -m pip install --disable-pip-version-check cryptography
    if errorlevel 1 (
        echo.
        echo   ERROR: Failed to install cryptography.
        echo   Try manually: pip install cryptography
        echo.
        pause & exit /b 1
    )
)

:: ── Game detection: Steam registry + libraryfolders.vdf ───────────
set "GAME_DIR="

for /f "tokens=2*" %%A in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul') do (
    set "STEAMPATH=%%B"
    set "STEAMPATH=!STEAMPATH:/=\!"
    if exist "!STEAMPATH!\steamapps\common\Crimson Desert\0012\0.pamt" (
        if "!GAME_DIR!"=="" set "GAME_DIR=!STEAMPATH!\steamapps\common\Crimson Desert"
    )
    if "!GAME_DIR!"=="" (
        if exist "!STEAMPATH!\steamapps\libraryfolders.vdf" (
            for /f "tokens=2 delims=	 " %%P in ('findstr /C:"\"path\"" "!STEAMPATH!\steamapps\libraryfolders.vdf" 2^>nul') do (
                set "LPATH=%%~P"
                set "LPATH=!LPATH:\\=\!"
                if "!GAME_DIR!"=="" (
                    if exist "!LPATH!\steamapps\common\Crimson Desert\0012\0.pamt" (
                        set "GAME_DIR=!LPATH!\steamapps\common\Crimson Desert"
                    )
                )
            )
        )
    )
)

:: ── Manual prompt fallback ────────────────────────────────────────
:ask_path
if "!GAME_DIR!"=="" (
    echo.
    echo   Could not find Crimson Desert automatically.
    echo   Enter the full path to your Crimson Desert game folder.
    echo   Example: G:\Games\Steam\steamapps\common\Crimson Desert
    echo.
    set /p "GAME_DIR=  Path: "
    set "GAME_DIR=!GAME_DIR:"=!"
    if "!GAME_DIR!"=="" (
        echo   No path entered. Cancelled.
        pause & exit /b 1
    )
    if not exist "!GAME_DIR!\0012\0.pamt" (
        echo.
        echo   0012\0.pamt not found in that folder. Check the path and try again.
        echo.
        set "GAME_DIR="
        goto ask_path
    )
)

:: ── Run ────────────────────────────────────────────────────────────
echo.
echo   Game: !GAME_DIR!
echo.

!PYTHON! "%SD%\patch.py" "!GAME_DIR!" --restore
if errorlevel 1 (
    echo.
    echo   ERROR: Restore failed. See output above.
    pause & exit /b 1
)

echo.
echo   Vanilla restored.
echo.
pause
