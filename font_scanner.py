"""
Font Scanner Module

Scans custom fonts directory and generates CSS @font-face rules.
Extracts font family names directly from font file metadata using fonttools.

IMPORTANT: Results are cached at first access. Adding new custom fonts
requires a server restart.
"""
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# Lazy import to avoid loading fonttools unless needed
_TTFont = None

# Cache for font data (populated once, never re-scanned)
_cached_fonts: Optional[Dict[str, List[Tuple[Path, int]]]] = None
_cached_css: Optional[str] = None
_cached_font_names: Optional[List[str]] = None


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
    
    Supports variable fonts (e.g., Rubik-VariableFont_wght.ttf) by reading
    from the embedded name table.
    
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
        # For variable fonts, this is typically 400 (the default instance)
        weight = 400
        if 'OS/2' in font:
            weight = font['OS/2'].usWeightClass
        
        font.close()
        return family_name or font_path.stem, weight
    except Exception as e:
        logger.warning(f"Could not parse font {font_path.name}: {e}")
        # Fallback: use filename
        return font_path.stem, 400


def _scan_custom_fonts_uncached(fonts_dir: Path) -> Dict[str, List[Tuple[Path, int]]]:
    """
    Internal: Scan custom fonts directory without caching.
    Recursively scans subdirectories to support folder-based font downloads.
    """
    fonts = {}
    custom_dir = fonts_dir / "custom"
    
    if not custom_dir.exists():
        return fonts
    
    # Use rglob to recursively find font files in subdirectories
    for ext in ['.woff2', '.woff', '.ttf', '.otf']:
        for file in custom_dir.rglob(f'*{ext}'):
            if file.name.startswith('.'):
                continue  # Skip hidden files
            
            family_name, weight = get_font_info(file)
            
            if family_name not in fonts:
                fonts[family_name] = []
            fonts[family_name].append((file, weight))
    
    return fonts


def scan_custom_fonts(fonts_dir: Path) -> Dict[str, List[Tuple[Path, int]]]:
    """
    Scan custom fonts directory and return font info.
    Results are cached after first call.
    
    Returns:
        Dict mapping font family names to list of (file_path, weight) tuples
    """
    global _cached_fonts
    if _cached_fonts is None:
        logger.info("Scanning custom fonts directory (one-time)")
        _cached_fonts = _scan_custom_fonts_uncached(fonts_dir)
        if _cached_fonts:
            logger.info(f"Found {len(_cached_fonts)} custom font(s): {list(_cached_fonts.keys())}")
        else:
            logger.info("No custom fonts found")
    return _cached_fonts


def generate_custom_css(fonts_dir: Path) -> str:
    """
    Generate @font-face CSS rules for all custom fonts.
    Results are cached after first call.
    
    Returns:
        CSS string with @font-face declarations
    """
    global _cached_css
    if _cached_css is not None:
        return _cached_css
    
    fonts = scan_custom_fonts(fonts_dir)
    custom_dir = fonts_dir / "custom"
    
    if not fonts:
        _cached_css = "/* No custom fonts found */"
        return _cached_css
    
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
            
            # Use relative path from custom/ to support subdirectories
            relative_path = file_path.relative_to(custom_dir).as_posix()
            
            lines.append(
                f"@font-face {{ font-family: '{font_name}'; font-weight: {weight}; "
                f"font-display: swap; src: url('/fonts/custom/{relative_path}') format('{fmt}'); }}"
            )
    
    _cached_css = '\n'.join(lines)
    return _cached_css


def get_custom_font_names(fonts_dir: Path) -> List[str]:
    """
    Get list of available custom font family names.
    Results are cached after first call.
    
    Returns:
        Sorted list of font family names
    """
    global _cached_font_names
    if _cached_font_names is None:
        _cached_font_names = sorted(scan_custom_fonts(fonts_dir).keys())
    return _cached_font_names

