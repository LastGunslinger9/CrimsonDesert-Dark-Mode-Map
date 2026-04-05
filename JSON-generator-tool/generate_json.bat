@echo off
setlocal EnableDelayedExpansion
title Dark Mode Map - JSON Offset Generator
color 0B

echo  Dark Mode Map - JSON Offset Generator v1.2
echo  Rebuilds worldmap_darkmode.json from current game files.
echo  --------------------------------------------
echo.

set "SD=%~dp0"
if "%SD:~-1%"=="\" set "SD=%SD:~0,-1%"

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
    pause ^& exit /b 1
)

:: ── lz4 + cryptography dep check ──────────────────────────────────
!PYTHON! -m pip show lz4 >nul 2>&1
if errorlevel 1 (
    echo   Installing lz4...
    !PYTHON! -m pip install --disable-pip-version-check lz4
    if errorlevel 1 (
        echo.
        echo   ERROR: Failed to install lz4.  Try manually: pip install lz4
        echo.
        pause ^& exit /b 1
    )
)
!PYTHON! -m pip show cryptography >nul 2>&1
if errorlevel 1 (
    echo   Installing cryptography...
    !PYTHON! -m pip install --disable-pip-version-check cryptography
    if errorlevel 1 (
        echo.
        echo   ERROR: Failed to install cryptography.  Try manually: pip install cryptography
        echo.
        pause ^& exit /b 1
    )
)

:: ── Game detection: Steam registry + libraryfolders.vdf ───────────
set "GAME_DIR="
set "FOUND_AUTO=0"

for /f "tokens=2*" %%A in ('reg query "HKCU\Software\Valve\Steam" /v SteamPath 2^>nul') do (
    set "STEAMPATH=%%B"
    set "STEAMPATH=!STEAMPATH:/=\!"
    if exist "!STEAMPATH!\steamapps\common\Crimson Desert\bin64\CrimsonDesert.exe" (
        if "!GAME_DIR!"=="" (
            set "GAME_DIR=!STEAMPATH!\steamapps\common\Crimson Desert"
            set "FOUND_AUTO=1"
        )
    )
    if "!GAME_DIR!"=="" (
        if exist "!STEAMPATH!\steamapps\libraryfolders.vdf" (
            for /f "tokens=2 delims=	 " %%P in ('findstr /C:"\"path\"" "!STEAMPATH!\steamapps\libraryfolders.vdf" 2^>nul') do (
                set "LPATH=%%~P"
                set "LPATH=!LPATH:\\=\!"
                if "!GAME_DIR!"=="" (
                    if exist "!LPATH!\steamapps\common\Crimson Desert\bin64\CrimsonDesert.exe" (
                        set "GAME_DIR=!LPATH!\steamapps\common\Crimson Desert"
                        set "FOUND_AUTO=1"
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
    echo   Example: G:\SteamLibrary\steamapps\common\Crimson Desert
    echo.
    set /p "GAME_DIR=  Path: "
    set "GAME_DIR=!GAME_DIR:"=!"
    if "!GAME_DIR!"=="" (
        echo   No path entered. Cancelled.
        pause & exit /b 1
    )
    if not exist "!GAME_DIR!\bin64\CrimsonDesert.exe" (
        echo.
        echo   CrimsonDesert.exe not found in that folder. Check the path and try again.
        echo.
        set "GAME_DIR="
        goto ask_path
    )
)

:: ── Run ────────────────────────────────────────────────────────────
echo.
if "!FOUND_AUTO!"=="1" (
    echo   Steam installation found automatically.
) else (
    echo   Using manually entered path.
)
echo   Game: !GAME_DIR!
echo.

!PYTHON! "%SD%\gen_json_offsets.py" "!GAME_DIR!"
if errorlevel 1 (
    echo.
    echo   ERROR: Script failed. See output above.
    pause & exit /b 1
)

echo  --------------------------------------------
echo   Done! worldmap_darkmode.json has been updated.
echo   Load it with your JSON mod manager.
echo.
pause
