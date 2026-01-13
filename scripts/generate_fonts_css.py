#!/usr/bin/env python3
"""
Generate fonts.css from downloaded font files.
Run after download_fonts.py to create matching CSS.
"""

import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
FONTS_DIR = SCRIPT_DIR.parent / "resources" / "fonts" / "bundled"
OUTPUT_FILE = SCRIPT_DIR.parent / "resources" / "css" / "fonts.css"

# Font family display names
FONT_NAMES = {
    "inter": "Inter",
    "outfit": "Outfit",
    "poppins": "Poppins",
    "opensans": "Open Sans",
    "nunito": "Nunito",
    "roboto": "Roboto",
    "montserrat": "Montserrat",
    "worksans": "Work Sans",
    "oswald": "Oswald",
    "raleway": "Raleway",
    "bebasneue": "Bebas Neue",
    "spacegrotesk": "Space Grotesk",
    "playfairdisplay": "Playfair Display",
    "lora": "Lora",
    "fraunces": "Fraunces",
}

# Weight mapping
WEIGHT_MAP = {
    "300": 300,
    "regular": 400,
    "500": 500,
    "600": 600,
    "700": 700,
}

def generate_fonts_css():
    lines = [
        "/* ========== BUNDLED FONTS ========== */",
        "/* Auto-generated from downloaded font files */",
        "",
    ]
    
    for folder_name, display_name in FONT_NAMES.items():
        folder_path = FONTS_DIR / folder_name
        if not folder_path.exists():
            print(f"Skipping {display_name} - folder not found")
            continue
        
        # Get all woff2 files
        woff2_files = list(folder_path.glob("*.woff2"))
        if not woff2_files:
            print(f"Skipping {display_name} - no woff2 files")
            continue
        
        lines.append(f"/* {display_name} */")
        
        for woff2 in sorted(woff2_files):
            # Extract weight from filename
            filename = woff2.stem  # e.g., "inter-v20-latin-regular"
            
            # Find weight
            weight = None
            for w_str, w_val in WEIGHT_MAP.items():
                if filename.endswith(f"-{w_str}"):
                    weight = w_val
                    break
            
            if weight is None:
                continue
            
            woff_file = woff2.with_suffix(".woff")
            woff_exists = woff_file.exists()
            
            # Build src
            src_parts = [f"url('/fonts/bundled/{folder_name}/{woff2.name}') format('woff2')"]
            if woff_exists:
                src_parts.append(f"url('/fonts/bundled/{folder_name}/{woff_file.name}') format('woff')")
            
            src = ", ".join(src_parts)
            
            lines.append(
                f"@font-face {{ font-family: '{display_name}'; font-weight: {weight}; font-display: swap; src: {src}; }}"
            )
        
        lines.append("")
    
    # Add CSS variables
    lines.extend([
        "/* ========== CSS VARIABLES FOR FONTS & STYLING ========== */",
        ":root {",
        "    --system-font-stack: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;",
        "    --ui-font-family: var(--system-font-stack);",
        "    --lyrics-font-family: var(--system-font-stack);",
        "    --lyrics-glow-intensity: 1;",
        "    --lyrics-text-color: #ffffff;",
        "    --lyrics-font-weight: 400;",
        "    --lyrics-font-weight-current: 500;",
        "}",
    ])
    
    # Write output
    OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Font families: {len(FONT_NAMES)}")

if __name__ == "__main__":
    generate_fonts_css()
