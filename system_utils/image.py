"""
Image utilities for system_utils package.
Handles image I/O, color extraction, and format detection.

Dependencies: state (for caches)
"""
from __future__ import annotations
import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from . import state
from config import CACHE_DIR
from logging_config import get_logger

logger = get_logger(__name__)


def extract_dominant_colors_sync(image_path: Path) -> list:
    """
    Synchronous helper function for color extraction.
    This runs in a separate thread to avoid blocking the event loop.
    """
    try:
        if not image_path.exists():
            return ["#24273a", "#363b54"]

        # Open image and resize for faster processing
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img = img.resize((100, 100))  # Small size is enough for dominant colors
            
            # Quantize to more colors to get a better palette
            result = img.quantize(colors=10)
            palette = result.getpalette()[:30]  # Get first 10 RGB triplets
            
            colors = []
            for i in range(0, len(palette), 3):
                r, g, b = palette[i], palette[i+1], palette[i+2]
                # Skip very dark or very light colors unless we have no choice
                brightness = (r * 299 + g * 587 + b * 114) / 1000
                if 10 < brightness < 245:
                    colors.append(f"#{r:02x}{g:02x}{b:02x}")
            
            # Fallback if we filtered everything out
            if not colors:
                for i in range(0, len(palette), 3):
                    r, g, b = palette[i], palette[i+1], palette[i+2]
                    colors.append(f"#{r:02x}{g:02x}{b:02x}")

            # FINAL FALLBACK: If palette was empty or failed completely
            if not colors:
                return ["#24273a", "#363b54"]

            # Ensure we have 2 unique colors
            final_colors = []
            seen = set()
            for c in colors:
                if c not in seen:
                    final_colors.append(c)
                    seen.add(c)
                if len(final_colors) >= 2:
                    break
            
            while len(final_colors) < 2:
                final_colors.append(final_colors[0] if final_colors else "#363b54")
                
            return final_colors
            
    except Exception as e:
        logger.error(f"Color extraction failed: {e}")
        return ["#24273a", "#363b54"]


async def extract_dominant_colors(image_path: Path) -> list:
    """
    Extracts two dominant colors from an image using a simple quantization method.
    Results are cached in memory to prevent high CPU usage on repeated polls.
    
    This async version runs CPU-bound Pillow operations in a thread executor
    to prevent blocking the event loop, ensuring smooth lyrics animation.
    """
    path_str = str(image_path)
    
    # Check cache first with mtime validation (Fix: Optimize Color Extraction)
    try:
        current_mtime = image_path.stat().st_mtime
        if path_str in state._color_cache:
            cached_mtime, cached_colors = state._color_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_colors
    except FileNotFoundError:
        return ["#24273a", "#363b54"]
    except Exception as e:
        logger.debug(f"Error checking mtime for color cache: {e}")
    
    # Prevent cache from growing indefinitely - remove oldest entry if too large
    if len(state._color_cache) > state._MAX_CACHE_SIZE:
        oldest_key = next(iter(state._color_cache))
        state._color_cache.pop(oldest_key)
        logger.debug(f"Color cache: removed oldest entry (size was {state._MAX_CACHE_SIZE + 1})")
    
    # Run CPU-bound task in thread executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    final_colors = await loop.run_in_executor(None, extract_dominant_colors_sync, image_path)
    
    # Cache the result with current mtime
    try:
        current_mtime = image_path.stat().st_mtime
        state._color_cache[path_str] = (current_mtime, final_colors)
    except:
        pass
        
    return final_colors


def get_image_extension(data: bytes) -> str:
    """Detect image format from file header bytes."""
    if data.startswith(b'\xff\xd8'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'BM'):
        return '.bmp'
    if data.startswith(b'GIF8'):
        return '.gif'
    return '.jpg'


def determine_image_extension(url: str, content_type: str = None) -> str:
    """
    Determine the appropriate file extension for an image based on URL or Content-Type.
    
    Args:
        url: Image URL (may contain extension in path)
        content_type: HTTP Content-Type header (e.g., 'image/png', 'image/jpeg')
        
    Returns:
        File extension with dot (e.g., '.jpg', '.png', '.webp')
    """
    # First, try to get extension from Content-Type header (most reliable)
    if content_type:
        content_type_lower = content_type.lower().split(';')[0].strip()
        if 'image/jpeg' in content_type_lower or 'image/jpg' in content_type_lower:
            return '.jpg'
        elif 'image/png' in content_type_lower:
            return '.png'
        elif 'image/webp' in content_type_lower:
            return '.webp'
        elif 'image/gif' in content_type_lower:
            return '.gif'
        elif 'image/bmp' in content_type_lower:
            return '.bmp'
    
    # Fallback: try to extract from URL
    if url:
        url_lower = url.lower()
        # Check common image extensions in URL
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']:
            if ext in url_lower:
                # Find the last occurrence to get the actual extension
                idx = url_lower.rfind(ext)
                if idx > 0:
                    return '.jpg' if ext == '.jpeg' else ext  # Normalize .jpeg to .jpg
        # Check for query parameters that might indicate format
        if 'format=jpg' in url_lower or 'format=jpeg' in url_lower:
            return '.jpg'
        elif 'format=png' in url_lower:
            return '.png'
    
    # Default to JPG if we can't determine (most common format)
    return '.jpg'


