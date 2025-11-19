@echo off
REM Set SSL certificate path and verify settings to handle EventGhost properly
SET REQUESTS_CA_BUNDLE=C:\Users\Anshul\AppData\Local\Programs\Python\Python311\Lib\site-packages\certifi\cacert.pem

SET PYTHONWARNINGS=ignore:Unverified HTTPS request

REM Change to script directory
cd /d %~dp0

REM Run options
REM Option 1: Run completely hidden (no window)
REM start "" pythonw.exe sync_lyrics.py

REM Option 2: Run with cmd prompt
REM python sync_lyrics.py

REM Option 3: Run with Terminal and title (current default)
REM wt.exe --title "SyncLyrics" python sync_lyrics.py

pythonw.exe sync_lyrics.py >nul 2>&1

REM start "" /B pythonw.exe sync_lyrics.py
