from __future__ import annotations
import subprocess
import platform
import re
import time
import asyncio
from typing import Optional, Dict, Any, List, Tuple
import config
from config import DEBUG, FEATURES, ALBUM_ART_DB_DIR
from state_manager import get_state, set_state
from providers.spotify_api import get_shared_spotify_client
from providers.album_art import get_album_art_provider
from logging_config import get_logger
from config import CACHE_DIR
import os
from functools import lru_cache
from PIL import Image
from pathlib import Path
import requests
import logging
import json
import shutil
from datetime import datetime
from io import BytesIO

# Initialize Logger
logger = get_logger(__name__)

# Intervals
ACTIVE_INTERVAL = config.LYRICS["display"]["update_interval"]
IDLE_INTERVAL = config.LYRICS["display"]["idle_interval"]
IDLE_WAIT_TIME = config.LYRICS["display"]["idle_wait_time"]

# Globals
# NOTE: spotify_client is now obtained via get_shared_spotify_client() for singleton pattern
# This ensures all stats are consolidated across the entire app
_last_state_log_time = 0
STATE_LOG_INTERVAL = 300  # Log app state every 300 seconds (5 minutes)
# Track metadata fetch calls (not the same as API calls - one fetch may use cache)
_metadata_fetch_counters = {'spotify': 0, 'windows_media': 0}
_last_windows_track_id = None  # Track ID to avoid re-reading thumbnail
# Track running background art upgrade tasks to prevent duplicates
_running_art_upgrade_tasks = {}  # Key: track_id, Value: asyncio.Task
# NEW: Track which songs we've already checked/populated the DB for to prevent infinite loops
_db_checked_tracks = set()
_MAX_DB_CHECKED_SIZE = 100
# OPTIMIZATION: Semaphore to limit concurrent background downloads (Fix #4)
# Prevents network saturation if user skips many tracks quickly
_art_download_semaphore = asyncio.Semaphore(2)

# Global set to track background tasks and prevent garbage collection (Fix: Task Tracking)
_background_tasks = set()

# OPTIMIZATION: Track in-progress downloads to prevent polling loop from spawning duplicates
_spotify_download_tracker = set()

def create_tracked_task(coro):
    """
    Create a background task with automatic cleanup and error logging.
    Prevents silent failures and ensures tasks complete even if references are lost.
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    
    def cleanup(t):
        _background_tasks.discard(t)
        try:
            t.result()
        except asyncio.CancelledError:
            pass  # Expected during shutdown
        except Exception as e:
            logger.error(f"Background task failed: {e}", exc_info=True)
    
    task.add_done_callback(cleanup)
    return task

# Cache for color extraction to avoid re-processing the same image
# Key: file_path, Value: (mtime, [color1, color2])
# Limited to 50 entries to prevent memory leaks
_color_cache = {}
_MAX_CACHE_SIZE = 50

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
        if path_str in _color_cache:
            cached_mtime, cached_colors = _color_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_colors
    except FileNotFoundError:
        return ["#24273a", "#363b54"]
    except Exception as e:
        logger.debug(f"Error checking mtime for color cache: {e}")
    
    # Prevent cache from growing indefinitely - remove oldest entry if too large
    if len(_color_cache) > _MAX_CACHE_SIZE:
        oldest_key = next(iter(_color_cache))
        _color_cache.pop(oldest_key)
        logger.debug(f"Color cache: removed oldest entry (size was {_MAX_CACHE_SIZE + 1})")
    
    # Run CPU-bound task in thread executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    final_colors = await loop.run_in_executor(None, extract_dominant_colors_sync, image_path)
    
    # Cache the result with current mtime
    try:
        current_mtime = image_path.stat().st_mtime
        _color_cache[path_str] = (current_mtime, final_colors)
    except:
        pass
        
    return final_colors

# --- Helper Functions ---

def _remove_text_inside_parentheses_and_brackets(text: str) -> str:
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", '', text)

def _log_app_state() -> None:
    """Log key application state periodically."""
    global _last_state_log_time
    current_time = time.time()
    
    if current_time - _last_state_log_time < STATE_LOG_INTERVAL:
        return
        
    _last_state_log_time = current_time
    
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')

    # Update state file
    state = get_state()
    state['current_song'] = last_song
    state['active_source'] = last_source
    set_state(state)

    # --- LOGGING LOGIC ---
    # We log if the level is INFO or lower, regardless of "Debug Mode" toggle.
    if logger.isEnabledFor(logging.INFO):
        current_time_str = time.strftime("%I:%M %p - %b %d, %Y")
        
        # Base state summary
        state_summary = (
            f"\nApplication State Summary:\n"
            f"|- Time: {current_time_str}\n"
            f"|- Mode: {'Active' if is_active else 'Idle'}\n"
            f"|- Current Song: {last_song}\n"
            f"|- Active Source: {last_source}\n"
            f"|- Metadata Fetches:\n"
            f"|  |- Spotify: {_metadata_fetch_counters['spotify']}\n"
            f"|  `- Windows Media: {_metadata_fetch_counters['windows_media']}\n"
        )
        logger.info(state_summary)

        # Log Spotify API stats if available (this is the important one for rate limits)
        # Use shared singleton instance to get consolidated stats from entire app
        spotify_client = get_shared_spotify_client()
        if spotify_client and spotify_client.initialized:
            try:
                stats = spotify_client.get_request_stats()
                
                # Calculate requests per hour for rate limit awareness
                # Spotify's rate limit is typically ~180 requests/minute
                total_requests = stats['Total Requests']
                
                spotify_stats = (
                    "\nSpotify API Statistics:\n"
                    f"|- Total API Requests: {total_requests}\n"
                    f"|- Total Function Calls: {stats['Total Function Calls']}\n"
                    f"|- Cache Hits: {stats['Cached Responses']} ({stats['Cache Hit Rate']})\n"
                    f"|- API Calls by Endpoint:\n"
                )
            
                for endpoint, count in stats['API Calls'].items():
                    if count > 0:  # Only show endpoints that have been called
                        spotify_stats += f"|  |- {endpoint}: {count}\n"
                
                # Always show errors section if there are any errors
                total_errors = sum(stats['Errors'].values())
                if total_errors > 0:
                    spotify_stats += f"|- Errors ({total_errors} total):\n"
                    for error_type, count in stats['Errors'].items():
                        if count > 0:  # Only show error types that occurred
                            spotify_stats += f"|  |- {error_type}: {count}\n"
                
                spotify_stats += f"`- Cache Age: {stats['Cache Age']}"
                logger.info(spotify_stats)
                
            except Exception as e:
                logger.error(f"Failed to log Spotify stats: {e}")

# --- Platform Specific Logic ---

DESKTOP = platform.system()
_win_media_manager = None

if DESKTOP == "Linux":
    try:
        process = subprocess.Popen("gsettings get org.gnome.desktop.interface color-scheme",
            shell=True, stdout=subprocess.PIPE)
        process.wait()
        if process.returncode == 0:
            DESKTOP = "Gnome"
    except Exception:
        pass

elif DESKTOP == "Windows":
    try:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
        from winsdk.windows.storage.streams import DataReader
    except ImportError:
        logger.error("Winsdk not installed. Windows Media integration will not work.")
        MediaManager = None

# --- Metadata Fetching Functions ---

def _get_current_song_meta_data_gnome() -> Optional[dict]:
    """Gnome/Linux metadata fetcher with standardized output."""
    try:
        command = "playerctl metadata --format '{{artist}}`{{title}}`{{album}}`{{position}}`{{mpris:artUrl}}'"
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        output = process.stdout.read().decode("utf-8").split("`")
        if len(output) < 4: return None 
        
        artist, title, album, position = output[:4]
        art_url = output[4].strip() if len(output) > 4 else None
        
        if not album: 
            title = _remove_text_inside_parentheses_and_brackets(title)
            artist = "" 

        return {
            "artist": artist.strip(), 
            "title": title.strip(),
            "album": album.strip() if album else None,
            "position": int(position)/1000000,
            "duration_ms": None,  # Not available from playerctl
            "colors": ("#24273a", "#363b54"),
            "album_art_url": art_url,
            "is_playing": True,
            "source": "gnome"
        }
    except Exception:
        return None

def get_image_extension(data: bytes) -> str:
    if data.startswith(b'\xff\xd8'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'BM'):
        return '.bmp'
    if data.startswith(b'GIF8'):
        return '.gif'
    return '.jpg'

def get_cached_art_path() -> Optional[Path]:
    """
    Finds the cached album art file by checking common image extensions.
    Returns the first matching file found.
    Supports: JPG, PNG, BMP, GIF, WebP (preserves original format).
    """
    for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
        path = CACHE_DIR / f"current_art{ext}"
        if path.exists():
            return path
    return None

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

# ==========================================
# Album Art Database Functions
# ==========================================

def sanitize_folder_name(name: str) -> str:
    """
    Sanitize a string to be safe for use as a folder name.
    Replaces illegal characters with underscores for cross-platform compatibility.
    
    Args:
        name: String to sanitize
        
    Returns:
        Sanitized string safe for folder names
    """
    if not name:
        return "Unknown"
    
    # Replace illegal characters for Windows/Linux/Docker compatibility
    # Illegal chars: / \ : * ? " < > |
    illegal_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(illegal_chars, '_', name)
    
    # Remove leading/trailing spaces and dots (Windows doesn't allow these)
    sanitized = sanitized.strip(' .')
    
    # Truncate if too long (Windows has 260 char path limit, but we'll be conservative)
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
    
    # If empty after sanitization, use fallback
    if not sanitized:
        sanitized = "Unknown"
    
    return sanitized

def get_album_db_folder(artist: str, album: Optional[str]) -> Path:
    """
    Get the database folder path for an album.
    Uses Artist - Album format, with fallback to Artist - Title if no album.
    
    Args:
        artist: Artist name
        album: Album name (optional, falls back to title if None)
        
    Returns:
        Path to the album database folder
    """
    safe_artist = sanitize_folder_name(artist or "Unknown")
    
    # Use album if available, otherwise we'll use title when called
    if album:
        safe_album = sanitize_folder_name(album)
        folder_name = f"{safe_artist} - {safe_album}"
    else:
        # This will be used when album is None - caller should pass title
        folder_name = safe_artist
    
    return ALBUM_ART_DB_DIR / folder_name

def save_image_original(image_data: bytes, output_path: Path, file_extension: str = None) -> bool:
    """
    Save image data in its original format without conversion.
    Preserves the pristine quality of the source image.
    
    Args:
        image_data: Raw image bytes from the provider
        output_path: Path where to save the image file (should include correct extension)
        file_extension: Optional file extension (e.g., '.jpg', '.png'). 
                       If not provided, will be inferred from output_path.
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure output_path has the correct extension
        if file_extension:
            # Replace extension if provided
            output_path = output_path.with_suffix(file_extension)
        
        # Write original bytes directly (no conversion = no quality loss)
        with open(output_path, 'wb') as f:
            f.write(image_data)
        
        return True
    except Exception as e:
        logger.error(f"Failed to save image to {output_path}: {e}")
        return False

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
                    return ext
        
        # Check for query parameters that might indicate format
        if 'format=jpg' in url_lower or 'format=jpeg' in url_lower:
            return '.jpg'
        elif 'format=png' in url_lower:
            return '.png'
    
    # Default to JPG if we can't determine (most common format)
    return '.jpg'

