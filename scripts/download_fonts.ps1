# Font Download Script for SyncLyrics
# Downloads Google Fonts in woff2 format

$fonts = @(
    "Inter",
    "Outfit", 
    "Poppins",
    "Open+Sans",
    "Nunito",
    "Roboto",
    "Montserrat",
    "Work+Sans",
    "Oswald",
    "Raleway",
    "Bebas+Neue",
    "Space+Grotesk",
    "Playfair+Display",
    "Lora",
    "Fraunces"
)

$weights = "300;400;500;600;700"
$outputDir = "g:\GitHub\SyncLyrics\resources\fonts\bundled"

foreach ($font in $fonts) {
    $fontName = $font -replace '\+', ''
    $fontDir = Join-Path $outputDir $fontName.ToLower()
    
    Write-Host "Creating directory: $fontDir"
    New-Item -ItemType Directory -Force -Path $fontDir | Out-Null
    
    # Download using google-webfonts-helper API style URL
    $url = "https://fonts.googleapis.com/css2?family=$font`:wght@$weights&display=swap"
    
    Write-Host "Downloading $fontName from Google Fonts..."
    Write-Host "  URL: $url"
    Write-Host "  (Manual download required - see instructions below)"
    Write-Host ""
}

Write-Host @"

=== MANUAL DOWNLOAD INSTRUCTIONS ===

Google Fonts doesn't allow direct woff2 downloads via script.
Please download fonts manually:

1. Go to https://fonts.google.com/
2. Search for each font and click "Download family"
3. Extract and copy the .ttf files to the corresponding folders in:
   $outputDir

OR use google-webfonts-helper:

1. Go to https://gwfh.mranftl.com/fonts
2. Search for each font
3. Select weights: 300, 400, 500, 600, 700
4. Download and extract to the bundled folder

Fonts to download:
$($fonts -join ", " -replace '\+', ' ')

"@
