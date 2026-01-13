# SyncLyrics Font Downloader
# Downloads all required Google Fonts using goog-webfont-dl npm package
# Run this script once to populate resources/fonts/bundled/

$ErrorActionPreference = "Stop"

# Check if Node.js is installed
try {
    $nodeVersion = node --version
    Write-Host "Node.js found: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Node.js is not installed. Please install it from https://nodejs.org/" -ForegroundColor Red
    exit 1
}

# Install goog-webfont-dl globally if not present
Write-Host "`nChecking for goog-webfont-dl..." -ForegroundColor Cyan
$hasGwdl = npm list -g goog-webfont-dl 2>$null
if (-not $hasGwdl -or $hasGwdl -match "empty") {
    Write-Host "Installing goog-webfont-dl globally..." -ForegroundColor Yellow
    npm install -g goog-webfont-dl
}

# Define fonts to download
$fonts = @(
    @{ name = "Inter"; folder = "inter" },
    @{ name = "Outfit"; folder = "outfit" },
    @{ name = "Poppins"; folder = "poppins" },
    @{ name = "Open Sans"; folder = "opensans" },
    @{ name = "Nunito"; folder = "nunito" },
    @{ name = "Roboto"; folder = "roboto" },
    @{ name = "Montserrat"; folder = "montserrat" },
    @{ name = "Work Sans"; folder = "worksans" },
    @{ name = "Oswald"; folder = "oswald" },
    @{ name = "Raleway"; folder = "raleway" },
    @{ name = "Bebas Neue"; folder = "bebasneue" },
    @{ name = "Space Grotesk"; folder = "spacegrotesk" },
    @{ name = "Playfair Display"; folder = "playfairdisplay" },
    @{ name = "Lora"; folder = "lora" },
    @{ name = "Fraunces"; folder = "fraunces" }
)

$outputBase = Join-Path $PSScriptRoot "..\resources\fonts\bundled"
$outputBase = [System.IO.Path]::GetFullPath($outputBase)

Write-Host "`nDownloading fonts to: $outputBase" -ForegroundColor Cyan
Write-Host "This may take a few minutes...`n" -ForegroundColor Yellow

foreach ($font in $fonts) {
    $fontName = $font.name
    $folderName = $font.folder
    $outputDir = Join-Path $outputBase $folderName
    
    Write-Host "[$($fonts.IndexOf($font) + 1)/$($fonts.Count)] Downloading $fontName..." -ForegroundColor White
    
    # Create directory
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    
    # Download using goog-webfont-dl
    # -f woff2,woff = formats to download
    # -o = output directory
    # -p = prefix for CSS (not needed, we just want files)
    try {
        Push-Location $outputDir
        goog-webfont-dl -f woff2,woff -o . "$fontName"
        Pop-Location
        Write-Host "  ✓ $fontName downloaded" -ForegroundColor Green
    } catch {
        Pop-Location
        Write-Host "  ✗ Failed to download $fontName - $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Font download complete!" -ForegroundColor Green
Write-Host "Location: $outputBase" -ForegroundColor White
Write-Host "========================================`n" -ForegroundColor Cyan

# List downloaded fonts
Write-Host "Downloaded fonts:" -ForegroundColor Yellow
Get-ChildItem $outputBase -Directory | ForEach-Object {
    $fileCount = (Get-ChildItem $_.FullName -File).Count
    Write-Host "  - $($_.Name): $fileCount files" -ForegroundColor White
}
