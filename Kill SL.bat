@echo off
title Kill SyncLyrics
echo ========================================
echo        SyncLyrics Process Killer
echo ========================================
echo.

REM Check if running as administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script requires Administrator privileges!
    echo Right-click and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo Running as Administrator - OK
echo.

REM Show current Python processes
echo Current Python processes:
tasklist /fi "imagename eq python.exe" /fo table
echo.

REM Kill all Python processes
echo Killing all Python processes...
taskkill /f /im python.exe >nul 2>&1
if %errorLevel% equ 0 (
    echo Successfully killed python.exe processes
) else (
    echo No python.exe processes found or failed to kill
)

taskkill /f /im pythonw.exe >nul 2>&1
if %errorLevel% equ 0 (
    echo Successfully killed pythonw.exe processes
) else (
    echo No pythonw.exe processes found or failed to kill
)

echo.
echo Final check for remaining Python processes:
tasklist /fi "imagename eq python.exe" /fo table 2>nul
if %errorLevel% neq 0 (
    echo No Python processes found - All killed successfully!
) else (
    echo Some Python processes may still be running.
)

echo.
echo ========================================
echo        Operation Complete
echo ========================================
pause