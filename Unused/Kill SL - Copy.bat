@echo off
REM Find pythonw.exe processes running sync_lyrics.py
for /f "tokens=2" %%i in ('tasklist /fi "imagename eq pythonw.exe" /v /fo list ^| findstr "PID:"') do (
    REM For each pythonw.exe process, check if it's running sync_lyrics.py
    wmic process where "ProcessId=%%i" get CommandLine /format:list | findstr /i "sync_lyrics.py" >nul
    if not errorlevel 1 (
        REM If it is sync_lyrics.py, kill it
        taskkill /f /pid %%i
    )
)