def save_image_original(image_data: bytes, output_path: Path, file_extension: str = None) -> bool:
    """
    Save image data in its original format without conversion.
    Preserves the pristine quality of the source image.
    Uses atomic write pattern (temp file + os.replace) to prevent corruption.
    Includes retry logic for Windows file locking issues (antivirus, thumbnail cache).
    
    Args:
        image_data: Raw image bytes from the provider
        output_path: Path where to save the image file (should include correct extension)
        file_extension: Optional file extension (e.g., '.jpg', '.png'). 
                       If not provided, will be inferred from output_path.
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Sanity Check: Don't save empty or extremely tiny files (likely errors)
        if not image_data or len(image_data) < 100:
            logger.warning(f"Refusing to save empty/tiny image to {output_path} ({len(image_data) if image_data else 0} bytes)")
            return False

        # Ensure output_path has the correct extension
        if file_extension:
            # Replace extension if provided
            output_path = output_path.with_suffix(file_extension)
        
        # FIX: Use unique temp filename to prevent race conditions during rapid song skipping
        # This ensures atomic writes even if multiple downloads happen concurrently
        temp_filename = f"{output_path.stem}_{uuid.uuid4().hex}{output_path.suffix}.tmp"
        temp_path = output_path.parent / temp_filename
        
        try:
            # Write original bytes to temp file first (no conversion = no quality loss)
            with open(temp_path, 'wb') as f:
                f.write(image_data)
            
            # CRITICAL FIX: Atomic replace with retry for Windows file locking
            # Windows may temporarily lock files due to antivirus scanning, thumbnail cache, or delayed handle release
            # No lock needed here - each image has a unique filename, so no thread contention
            # Retry logic matches save_album_db_metadata() for consistency
            for attempt in range(3):
                try:
                    # Remove existing file if it exists (Windows requires this before replace)
                    if output_path.exists():
                        os.remove(output_path)
                    os.replace(temp_path, output_path)
                    return True
                except OSError as e:
                    if attempt < 2:
                        # Wait briefly before retry (0.1s, 0.2s)
                        time.sleep(0.1 * (attempt + 1))
                        logger.debug(f"Retry {attempt + 1}/3 for {output_path.name}: {e}")
                    else:
                        # Final attempt failed - log error and clean up
                        logger.error(f"Failed to atomically replace {output_path.name} after 3 attempts: {e}")
                        # Clean up temp file
                        if temp_path.exists():
                            try:
                                os.remove(temp_path)
                            except:
                                pass
                        raise
        except Exception as write_err:
            # Clean up temp file if it exists
            if temp_path.exists():
                try:
                    os.remove(temp_path)
                except:
                    pass
            raise write_err
        
    except Exception as e:
        logger.error(f"Failed to save image to {output_path}: {e}")
        return False


def get_cached_art_path() -> Optional[Path]:
    """
    Finds the cached album art file by checking common image extensions.
    Returns the file with the most recent modification time to avoid stale art race conditions.
    Supports: JPG, PNG, BMP, GIF, WebP (preserves original format).
    """
    candidates = []
    for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
        path = CACHE_DIR / f"current_art{ext}"
        if path.exists():
            candidates.append(path)
    
    if not candidates:
        return None
        
    # Return the file with the most recent modification time
    # This prevents returning an old/stale file if cleanup failed (e.g. .jpg vs .png)
    try:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    except Exception:
        # Fallback to first candidate if stat fails
        return candidates[0]


def get_cached_art_mtime() -> int:
    """Get the modification time of the current cached art for cache busting"""
    path = get_cached_art_path()
    if path and path.exists():
        return int(path.stat().st_mtime)
    return int(time.time())


def cleanup_old_art() -> None:
    """
    Removes previous album art files to prevent conflicts.
    
    When switching songs, the image format might change (e.g., PNG instead of JPG).
    If we don't delete the old file, get_cached_art_path() might return the stale file
    because it checks extensions in order (.jpg first, then .png, etc.).
    This function ensures only the current song's art exists.
    Supports: JPG, PNG, BMP, GIF, WebP (preserves original format).
    """
    for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
        try:
            path = CACHE_DIR / f"current_art{ext}"
            if path.exists():
                os.remove(path)
                logger.debug(f"Cleaned up old album art: {path.name}")
        except Exception as e:
            # Silently ignore errors (file might be in use or already deleted)
            logger.debug(f"Could not remove old art file {ext}: {e}")