def save_album_db_metadata(folder: Path, metadata: Dict[str, Any]) -> bool:
    """
    Save album art database metadata JSON file atomically.
    
    Args:
        folder: Path to the album folder
        metadata: Dictionary containing metadata to save
        
    Returns:
        True if successful, False otherwise
    """
    try:
        metadata_path = folder / "metadata.json"
        temp_path = folder / "metadata.json.tmp"
        
        # Ensure folder exists
        folder.mkdir(parents=True, exist_ok=True)
        
        # Write to temp file first
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        # Atomic replace with retry for Windows file locking
        for attempt in range(3):
            try:
                if metadata_path.exists():
                    os.remove(metadata_path)
                os.replace(temp_path, metadata_path)
                return True
            except OSError as e:
                if attempt < 2:
                    # Wait briefly before retry (0.1s, 0.2s)
                    time.sleep(0.1 * (attempt + 1))
                else:
                    logger.error(f"Failed to atomically replace metadata.json after 3 attempts: {e}")
                    # Clean up temp file
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    return False
    except Exception as e:
        logger.error(f"Failed to save album DB metadata: {e}")
        return False

def _download_and_save_sync(url: str, path: Path) -> Tuple[bool, str]:
    """
    Helper function to run download and save in thread executor.
    This performs blocking I/O operations (network request and file save).
    Preserves the original image format without conversion.
    
    Args:
        url: URL to download image from
        path: Path where to save the image file (extension will be determined automatically)
        
    Returns:
        Tuple of (success: bool, extension: str)
        - success: True if download and save succeeded, False otherwise
        - extension: File extension used (e.g., '.jpg', '.png')
    """
    try:
        response = requests.get(url, timeout=10, stream=True)
        response.raise_for_status()
        
        # Get Content-Type from response headers
        content_type = response.headers.get('Content-Type', '')
        
        # Determine file extension from URL or Content-Type
        file_extension = determine_image_extension(url, content_type)
        
        # Save original image bytes (no conversion = pristine quality)
        success = save_image_original(response.content, path, file_extension)
        
        return (success, file_extension)
    except Exception as e:
        logger.warning(f"Download failed for {url}: {e}")
        return (False, '.jpg')  # Return default extension on failure

