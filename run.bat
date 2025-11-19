@echo off

REM Run completely hidden (no window)

REM start "" pythonw.exe sync_lyrics.py

REM Run with cmd prompt

REM python sync_lyrics.py

REM Run with Terminal and title

REM wt.exe --title "SyncLyrics" python sync_lyrics.py

TITLE SyncLyrics Console
ECHO Starting SyncLyrics...

REM Check if Python is installed
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    ECHO Error: Python is not installed or not in your PATH.
    PAUSE
    EXIT /B
)

REM Run the app
REM To run in background (hidden), change "python" to "pythonw" below
wt.exe --title "SyncLyrics" python sync_lyrics.py

REM If the app crashes, keep window open so you can see why
IF %ERRORLEVEL% NEQ 0 (
    ECHO.
    ECHO Application crashed! See errors above.
    PAUSE
)