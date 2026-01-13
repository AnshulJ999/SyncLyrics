"""
Font Scanner Module

Scans custom fonts directory and generates CSS @font-face rules.
Extracts font family names directly from font file metadata using fonttools.
"""
from pathlib import Path
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

# Lazy import to avoid loading fonttools unless needed
_TTFont = None

def _get_ttfont():
    """Lazy load fonttools.TTFont to avoid startup overhead."""
    global _TTFont
    if _TTFont is None:
        from fontTools.ttLib import TTFont
        _TTFont = TTFont
    return _TTFont


def get_font_info(font_path: Path) -> Tuple[str, int]:
    """
    Extract font family name and weight from font file metadata.
    
    Returns:
        Tuple of (family_name, weight) where weight is 100-900
    """
    try:
        TTFont = _get_ttfont()
        font = TTFont(font_path)
        name_table = font['name']
        
        family_name = None
        for record in name_table.names:
            if record.nameID == 1:  # Family name
                try:
                    family_name = record.toUnicode()
                    break
                except Exception:
                    continue
        
        # Try to get weight from OS/2 table
        weight = 400
        if 'OS/2' in font:
            weight = font['OS/2'].usWeightClass
        
        font.close()
        return family_name or font_path.stem, weight
    except Exception as e:
        logger.warning(f"Could not parse font {font_path.name}: {e}")
        # Fallback: use filename
        return font_path.stem, 400


def scan_custom_fonts(fonts_dir: Path) -> Dict[str, List[Tuple[Path, int]]]:
    """
    Scan custom fonts directory and return font info.
    
    Returns:
        Dict mapping font family names to list of (file_path, weight) tuples
    """
    fonts = {}
    custom_dir = fonts_dir / "custom"
    
    if not custom_dir.exists():
        return fonts
    
    for file in custom_dir.iterdir():
        if file.suffix.lower() not in ['.woff2', '.woff', '.ttf', '.otf']:
            continue
        if file.name.startswith('.'):
            continue  # Skip hidden files
        
        family_name, weight = get_font_info(file)
        
        if family_name not in fonts:
            fonts[family_name] = []
        fonts[family_name].append((file, weight))
    
    return fonts


def generate_custom_css(fonts_dir: Path) -> str:
    """
    Generate @font-face CSS rules for all custom fonts.
    
    Returns:
        CSS string with @font-face declarations
    """
    fonts = scan_custom_fonts(fonts_dir)
    
    if not fonts:
        return "/* No custom fonts found */"
    
    lines = ["/* ========== CUSTOM FONTS ========== */"]
    
    for font_name, variants in sorted(fonts.items()):
        lines.append(f"\n/* {font_name} */")
        for file_path, weight in sorted(variants, key=lambda x: x[1]):
            ext = file_path.suffix.lower()[1:]  # Remove dot
            if ext == 'woff2':
                fmt = 'woff2'
            elif ext == 'woff':
                fmt = 'woff'
            elif ext == 'ttf':
                fmt = 'truetype'
            elif ext == 'otf':
                fmt = 'opentype'
            else:
                fmt = 'truetype'
            
            lines.append(
                f"@font-face {{ font-family: '{font_name}'; font-weight: {weight}; "
                f"font-display: swap; src: url('/fonts/custom/{file_path.name}') format('{fmt}'); }}"
            )
    
    return '\n'.join(lines)


def get_custom_font_names(fonts_dir: Path) -> List[str]:
    """
    Get list of available custom font family names.
    
    Returns:
        Sorted list of font family names
    """
    return sorted(scan_custom_fonts(fonts_dir).keys())
