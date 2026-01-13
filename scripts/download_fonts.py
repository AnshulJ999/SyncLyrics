#!/usr/bin/env python3
"""
SyncLyrics Font Downloader
Downloads all required Google Fonts using google-webfonts-helper API
Run: python download_fonts.py
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Fonts to download with their folder names
FONTS = [
    ("Inter", "inter"),
    ("Outfit", "outfit"),
    ("Poppins", "poppins"),
    ("Open Sans", "opensans"),
    ("Nunito", "nunito"),
    ("Roboto", "roboto"),
    ("Montserrat", "montserrat"),
    ("Work Sans", "worksans"),
    ("Oswald", "oswald"),
    ("Raleway", "raleway"),
    ("Bebas Neue", "bebasneue"),
    ("Space Grotesk", "spacegrotesk"),
    ("Playfair Display", "playfairdisplay"),
    ("Lora", "lora"),
    ("Fraunces", "fraunces"),
]

# Weights to download
WEIGHTS = ["300", "400", "500", "600", "700"]

# Output directory (relative to this script)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR.parent / "resources" / "fonts" / "bundled"


def get_font_id(font_name: str) -> str:
    """Convert font name to google-webfonts-helper ID format"""
    return font_name.lower().replace(" ", "-")


def download_font(font_name: str, folder_name: str) -> bool:
    """Download a single font family from google-webfonts-helper"""
    font_id = get_font_id(font_name)
    api_url = f"https://gwfh.mranftl.com/api/fonts/{font_id}"
    
    try:
        # Get font metadata
        req = Request(api_url, headers={"User-Agent": "SyncLyrics Font Downloader"})
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
        
        # Find available variants
        available_variants = [v["id"] for v in data.get("variants", [])]
        
        # Filter to weights we want (regular style only)
        wanted = []
        for w in WEIGHTS:
            variant_id = w if w != "400" else "regular"
            if variant_id in available_variants:
                wanted.append(variant_id)
            elif w in available_variants:
                wanted.append(w)
        
        if not wanted:
            print(f"  ⚠ No matching weights found for {font_name}")
            return False
        
        # Build download URL
        variants_param = ",".join(wanted)
        download_url = f"https://gwfh.mranftl.com/api/fonts/{font_id}?download=zip&subsets=latin&variants={variants_param}&formats=woff2,woff"
        
        # Create output directory
        output_path = OUTPUT_DIR / folder_name
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Download zip
        req = Request(download_url, headers={"User-Agent": "SyncLyrics Font Downloader"})
        with urlopen(req, timeout=60) as response:
            zip_data = response.read()
        
        # Extract to temp, then copy files
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "font.zip"
            zip_path.write_bytes(zip_data)
            
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(tmpdir)
            
            # Copy font files to output
            for file in Path(tmpdir).rglob("*"):
                if file.suffix.lower() in [".woff2", ".woff", ".ttf"]:
                    dest = output_path / file.name
                    shutil.copy2(file, dest)
        
        # Count files
        file_count = len(list(output_path.glob("*")))
        print(f"  ✓ {font_name}: {file_count} files")
        return True
        
    except HTTPError as e:
        print(f"  ✗ {font_name}: HTTP {e.code}")
        return False
    except URLError as e:
        print(f"  ✗ {font_name}: {e.reason}")
        return False
    except Exception as e:
        print(f"  ✗ {font_name}: {e}")
        return False


def main():
    print("=" * 50)
    print("SyncLyrics Font Downloader")
    print("=" * 50)
    print(f"\nOutput: {OUTPUT_DIR}")
    print(f"Fonts: {len(FONTS)}")
    print(f"Weights: {', '.join(WEIGHTS)}\n")
    
    # Create base directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    success = 0
    failed = 0
    
    for i, (font_name, folder_name) in enumerate(FONTS, 1):
        print(f"[{i}/{len(FONTS)}] Downloading {font_name}...")
        if download_font(font_name, folder_name):
            success += 1
        else:
            failed += 1
    
    print("\n" + "=" * 50)
    print(f"Complete! Success: {success}, Failed: {failed}")
    print("=" * 50)
    
    if failed > 0:
        print("\nSome fonts failed. You can manually download them from:")
        print("https://gwfh.mranftl.com/fonts")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
