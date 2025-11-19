# SyncLyrics Hidden Launcher - No visible windows
param(
    [switch]$Debug
)

# Set the working directory to the script location
Set-Location $PSScriptRoot

# Check if Python is available
try {
   #$pythonCheck = python --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python not found in PATH. Trying to find it..."
        $pythonPath = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
        if ($pythonPath) {
            $pythonCmd = $pythonPath
            Write-Host "Found Python at: $pythonPath"
        } else {
            Write-Host "ERROR: Python not found. Please install Python or add it to PATH."
            Read-Host "Press Enter to exit"
            exit 1
        }
    } else {
        $pythonCmd = "python"
        Write-Host "Using Python from PATH"
    }
} catch {
    Write-Host "ERROR: Failed to check Python installation."
    Read-Host "Press Enter to exit"
    exit 1
}

# Set certifi environment variable
try {
    $certPath = & $pythonCmd -c "import certifi; print(certifi.where())" 2>$null
    $env:REQUESTS_CA_BUNDLE = $certPath
} catch {
    Write-Host "Warning: Could not set certifi path"
}

# Check if debug mode is requested
if ($Debug) {
    Write-Host "Starting SyncLyrics in DEBUG mode..."
    & $pythonCmd sync_lyrics.py
} else {
    Write-Host "Starting SyncLyrics in background (completely hidden)..."
    
    # Create a hidden PowerShell process that runs Python
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $pythonCmd
    $startInfo.Arguments = "sync_lyrics.py"
    $startInfo.WorkingDirectory = $PSScriptRoot
    $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $startInfo.CreateNoWindow = $true
    $startInfo.UseShellExecute = $false
    
    # Set environment variable for the new process
    $startInfo.EnvironmentVariables["REQUESTS_CA_BUNDLE"] = $env:REQUESTS_CA_BUNDLE
    
    # Start the process
    $process = [System.Diagnostics.Process]::Start($startInfo)
    
    if ($process) {
        Write-Host "SyncLyrics started successfully in background (PID: $($process.Id))"
        Write-Host "Check system tray for the icon."
        Write-Host "Web interface available at: http://localhost:9012"
    } else {
        Write-Host "Failed to start SyncLyrics"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

Write-Host "Done."
