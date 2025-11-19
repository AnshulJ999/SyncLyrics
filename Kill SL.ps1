# Kill SyncLyrics - PowerShell Version
Write-Host "Searching for SyncLyrics processes..." -ForegroundColor Yellow

# Get all Python processes
$pythonProcesses = Get-Process python* -ErrorAction SilentlyContinue

if ($pythonProcesses) {
    Write-Host "Found $($pythonProcesses.Count) Python process(es):" -ForegroundColor Cyan
    
    foreach ($process in $pythonProcesses) {
        Write-Host "  PID: $($process.Id) - $($process.ProcessName) - Memory: $([math]::Round($process.WorkingSet64/1MB, 2)) MB" -ForegroundColor White
        
        # Try to get command line to identify SyncLyrics
        try {
            $cmdLine = Get-WmiObject Win32_Process -Filter "ProcessId = $($process.Id)" | Select-Object -ExpandProperty CommandLine
            if ($cmdLine -like "*sync_lyrics.py*") {
                Write-Host "    -> This is SyncLyrics!" -ForegroundColor Green
            }
        } catch {
            Write-Host "    -> Could not determine command line" -ForegroundColor Gray
        }
    }
    
    # Kill all Python processes
    Write-Host "`nKilling all Python processes..." -ForegroundColor Red
    try {
        Stop-Process -Name python* -Force -ErrorAction Stop
        Write-Host "Successfully terminated all Python processes!" -ForegroundColor Green
    } catch {
        Write-Host "Error killing processes: $($_.Exception.Message)" -ForegroundColor Red
        
        # Try individual process killing
        Write-Host "Attempting individual process termination..." -ForegroundColor Yellow
        foreach ($process in $pythonProcesses) {
            try {
                $process.Kill()
                Write-Host "  Killed PID: $($process.Id)" -ForegroundColor Green
            } catch {
                Write-Host "  Failed to kill PID: $($process.Id) - $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }
} else {
    Write-Host "No Python processes found." -ForegroundColor Green
}

# Final check
Write-Host "`nFinal check for remaining Python processes..." -ForegroundColor Yellow
$remaining = Get-Process python* -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Host "Warning: $($remaining.Count) Python process(es) still running:" -ForegroundColor Red
    $remaining | ForEach-Object { Write-Host "  PID: $($_.Id) - $($_.ProcessName)" -ForegroundColor Red }
} else {
    Write-Host "All Python processes have been terminated successfully!" -ForegroundColor Green
}

Write-Host "`nSyncLyrics kill operation complete." -ForegroundColor Yellow
Read-Host "Press Enter to continue"
