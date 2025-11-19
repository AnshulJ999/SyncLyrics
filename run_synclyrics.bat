@echo off
title SyncLyrics Launcher

REM Set the working directory to the script location
cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found in PATH. Trying to find it...
    for /f "tokens=*" %%i in ('where python.exe 2^>nul') do (
        set "PYTHON_PATH=%%i"
        goto :found_python
    )
    echo ERROR: Python not found. Please install Python or add it to PATH.
    pause
    exit /b 1
)

:found_python
if defined PYTHON_PATH (
    echo Found Python at: %PYTHON_PATH%
    set "python_cmd=%PYTHON_PATH%"
) else (
    echo Using Python from PATH
    set "python_cmd=python"
)

REM Set certifi environment variable
for /f "tokens=*" %%i in ('%python_cmd% -c "import certifi; print(certifi.where())" 2^>nul') do (
    set "REQUESTS_CA_BUNDLE=%%i"
)

REM Check if debug mode is requested
if "%1"=="/debug" (
    echo Starting SyncLyrics in DEBUG mode...
    %python_cmd% sync_lyrics.py
) else (
    echo Starting SyncLyrics in background...
    REM Start Python in background and redirect output to avoid console window
    start /min "" %python_cmd% sync_lyrics.py
    echo SyncLyrics started in background.
    echo Check system tray for the icon.
    echo Web interface available at: http://localhost:9012
    timeout /t 3 >nul
)

echo Done.
