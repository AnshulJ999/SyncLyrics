@echo off
REM Build sfp-cli for Windows (self-contained)
REM This creates a portable exe that doesn't require .NET runtime

echo Building sfp-cli (self-contained)...
dotnet publish -c Release -r win-x64 --self-contained -o bin/publish

if %ERRORLEVEL% EQU 0 (
    echo.
    echo Build successful!
    echo Output: bin\publish\sfp-cli.exe
) else (
    echo.
    echo Build failed with error code %ERRORLEVEL%
)
pause