async def ensure_album_art_db(artist: str, album: Optional[str], title: str, spotify_url: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """
    Background task to fetch all album art options and save them to the database.
    Downloads images from all providers and saves them in their original format (pristine quality).
    Creates metadata.json with URLs, resolutions, and preferences.
    
    Args:
        artist: Artist name
        album: Album name (optional)
        title: Track title
        spotify_url: Spotify album art URL (optional)
        
    Returns:
        Tuple of (preferred_url, resolution_str) of the selected art, or None if failed.
    """
    # OPTIMIZATION: Acquire semaphore to limit concurrent downloads (Fix #4)
    # This prevents network saturation if user skips many tracks quickly
    async with _art_download_semaphore:
        logger.debug(f"DEBUG: Entering ensure_album_art_db for {artist} - {title}")  # Debug Log 1

        # Check if feature is enabled
        enabled = FEATURES.get("album_art_db", True)
        logger.debug(f"DEBUG: album_art_db enabled: {enabled}")  # Debug Log 2
        if not enabled:
            return None
    
        try:
            # Get album art provider
            art_provider = get_album_art_provider()
            
            # Fetch all options in parallel
            logger.debug(f"DEBUG: Calling get_all_art_options...")  # Debug Log 3
            options = await art_provider.get_all_art_options(artist, album, title, spotify_url)
            logger.debug(f"DEBUG: get_all_art_options returned {len(options)} options")  # Debug Log 4
            
            if not options:
                logger.debug(f"No album art options found for {artist} - {album or title}")
                return None
            
            # Get folder path
            folder = get_album_db_folder(artist, album or title)
            folder.mkdir(parents=True, exist_ok=True)
            
            # Check if metadata already exists (to avoid re-downloading)
            metadata_path = folder / "metadata.json"
            existing_metadata = None
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        existing_metadata = json.load(f)
                except:
                    pass
            
            # Download and save images for each provider
            # FIX: Initialize with existing data so we don't wipe out providers if a network call fails
            providers_data = existing_metadata.get("providers", {}) if existing_metadata else {}
            
            preferred_provider = None
            highest_resolution = 0
            
            # Re-calculate highest resolution from EXISTING data first
            for provider_name, data in providers_data.items():
                width = data.get("width", 0)
                height = data.get("height", 0)
                res = max(width, height)
                if res > highest_resolution and data.get("downloaded", False):
                    highest_resolution = res
                    preferred_provider = provider_name

            # Get event loop for running blocking I/O in executor
            loop = asyncio.get_running_loop()
            
            for option in options:
                provider_name = option["provider"]
                url = option["url"]
                resolution_str = option["resolution"]
                
                # Extract resolution for comparison
                width = option.get("width", 0)
                height = option.get("height", 0)
                resolution = max(width, height) if width > 0 and height > 0 else 0
                
                # Check if we already have this image (check metadata for correct filename)
                image_filename = None
                if existing_metadata and provider_name in existing_metadata.get("providers", {}):
                    # Use existing filename from metadata (preserves original extension)
                    image_filename = existing_metadata["providers"][provider_name].get("filename", f"{provider_name}.jpg")
                else:
                    # Default filename (will be updated after download with correct extension)
                    image_filename = f"{provider_name}.jpg"
                
                image_path = folder / image_filename
                
                # NEW: Explicitly check if the file exists on disk, even if metadata says it does
                # This fixes cases where user might have deleted images but metadata.json remains
                file_exists_on_disk = image_path.exists()

                # Download image if we don't have it or if it's missing
                if not file_exists_on_disk or (existing_metadata and provider_name not in existing_metadata.get("providers", {})):
                    try:
                        # Create a temporary path without extension (will be set by download function)
                        temp_path = folder / provider_name
                        
                        # Run blocking download/save in executor to avoid freezing the event loop
                        # Returns (success: bool, extension: str)
                        success, file_extension = await loop.run_in_executor(
                            None,
                            _download_and_save_sync,
                            url,
                            temp_path
                        )
                        
                        if success:
                            # Update filename with correct extension
                            image_filename = f"{provider_name}{file_extension}"
                            image_path = folder / image_filename
                            
                            # If temp file has different name, rename it
                            temp_path_with_ext = temp_path.with_suffix(file_extension)
                            if temp_path_with_ext.exists() and temp_path_with_ext != image_path:
                                # Move to final location
                                try:
                                    os.replace(temp_path_with_ext, image_path)
                                except:
                                    # If replace fails, try copy then delete
                                    shutil.copy2(temp_path_with_ext, image_path)
                                    try:
                                        os.remove(temp_path_with_ext)
                                    except:
                                        pass
                            
                            logger.info(f"Downloaded and saved {provider_name} art ({file_extension}) for {artist} - {album or title}")
                            
                            # Get actual resolution from saved image (also run in executor since it's I/O)
                            try:
                                def get_image_resolution(path: Path) -> tuple:
                                    with Image.open(path) as img:
                                        return img.size
                                
                                actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution, image_path)
                                resolution = max(actual_width, actual_height)
                                resolution_str = f"{actual_width}x{actual_height}"
                                # Update width/height with actual values
                                width = actual_width
                                height = actual_height
                                logger.info(f"Verified resolution for {provider_name}: {resolution_str}") # Add success log
                            except Exception as e:
                                logger.warning(f"Failed to verify resolution for {image_path}: {e}") # Log error
                        else:
                            logger.warning(f"Failed to save {provider_name} art for {artist} - {album or title}")
                            continue
                    except Exception as e:
                        logger.warning(f"Failed to download {provider_name} art: {e}")
                        continue
                else:
                    # Image exists, get resolution from file (run in executor to avoid blocking)
                    try:
                        def get_image_resolution_existing(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution_existing, image_path)
                        resolution = max(actual_width, actual_height)
                        resolution_str = f"{actual_width}x{actual_height}"
                        # Update width/height with actual values
                        width = actual_width
                        height = actual_height
                        logger.info(f"Verified existing resolution for {provider_name}: {resolution_str}") # Add success log
                    except Exception as e:
                        logger.warning(f"Failed to verify existing resolution for {image_path}: {e}") # Log error
                        # Fallback to metadata if available
                        if existing_metadata and provider_name in existing_metadata.get("providers", {}):
                            existing_provider_data = existing_metadata["providers"][provider_name]
                            resolution_str = existing_provider_data.get("resolution", resolution_str)
                
                # Store provider data (with actual filename including extension)
                providers_data[provider_name] = {
                    "url": url,
                    "resolution": resolution_str,
                    "width": width,
                    "height": height,
                    "filename": image_filename,  # Now includes correct extension (e.g., "iTunes.png")
                    "downloaded": image_path.exists()
                }
                
                # Track highest resolution for auto-selection
                # FIX: Only select as preferred if the file was successfully downloaded/exists
                if resolution > highest_resolution and image_path.exists():
                    highest_resolution = resolution
                    preferred_provider = provider_name
            
            # Use existing preference if available, otherwise use highest resolution
            if existing_metadata and "preferred_provider" in existing_metadata:
                preferred_provider = existing_metadata["preferred_provider"]
            
            # Create metadata structure
            metadata = {
                "artist": artist,
                "album": album or title,
                "is_single": album is None or album.lower() == title.lower(),
                "preferred_provider": preferred_provider,
                "created_at": existing_metadata.get("created_at") if existing_metadata else datetime.utcnow().isoformat() + "Z",
                "last_accessed": datetime.utcnow().isoformat() + "Z",
                "providers": providers_data
            }
            
            # Save metadata
            # OPTIMIZATION: Run file I/O in executor to avoid blocking event loop (Fix #4)
            # This prevents UI stutters if disk is busy or antivirus is scanning
            if await loop.run_in_executor(None, save_album_db_metadata, folder, metadata):
                logger.info(f"Saved album art database for {artist} - {album or title} with {len(providers_data)} providers")
                
                # Return the preferred provider info for immediate cache update
                if preferred_provider and preferred_provider in providers_data:
                    p_data = providers_data[preferred_provider]
                    return (p_data["url"], p_data["resolution"])
            else:
                logger.error(f"Failed to save album art database metadata for {artist} - {album or title}")
        
        except Exception as e:
            logger.error(f"Error in ensure_album_art_db: {e}")
            
        return None

def _save_windows_thumbnail_sync(path: Path, data: bytes) -> bool:
    """
    Helper function to save Windows thumbnail in a thread (Fix #2).
    This prevents blocking the event loop when writing large BMP files.
    
    Args:
        path: Path where to save the thumbnail
        data: Raw image bytes to write
        
    Returns:
        True if successful, False otherwise
    """
    try:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        # Write to temp file first
        with open(temp_path, "wb") as f:
            f.write(data)
        # Atomic replace
        if path.exists():
            try:
                os.remove(path)
            except:
                pass
        os.replace(temp_path, path)
        return True
    except Exception as e:
        logger.debug(f"Failed to save Windows thumbnail: {e}")
        try:
            if temp_path.exists():
                os.remove(temp_path)
        except:
            pass
        return False

def load_album_art_from_db(artist: str, album: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Load album art from database if available.
    Returns the preferred image path if found.
    
    Args:
        artist: Artist name
        album: Album name (optional)
        
    Returns:
        Dictionary with 'path' (Path to image) and 'metadata' (full metadata dict) if found, None otherwise
    """
    # Check if feature is enabled
    if not FEATURES.get("album_art_db", True):
        return None
    
    try:
        folder = get_album_db_folder(artist, album)
        metadata_path = folder / "metadata.json"
        
        if not metadata_path.exists():
            return None
        
        # Load metadata
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # Get preferred provider
        preferred_provider = metadata.get("preferred_provider")
        if not preferred_provider:
            # Auto-select highest resolution if no preference
            providers = metadata.get("providers", {})
            if not providers:
                return None
            
            highest_res = 0
            preferred_provider = None
            for provider_name, provider_data in providers.items():
                width = provider_data.get("width", 0)
                height = provider_data.get("height", 0)
                res = max(width, height)
                if res > highest_res:
                    highest_res = res
                    preferred_provider = provider_name
            
            if not preferred_provider:
                # Fallback to first available
                preferred_provider = list(providers.keys())[0]
        
        # Get image path
        providers = metadata.get("providers", {})
        if preferred_provider not in providers:
            return None
        
        provider_data = providers[preferred_provider]
        filename = provider_data.get("filename", f"{preferred_provider}.jpg")
        image_path = folder / filename
        
        if not image_path.exists():
            return None
        
        # Update last_accessed
        metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
        save_album_db_metadata(folder, metadata)
        
        return {
            "path": image_path,
            "metadata": metadata
        }
    
    except Exception as e:
        logger.debug(f"Error loading album art from DB: {e}")
        return None

async def _download_spotify_art_background(url: str, track_id: str) -> None:
    """
    Background task to download Spotify art (Fix #3).
    This allows the metadata function to return immediately without waiting for the download.
    Includes race condition protection using track_id validation.
    
    Args:
        url: Spotify album art URL to download
        track_id: ID of the track requesting the art (for validation)
    """
    # Use semaphore to limit concurrent downloads (Fix: Apply Semaphore)
    async with _art_download_semaphore:
        try:
            # Check if file already exists and matches URL
            if (CACHE_DIR / "spotify_art.jpg").exists():
                if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_art_url') and \
                   _get_current_song_meta_data_spotify._last_spotify_art_url == url:
                    return

            logger.debug(f"Starting background download of Spotify art: {url}")
            
            # Download in executor to avoid blocking
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(url, timeout=5)
            )
            
            if response.status_code == 200:
                # Validation: Check if track is still current before saving (Fix: Race Condition)
                spotify_client = get_shared_spotify_client()
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = current_track.get("track_id") or f"{current_track.get('artist', '')}::{current_track.get('title', '')}"
                    if current_track_id != track_id:
                        logger.debug(f"Track changed during download ({track_id} -> {current_track_id}), discarding art")
                        return

                # Save to cache
                art_path = CACHE_DIR / "spotify_art.jpg"
                temp_path = CACHE_DIR / "spotify_art.jpg.tmp"
                
                # Write to temp (blocking I/O in executor)
                def write_file():
                    with open(temp_path, "wb") as f:
                        f.write(response.content)
                
                await loop.run_in_executor(None, write_file)
                
                # Final Validation: Check one last time before atomic replace
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = current_track.get("track_id") or f"{current_track.get('artist', '')}::{current_track.get('title', '')}"
                    if current_track_id != track_id:
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                        return

                # Atomic replace with retry
                replaced = False
                for attempt in range(3):
                    try:
                        os.replace(temp_path, art_path)
                        replaced = True
                        break
                    except OSError:
                        if attempt < 2:
                            await asyncio.sleep(0.1)
                        else:
                            logger.debug(f"Could not atomically replace spotify_art.jpg after 3 attempts (file may be locked)")
                
                if not replaced:
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    return

                # Verify resolution (optional, fast enough)
                try:
                    from PIL import Image
                    with Image.open(art_path) as img:
                        logger.info(f"Downloaded album art actual resolution: {img.size[0]}x{img.size[1]}")
                except:
                    pass

                # Invalidate color cache (managed by extract_dominant_colors mtime check now)
                
                # Extract colors
                colors = await extract_dominant_colors(art_path)
                
                # Update cache
                _get_current_song_meta_data_spotify._last_spotify_art_url = url
                _get_current_song_meta_data_spotify._last_spotify_colors = colors
                
        except Exception as e:
            logger.debug(f"Background Spotify art download failed: {e}")
        finally:
            # FIX: Ensure URL is removed from tracker when done, even if error occurred
            _spotify_download_tracker.discard(url)

async def _get_current_song_meta_data_windows() -> Optional[dict]:
    """Windows Media metadata fetcher with standardized output."""
    global _win_media_manager
    if not MediaManager: return None

    try:
        # Track metadata fetch (always, not just in debug mode)
        _metadata_fetch_counters['windows_media'] += 1
            
        if _win_media_manager is None:
            _win_media_manager = await MediaManager.request_async()
        if not _win_media_manager: return None

        current_session = _win_media_manager.get_current_session()
        if not current_session: return None
            
        playback_info = current_session.get_playback_info()
        if not playback_info or playback_info.playback_status != 4:
            return None
            
        info = await current_session.try_get_media_properties_async()
        if not info: return None
            
        artist = info.artist
        title = info.title
        album = info.album_title

        if not album:
            title = _remove_text_inside_parentheses_and_brackets(title)
            artist = ""

        timeline = current_session.get_timeline_properties()
        if not timeline: return None
            
        seconds = timeline.position.total_seconds()
        
        # Check for invalid timestamp (Windows epoch 1601-01-01)
        # We use a safe threshold like year 2000
        if timeline.last_updated_time.year < 2000:
            # Invalid timestamp means we can't calculate elapsed time
            # If position is also 0, we probably have no data
            if seconds == 0:
                return None
            position = seconds
        else:
            elapsed = time.time() - timeline.last_updated_time.timestamp()
            position = seconds + elapsed
        
        # Get duration if available
        duration_ms = None
        try:
            duration_ms = int(timeline.end_time.total_seconds() * 1000)
        except:
            pass

        # Create track ID
        global _last_windows_track_id
        current_track_id = f"{artist}:{title}"
        
        # Flag to track if we found art in DB
        found_in_db = False
        album_art_url = None

        # 1. Check Album Art Database first (Fast Path)
        db_result = load_album_art_from_db(artist, album)
        if db_result:
            found_in_db = True
            db_image_path = db_result["path"]
            
            # Use track ID as cache buster
            album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
            
            # Check if we need to copy to cache
            should_copy = True
            # Check if current cache file exists and is high-res
            cached_art = get_cached_art_path()
            if cached_art:
                try:
                    with Image.open(cached_art) as img:
                        # If cached art is already large, don't re-copy needlessly
                        # But if it's small (Windows thumb), force overwrite
                        if img.width >= 900 and img.height >= 900:
                            should_copy = False
                except:
                    should_copy = True
            
            if should_copy:
                try:
                    # FIX: Atomic Copy (No cleanup_old_art first)
                    original_extension = db_image_path.suffix or '.jpg'
                    cache_path = CACHE_DIR / f"current_art{original_extension}"
                    temp_path = CACHE_DIR / f"current_art{original_extension}.tmp"
                    
                    shutil.copy2(db_image_path, temp_path)
                    os.replace(temp_path, cache_path)
                    
                    # Clean up stale extensions ONLY after new file is in place
                    for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
                        if ext == original_extension: continue
                        try:
                            stale = CACHE_DIR / f"current_art{ext}"
                            if stale.exists(): os.remove(stale)
                        except: pass
                except Exception as e:
                    logger.debug(f"Failed to copy DB art to cache: {e}")

        # 2. Windows Thumbnail Extraction (Fallback)
        # Only if not found in DB
        if not found_in_db:
            try:
                thumbnail_ref = info.thumbnail
                if thumbnail_ref and current_track_id != _last_windows_track_id:
                    stream = await thumbnail_ref.open_read_async()
                    if stream:
                        reader = DataReader(stream)
                        await reader.load_async(stream.size)
                        byte_data = bytearray(stream.size)
                        reader.read_bytes(byte_data)
                        
                        ext = get_image_extension(byte_data)
                        art_path = CACHE_DIR / f"current_art{ext}"
                        
                        # Save thumbnail
                        # FIX: Atomic Save
                        loop = asyncio.get_running_loop()
                        save_ok = await loop.run_in_executor(None, _save_windows_thumbnail_sync, art_path, byte_data)
                        
                        if save_ok:
                            # Clean up stale extensions
                            for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
                                if ext == get_image_extension(byte_data): continue
                                try:
                                    stale = CACHE_DIR / f"current_art{ext}"
                                    if stale.exists(): os.remove(stale)
                                except: pass
                            
                            _last_windows_track_id = current_track_id
                            album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
                elif thumbnail_ref:
                     # Reuse existing
                     album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
            except Exception as e:
                pass

        # 3. Background High-Res Fetch (Progressive Upgrade)
        # Only if not found in DB and not checked this session
        if not found_in_db and current_track_id not in _db_checked_tracks:
            if current_track_id not in _running_art_upgrade_tasks:
                 _db_checked_tracks.add(current_track_id)
                 if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                     _db_checked_tracks.pop()
                     
                 async def background_windows_art_upgrade():
                     try:
                         # Fetch and save to DB (no spotify_url available)
                         await ensure_album_art_db(artist, album, title, None)
                         # We don't need to do anything else; the NEXT poll loop 
                         # will see the file in DB (step 1 above) and auto-upgrade the UI.
                     except Exception as e:
                         logger.debug(f"Windows background art fetch failed: {e}")
                     finally:
                         _running_art_upgrade_tasks.pop(current_track_id, None)
                         
                 task = create_tracked_task(background_windows_art_upgrade())
                 _running_art_upgrade_tasks[current_track_id] = task

                 # FIX: Wait for DB to avoid flicker
                 try:
                     # Wait 300ms for high-res art
                     await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
                     
                     # Check DB again!
                     db_result = load_album_art_from_db(artist, album)
                     if db_result:
                         # Found it! Update variables to use High-Res immediately
                         found_in_db = True
                         db_image_path = db_result["path"]
                         
                         # ACTUAL Atomic Copy Logic
                         try:
                             original_extension = db_image_path.suffix or '.jpg'
                             cache_path = CACHE_DIR / f"current_art{original_extension}"
                             temp_path = CACHE_DIR / f"current_art{original_extension}.tmp"
                             
                             shutil.copy2(db_image_path, temp_path)
                             os.replace(temp_path, cache_path)
                             
                             # Clean up stale extensions ONLY after new file is in place
                             for ext in ['.jpg', '.png', '.bmp', '.gif', '.webp']:
                                 if ext == original_extension: continue
                                 try:
                                     stale = CACHE_DIR / f"current_art{ext}"
                                     if stale.exists(): os.remove(stale)
                                 except: pass
                                 
                             album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
                         except Exception as e:
                             logger.debug(f"Failed to copy DB art after wait: {e}")
                             # If copy fails, we fall back to the Windows thumbnail which is already set
                 except asyncio.TimeoutError:
                     pass # Fallback to Windows thumbnail

        return {
            "artist": artist,
            "title": title,
            "album": album if album else None,
            "position": position,
            "duration_ms": duration_ms,
            "colors": ("#24273a", "#363b54"),
            "album_art_url": album_art_url,
            "is_playing": True,
            "source": "windows_media"
        }
            
    except Exception as e:
        logger.error(f"Windows Media Error: {e}")
        _win_media_manager = None
        return None

async def _get_current_song_meta_data_spotify(target_title: str = None, target_artist: str = None, force_refresh: bool = False) -> Optional[dict]:
    """Spotify API metadata fetcher with standardized output."""
    global _last_spotify_art_url
    try:
        # Use shared singleton instance (consolidates all stats across the app)
        spotify_client = get_shared_spotify_client()
        
        if spotify_client is None or not spotify_client.initialized:
            return None

        # Track metadata fetch (always, not just in debug mode)
        _metadata_fetch_counters['spotify'] += 1

        track = None
        
        # Hybrid Cache Optimization:
        # If we are looking for a specific song (e.g. from Windows Media) and we have it cached,
        # use the cache to avoid hitting the API just for album art/colors.
        if target_title and target_artist and spotify_client._metadata_cache:
            cache = spotify_client._metadata_cache
            s_title = cache.get('title', '').lower()
            s_artist = cache.get('artist', '').lower()
            t_title = target_title.lower()
            t_artist = target_artist.lower()
            
            # Check for match (fuzzy)
            if (t_title in s_title or s_title in t_title) and \
               (t_artist in s_artist or s_artist in t_artist):
                # Check if cache is fresh enough for hybrid use (30s)
                # We allow a longer TTL here because we primarily want the Art/Colors, which don't change.
                if time.time() - spotify_client._last_metadata_check < 30:
                    track = cache
                    # logger.debug("Hybrid: Using cached Spotify data")

        # If no cache hit, fetch from API (or internal smart cache)
        if track is None:
            track = await spotify_client.get_current_track(force_refresh=force_refresh)
            
        if not track or not track.get("is_playing", False):
            return None
        
        # Extract colors from Spotify album art
        colors = ("#24273a", "#363b54")  # Default
        album_art_url = track.get("album_art")
        
        # CRITICAL FIX: Store original Spotify URL for background tasks
        # (album_art_url might be overwritten with local path if DB hit occurs)
        raw_spotify_url = album_art_url
        
        # Capture track info for DB check and background tasks
        captured_artist = track["artist"]
        captured_title = track["title"]
        captured_album = track.get("album")
        captured_track_id = track.get("track_id") or f"{captured_artist}::{captured_title}"
        
        # Flag to track if we found art in DB
        found_in_db = False

        # Check Album Art Database first (fast path - zero delay if cached)
        db_result = load_album_art_from_db(captured_artist, captured_album)
        if db_result:
            found_in_db = True
            db_image_path = db_result["path"]
            db_metadata = db_result["metadata"]
            
            # Always set the URL to our local cache route
            album_art_url = f"/cover-art?t={hash(captured_track_id) % 100000}"

            # Check if we need to perform the physical file copy
            should_copy = True
            if hasattr(_get_current_song_meta_data_spotify, '_last_db_art_track_id') and \
               _get_current_song_meta_data_spotify._last_db_art_track_id == captured_track_id:
                # We already processed this track. Check if file actually exists.
                cached_art = get_cached_art_path()
                if cached_art:
                    # NEW FIX: Check resolution. If it's small (Windows thumb), force copy anyway.
                    try:
                        with Image.open(cached_art) as img:
                            if img.width < 600 or img.height < 600:
                                logger.debug(f"Cached art is low-res ({img.width}x{img.height}), forcing upgrade from DB")
                                should_copy = True
                            else:
                                should_copy = False
                    except:
                        # If we can't read it, safer to overwrite
                        should_copy = True
                else:
                    should_copy = True
            
            if should_copy:
                # Atomic copy from DB to cache for immediate use (preserving original format)
                try:
                    # Clean up old art first
                    cleanup_old_art()
                    
                    # Get the original file extension from the DB image (preserves format)
                    original_extension = db_image_path.suffix or '.jpg'
                    
                    # Copy DB image to cache with original extension (e.g., current_art.png, current_art.jpg)
                    cache_path = CACHE_DIR / f"current_art{original_extension}"
                    temp_path = CACHE_DIR / f"current_art{original_extension}.tmp"
                    
                    # Copy file atomically (preserves pristine quality)
                    shutil.copy2(db_image_path, temp_path)
                    try:
                        os.replace(temp_path, cache_path)
                        album_art_url = f"/cover-art?t={hash(captured_track_id) % 100000}"
                        # CHANGED: Downgrade to DEBUG to stop console spam on every poll
                        # logger.debug(f"Using album art from database ({original_extension}) for {captured_artist} - {captured_album or captured_title}")
                        
                        # OPTIMIZATION: Only delete spotify_art.jpg AFTER successful copy
                        # This ensures we don't delete it if the copy failed, and prevents
                        # aggressive deletion on every poll loop. server.py prefers spotify_art.jpg,
                        # so we delete it to force fallback to our high-res current_art.*
                        spotify_art_path = CACHE_DIR / "spotify_art.jpg"
                        if spotify_art_path.exists():
                            try:
                                os.remove(spotify_art_path)
                            except Exception:
                                pass
                        
                        # Mark this track as processed so we don't copy again
                        _get_current_song_meta_data_spotify._last_db_art_track_id = captured_track_id
                        
                        # Check if DB is already populated with ALL enabled providers
                        # This logic respects user config: if Last.fm is disabled/no key, we won't look for it.
                        existing_providers = set(db_metadata.get("providers", {}).keys())
                        
                        # Determine which providers SHOULD be there
                        # Spotify is always a source if we are here (since we have raw_spotify_url)
                        expected_providers = {"Spotify"}
                        
                        # Check if other providers are enabled in the singleton instance
                        # We need to get the provider instance to check config
                        # FIX: Removed redundant local import that was causing UnboundLocalError
                        art_provider = get_album_art_provider()
                        
                        if art_provider.enable_itunes:
                            expected_providers.add("iTunes")
                        
                        if art_provider.enable_lastfm and art_provider.lastfm_api_key:
                            expected_providers.add("LastFM")
                            
                        # If we have all expected providers, the DB is complete
                        db_is_complete = expected_providers.issubset(existing_providers)
                        
                        # SELF-HEAL: Check if any existing provider has invalid/unknown resolution
                        # This ensures we re-run the check to fix metadata for files that were downloaded but have 0x0 resolution
                        has_invalid_resolution = False
                        if db_metadata:  # Corrected variable name
                            for p_name, p_data in db_metadata.get("providers", {}).items():
                                if p_data.get("downloaded") and (p_data.get("width", 0) == 0 or "unknown" in str(p_data.get("resolution", "")).lower()):
                                    has_invalid_resolution = True
                                    logger.debug(f"Found invalid resolution for {p_name}, triggering self-heal")
                                    break

                        # Trigger background task ONLY if DB is incomplete OR has invalid data (and not already running)
                        # Use raw_spotify_url (not album_art_url which is now a local path)
                        # CRITICAL FIX: Only run this once per track to prevent infinite loops
                        if (not db_is_complete or has_invalid_resolution) and captured_track_id not in _running_art_upgrade_tasks and captured_track_id not in _db_checked_tracks:
                            # Mark as checked immediately to prevent re-entry on next poll
                            _db_checked_tracks.add(captured_track_id)
                            
                            # Limit set size to prevent memory leaks
                            if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                                # Remove random element (sets are unordered) - simple cleanup
                                _db_checked_tracks.pop()

                            async def background_refresh_db():
                                try:
                                    # This function now returns the best URL and resolution
                                    return await ensure_album_art_db(captured_artist, captured_album, captured_title, raw_spotify_url)
                                except Exception as e:
                                    logger.debug(f"Background DB refresh failed: {e}")
                                finally:
                                    _running_art_upgrade_tasks.pop(captured_track_id, None)
                            
                            # Use tracked task
                            task = create_tracked_task(background_refresh_db())
                            _running_art_upgrade_tasks[captured_track_id] = task
                    except OSError as e:
                        logger.debug(f"Could not atomically replace current_art{original_extension}: {e}")
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                except Exception as e:
                    logger.debug(f"Failed to copy DB image to cache: {e}")
            else:
                # Even if we didn't copy, we need to set the URL correctly
                album_art_url = f"/cover-art?t={hash(captured_track_id) % 100000}"
        
        # Progressive Enhancement: Return Spotify 640px immediately, upgrade in background
        if album_art_url:
            try:
                # Get high-res album art provider
                art_provider = get_album_art_provider()
                
                # Store original Spotify URL as fallback (use raw_spotify_url, not album_art_url)
                original_spotify_url = raw_spotify_url
                # Capture track info for background task (prevents race conditions)
                captured_artist = track["artist"]
                captured_title = track["title"]
                captured_album = track.get("album")
                captured_track_id = track.get("track_id") or f"{captured_artist}::{captured_title}"
                
                # 1. Check cache first - if we have cached high-res, use it immediately
                # Use album-level cache (same album = same art for all tracks)
                cached_result = art_provider.get_from_cache(captured_artist, captured_title, captured_album)
                if cached_result:
                    cached_url, cached_resolution_info = cached_result
                    # Only use cached result if it's better than Spotify (not the Spotify fallback)
                    # AND if we didn't just load a preferred image from the DB (which takes precedence)
                    if cached_url != original_spotify_url and not found_in_db:
                        album_art_url = cached_url
                        # Log upgrade if not already logged for this track
                        if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_track_id') or \
                           _get_current_song_meta_data_spotify._last_logged_track_id != captured_track_id:
                            logger.info(f"Using cached high-res album art for {captured_artist} - {captured_title}: {cached_resolution_info}")
                            _get_current_song_meta_data_spotify._last_logged_track_id = captured_track_id
                    else:
                        # 2. Not cached - start background task to fetch high-res AND populate DB
                        # Return Spotify URL immediately for instant UI, upgrade happens in background
                        pass

                    # ALWAYS start background task to populate DB if not running
                    # This ensures DB is populated even if we have a memory cache hit
                    
                    # CRITICAL FIX: Don't run background task if we just loaded from DB
                    # OR if we have already checked/populated the DB for this track in this session
                    if not found_in_db and captured_track_id not in _db_checked_tracks:
                        if captured_track_id in _running_art_upgrade_tasks:
                            # Task already running
                            logger.debug(f"Background art upgrade already running for {captured_track_id}, skipping duplicate task")
                        else:
                            # Mark as checked immediately to prevent re-entry on next poll
                            _db_checked_tracks.add(captured_track_id)
                            
                            # Limit set size to prevent memory leaks
                            if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                                # Remove random element (sets are unordered) - simple cleanup
                                _db_checked_tracks.pop()
                            
                            async def background_upgrade_art():
                                """Background task to fetch high-res art, update cache, and populate DB"""
                                try:
                                    # Only log once per track (check if we've logged this track before)
                                    if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_startup_track_id') or \
                                       _get_current_song_meta_data_spotify._last_logged_startup_track_id != captured_track_id:
                                        logger.info(f"Starting background album art upgrade for {captured_artist} - {captured_title} (album: {captured_album or 'N/A'})")
                                        _get_current_song_meta_data_spotify._last_logged_startup_track_id = captured_track_id
                                    # Wait a tiny bit to let the initial response return first
                                    await asyncio.sleep(0.1)
                                    
                                    # Populate Album Art Database (fetches all options and saves them)
                                    # CRITICAL: This must run even if we skip high-res fetch
                                    high_res_result = None
                                    try:
                                        logger.info(f"Calling ensure_album_art_db for {captured_artist} - {captured_title}")
                                        # Use the result from DB population directly (avoid redundant fetch)
                                        high_res_result = await ensure_album_art_db(captured_artist, captured_album, captured_title, original_spotify_url)
                                        
                                        # Update the provider cache immediately
                                        if high_res_result:
                                            # Update cache manually since we skipped get_high_res_art
                                            # We need to construct the cache key exactly like the provider does
                                            cache_key = art_provider._get_cache_key(captured_artist, captured_title, captured_album)
                                            art_provider._cache[cache_key] = high_res_result
                                            logger.debug(f"Updated art provider cache from DB result for {captured_artist} - {captured_title}")
                                            
                                    except Exception as e:
                                        logger.error(f"ensure_album_art_db failed: {e}")
                                    
                                    # REMOVED: Redundant call to art_provider.get_high_res_art
                                    # This prevents the double-flicker (once for remote high-res, once for local DB)
                                    # and saves sequential network requests since ensure_album_art_db already fetched everything in parallel.
                                    
                                    # Check if track changed during fetch (race condition protection)
                                    # Get current track from Spotify cache to verify
                                    current_spotify_client = get_shared_spotify_client()
                                    if current_spotify_client and current_spotify_client._metadata_cache:
                                        current_track = current_spotify_client._metadata_cache
                                        current_track_id = current_track.get("track_id") or f"{current_track.get('artist', '')}::{current_track.get('title', '')}"
                                        if current_track_id != captured_track_id:
                                            logger.debug(f"Track changed during background art fetch ({captured_track_id} -> {current_track_id}), discarding result")
                                            return
                                    
                                    # If we got a better URL, it's now cached for next poll
                                    # The frontend will pick it up on the next metadata poll (0.1s later)
                                    if high_res_result:
                                        # Update cache with the best URL and resolution
                                        _get_current_song_meta_data_spotify._last_logged_track_id = captured_track_id
                                        logger.info(f"Upgraded album art from Spotify to high-res source for {captured_artist} - {captured_title}: {high_res_result[1]}")
                                except Exception as e:
                                    logger.error(f"Background art upgrade failed for {captured_artist} - {captured_title}: {type(e).__name__}: {e}", exc_info=True)
                                finally:
                                    # Remove from running tasks when done
                                    _running_art_upgrade_tasks.pop(captured_track_id, None)
                            
                            # Start background task (non-blocking) and track it
                            # Use tracked task to prevent garbage collection issues
                            task = create_tracked_task(background_upgrade_art())
                            _running_art_upgrade_tasks[captured_track_id] = task
                    
            except Exception as e:
                # FIX: Log only once per track to prevent spam (but still catch errors)
                if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_error_track_id') or \
                   _get_current_song_meta_data_spotify._last_logged_error_track_id != captured_track_id:
                     logger.debug(f"Failed to setup high-res album art, using Spotify default: {e}")
                     _get_current_song_meta_data_spotify._last_logged_error_track_id = captured_track_id
                pass # It is safe to keep this if you want, but it is not strictly needed anymore
        
        # CRITICAL FIX: Only attempt download if it's a remote URL (not a local path starting with /)
        # This prevents 'MissingSchema' exceptions when using cached art
        if album_art_url and not album_art_url.startswith('/'):
            try:
                # Check if we need to download new art (track changed)
                # CRITICAL FIX: Only download if URL changed OR file is missing
                current_art_exists = (CACHE_DIR / "spotify_art.jpg").exists()
                
                # OPTIMIZATION: Check if this exact URL is already being downloaded by a background task
                # This prevents the polling loop from spawning duplicates (Fix: Task Spam)
                is_downloading = album_art_url in _spotify_download_tracker
                
                # FIX: Properly group conditions so tracker check applies to all conditions
                # Without this, if URL changed, condition would be True even if already downloading
                if (
                    not is_downloading
                    and (
                        not hasattr(_get_current_song_meta_data_spotify, '_last_spotify_art_url')
                        or _get_current_song_meta_data_spotify._last_spotify_art_url != album_art_url
                        or not current_art_exists
                    )
                ):
                    
                    # Mark as downloading to prevent duplicates
                    _spotify_download_tracker.add(album_art_url)
                    
                    # OPTIMIZATION: Offload download to background task (Fix #3)
                    # This returns metadata immediately without waiting for the image
                    # Uses tracked task to prevent silent failures
                    # Passes captured_track_id for race condition validation
                    create_tracked_task(_download_spotify_art_background(album_art_url, captured_track_id))
                    
                    # Use cached colors if available temporarily, or default
                    if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_colors'):
                        colors = _get_current_song_meta_data_spotify._last_spotify_colors
                else:
                    # Use cached colors
                    if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_colors'):
                        colors = _get_current_song_meta_data_spotify._last_spotify_colors
                        
            except Exception as e:
                logger.debug(f"Failed to setup Spotify art download: {e}")
            
        # Return standardized structure with all fields
        # Include artist_id and artist_name for visual mode and artist image fetching
        return {
            "artist": track["artist"],
            "title": track["title"],
            "album": track.get("album"),
            "position": track["progress_ms"] / 1000,
            "duration_ms": track.get("duration_ms"),
            "colors": colors,
            "album_art_url": album_art_url,
            "is_playing": True,
            "source": "spotify",
            "artist_id": track.get("artist_id"),  # For fetching artist images
            "artist_name": track.get("artist_name")  # For display purposes
        }
    except Exception as e:
        logger.error(f"Spotify API Error: {e}")
        return None

# --- Main Function ---

async def get_current_song_meta_data() -> Optional[dict]:
    """
    Main orchestrator to get song data from configured sources with hybrid enrichment.
    
    CRITICAL FIX: Checks if song changed before using cache to prevent stale metadata
    from being returned when a song change occurs within the cache interval.
    """
    current_time = time.time()
    last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
    
    required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
    
    # CRITICAL FIX: Check if song changed before using cache
    # If song changed, we MUST fetch fresh data even if within interval
    # This prevents the race condition where:
    # 1. Song A is playing, metadata is cached
    # 2. User skips to Song B
    # 3. Cache still returns Song A metadata (within interval)
    # 4. System thinks no change occurred, displays Song A lyrics for Song B
    last_song = getattr(get_current_song_meta_data, '_last_song', None)
    
    # Only use cache if within interval AND song hasn't changed
    if (current_time - last_check) < required_interval:
        cached_result = getattr(get_current_song_meta_data, '_last_result', None)
        if cached_result:
            # Verify the cached result matches the last known song
            # This prevents returning stale metadata when song changed but cache hasn't expired
            cached_song_name = f"{cached_result.get('artist', '')} - {cached_result.get('title', '')}"
            if last_song == cached_song_name:
                # Song hasn't changed, safe to use cache
                # CRITICAL FIX: Update _last_song to stay in sync with cached data
                # Without this, the next call will see a mismatch and invalidate cache unnecessarily
                get_current_song_meta_data._last_song = cached_song_name
                return cached_result
            else:
                # Song changed! Invalidate cache and fetch fresh data
                # This ensures we detect song changes immediately, not after cache expires
                logger.debug(f"Song changed in cache ({last_song} -> {cached_song_name}), invalidating cache to fetch fresh data")
                get_current_song_meta_data._last_check_time = 0  # Force refresh by resetting check time
    
    get_current_song_meta_data._last_check_time = current_time
    
    sources = config.MEDIA_SOURCE.get("sources", [])
    sorted_sources = [s for s in sorted(sources, key=lambda x: int(x.get("priority", 999))) 
                     if s.get("enabled", False)]

    result = None
    windows_media_checked = False
    windows_media_result = None
    
    # 1. Fetch Primary Data from sorted sources
    for source in sorted_sources:
        try:
            if source["name"] == "windows_media" and DESKTOP == "Windows":
                windows_media_checked = True
                windows_media_result = await _get_current_song_meta_data_windows()
                if windows_media_result:
                    result = windows_media_result
            elif source["name"] == "spotify":
                result = await _get_current_song_meta_data_spotify()
            elif source["name"] == "gnome" and DESKTOP == "Gnome":
                result = _get_current_song_meta_data_gnome()
                
            if result:
                # Source is already set in the function
                break
        except Exception:
            continue
    
    # Detect Spotify-only mode: Windows Media was checked but returned None, Spotify is primary source
    # Spotify-only means: result exists, source is "spotify" (not "spotify_hybrid"), and Windows Media didn't provide data
    is_spotify_only = (result and 
                       result.get("source") == "spotify" and  # Pure Spotify source (not hybrid)
                       (not windows_media_checked or windows_media_result is None))  # Windows Media not available or returned None
    
    # Adjust Spotify API polling speed based on mode
    # Fast mode (2.0s) for Spotify-only to reduce latency, Normal mode (6.0s) when Windows Media is active
    spotify_client = get_shared_spotify_client()
    if spotify_client and spotify_client.initialized:
        if is_spotify_only:
            spotify_client.set_fast_mode(True)  # Fast mode: 2.0s polling for lower latency
        else:
            spotify_client.set_fast_mode(False)  # Normal mode: 6.0s polling for rate limit protection
    
    # 2. HYBRID ENRICHMENT - Merge Spotify data if primary source lacks album art/controls
    if result and result.get("source") == "windows_media":
        try:
            # Smart Wake-Up Logic: Only force refresh if Windows says playing BUT Spotify cache says paused
            # This prevents unnecessary force_refresh flags and reduces API calls
            is_windows_playing = result.get("is_playing", False)
            spotify_cached_paused = False
            
            # Check Spotify cache state to determine if we need to wake it up
            # spotify_client already obtained above via get_shared_spotify_client()
            if spotify_client and spotify_client._metadata_cache:
                spotify_cached_paused = not spotify_client._metadata_cache.get('is_playing', False)
            
            # Only force refresh when there's a mismatch (Windows playing + Spotify paused)
            force_wake = is_windows_playing and spotify_cached_paused
            
            spotify_data = await _get_current_song_meta_data_spotify(
                target_title=result.get("title"),
                target_artist=result.get("artist"),
                force_refresh=force_wake
            )
            if spotify_data:
                # Fuzzy match check: If title and artist are roughly the same
                win_title = result.get("title", "").lower()
                win_artist = result.get("artist", "").lower()
                spot_title = spotify_data.get("title", "").lower()
                spot_artist = spotify_data.get("artist", "").lower()
                
                # Match if titles overlap or artist+title combo matches
                title_match = win_title in spot_title or spot_title in win_title
                artist_match = win_artist in spot_artist or spot_artist in win_artist
                
                if title_match and (artist_match or not win_artist):
                    # Steal Album Art (Progressive Enhancement: return Spotify immediately, upgrade in background)
                    spotify_art_url = spotify_data.get("album_art_url")
                    if spotify_art_url:
                        try:
                            # CRITICAL FIX: If URL is local (starts with /), it means we loaded from DB (user preference).
                            # Don't try to upgrade/override it with cached remote art.
                            if spotify_art_url.startswith('/'):
                                result["album_art_url"] = spotify_art_url
                            else:
                                art_provider = get_album_art_provider()
                                
                                # Check cache first - if cached high-res exists, use it immediately
                                # Use album-level cache (same album = same art for all tracks)
                                cached_result = art_provider.get_from_cache(
                                    spotify_data.get("artist", ""),
                                    spotify_data.get("title", ""),
                                    spotify_data.get("album")  # Album-level cache
                                )
                                if cached_result:
                                    cached_url, _ = cached_result
                                    if cached_url != spotify_art_url:
                                        result["album_art_url"] = cached_url
                                    else:
                                        result["album_art_url"] = spotify_art_url
                                else:
                                    # Not cached - use Spotify immediately, upgrade in background
                                    result["album_art_url"] = spotify_art_url
                                    
                                    # Check if a background task is already running for this track
                                    hybrid_track_id = f"{spotify_data.get('artist', '')}::{spotify_data.get('title', '')}"
                                    if hybrid_track_id in _running_art_upgrade_tasks:
                                        # Task already running, skip creating duplicate
                                        logger.debug(f"Background art upgrade already running for {hybrid_track_id}, skipping duplicate task")
                                    else:
                                        # Start background task to fetch high-res
                                        async def background_upgrade_hybrid():
                                            try:
                                                await asyncio.sleep(0.1)
                                                high_res_result = await art_provider.get_high_res_art(
                                                    artist=spotify_data.get("artist", ""),
                                                    title=spotify_data.get("title", ""),
                                                    album=spotify_data.get("album"),
                                                    spotify_url=spotify_art_url
                                                )
                                                # Result is cached, will be picked up on next poll
                                            except Exception as e:
                                                logger.debug(f"Background art upgrade failed in hybrid mode: {e}")
                                            finally:
                                                # Remove from running tasks when done
                                                _running_art_upgrade_tasks.pop(hybrid_track_id, None)
                                        
                                        # Use tracked task
                                        task = create_tracked_task(background_upgrade_hybrid())
                                        _running_art_upgrade_tasks[hybrid_track_id] = task
                        except Exception as e:
                            logger.debug(f"Failed to setup high-res art in hybrid mode: {e}")
                            result["album_art_url"] = spotify_art_url
                    
                    # Steal Colors from Spotify (now properly extracted!)
                    if spotify_data.get("colors"):
                        result["colors"] = spotify_data.get("colors")

                    # Enable Controls by marking as hybrid
                    # Frontend will allow controls for this source type
                    result["source"] = "spotify_hybrid"
                    
                    # Copy Artist ID and Name for Visual Mode
                    # This ensures artist slideshows work even when playing from Windows Media
                    if spotify_data.get("artist_id"):
                        result["artist_id"] = spotify_data.get("artist_id")
                    if spotify_data.get("artist_name"):
                        result["artist_name"] = spotify_data.get("artist_name")
                    
#                   if DEBUG["enabled"]:
#                       logger.info(f"Hybrid mode: Enriched Windows Media data with Spotify album art and controls")
        except Exception as e:
            logger.error(f"Hybrid enrichment failed: {e}")
    
    # 4. If we still don't have colors (e.g. local file), extract them
    if result and result.get("source") == "windows_media":
        # Check if we have a local art path in the cache
        local_art_path = get_cached_art_path()
        if result.get("colors") == ("#24273a", "#363b54") and local_art_path:
             # Only extract if we have a valid local file and default colors
             # Now async, so we await it
             result["colors"] = await extract_dominant_colors(local_art_path)

    # 3. State Management (Active vs Idle)
    if result:
        get_current_song_meta_data._is_active = True
        get_current_song_meta_data._last_active_time = current_time
        
        last_song = getattr(get_current_song_meta_data, '_last_song', None)
        current_song_name = f"{result.get('artist')} - {result.get('title')}"
        
        if last_song != current_song_name:
            get_current_song_meta_data._last_song = current_song_name
            get_current_song_meta_data._last_source = result.get('source')
            _log_app_state()
    else:
        if (current_time - last_active_time) > IDLE_WAIT_TIME:
            get_current_song_meta_data._is_active = False

    get_current_song_meta_data._last_result = result
    _log_app_state()
    
    return result