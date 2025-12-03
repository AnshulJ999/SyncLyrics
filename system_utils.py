from __future__ import annotations
import subprocess
import platform
import re
import time
import asyncio
import threading
from typing import Optional, Dict, Any, List, Tuple
from collections import OrderedDict
import config
from config import DEBUG, FEATURES, ALBUM_ART_DB_DIR
from state_manager import get_state, set_state
from providers.spotify_api import get_shared_spotify_client
from providers.album_art import get_album_art_provider
from providers.artist_image import ArtistImageProvider
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
import uuid

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
_last_windows_app_id = None  # Track last app_id to avoid log spam
# Track running background art upgrade tasks to prevent duplicates
_running_art_upgrade_tasks = {}  # Key: track_id, Value: asyncio.Task
# NEW: Track which songs we've already checked/populated the DB for to prevent infinite loops
# Using OrderedDict for FIFO eviction (oldest entries removed first)
_db_checked_tracks = OrderedDict()  # Key: track_id, Value: timestamp
_MAX_DB_CHECKED_SIZE = 100
# OPTIMIZATION: Semaphore to limit concurrent background downloads (Fix #4)
# Prevents network saturation if user skips many tracks quickly
_art_download_semaphore = asyncio.Semaphore(2)  # For album art downloads
_artist_download_semaphore = asyncio.Semaphore(2)  # For artist image downloads (separate to prevent deadlock)
# Global lock to prevent concurrent album art updates (prevents flicker)
_art_update_lock = asyncio.Lock()  # For async operations
_art_update_thread_lock = threading.Lock()  # For sync operations in thread executors

# Per-folder locks for metadata.json file operations (prevents Windows file locking errors)
# Each album folder gets its own lock, allowing parallel writes to different albums
_metadata_file_locks = {}  # Key: folder path (str), Value: threading.Lock
_metadata_locks_lock = threading.Lock()  # Protects the lock dictionary itself

# Global set to track background tasks and prevent garbage collection (Fix: Task Tracking)
_background_tasks = set()

# OPTIMIZATION: Track in-progress downloads to prevent polling loop from spawning duplicates
_spotify_download_tracker = set()

# NEW: Track in-progress artist image downloads to prevent race conditions
_artist_download_tracker = set()

# Throttle for artist image fetch logs (prevents spam)
# Key: artist name, Value: last log timestamp
_artist_image_log_throttle = {}
_ARTIST_IMAGE_LOG_THROTTLE_SECONDS = 60  # Log at most once per minute per artist

# Cache for ensure_artist_image_db results to prevent spamming checks
# Key: artist, Value: (timestamp, result_list)
# This prevents the function from running expensive logic when called repeatedly for the same artist
_artist_db_check_cache = {}

# Global instance for ArtistImageProvider (singleton pattern)
_artist_image_provider: Optional[ArtistImageProvider] = None

# Global lock to prevent race conditions during metadata updates
_meta_data_lock = asyncio.Lock()

# OPTIMIZATION: Cache for album art metadata.json files
# Key: file_path (str), Value: (mtime, metadata_dict)
# Uses file modification time to automatically invalidate when file changes
_album_art_metadata_cache = {}
_MAX_METADATA_CACHE_SIZE = 50  # Limit cache size to prevent memory leaks

# Cache for custom image discovery results
# Key: folder_path (str), Value: (folder_mtime, discovered_count)
# Uses folder modification time to avoid re-scanning on every metadata load
_discovery_cache = {}
_MAX_DISCOVERY_CACHE_SIZE = 50  # Limit cache size to prevent memory leaks

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

def _normalize_track_id(artist: str, title: str) -> str:
    """
    Generates a consistent, source-agnostic track ID.
    Used to prevent UI flickering when switching sources (e.g. Windows -> Spotify Hybrid).
    """
    if not artist: artist = ""
    if not title: title = ""
    
    # Simple alphanumeric normalization
    norm_artist = "".join(c for c in artist.lower() if c.isalnum())
    norm_title = "".join(c for c in title.lower() if c.isalnum())
    return f"{norm_artist}_{norm_title}"

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
            # artist = ""  # [REMOVED] Don't wipe artist name just because album is missing

        # Generate normalized track ID for change detection
        current_track_id = _normalize_track_id(artist.strip(), title.strip())

        return {
            "track_id": current_track_id,  # ADDED: Normalized ID for frontend change detection
            "artist": artist.strip(), 
            "title": title.strip(),
            "album": album.strip() if album else None,
            "position": int(position)/1000000,
            "duration_ms": None,  # Not available from playerctl
            "colors": ("#24273a", "#363b54"),
            "album_art_url": art_url,
            "background_image_url": art_url,  # ADDED: Use same art for background default (GNOME support)
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

def get_album_db_folder(artist: str, album: Optional[str] = None) -> Path:
    """
    Get the database folder path for an album or artist images.
    Uses Artist - Album format, with fallback to Artist - Title if no album.
    
    Args:
        artist: Artist name
        album: Album name (optional). If None, returns Artist-only folder.
        
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
    Uses atomic write pattern (temp file + os.replace) to prevent corruption.
    
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
            
            # Atomic replace - if this fails, original file is untouched
            os.replace(temp_path, output_path)
            return True
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
                    return '.jpg' if ext == '.jpeg' else ext  # Fix 1: Normalize .jpeg to .jpg
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
    Preserves unknown keys from existing metadata and includes schema version.
    
    Uses per-folder threading locks to prevent Windows file locking errors when
    multiple operations try to access the same metadata.json file concurrently.
    Each album folder has its own lock, allowing parallel writes to different albums.
    
    Args:
        folder: Path to the album folder
        metadata: Dictionary containing metadata to save
        
    Returns:
        True if successful, False otherwise
    """
    # Get or create a lock for this specific folder
    # This allows parallel writes to different albums while serializing writes to the same album
    try:
        folder_key = str(folder.resolve())  # Use resolved path to handle symlinks/relative paths
    except (OSError, ValueError) as e:
        # Handle edge cases where path resolution fails (e.g., invalid characters, permissions)
        logger.error(f"Failed to resolve folder path {folder}: {e}")
        return False
    
    with _metadata_locks_lock:
        if folder_key not in _metadata_file_locks:
            _metadata_file_locks[folder_key] = threading.Lock()
        file_lock = _metadata_file_locks[folder_key]
    
    # Protect all file I/O operations with the folder-specific lock
    # This prevents Windows file locking errors (WinError 32) when multiple threads
    # try to read/write the same metadata.json file simultaneously
    with file_lock:
        try:
            metadata_path = folder / "metadata.json"
            # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
            # This prevents race conditions when multiple tracks from the same album are processed simultaneously
            temp_filename = f"metadata_{uuid.uuid4().hex}.json.tmp"
            temp_path = folder / temp_filename
            
            # Ensure folder exists
            folder.mkdir(parents=True, exist_ok=True)
            
            # Load existing metadata to preserve unknown keys (for backward compatibility)
            existing_metadata = {}
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        existing_metadata = json.load(f)
                except Exception:
                    # If read fails, start fresh
                    pass
            
            # Preserve unknown keys from existing metadata (except schema_version which we update)
            # Also skip keys that are explicitly set to None (indicating intentional deletion)
            for key, value in existing_metadata.items():
                if key not in metadata and key != 'schema_version':
                    metadata[key] = value
            
            # Remove keys that are explicitly set to None (indicating intentional deletion)
            # This allows callers to delete keys by setting them to None
            keys_to_remove = [key for key, value in metadata.items() if value is None and key != 'schema_version']
            for key in keys_to_remove:
                del metadata[key]
            
            # Add schema version (current version is 1)
            # This allows future code to handle format changes gracefully
            metadata['schema_version'] = 1
            
            # Write to temp file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            # Atomic replace with retry for Windows file locking
            # Note: The lock above should prevent most conflicts, but we keep retries
            # as a safety measure for edge cases (e.g., external processes, antivirus)
            for attempt in range(3):
                try:
                    if metadata_path.exists():
                        os.remove(metadata_path)
                    os.replace(temp_path, metadata_path)
                    
                    # OPTIMIZATION: Invalidate cache after successful save
                    # This ensures cache is cleared when file changes
                    metadata_path_str = str(metadata_path)
                    if metadata_path_str in _album_art_metadata_cache:
                        del _album_art_metadata_cache[metadata_path_str]
                    
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

async def ensure_album_art_db(
    artist: str, album: Optional[str], title: str, spotify_url: Optional[str] = None, retry_count: int = 0
) -> Optional[Tuple[str, str]]:
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
    # Prevent infinite recursion for self-healing
    if retry_count > 1:
        logger.warning(f"Aborting ensure_album_art_db for {artist} - {title} after {retry_count} retries")
        return None

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
            
            # FIX: Check for existing user preference FIRST before auto-selecting highest resolution
            # This ensures that if user manually selected a provider (e.g., via UI), that choice is preserved
            # even if a higher-resolution image is downloaded later
            preferred_provider = None
            if existing_metadata and "preferred_provider" in existing_metadata:
                preferred_provider = existing_metadata["preferred_provider"]
            
            # Only auto-select highest resolution if no user preference exists
            highest_resolution = 0
            if not preferred_provider:
                # Re-calculate highest resolution from EXISTING data
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
                        # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
                        # This prevents race conditions when the same provider downloads for the same album simultaneously
                        temp_filename = f"{provider_name}_{uuid.uuid4().hex}"
                        temp_path = folder / temp_filename
                        
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
                            # Clean up temp file if download failed
                            try:
                                temp_path_with_ext = temp_path.with_suffix(file_extension) if 'file_extension' in locals() else temp_path
                                if temp_path_with_ext.exists():
                                    os.remove(temp_path_with_ext)
                                elif temp_path.exists():
                                    os.remove(temp_path)
                            except:
                                pass
                            continue
                    except Exception as e:
                        logger.warning(f"Failed to download {provider_name} art: {e}")
                        # Clean up temp file if exception occurred
                        try:
                            if 'temp_path' in locals() and temp_path.exists():
                                # Try to remove with any possible extension
                                for ext in ['.jpg', '.png', '.webp', '']:
                                    temp_with_ext = temp_path.with_suffix(ext) if ext else temp_path
                                    if temp_with_ext.exists():
                                        os.remove(temp_with_ext)
                                        break
                        except:
                            pass
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
            # FIX: Check existing preference FIRST before auto-selecting highest resolution
            # This ensures user's manual selection is preserved even if a higher-res image is downloaded
            if existing_metadata and "preferred_provider" in existing_metadata:
                preferred_provider = existing_metadata["preferred_provider"]
            
            # Create metadata structure
            # FIX: Preserve background_style from existing metadata to prevent it from being wiped
            # when the background task runs (e.g., for self-healing or adding new providers)
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
            # Use lock to ensure atomic update of metadata and prevent race conditions
            # This ensures we don't overwrite changes made by the API (e.g., background_style) while we were downloading images
            async with _art_update_lock:
                # CRITICAL: Re-read metadata inside lock to get latest state (e.g. background_style changes)
                # This prevents overwriting changes made by the API while we were downloading images
                latest_db = load_album_art_from_db(artist, album, title)
                latest_metadata = latest_db["metadata"] if latest_db else existing_metadata
                
                # Preserve background_style if it exists in LATEST metadata (not stale existing_metadata)
                # This prevents the user's saved preference (Sharp/Soft/Blur) from being lost
                # when the background task updates the metadata (e.g., adding new providers or self-healing)
                # BUT also respects if the user cleared it (Auto) while we were downloading
                # Only preserve if it's not None (None indicates intentional deletion)
                if latest_metadata and "background_style" in latest_metadata and latest_metadata["background_style"] is not None:
                    metadata["background_style"] = latest_metadata["background_style"]
                
                # OPTIMIZATION: Run file I/O in executor to avoid blocking event loop (Fix #4)
                # This prevents UI stutters if disk is busy or antivirus is scanning
                save_success = await loop.run_in_executor(None, save_album_db_metadata, folder, metadata)
            
            # Handle save result outside the lock
            if save_success:
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
        # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
        # This prevents race conditions when the same image URL is downloaded multiple times simultaneously
        temp_filename = f"{path.stem}_{uuid.uuid4().hex}{path.suffix}.tmp"
        temp_path = path.parent / temp_filename
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

def discover_custom_images(folder: Path, metadata: Dict[str, Any], is_artist_images: bool = False) -> Dict[str, Any]:
    """
    Auto-discover custom images in folder that aren't in metadata.json.
    Scans for image files and adds them to metadata automatically.
    
    Uses folder mtime caching to avoid re-scanning on every metadata load.
    Only re-discovers if folder modification time changed.
    
    Args:
        folder: Path to the album/artist folder
        metadata: Existing metadata dictionary (will be modified)
        is_artist_images: True if this is artist images metadata, False for album art
        
    Returns:
        Updated metadata dictionary (same object, modified in place)
    """
    if not folder.exists():
        return metadata
    
    try:
        # Check if we need to re-discover (folder mtime changed)
        folder_key = str(folder.resolve())
        folder_mtime = 0
        
        # Get max mtime of all files in folder (indicates if new files were added)
        try:
            if folder.exists():
                folder_mtime = max(
                    (f.stat().st_mtime for f in folder.iterdir() if f.is_file()),
                    default=0
                )
        except OSError:
            # Folder might not exist or be inaccessible
            return metadata
        
        # Check cache
        should_discover = True
        if folder_key in _discovery_cache:
            cached_mtime, _ = _discovery_cache[folder_key]
            if cached_mtime == folder_mtime:
                # Folder hasn't changed, skip discovery
                should_discover = False
        
        if not should_discover:
            return metadata
        
        # Scan for image files
        image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']
        discovered_count = 0
        
        for file in folder.iterdir():
            if not file.is_file():
                continue
            
            # Skip metadata.json and temp files
            if (file.name == 'metadata.json' or 
                file.name.endswith('.tmp') or 
                'metadata_' in file.name):
                continue
            
            # Check if it's an image file
            if file.suffix.lower() not in image_extensions:
                continue
            
            # Extract provider name from filename (remove extension)
            provider_name = file.stem  # "Custom.jpg" -> "Custom"
            
            if is_artist_images:
                # For artist images: check if already in images array
                images = metadata.get("images", [])
                already_exists = any(
                    img.get("filename") == file.name for img in images
                )
                
                if not already_exists:
                    # Extract actual resolution from file
                    try:
                        def get_image_size_sync(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        # Run in sync context (will be called from async context if needed)
                        width, height = get_image_size_sync(file)
                        
                        # Add to images array
                        if "images" not in metadata:
                            metadata["images"] = []
                        
                        metadata["images"].append({
                            "source": provider_name,
                            "url": f"file://local/{file.name}",  # Placeholder URL
                            "filename": file.name,
                            "width": width,
                            "height": height,
                            "downloaded": True,
                            "added_at": datetime.utcnow().isoformat() + "Z"
                        })
                        discovered_count += 1
                        logger.debug(f"Discovered custom artist image: {file.name} ({width}x{height})")
                    except Exception as e:
                        logger.debug(f"Failed to process custom image {file.name}: {e}")
            else:
                # For album art: check if already in providers dict
                providers = metadata.get("providers", {})
                already_exists = provider_name in providers
                
                if not already_exists:
                    # Extract actual resolution from file
                    try:
                        def get_image_size_sync(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        width, height = get_image_size_sync(file)
                        resolution_str = f"{width}x{height}"
                        
                        # Add to providers dict
                        if "providers" not in metadata:
                            metadata["providers"] = {}
                        
                        metadata["providers"][provider_name] = {
                            "url": f"file://local/{file.name}",  # Placeholder URL
                            "filename": file.name,
                            "width": width,
                            "height": height,
                            "resolution": resolution_str,
                            "downloaded": True
                        }
                        discovered_count += 1
                        logger.debug(f"Discovered custom album art: {file.name} ({width}x{height})")
                    except Exception as e:
                        logger.debug(f"Failed to process custom image {file.name}: {e}")
        
        # Update cache
        if discovered_count > 0:
            # Update cache with new mtime
            if len(_discovery_cache) >= _MAX_DISCOVERY_CACHE_SIZE:
                # Remove oldest entry
                oldest_key = next(iter(_discovery_cache))
                del _discovery_cache[oldest_key]
            
            _discovery_cache[folder_key] = (folder_mtime, discovered_count)
            logger.info(f"Auto-discovered {discovered_count} custom image(s) in {folder.name}")
        else:
            # No new images, but update cache to prevent re-scanning
            _discovery_cache[folder_key] = (folder_mtime, 0)
        
    except Exception as e:
        logger.debug(f"Error during custom image discovery: {e}")
    
    return metadata

def load_album_art_from_db(artist: str, album: Optional[str], title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Load album art from database if available.
    Returns the preferred image path if found.
    
    OPTIMIZED: Uses in-memory cache based on file modification time to prevent
    constant disk reads during polling. Also limits 'last_accessed' writes to once per hour.
    
    Args:
        artist: Artist name
        album: Album name (optional)
        title: Track title (optional, used as fallback if album is missing)
        
    Returns:
        Dictionary with 'path' (Path to image) and 'metadata' (full metadata dict) if found, None otherwise
    """
    # Check if feature is enabled
    if not FEATURES.get("album_art_db", True):
        return None
    
    try:
        # Match saving logic: use title as fallback if album is missing
        folder_name = album if album else title
        folder = get_album_db_folder(artist, folder_name)
        metadata_path = folder / "metadata.json"
        
        if not metadata_path.exists():
            return None
        
        # OPTIMIZATION: Check cache first using file modification time
        # This is much faster than reading/parsing the JSON every time
        metadata_path_str = str(metadata_path)
        current_mtime = metadata_path.stat().st_mtime
        
        metadata = None
        if metadata_path_str in _album_art_metadata_cache:
            cached_mtime, cached_metadata = _album_art_metadata_cache[metadata_path_str]
            if cached_mtime == current_mtime:
                # Cache hit - file hasn't changed, use cached data
                metadata = cached_metadata.copy()  # Copy to avoid modifying cache directly
            else:
                # File changed, remove stale cache entry
                del _album_art_metadata_cache[metadata_path_str]
        
        # If not in cache or file changed, load from disk
        if metadata is None:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Update cache (limit size to prevent memory leaks)
            if len(_album_art_metadata_cache) >= _MAX_METADATA_CACHE_SIZE:
                # Remove oldest entry (simple FIFO - remove first key)
                oldest_key = next(iter(_album_art_metadata_cache))
                del _album_art_metadata_cache[oldest_key]
            
            _album_art_metadata_cache[metadata_path_str] = (current_mtime, metadata.copy())
        
        # CRITICAL FIX: Auto-discover custom images that aren't in metadata
        # This allows users to drop images into folders without manual JSON editing
        # Uses mtime caching to avoid performance impact on every metadata load
        metadata = discover_custom_images(folder, metadata, is_artist_images=False)
        
        # If new images were discovered, save updated metadata
        # Check if discovery found new images by comparing cache
        folder_key = str(folder.resolve())
        if folder_key in _discovery_cache:
            _, discovered_count = _discovery_cache[folder_key]
            if discovered_count > 0:
                # Save updated metadata with discovered images
                # Use existing save function which handles locks properly
                save_album_db_metadata(folder, metadata)
                # Invalidate cache after save
                if metadata_path_str in _album_art_metadata_cache:
                    del _album_art_metadata_cache[metadata_path_str]
        
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
            logger.warning(f"Preferred provider '{preferred_provider}' not found in DB for {artist} - {album}")
            return None
        
        provider_data = providers[preferred_provider]
        filename = provider_data.get("filename", f"{preferred_provider}.jpg")
        image_path = folder / filename
        
        # FIX: If preferred provider's file doesn't exist (e.g., download in progress or failed),
        # try to fall back to another available provider instead of returning None
        # This prevents the album art selector from appearing broken when a download is in progress
        if not image_path.exists():
            logger.debug(f"Preferred provider '{preferred_provider}' file not found, trying fallback providers")
            # Try to find any provider with an existing file
            for fallback_provider, fallback_data in providers.items():
                fallback_filename = fallback_data.get("filename", f"{fallback_provider}.jpg")
                fallback_path = folder / fallback_filename
                if fallback_path.exists():
                    logger.info(f"Using fallback provider '{fallback_provider}' (preferred '{preferred_provider}' file missing)")
                    # Use fallback but keep preferred_provider in metadata so UI shows correct selection
                    provider_data = fallback_data
                    filename = fallback_filename
                    image_path = fallback_path
                    break
            else:
                # No provider has a file - return None (downloads probably in progress)
                logger.debug(f"No provider files found for {artist} - {album}, downloads may be in progress")
                return None
        
        # OPTIMIZATION: Only update last_accessed if it's been more than 1 hour
        # This prevents constant disk writes on every poll cycle (every 100ms)
        should_save = True
        last_accessed_str = metadata.get("last_accessed")
        if last_accessed_str:
            try:
                # Parse the timestamp (handle Z suffix for UTC)
                if last_accessed_str.endswith('Z'):
                    last_accessed_str = last_accessed_str[:-1] + '+00:00'
                last_accessed = datetime.fromisoformat(last_accessed_str)
                # Convert to naive datetime for comparison with datetime.utcnow()
                if last_accessed.tzinfo is not None:
                    last_accessed = last_accessed.replace(tzinfo=None)
                # If less than 1 hour has passed, don't save
                time_diff = (datetime.utcnow() - last_accessed).total_seconds()
                if time_diff < 3600:  # 1 hour in seconds
                    should_save = False
            except (ValueError, AttributeError):
                # Parse error or missing datetime, save to fix format
                pass
        
        if should_save:
            metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            if save_album_db_metadata(folder, metadata):
                # Invalidate cache after save (since file mtime changed)
                if metadata_path_str in _album_art_metadata_cache:
                    del _album_art_metadata_cache[metadata_path_str]
        
        # Get saved background style (NEW for Phase 2)
        background_style = metadata.get("background_style")
        
        return {
            "path": image_path,
            "metadata": metadata,
            "background_style": background_style  # Return saved style preference
        }
    
    except Exception as e:
        logger.debug(f"Error loading album art from DB: {e}")
        return None

def load_artist_image_from_db(artist: str) -> Optional[Dict[str, Any]]:
    """
    Load preferred artist image from database if available.
    Returns the preferred image path if found.
    
    Args:
        artist: Artist name
        
    Returns:
        Dictionary with 'path' (Path to image) and 'metadata' (full metadata dict) if found, None otherwise
    """
    # Check if feature is enabled
    if not FEATURES.get("album_art_db", True):
        return None
    
    try:
        folder = get_album_db_folder(artist, None)  # Artist-only folder
        metadata_path = folder / "metadata.json"
        
        if not metadata_path.exists():
            return None
        
        # Load metadata
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # Check if this is artist images metadata
        if metadata.get("type") != "artist_images":
            return None
        
        # CRITICAL FIX: Auto-discover custom images that aren't in metadata
        # This allows users to drop images into folders without manual JSON editing
        # Uses mtime caching to avoid performance impact on every metadata load
        metadata = discover_custom_images(folder, metadata, is_artist_images=True)
        
        # If new images were discovered, save updated metadata
        # Check if discovery found new images by comparing cache
        folder_key = str(folder.resolve())
        if folder_key in _discovery_cache:
            _, discovered_count = _discovery_cache[folder_key]
            if discovered_count > 0:
                # Save updated metadata with discovered images
                # Use existing save function which handles locks properly
                save_album_db_metadata(folder, metadata)
                # Invalidate cache after save
                metadata_path_str = str(metadata_path)
                if metadata_path_str in _album_art_metadata_cache:
                    del _album_art_metadata_cache[metadata_path_str]
        
        preferred_provider = metadata.get("preferred_provider")
        preferred_filename = metadata.get("preferred_image_filename")  # NEW: Most robust matching method
        images = metadata.get("images", [])
        
        # CRITICAL FIX: Only return image if user EXPLICITLY selected one (has preferred_provider or preferred_filename)
        # This allows album art to be used when no explicit preference exists
        # The fallback to first available image happens in the fallback code block, not here
        if not preferred_provider and not preferred_filename:
            return None  # No explicit preference - let album art be used
        
        # Find preferred image (only if preference exists)
        matching_image = None
        
        # 1. Match by specific filename (MOST ROBUST - fixes multiple images from same source issue)
        if preferred_filename:
            for img in images:
                if img.get("filename") == preferred_filename and img.get("downloaded"):
                    matching_image = img
                    break
        
        # 2. Fallback: Parse provider name (backward compatibility)
        if not matching_image and preferred_provider:
            # Remove "(Artist)" suffix if present (backward compatibility)
            provider_name_clean = preferred_provider.replace(" (Artist)", "")
            
            # Check if provider name contains filename: "Source (filename)"
            if " (" in provider_name_clean:
                # Has filename in provider name: "Source (filename)"
                parts = provider_name_clean.split(" (", 1)
                if len(parts) == 2:
                    source_name = parts[0]
                    filename_from_provider = parts[1].rstrip(")")
                    
                    # Match by source AND filename (case-insensitive source comparison)
                    source_name_lower = source_name.lower()  # Normalize to lowercase
                    for img in images:
                        source = img.get("source", "")
                        if (source.lower() == source_name_lower and 
                            img.get("filename") == filename_from_provider and 
                            img.get("downloaded")):
                            matching_image = img
                            break
                else:
                    # Fallback: just source name (case-insensitive)
                    source_name = parts[0]
                    source_name_lower = source_name.lower()
                    for img in images:
                        source = img.get("source", "")
                        if source.lower() == source_name_lower and img.get("downloaded") and img.get("filename"):
                            matching_image = img
                            break
            else:
                # No filename in provider name - match by source only (gets first match)
                # CRITICAL FIX: Case-insensitive comparison to handle "Deezer" vs "deezer" mismatches
                source_name = provider_name_clean
                source_name_lower = source_name.lower()  # Normalize to lowercase for comparison
                for img in images:
                    source = img.get("source", "")
                    # Case-insensitive comparison to handle API inconsistencies
                    if source.lower() == source_name_lower and img.get("downloaded") and img.get("filename"):
                        matching_image = img
                        break
        
        if not matching_image:
            logger.debug(f"Preferred artist image not found for {artist}: preferred_provider={preferred_provider}, preferred_filename={preferred_filename}")
            return None  # Preferred image not found
        
        filename = matching_image.get("filename")
        image_path = folder / filename
        
        if not image_path.exists():
            return None
        
        # OPTIMIZATION: Only update last_accessed if it's been more than 1 hour
        # This prevents constant disk writes on every poll cycle (every 100ms)
        # Same optimization as album art to reduce unnecessary metadata.json writes
        should_save = True
        last_accessed_str = metadata.get("last_accessed")
        if last_accessed_str:
            try:
                # Parse the timestamp (handle Z suffix for UTC)
                if last_accessed_str.endswith('Z'):
                    last_accessed_str = last_accessed_str[:-1] + '+00:00'
                last_accessed = datetime.fromisoformat(last_accessed_str)
                # Convert to naive datetime for comparison with datetime.utcnow()
                if last_accessed.tzinfo is not None:
                    last_accessed = last_accessed.replace(tzinfo=None)
                # If less than 1 hour has passed, don't save
                time_diff = (datetime.utcnow() - last_accessed).total_seconds()
                if time_diff < 3600:  # 1 hour in seconds
                    should_save = False
            except (ValueError, AttributeError):
                # Parse error or missing datetime, save to fix format
                pass
        
        if should_save:
            metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            if save_album_db_metadata(folder, metadata):
                # Invalidate cache after save (since file mtime changed)
                metadata_path_str = str(metadata_path)
                if metadata_path_str in _album_art_metadata_cache:
                    del _album_art_metadata_cache[metadata_path_str]
        
        return {"path": image_path, "metadata": metadata}
        
    except Exception as e:
        logger.debug(f"Failed to load artist image from DB: {e}")
        return None

def _get_artist_image_fallback(artist: str) -> Optional[Dict[str, Any]]:
    """
    Get first available artist image as fallback (when no album art exists and no explicit preference).
    This is used as a last resort when no album art is found.
    
    Args:
        artist: Artist name
        
    Returns:
        Dictionary with 'path' (Path to image) and 'source' (source name) if found, None otherwise
    """
    try:
        artist_folder = get_album_db_folder(artist, None)
        artist_metadata_path = artist_folder / "metadata.json"
        
        if not artist_metadata_path.exists():
            return None
        
        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
            artist_metadata = json.load(f)
        
        if artist_metadata.get("type") != "artist_images":
            return None
        
        artist_images = artist_metadata.get("images", [])
        
        # Defensive logging: Log if no images found or all images failed to download
        if not artist_images:
            logger.debug(f"No artist images found in DB for fallback: {artist}")
            return None
        
        # Use first available artist image as fallback (no explicit preference needed)
        for img in artist_images:
            if img.get("downloaded") and img.get("filename"):
                filename = img.get("filename")
                artist_image_path = artist_folder / filename
                
                if artist_image_path.exists():
                    return {
                        "path": artist_image_path,
                        "source": img.get("source", "Unknown")
                    }
        
        # Log if images exist but none are downloaded or available
        logger.debug(f"Artist images found in DB for {artist} but none are downloaded or available")
        return None
    except Exception as e:
        logger.debug(f"Failed to load artist image fallback: {e}")
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
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        logger.debug(f"Track changed during download ({track_id} -> {current_track_id}), discarding art")
                        return

                # Save to cache
                art_path = CACHE_DIR / "spotify_art.jpg"
                # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
                # This prevents race conditions when skipping songs rapidly
                temp_filename = f"spotify_art_{uuid.uuid4().hex}.jpg.tmp"
                temp_path = CACHE_DIR / temp_filename
                
                # Write to temp (blocking I/O in executor)
                def write_file():
                    with open(temp_path, "wb") as f:
                        f.write(response.content)
                
                await loop.run_in_executor(None, write_file)
                
                # Final Validation: Check one last time before atomic replace
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                        return

                # Atomic replace with retry (use lock to prevent concurrent updates)
                # Run blocking I/O in executor while holding lock to prevent race conditions
                async with _art_update_lock:
                    replaced = False
                    for attempt in range(3):
                        try:
                            # Run blocking os.replace in executor to avoid blocking event loop
                            await loop.run_in_executor(None, os.replace, temp_path, art_path)
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
                
                # Extract colors (CPU-bound operation, might take time)
                colors = await extract_dominant_colors(art_path)
                
                # CRITICAL FIX: Re-validate track hasn't changed AFTER color extraction
                # Color extraction is CPU-bound and might take time, so track could change during it
                # If track changed, discard colors to prevent wrong track inheriting old colors
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        logger.debug(f"Track changed after color extraction ({track_id} -> {current_track_id}), discarding colors")
                        return
                
                # Update cache (only if track is still current)
                _get_current_song_meta_data_spotify._last_spotify_art_url = url
                _get_current_song_meta_data_spotify._last_spotify_colors = colors
                
        except Exception as e:
            logger.debug(f"Background Spotify art download failed: {e}")
            # Clean up unique temp file if it was created before the error
            if 'temp_path' in locals() and temp_path.exists():
                try:
                    os.remove(temp_path)
                except:
                    pass
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
        
        # --- APP BLOCKLIST CHECK ---
        # Get the App ID (e.g., "chrome.exe" or "Microsoft.MicrosoftEdge...")
        global _last_windows_app_id
        try:
            from settings import settings
            app_id = current_session.source_app_user_model_id.lower()
            blocklist = settings.get("system.windows.app_blocklist", [])
            
            # Track if this is a new app_id to avoid log spam
            is_new_app_id = (app_id != _last_windows_app_id)
            
            # Only log when app_id changes to avoid log spam
            if is_new_app_id:
                logger.info(f"Windows Media detected from app_id: '{app_id}' (blocklist: {blocklist})")
                _last_windows_app_id = app_id
            
            # Check if any blocklisted string is in the app_id
            if blocklist:
                for blocked_app in blocklist:
                    blocked_lower = blocked_app.lower()
                    if blocked_lower in app_id:
                        # Only log blocking when app_id first changes
                        if is_new_app_id:
                            logger.info(f"Ignoring media from blocked app: '{app_id}' (matched blocklist entry: '{blocked_app}')")
                        return None
                # If we get here, no match was found (detection already logged above if new app_id)
            else:
                # Blocklist is empty (detection already logged above if new app_id)
                pass
        except Exception as e:
            # Log the error instead of silently swallowing it
            logger.warning(f"Error checking app blocklist: {e} (app_id may be unavailable, allowing media to proceed)")
        # ---------------------------
            
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
            # artist = ""  # [REMOVED] Don't wipe artist name just because album is missing

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
        current_track_id = _normalize_track_id(artist, title)
        
        # Flag to track if we found art in DB
        found_in_db = False
        album_art_url = None
        result_extra_fields = {}  # Store album_art_path for direct serving
        saved_background_style = None  # Initialize to prevent UnboundLocalError

        # CRITICAL FIX: Separate album art (top left display) from background image
        # Album art should ALWAYS be album art, background can be artist image if selected
        background_image_url = None
        background_image_path = None
        
        # 1. Always load album art for top left display (independent of artist image preference)
        db_result = load_album_art_from_db(artist, album, title)
        if db_result:
            found_in_db = True
            db_image_path = db_result["path"]
            saved_background_style = db_result.get("background_style")  # Capture saved style
            
            # FIX: Add timestamp to URL to force browser cache busting when file updates
            mtime = int(time.time())
            try:
                if db_image_path.exists():
                    mtime = int(db_image_path.stat().st_mtime)
            except: pass
            
            # Album art URL is ALWAYS album art (for top left display)
            album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
            
            # NEW: Pass the path directly so server.py can serve it without copying
            # This eliminates race conditions from file copying
            result_extra_fields = {"album_art_path": str(db_image_path)}
            
            # Default background to album art (will be overridden if artist image is selected)
            background_image_url = album_art_url
            background_image_path = str(db_image_path)
        
        # 2. Check for artist image preference for background (separate from album art)
        # If user selected an artist image, use it for background instead of album art
        artist_image_result = load_artist_image_from_db(artist)
        if artist_image_result:
            artist_image_path = artist_image_result["path"]
            if artist_image_path.exists():
                mtime = int(artist_image_path.stat().st_mtime)
                # Use artist image for background (not for album art display)
                # Add type=background parameter so server knows to serve background_image_path
                background_image_url = f"/cover-art?id={current_track_id}&t={mtime}&type=background"
                background_image_path = str(artist_image_path)
                logger.debug(f"Using preferred artist image for background: {artist}")
        
        # CRITICAL FIX: Check if artist images DB is populated with ALL expected sources
        # This ensures all provider options are available in the selection menu (similar to album art backfill)
        # Only check if we have an artist name (required for folder lookup)
        if artist:
            try:
                artist_folder = get_album_db_folder(artist, None)
                artist_metadata_path = artist_folder / "metadata.json"
                
                # Check if metadata exists and has artist images
                artist_metadata_exists = artist_metadata_path.exists()
                artist_images_complete = False
                
                if artist_metadata_exists:
                    try:
                        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                            artist_metadata_check = json.load(f)
                        
                        if artist_metadata_check.get("type") == "artist_images":
                            existing_images = artist_metadata_check.get("images", [])
                            # Get sources that have downloaded images
                            existing_sources = {img.get("source") for img in existing_images if img.get("downloaded")}
                            
                            # Determine which sources SHOULD be there
                            # Deezer and TheAudioDB are always available (free, no auth)
                            expected_sources = {"Deezer", "TheAudioDB"}
                            
                            # FanArt.tv (if API key exists in environment)
                            if os.getenv("FANART_TV_API_KEY"):
                                expected_sources.add("FanArt.tv")
                            
                            # NOTE: Spotify and Last.fm are excluded for Windows Media source
                            # Windows Media doesn't provide artist_id, so Spotify fallback isn't available
                            # Last.fm is excluded from backfill as it's not necessary
                            
                            # Check if we have all expected sources
                            artist_images_complete = expected_sources.issubset(existing_sources)
                    except Exception as e:
                        logger.debug(f"Failed to check artist images completeness: {e}")
                        artist_images_complete = False
                
                # Trigger background task ONLY if artist images are incomplete (and not already running)
                # Use composite key with 'no_id' since Windows Media doesn't have artist_id
                artist_request_key = f"{artist}::no_id"
                
                if not artist_images_complete and artist_request_key not in _artist_download_tracker:
                    # Start background task to fetch from ALL missing sources
                    async def background_artist_images_backfill():
                        """Background task to fetch artist images from all enabled sources"""
                        try:
                            # This will fetch from Deezer, TheAudioDB, and FanArt.tv (if key exists)
                            # Spotify is not available for Windows Media (no artist_id)
                            # Last.fm is excluded per user preference
                            await ensure_artist_image_db(artist, None)  # No artist_id for Windows Media
                        except Exception as e:
                            logger.debug(f"Background artist image backfill failed for {artist}: {e}")
                    
                    # Use tracked task to prevent silent failures
                    create_tracked_task(background_artist_images_backfill())
            except Exception as e:
                logger.debug(f"Failed to check/trigger artist image backfill: {e}")
        
        # Fallback: Check for artist image if no album art found (but no explicit preference)
        # This uses first available artist image as fallback when no album art exists
        # Only use for background, not for album art display
        if not found_in_db:
            fallback_result = _get_artist_image_fallback(artist)
            if fallback_result:
                artist_image_path = fallback_result["path"]
                mtime = int(artist_image_path.stat().st_mtime)
                # Use fallback artist image for both (only when no album art exists)
                album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
                background_image_url = album_art_url
                result_extra_fields = {"album_art_path": str(artist_image_path)}
                background_image_path = str(artist_image_path)
                found_in_db = True
                logger.debug(f"Using artist image '{fallback_result.get('source')}' as fallback for {artist}")

        # 2. Windows Thumbnail Extraction (Fallback)
        # Only if not found in DB
        if not found_in_db:
            try:
                thumbnail_ref = info.thumbnail
                # Create a unique filename for this track's thumbnail to avoid race conditions
                # e.g., thumb_Artist_Title.jpg
                thumb_filename = f"thumb_{current_track_id}.jpg"
                thumb_path = CACHE_DIR / thumb_filename
                
                # Only extract if we haven't already for this track OR if file doesn't exist
                if thumbnail_ref and (not thumb_path.exists() or current_track_id != _last_windows_track_id):
                    stream = await thumbnail_ref.open_read_async()
                    if stream:
                        reader = DataReader(stream)
                        await reader.load_async(stream.size)
                        byte_data = bytearray(stream.size)
                        reader.read_bytes(byte_data)
                        
                        # Save directly to unique file (no race condition possible)
                        loop = asyncio.get_running_loop()
                        save_ok = await loop.run_in_executor(None, _save_windows_thumbnail_sync, thumb_path, byte_data)
                        
                        if save_ok:
                            _last_windows_track_id = current_track_id
                            
                            # Cleanup: Delete OLD thumbnails to keep cache small
                            # We only keep the current one
                            for f in CACHE_DIR.glob("thumb_*.jpg"):
                                if f.name != thumb_filename:
                                    try:
                                        os.remove(f)
                                    except:
                                        pass
                
                # If the file exists (either just saved or already there), use it
                if thumb_path.exists():
                    album_art_url = f"/cover-art?id={current_track_id}&t={int(time.time())}"
                    result_extra_fields = {"album_art_path": str(thumb_path)}
            except Exception as e:
                pass

        # 3. Background High-Res Fetch (Progressive Upgrade)
        # Only if not found in DB and not checked this session
        # Use 'win::' namespace to avoid blocking Spotify fetcher which might have better URLs
        checked_key = f"win::{current_track_id}"
        if not found_in_db and checked_key not in _db_checked_tracks:
            if current_track_id not in _running_art_upgrade_tasks:
                 _db_checked_tracks[checked_key] = time.time()
                 if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                     _db_checked_tracks.popitem(last=False)  # Remove oldest (FIFO)
                     
                 async def background_windows_art_upgrade():
                     try:
                         # Fetch and save to DB (no spotify_url available)
                         result = await ensure_album_art_db(artist, album, title, None)
                         # Check result; if failed, uncheck to allow retry on next poll
                         if not result:
                             # Failed (network error, etc) - remove from checked so we retry later
                             if checked_key in _db_checked_tracks:
                                 del _db_checked_tracks[checked_key]
                         # We don't need to do anything else; the NEXT poll loop 
                         # will see the file in DB (step 1 above) and auto-upgrade the UI.
                     except Exception as e:
                         logger.debug(f"Windows background art fetch failed: {e}")
                         # Remove from checked on error to allow retry
                         if checked_key in _db_checked_tracks:
                             del _db_checked_tracks[checked_key]
                     finally:
                         # CRITICAL FIX: Always remove from running tasks, even if task creation failed
                         _running_art_upgrade_tasks.pop(current_track_id, None)
                         
                 # CRITICAL FIX: Wrap task creation in try/finally to ensure cleanup
                 try:
                     task = create_tracked_task(background_windows_art_upgrade())
                     _running_art_upgrade_tasks[current_track_id] = task
                 except Exception as e:
                     # If task creation fails, ensure cleanup happens
                     _running_art_upgrade_tasks.pop(current_track_id, None)
                     logger.debug(f"Failed to create Windows art upgrade task: {e}")
                     raise

                 # FIX: Wait for DB to avoid flicker
                 try:
                     # Wait 300ms for high-res art
                     await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
                     
                     # Check DB again!
                     db_result = load_album_art_from_db(artist, album, title)
                     if db_result:
                         # Found it! Update variables to use High-Res immediately
                         found_in_db = True
                         db_image_path = db_result["path"]
                         
                         # NEW: Use path directly instead of copying (eliminates race conditions)
                         try:
                             # FIX: Add timestamp for cache busting
                             mtime = int(time.time())
                             try:
                                 if db_image_path.exists():
                                     mtime = int(db_image_path.stat().st_mtime)
                             except: pass
                             album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
                             result_extra_fields = {"album_art_path": str(db_image_path)}
                         except Exception as e:
                             logger.debug(f"Failed to set DB art path after wait: {e}")
                             # If setting path fails, we fall back to the Windows thumbnail which is already set
                 except asyncio.TimeoutError:
                     pass # Fallback to Windows thumbnail

        # CRITICAL FIX: Separate album_art_url (top left display) from background_image_url (background)
        result = {
            "track_id": current_track_id,  # ADDED: Normalized ID for frontend change detection
            "artist": artist,
            "title": title,
            "album": album if album else None,
            "position": position,
            "duration_ms": duration_ms,
            "colors": ("#24273a", "#363b54"),
            "album_art_url": album_art_url,  # ALWAYS album art (for top left display)
            "background_image_url": background_image_url if background_image_url else album_art_url,  # Artist image if selected, else album art
            "is_playing": True,
            "source": "windows_media",
            "background_style": saved_background_style  # Return saved style preference
        }
        
        # Add album_art_path if we have a direct path (DB file or unique thumbnail)
        if result_extra_fields.get("album_art_path"):
            result["album_art_path"] = result_extra_fields["album_art_path"]
        
        # CRITICAL FIX: Add background_image_path if it exists (for server.py to serve background)
        # This was missing in Windows Media function but present in Spotify function
        if background_image_path:
            result["background_image_path"] = background_image_path
        
        return result
            
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
        captured_artist_id = track.get("artist_id")  # For artist image backfill
        captured_track_id = _normalize_track_id(captured_artist, captured_title)
        
        # Flag to track if we found art in DB
        found_in_db = False
        album_art_path = None  # Store direct path for serving without copying
        saved_background_style = None  # Initialize to prevent UnboundLocalError
        db_metadata = None  # Initialize to prevent UnboundLocalError

        # CRITICAL FIX: Separate album art (top left display) from background image
        # Album art should ALWAYS be album art, background can be artist image if selected
        background_image_url = None
        background_image_path = None
        
        # 1. Always load album art for top left display (independent of artist image preference)
        db_result = load_album_art_from_db(captured_artist, captured_album, captured_title)
        if db_result:
            found_in_db = True
            db_image_path = db_result["path"]
            db_metadata = db_result["metadata"]
            saved_background_style = db_result.get("background_style")  # Capture saved style
            
            # FIX: Add timestamp to URL to force browser cache busting
            mtime = int(time.time())
            try:
                if db_image_path.exists():
                    mtime = int(db_image_path.stat().st_mtime)
            except: pass
            
            # Album art URL is ALWAYS album art (for top left display)
            album_art_url = f"/cover-art?id={captured_track_id}&t={mtime}"

            # NEW: Store path directly so server.py can serve it without copying
            # This eliminates race conditions from file copying
            album_art_path = str(db_image_path)
            
            # Default background to album art (will be overridden if artist image is selected)
            background_image_url = album_art_url
            background_image_path = album_art_path
        
        # 2. Check for artist image preference for background (separate from album art)
        # If user selected an artist image, use it for background instead of album art
        artist_image_result = load_artist_image_from_db(captured_artist)
        if artist_image_result:
            artist_image_path = artist_image_result["path"]
            if artist_image_path.exists():
                mtime = int(artist_image_path.stat().st_mtime)
                # Use artist image for background (not for album art display)
                # Add type=background parameter so server knows to serve background_image_path
                background_image_url = f"/cover-art?id={captured_track_id}&t={mtime}&type=background"
                background_image_path = str(artist_image_path)
                logger.debug(f"Using preferred artist image for background: {captured_artist}")
        
        # If no album art found but artist image is selected, still set background
        if not found_in_db and artist_image_result:
            found_in_db = True  # At least we have something for background
        
        # CRITICAL FIX: Check if artist images DB is populated with ALL expected sources
        # This ensures all provider options are available in the selection menu (similar to album art backfill)
        # Only check if we have an artist name (required for folder lookup)
        if captured_artist:
            try:
                artist_folder = get_album_db_folder(captured_artist, None)
                artist_metadata_path = artist_folder / "metadata.json"
                
                # Check if metadata exists and has artist images
                artist_metadata_exists = artist_metadata_path.exists()
                artist_images_complete = False
                
                if artist_metadata_exists:
                    try:
                        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                            artist_metadata_check = json.load(f)
                        
                        if artist_metadata_check.get("type") == "artist_images":
                            existing_images = artist_metadata_check.get("images", [])
                            # Get sources that have downloaded images
                            existing_sources = {img.get("source") for img in existing_images if img.get("downloaded")}
                            
                            # Determine which sources SHOULD be there
                            # Deezer and TheAudioDB are always available (free, no auth)
                            expected_sources = {"Deezer", "TheAudioDB"}
                            
                            # FanArt.tv (if API key exists in environment)
                            if os.getenv("FANART_TV_API_KEY"):
                                expected_sources.add("FanArt.tv")
                            
                            # Spotify (if artist_id is available)
                            if captured_artist_id:
                                expected_sources.add("Spotify")
                            
                            # NOTE: Last.fm is excluded from backfill as it's not necessary
                            # Last.fm images are often low-quality placeholders and not needed for selection menu
                            
                            # Check if we have all expected sources
                            artist_images_complete = expected_sources.issubset(existing_sources)
                    except Exception as e:
                        logger.debug(f"Failed to check artist images completeness: {e}")
                        artist_images_complete = False
                
                # Trigger background task ONLY if artist images are incomplete (and not already running)
                # Use composite key to prevent duplicate downloads for same artist+ID
                artist_request_key = f"{captured_artist}::{captured_artist_id or 'no_id'}"
                
                if not artist_images_complete and artist_request_key not in _artist_download_tracker:
                    # Start background task to fetch from ALL missing sources
                    async def background_artist_images_backfill():
                        """Background task to fetch artist images from all enabled sources"""
                        try:
                            # This will fetch from Deezer, TheAudioDB, FanArt.tv (if key exists), and Spotify (if ID available)
                            # Last.fm is excluded per user preference
                            await ensure_artist_image_db(captured_artist, captured_artist_id)
                        except Exception as e:
                            logger.debug(f"Background artist image backfill failed for {captured_artist}: {e}")
                    
                    # Use tracked task to prevent silent failures
                    create_tracked_task(background_artist_images_backfill())
            except Exception as e:
                logger.debug(f"Failed to check/trigger artist image backfill: {e}")
        
        # Check if DB is already populated with ALL enabled providers (only if we have album art metadata)
        if db_metadata:
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
            # Use 'spot::' namespace to distinguish from Windows fetcher checks
            checked_key = f"spot::{captured_track_id}"
            if (not db_is_complete or has_invalid_resolution) and captured_track_id not in _running_art_upgrade_tasks and checked_key not in _db_checked_tracks:
                # Mark as checked immediately to prevent re-entry on next poll
                _db_checked_tracks[checked_key] = time.time()
                
                # Limit set size to prevent memory leaks (FIFO eviction)
                if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                    _db_checked_tracks.popitem(last=False)  # Remove oldest

                async def background_refresh_db():
                    try:
                        # This function now returns the best URL and resolution
                        # Pass retry_count=1 to prevent infinite recursion
                        result = await ensure_album_art_db(
                            captured_artist,
                            captured_album,
                            captured_title,
                            raw_spotify_url,
                            retry_count=1
                        )
                        # Check result; if failed, uncheck to allow retry on next poll
                        if not result:
                            if checked_key in _db_checked_tracks:
                                del _db_checked_tracks[checked_key]
                        return result
                    except Exception as e:
                        logger.debug(f"Background DB refresh failed: {e}")
                        # Remove from checked on error to allow retry
                        if checked_key in _db_checked_tracks:
                            del _db_checked_tracks[checked_key]
                    finally:
                        _running_art_upgrade_tasks.pop(captured_track_id, None)
                
                # Use tracked task
                task = create_tracked_task(background_refresh_db())
                _running_art_upgrade_tasks[captured_track_id] = task
        
        # Fallback: Check for artist image if no album art found (but no explicit preference)
        # This uses first available artist image as fallback when no album art exists
        if not found_in_db:
            fallback_result = _get_artist_image_fallback(captured_artist)
            if fallback_result:
                artist_image_path = fallback_result["path"]
                mtime = int(artist_image_path.stat().st_mtime)
                # Use fallback artist image for both (only when no album art exists)
                album_art_url = f"/cover-art?id={captured_track_id}&t={mtime}"
                background_image_url = album_art_url
                album_art_path = str(artist_image_path)
                background_image_path = str(artist_image_path)
                found_in_db = True
                logger.debug(f"Using artist image '{fallback_result.get('source')}' as fallback for {captured_artist}")
        
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
                captured_track_id = _normalize_track_id(captured_artist, captured_title)
                
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
                    # Use 'spot::' namespace to distinguish from Windows fetcher checks
                    checked_key = f"spot::{captured_track_id}"
                    if not found_in_db and checked_key not in _db_checked_tracks:
                        if captured_track_id in _running_art_upgrade_tasks:
                            # Task already running - only log once per track to prevent spam
                            if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_art_upgrade_running_track_id') or \
                               _get_current_song_meta_data_spotify._last_logged_art_upgrade_running_track_id != captured_track_id:
                                logger.debug(f"Background art upgrade already running for {captured_track_id}, skipping duplicate task")
                                _get_current_song_meta_data_spotify._last_logged_art_upgrade_running_track_id = captured_track_id
                        else:
                            # Mark as checked immediately to prevent re-entry on next poll
                            _db_checked_tracks[checked_key] = time.time()
                            
                            # Limit set size to prevent memory leaks (FIFO eviction)
                            if len(_db_checked_tracks) > _MAX_DB_CHECKED_SIZE:
                                _db_checked_tracks.popitem(last=False)  # Remove oldest
                            
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
                                        
                                        # Check result; if failed, uncheck to allow retry on next poll
                                        if not high_res_result:
                                            if checked_key in _db_checked_tracks:
                                                del _db_checked_tracks[checked_key]
                                        
                                        # Update the provider cache immediately
                                        if high_res_result:
                                            # Update cache manually since we skipped get_high_res_art
                                            # We need to construct the cache key exactly like the provider does
                                            cache_key = art_provider._get_cache_key(captured_artist, captured_title, captured_album)
                                            art_provider._cache[cache_key] = high_res_result
                                            logger.debug(f"Updated art provider cache from DB result for {captured_artist} - {captured_title}")
                                            
                                    except Exception as e:
                                        logger.error(f"ensure_album_art_db failed: {e}")
                                        # Remove from checked on error to allow retry
                                        if checked_key in _db_checked_tracks:
                                            del _db_checked_tracks[checked_key]
                                    
                                    # REMOVED: Redundant call to art_provider.get_high_res_art
                                    # This prevents the double-flicker (once for remote high-res, once for local DB)
                                    # and saves sequential network requests since ensure_album_art_db already fetched everything in parallel.
                                    
                                    # Check if track changed during fetch (race condition protection)
                                    # Get current track from Spotify cache to verify
                                    current_spotify_client = get_shared_spotify_client()
                                    if current_spotify_client and current_spotify_client._metadata_cache:
                                        current_track = current_spotify_client._metadata_cache
                                        current_track_id = _normalize_track_id(
                                            current_track.get('artist', ''),
                                            current_track.get('title', '')
                                        )
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
                                    # Remove from checked on error to allow retry
                                    if checked_key in _db_checked_tracks:
                                        del _db_checked_tracks[checked_key]
                                finally:
                                    # Remove from running tasks when done
                                    _running_art_upgrade_tasks.pop(captured_track_id, None)
                            
                            # Start background task (non-blocking) and track it
                            # Use tracked task to prevent garbage collection issues
                            # CRITICAL FIX: Reserve slot first to prevent race condition
                            # If task creation fails after reserving slot, we can still clean up
                            _running_art_upgrade_tasks[captured_track_id] = None  # Reserve slot
                            try:
                                task = create_tracked_task(background_upgrade_art())
                                _running_art_upgrade_tasks[captured_track_id] = task
                            except Exception as e:
                                # If task creation fails, ensure cleanup happens
                                _running_art_upgrade_tasks.pop(captured_track_id, None)
                                logger.debug(f"Failed to create background art upgrade task: {e}")
                                raise
                    
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
                    # CRITICAL FIX: Wrap in try/finally to ensure cleanup even if task creation fails
                    try:
                        create_tracked_task(_download_spotify_art_background(album_art_url, captured_track_id))
                    except Exception as e:
                        # If task creation fails, ensure cleanup happens
                        _spotify_download_tracker.discard(album_art_url)
                        logger.debug(f"Failed to create download task: {e}")
                        raise
                    
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
        # Include background_style for Phase 2: Visual Preference Persistence
        # CRITICAL FIX: Separate album_art_url (top left display) from background_image_url (background)
        result = {
            "id": track.get("track_id"),    # CHANGED: Use REAL Spotify ID (fixes Like button)
            "track_id": captured_track_id,  # ADDED: Normalized ID (fixes Visual Mode detection)
            "artist": track["artist"],
            "title": track["title"],
            "album": track.get("album"),
            "position": track["progress_ms"] / 1000,
            "duration_ms": track.get("duration_ms"),
            "colors": colors,
            "album_art_url": album_art_url,  # ALWAYS album art (for top left display)
            "background_image_url": background_image_url if background_image_url else album_art_url,  # Artist image if selected, else album art
            "is_playing": True,
            "source": "spotify",
            "artist_id": track.get("artist_id"),  # For fetching artist images
            "artist_name": track.get("artist_name"),  # For display purposes
            "background_style": saved_background_style,  # Return saved style preference (Phase 2)
            "url": track.get("url")  # Spotify Web URL for album art click functionality
        }
        
        # Add album_art_path if we have a direct path (DB file)
        if album_art_path:
            result["album_art_path"] = album_art_path
        
        # Add background_image_path if it exists (for server.py to serve background)
        if background_image_path:
            result["background_image_path"] = background_image_path
        
        return result
    except Exception as e:
        logger.error(f"Spotify API Error: {e}")
        return None

# --- Main Function ---

async def get_current_song_meta_data() -> Optional[dict]:
    """
    Main orchestrator to get song data from configured sources with hybrid enrichment.
    
    CRITICAL FIX: Uses a lock to prevent concurrent execution.
    Checks if song changed before using cache to prevent stale metadata.
    """
    # CRITICAL FIX: Lock the entire fetching process
    # This prevents the race condition where Task B reads cache while Task A is still updating it
    async with _meta_data_lock:
        current_time = time.time()
        last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
        is_active = getattr(get_current_song_meta_data, '_is_active', True)
        last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
        
        required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
        
        last_song = getattr(get_current_song_meta_data, '_last_song', None)
        last_track_id = getattr(get_current_song_meta_data, '_last_track_id', None)
        
        # Only use cache if within interval AND song hasn't changed
        if (current_time - last_check) < required_interval:
            cached_result = getattr(get_current_song_meta_data, '_last_result', None)
            if cached_result:
                # IMPROVED: Check both song name AND track_id for more reliable change detection
                # This handles rapid track changes better than name-only comparison
                cached_song_name = f"{cached_result.get('artist', '')} - {cached_result.get('title', '')}"
                cached_track_id = cached_result.get('track_id') or cached_result.get('id')
                
                # Verify both song name and track_id match (if track_id is available)
                song_name_matches = last_song == cached_song_name
                
                # Track ID matching logic:
                # - If both have track_ids, they must be equal
                # - If both are missing (None/empty), they match (both None)
                # - If one has track_id and other doesn't, they DON'T match (different tracks)
                if cached_track_id and last_track_id:
                    # Both have track_ids - must be equal
                    track_id_matches = (cached_track_id == last_track_id)
                elif not cached_track_id and not last_track_id:
                    # Both missing - match (both None, can't distinguish)
                    track_id_matches = True
                else:
                    # One has track_id, other doesn't - different tracks
                    track_id_matches = False
                
                if song_name_matches and track_id_matches:
                    # Song hasn't changed, safe to use cache
                    # CRITICAL FIX: Update _last_song and _last_track_id to stay in sync with cached data
                    get_current_song_meta_data._last_song = cached_song_name
                    if cached_track_id:
                        get_current_song_meta_data._last_track_id = cached_track_id
                    return cached_result
                else:
                    # Song changed! Invalidate cache and fetch fresh data
                    # This ensures we detect song changes immediately, not after cache expires
                    change_reason = []
                    if not song_name_matches:
                        change_reason.append(f"name ({last_song} -> {cached_song_name})")
                    if not track_id_matches:
                        change_reason.append(f"track_id ({last_track_id} -> {cached_track_id})")
                    logger.debug(f"Song changed in cache ({', '.join(change_reason)}), invalidating cache to fetch fresh data")
                    get_current_song_meta_data._last_check_time = 0  # Force refresh by resetting check time
            else:
                # If last result was None (Idle/Paused) and we are within interval,
                # return None immediately. This prevents aggressive polling when nothing is playing.
                return None
        
        # Update check time only when we are committed to fetching (inside the lock)
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
        is_spotify_only = (result and 
                        result.get("source") == "spotify" and 
                        (not windows_media_checked or windows_media_result is None))
        
        # Adjust Spotify API polling speed based on mode
        # Fast mode (2.0s) for Spotify-only to reduce latency, Normal mode (6.0s) when Windows Media is active
        spotify_client = get_shared_spotify_client()
        if spotify_client and spotify_client.initialized:
            if is_spotify_only:
                spotify_client.set_fast_mode(True)
            else:
                spotify_client.set_fast_mode(False)
        
        # 2. HYBRID ENRICHMENT - Merge Spotify data if primary source lacks album art/controls
        if result and result.get("source") == "windows_media":
            try:
                # Smart Wake-Up Logic: Only force refresh if Windows says playing BUT Spotify cache says paused
                # This prevents unnecessary force_refresh flags and reduces API calls
                is_windows_playing = result.get("is_playing", False)
                spotify_cached_paused = False
                
                # Check Spotify cache state to determine if we need to wake it up
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
                                    # CRITICAL FIX: Also copy the album_art_path if Spotify loaded from DB
                                    # This ensures server.py serves the high-res DB image instead of the low-res thumbnail
                                    if spotify_data.get("album_art_path"):
                                        result["album_art_path"] = spotify_data["album_art_path"]
                                else:
                                    art_provider = get_album_art_provider()
                                    
                                    # Check cache first - if cached high-res exists, use it immediately
                                    # Use album-level cache (same album = same art for all tracks)
                                    cached_result = art_provider.get_from_cache(
                                        spotify_data.get("artist", ""),
                                        spotify_data.get("title", ""),
                                        spotify_data.get("album")
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
                                    
                                    # CRITICAL FIX: Clear Windows thumbnail path when using remote Spotify URL
                                    # This ensures frontend uses the remote URL directly instead of serving low-res thumbnail
                                    if result.get("album_art_path") and not spotify_data.get("album_art_path"):
                                        # Spotify doesn't have a local path (remote URL), so clear Windows path
                                        # Frontend will use album_art_url (remote) directly
                                        result.pop("album_art_path", None)
                                        
                                        # Check if a background task is already running for this track
                                        hybrid_track_id = _normalize_track_id(
                                            spotify_data.get('artist', ''),
                                            spotify_data.get('title', '')
                                        )
                                        if hybrid_track_id in _running_art_upgrade_tasks:
                                            # Task already running, skip creating duplicate - only log once per track to prevent spam
                                            if not hasattr(get_current_song_meta_data, '_last_logged_hybrid_art_upgrade_running_track_id') or \
                                               get_current_song_meta_data._last_logged_hybrid_art_upgrade_running_track_id != hybrid_track_id:
                                                logger.debug(f"Background art upgrade already running for {hybrid_track_id}, skipping duplicate task")
                                                get_current_song_meta_data._last_logged_hybrid_art_upgrade_running_track_id = hybrid_track_id
                                        else:
                                            # Start background task to fetch high-res
                                            async def background_upgrade_hybrid():
                                                try:
                                                    await asyncio.sleep(0.1)
                                                    # Use ensure_album_art_db instead of just get_high_res_art
                                                    # This ensures proper saving to DB, not just memory caching
                                                    # This fixes the issue where Spotify art wasn't being saved
                                                    # when Windows Media fetcher ran first (race condition fix)
                                                    high_res_result = await ensure_album_art_db(
                                                        spotify_data.get("artist", ""),
                                                        spotify_data.get("album"),
                                                        spotify_data.get("title", ""),
                                                        spotify_art_url
                                                    )
                                                    
                                                    # Update cache manually if successful (so UI updates immediately)
                                                    if high_res_result:
                                                        art_provider = get_album_art_provider()
                                                        cache_key = art_provider._get_cache_key(
                                                            spotify_data.get("artist", ""),
                                                            spotify_data.get("title", ""),
                                                            spotify_data.get("album")
                                                        )
                                                        art_provider._cache[cache_key] = high_res_result
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
                                # Also copy path if available (even on error, we might have a valid path)
                                if spotify_data.get("album_art_path"):
                                    result["album_art_path"] = spotify_data["album_art_path"]
                        
                        # Steal Colors from Spotify (now properly extracted!)
                        if spotify_data.get("colors"):
                            result["colors"] = spotify_data.get("colors")

                        # Enable Controls by marking as hybrid
                        # Frontend will allow controls for this source type
                        result["source"] = "spotify_hybrid"
                        
                        # CRITICAL FIX: Copy Spotify ID for Like button functionality
                        # This ensures the Like button works even when playing from Windows Media
                        if spotify_data.get("id"):
                            result["id"] = spotify_data.get("id")
                        
                        # Copy Spotify URL for album art click functionality
                        # This enables opening the song in Spotify app/web when clicking album art
                        if spotify_data.get("url"):
                            result["url"] = spotify_data.get("url")
                        
                        # Copy Artist ID and Name for Visual Mode
                        # This ensures artist slideshows work even when playing from Windows Media
                        if spotify_data.get("artist_id"):
                            result["artist_id"] = spotify_data.get("artist_id")
                        if spotify_data.get("artist_name"):
                            result["artist_name"] = spotify_data.get("artist_name")
                        
                        # Copy Background Style preference (Phase 2)
                        if spotify_data.get("background_style"):
                            result["background_style"] = spotify_data.get("background_style")
                        
                        # if DEBUG["enabled"]:
                        #    logger.info(f"Hybrid mode: Enriched Windows Media data with Spotify album art and controls")
            except Exception as e:
                logger.error(f"Hybrid enrichment failed: {e}")
        
        # 4. If we still don't have colors (e.g. local file), extract them
        if result and result.get("source") == "windows_media":
            # NEW: Use the specific path we found/created, falling back to legacy search
            # This fixes color extraction for the new unique thumbnail system (thumb_*.jpg)
            local_art_path = None
            if result.get("album_art_path"):
                local_art_path = Path(result["album_art_path"])
            else:
                local_art_path = get_cached_art_path()
            
            if result.get("colors") == ("#24273a", "#363b54") and local_art_path and local_art_path.exists():
                 # Only extract if we have a valid local file and default colors
                 # Now async, so we await it
                 result["colors"] = await extract_dominant_colors(local_art_path)

        # 3. State Management (Active vs Idle)
        if result:
            get_current_song_meta_data._is_active = True
            get_current_song_meta_data._last_active_time = current_time
            
            last_song = getattr(get_current_song_meta_data, '_last_song', None)
            current_song_name = f"{result.get('artist')} - {result.get('title')}"
            
            # Update last_song inside the lock
            if last_song != current_song_name:
                get_current_song_meta_data._last_song = current_song_name
                get_current_song_meta_data._last_source = result.get('source')
                _log_app_state()
        else:
            if (current_time - last_active_time) > IDLE_WAIT_TIME:
                get_current_song_meta_data._is_active = False

        get_current_song_meta_data._last_result = result
        
        # IMPROVED: Store track_id for rapid change detection
        # This helps detect track changes even when song name might be similar
        if result:
            result_song_name = f"{result.get('artist', '')} - {result.get('title', '')}"
            result_track_id = result.get('track_id') or result.get('id')
            get_current_song_meta_data._last_song = result_song_name
            if result_track_id:
                get_current_song_meta_data._last_track_id = result_track_id
        
        # RESTORED: Update current_art.jpg for debugging/external tools
        # This ensures the cache folder always has the current art file
        await _update_debug_art(result)
        
        _log_app_state()
        
        return result

async def ensure_artist_image_db(artist: str, spotify_artist_id: Optional[str] = None) -> List[str]:
    """
    Background task to fetch artist images and save them to the database.
    Fetches from multiple sources: Deezer, TheAudioDB, FanArt.tv, Spotify, and Last.fm.
    
    Priority order:
    1. Deezer (free, 1000x1000px, no auth required)
    2. TheAudioDB (free key '123', provides MBID for FanArt.tv)
    3. FanArt.tv (requires FANART_TV_API_KEY in .env + MBID from TheAudioDB)
    4. Spotify (fallback, if spotify_artist_id provided)
    5. Last.fm (fallback, if LASTFM_API_KEY in .env)
    
    Note: iTunes is NOT used for artist images (it rarely works for artists).
    """
    # Temporarily disable artist image fetching while we work on the bug. This is intentional. 
    # return [] 

    # Declare global variables for throttle tracking
    global _artist_image_log_throttle, _artist_db_check_cache

    # Check cache first (debouncing)
    # If we checked this artist recently (within 60 seconds), return cached result
    # This prevents spamming the logic/logs when frontend polls frequently
    current_time = time.time()
    cached_data = _artist_db_check_cache.get(artist)
    if cached_data:
        timestamp, cached_result = cached_data
        if current_time - timestamp < 60:
            return cached_result

    # CRITICAL FIX: Use composite key (artist + spotify_id) to prevent race conditions
    # This ensures that if track changes, we don't save images from previous artist
    request_key = f"{artist}::{spotify_artist_id or 'no_id'}"
    
    # Prevent duplicate downloads for the same artist+ID combination
    if request_key in _artist_download_tracker:
        return []
    
    # Fix 5: Add size limit to tracker (Defensive coding)
    if len(_artist_download_tracker) > 50:
        logger.warning("Artist download tracker full, clearing to prevent leaks")
        _artist_download_tracker.clear()

    _artist_download_tracker.add(request_key)
    
    # Store original values for validation
    original_artist = artist
    original_spotify_id = spotify_artist_id
    
    try:
        # Use dedicated semaphore for artist images to prevent deadlock with album art downloads
        async with _artist_download_semaphore:
            try:
                folder = get_album_db_folder(artist, None) # Artist-only folder
                folder.mkdir(parents=True, exist_ok=True)
                
                metadata_path = folder / "metadata.json"
                existing_metadata = {}
                
                # Check if artist images already exist in DB (optimization)
                # If images exist, return immediately (no need to re-fetch)
                if metadata_path.exists():
                    try:
                        with open(metadata_path, 'r', encoding='utf-8') as f:
                            existing_metadata = json.load(f)
                        
                        existing_images = existing_metadata.get("images", [])
                        
                        # If images exist, return immediately (no need to re-fetch)
                        if len(existing_images) > 0:
                            from urllib.parse import quote
                            encoded_folder = quote(folder.name, safe='')
                            result_paths = [
                                f"/api/album-art/image/{encoded_folder}/{quote(img.get('filename', ''), safe='')}" 
                                for img in existing_images 
                                if img.get('downloaded') and img.get('filename')
                            ]
                            
                            # Update cache
                            _artist_db_check_cache[artist] = (time.time(), result_paths)
                            return result_paths
                            
                    except Exception as e:
                        logger.debug(f"Failed to load cached artist images: {e}")
                        # Continue to fetch if cache read fails

                # Initialize our new dedicated artist image provider (singleton pattern)
                # Use global instance to prevent re-initialization on every call
                global _artist_image_provider
                if _artist_image_provider is None:
                    _artist_image_provider = ArtistImageProvider()
                artist_provider = _artist_image_provider
                
                # Fetch from new sources (Deezer, TheAudioDB, FanArt.tv)
                # This returns: [{'url':..., 'source':..., 'type':..., 'width':..., 'height':...}]
                all_images = await artist_provider.get_artist_images(artist)
                
                # Fallback 1: Spotify (if ID provided) - Keep as backup
                # CRITICAL FIX: Validate artist ID to prevent race conditions
                # If track changed while this function was running, spotify_artist_id might be stale
                if spotify_artist_id:
                    client = get_shared_spotify_client()
                    if client:
                        try:
                            # Verify the artist ID is still valid for this artist
                            # This prevents saving images from previous artist when track changes
                            spotify_urls = await client.get_artist_images(spotify_artist_id)
                            if spotify_urls:
                                # Only add if not already present (simple check)
                                existing_urls = {i['url'] for i in all_images}
                                for url in spotify_urls:
                                    if url not in existing_urls:
                                        all_images.append({
                                            "url": url,
                                            "source": "spotify",
                                            "type": "artist"
                                        })
                                        break # Just one from Spotify is enough if we have others
                        except Exception as e:
                            # If validation fails (e.g., artist_id is stale), skip Spotify images
                            logger.debug(f"Spotify artist image validation failed for {artist} (possible race condition): {e}")
                            # Don't add stale Spotify images from previous track
                
                # Fallback 2: Last.fm (via old provider if enabled)
                # NOTE: iTunes is NOT used for artist images because it rarely provides artist artwork.
                # iTunes Search API is designed for app icons and album art, not artist photos.
                # iTunes remains enabled for ALBUM art fetching in providers/album_art.py
                try:
                    art_provider = get_album_art_provider()
                    # Only use Last.fm from the old provider (skip iTunes)
                    if art_provider.enable_lastfm and art_provider.lastfm_api_key:
                        # Use public async method instead of private sync method
                        lastfm_images = await art_provider.get_artist_images(artist)
                        existing_urls = {i['url'] for i in all_images}
                        for img in lastfm_images:
                            if img.get('url') and img['url'] not in existing_urls:
                                all_images.append(img)
                except Exception as e:
                    logger.debug(f"Last.fm fallback failed: {e}")
                
                # Log summary with throttle (prevents spam when function runs multiple times)
                # Only log if enough time has passed since last log for this artist
                current_time = time.time()
                last_log_time = _artist_image_log_throttle.get(artist, 0)
                should_log = (current_time - last_log_time) >= _ARTIST_IMAGE_LOG_THROTTLE_SECONDS
                
                if should_log:
                    if all_images:
                        logger.info(f"Artist images fetched for '{artist}': {len(all_images)} total from all sources")
                    else:
                        logger.info(f"Artist images fetched for '{artist}': No images found from any source")
                    
                    # Update throttle timestamp
                    _artist_image_log_throttle[artist] = current_time
                    
                    # Clean up old entries to prevent memory leak (keep only last 100 artists)
                    if len(_artist_image_log_throttle) > 100:
                        # Remove oldest entries (artists not logged in last 5 minutes)
                        cutoff_time = current_time - 300  # 5 minutes
                        _artist_image_log_throttle = {
                            k: v for k, v in _artist_image_log_throttle.items() 
                            if v > cutoff_time
                        }

                # Download and Save
                saved_images = existing_metadata.get("images", [])
                metadata_changed = False  # OPTIMIZATION: Track if we actually need to save to disk
                
                # CRITICAL FIX: Track newly downloaded files for cleanup if validation fails
                # Store original list of existing filenames to identify new downloads
                existing_filenames = {img.get('filename') for img in saved_images if img.get('filename')}
                newly_downloaded_files = []  # Track (file_path, filename) tuples for cleanup
                
                # Simple deduplication set (by URL)
                existing_urls = {img.get('url') for img in saved_images if img.get('url')}
                
                loop = asyncio.get_running_loop()
                
                # Track counts per source for filename generation
                source_counts = {}
                
                for img_dict in all_images:
                    # CRITICAL FIX: Check if artist changed during download to prevent race conditions
                    # If track changed while we were fetching, discard these images
                    # IMPORTANT: Force fresh metadata fetch (bypass cache) to detect rapid track changes
                    try:
                        # Force fresh fetch by clearing cache timestamp
                        get_current_song_meta_data._last_check_time = 0
                        current_metadata = await get_current_song_meta_data()
                        if current_metadata:
                            current_artist = current_metadata.get("artist", "")
                            current_artist_id = current_metadata.get("artist_id")
                            
                            # CRITICAL FIX: Only abort if artist NAME changed OR if we HAD an ID and it changed to a DIFFERENT ID
                            # If original_spotify_id was None and now it's set (but artist name is same), that's fine
                            # This prevents infinite loops when ID gets populated during fetch
                            name_changed = current_artist != original_artist
                            id_changed = current_artist_id != original_spotify_id
                            
                            # Only consider ID change a failure if we HAD an ID originally and it changed to a DIFFERENT NON-NULL ID
                            # If original_spotify_id was None and now it's set, but artist name is same, that's fine.
                            # FIX: Ignore if current_artist_id became None (lost connection) but name is still same
                            # This prevents false positives when switching from Spotify (has ID) to Windows Media (no ID)
                            id_mismatch_is_critical = (
                                original_spotify_id is not None and 
                                current_artist_id is not None and 
                                current_artist_id != original_spotify_id
                            )
                            
                            if name_changed or id_mismatch_is_critical:
                                logger.info(f"Artist changed from '{original_artist}' to '{current_artist}' (ID: {original_spotify_id} -> {current_artist_id}) during fetch, discarding images")
                                return []  # Abort entire operation
                    except Exception as e:
                        logger.debug(f"Failed to check current artist during download: {e}")
                        # Continue if check fails (defensive)
                    
                    url = img_dict.get('url')
                    source = img_dict.get('source', 'unknown')
                    
                    if not url or url in existing_urls:
                        continue
                    
                    # Generate filename with source prefix and index
                    # Sanitize source name (remove dots, special chars) for filename safety
                    safe_source = source.lower().replace('.', '').replace(' ', '_').replace('-', '_')
                    if safe_source not in source_counts:
                        source_counts[safe_source] = 0
                    else:
                        source_counts[safe_source] += 1
                    
                    idx = source_counts[safe_source]
                    filename = f"{safe_source}_{idx}.jpg"
                    file_path = folder / filename
                    
                    # Get width/height from provider as fallback (will be replaced with actual values if file exists)
                    width = img_dict.get('width', 0)
                    height = img_dict.get('height', 0)
                    
                    if not file_path.exists():
                        success, ext = await loop.run_in_executor(None, _download_and_save_sync, url, file_path.with_suffix(''))
                        if success:
                            # CRITICAL FIX: Update file_path to reflect actual file extension
                            # The download function saves with the correct extension (e.g., .png, .jpg)
                            # but file_path was initialized with .jpg. Update it so cleanup works correctly.
                            file_path = file_path.with_suffix(ext)
                            
                            # CRITICAL FIX: Extract actual resolution from downloaded image file
                            # This ensures 100% accurate resolution information (not just provider's claimed values)
                            try:
                                def get_image_resolution(path: Path) -> tuple:
                                    """Extract actual width/height from image file"""
                                    with Image.open(path) as img:
                                        return img.size
                                
                                actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution, file_path)
                                width = actual_width
                                height = actual_height
                                logger.debug(f"Extracted actual resolution for {source} image: {width}x{height}")
                            except Exception as e:
                                logger.debug(f"Failed to extract resolution from {file_path}, using provider values: {e}")
                                # Keep provider values as fallback
                            
                            # Double-check artist hasn't changed before saving to metadata
                            # IMPORTANT: Force fresh metadata fetch (bypass cache) to detect rapid track changes
                            try:
                                # Force fresh fetch by clearing cache timestamp
                                get_current_song_meta_data._last_check_time = 0
                                current_metadata = await get_current_song_meta_data()
                                if current_metadata:
                                    current_artist = current_metadata.get("artist", "")
                                    current_artist_id = current_metadata.get("artist_id")
                                    
                                    # CRITICAL FIX: Only abort if artist NAME changed OR if we HAD an ID and it changed to a DIFFERENT ID
                                    # If original_spotify_id was None and now it's set (but artist name is same), that's fine
                                    # This prevents infinite loops when ID gets populated during fetch
                                    name_changed = current_artist != original_artist
                                    id_changed = current_artist_id != original_spotify_id
                                    
                                    # Only consider ID change a failure if we HAD an ID originally and it changed to a DIFFERENT NON-NULL ID
                                    # If original_spotify_id was None and now it's set, but artist name is same, that's fine.
                                    # FIX: Ignore if current_artist_id became None (lost connection) but name is still same
                                    # This prevents false positives when switching from Spotify (has ID) to Windows Media (no ID)
                                    id_mismatch_is_critical = (
                                        original_spotify_id is not None and 
                                        current_artist_id is not None and 
                                        current_artist_id != original_spotify_id
                                    )
                                    
                                    if name_changed or id_mismatch_is_critical:
                                        logger.info(f"Artist changed from '{original_artist}' to '{current_artist}' (ID: {original_spotify_id} -> {current_artist_id}) during download, discarding image")
                                        # Delete the file we just downloaded (now with correct extension)
                                        try:
                                            if file_path.exists():
                                                file_path.unlink()
                                        except: pass
                                        return []  # Abort
                            except Exception as e:
                                logger.debug(f"Failed to verify artist before save: {e}")
                            
                            filename = f"{safe_source}_{idx}{ext}"
                            saved_images.append({
                                "source": source,
                                "url": url,
                                "filename": filename,
                                "width": width,      # Actual resolution extracted from file
                                "height": height,    # Actual resolution extracted from file
                                "downloaded": True,
                                "added_at": datetime.utcnow().isoformat() + "Z"
                            })
                            existing_urls.add(url)  # Mark as processed
                            metadata_changed = True  # New image added, need to save
                    else:
                        # File already exists - check if it's already in saved_images and update resolution if missing
                        # First, try to find the actual file (might have different extension than .jpg)
                        actual_file_path = file_path
                        if not actual_file_path.exists():
                            # Try common extensions
                            for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                                test_path = file_path.with_suffix(ext)
                                if test_path.exists():
                                    actual_file_path = test_path
                                    break
                        
                        if actual_file_path.exists():
                            # Extract actual resolution from existing file
                            try:
                                def get_image_resolution_existing(path: Path) -> tuple:
                                    """Extract actual width/height from existing image file"""
                                    with Image.open(path) as img:
                                        return img.size
                                
                                actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution_existing, actual_file_path)
                                width = actual_width
                                height = actual_height
                                logger.debug(f"Extracted actual resolution from existing {source} image: {width}x{height}")
                            except Exception as e:
                                logger.debug(f"Failed to extract resolution from existing {actual_file_path}, using provider values: {e}")
                                # Keep provider values as fallback
                            
                            # Check if this image is already in saved_images (by URL)
                            # If yes, update width/height if missing; if no, add it
                            found_existing = False
                            for img in saved_images:
                                if img.get('url') == url:
                                    # Update width/height if missing
                                    if not img.get('width') or not img.get('height'):
                                        img['width'] = width
                                        img['height'] = height
                                        logger.debug(f"Updated resolution for existing {source} image in metadata: {width}x{height}")
                                        metadata_changed = True  # Resolution updated, need to save
                                    found_existing = True
                                    break
                            
                            if not found_existing:
                                # File exists but not in metadata - add it with actual resolution
                                actual_filename = actual_file_path.name
                                saved_images.append({
                                    "source": source,
                                    "url": url,
                                    "filename": actual_filename,
                                    "width": width,      # Actual resolution extracted from file
                                    "height": height,    # Actual resolution extracted from file
                                    "downloaded": True,
                                    "added_at": datetime.utcnow().isoformat() + "Z"
                                })
                                logger.debug(f"Added existing {source} image to metadata with resolution: {width}x{height}")
                                metadata_changed = True  # New image added to metadata, need to save
                            
                            existing_urls.add(url)  # Mark as processed
                            
                            # CRITICAL FIX: Track this as a newly downloaded file for cleanup if validation fails
                            # Only track if it's not in the original existing_filenames
                            if filename not in existing_filenames:
                                newly_downloaded_files.append((file_path, filename))
                
                # CRITICAL FIX: Final check before saving metadata - ensure artist hasn't changed
                # IMPORTANT: Force fresh metadata fetch (bypass cache) to detect rapid track changes
                try:
                    # Force fresh fetch by clearing cache timestamp
                    get_current_song_meta_data._last_check_time = 0
                    current_metadata = await get_current_song_meta_data()
                    if current_metadata:
                        current_artist = current_metadata.get("artist", "")
                        current_artist_id = current_metadata.get("artist_id")
                        
                        # CRITICAL FIX: Only abort if artist NAME changed OR if we HAD an ID and it changed to a DIFFERENT ID
                        # If original_spotify_id was None and now it's set (but artist name is same), that's fine
                        # This prevents infinite loops when ID gets populated during fetch
                        name_changed = current_artist != original_artist
                        id_changed = current_artist_id != original_spotify_id
                        
                        # Only consider ID change a failure if we HAD an ID originally and it changed to a DIFFERENT NON-NULL ID
                        # If original_spotify_id was None and now it's set, but artist name is same, that's fine.
                        # FIX: Ignore if current_artist_id became None (lost connection) but name is still same
                        # This prevents false positives when switching from Spotify (has ID) to Windows Media (no ID)
                        id_mismatch_is_critical = (
                            original_spotify_id is not None and 
                            current_artist_id is not None and 
                            current_artist_id != original_spotify_id
                        )
                        
                        if name_changed or id_mismatch_is_critical:
                            logger.info(f"Artist changed from '{original_artist}' to '{current_artist}' (ID: {original_spotify_id} -> {current_artist_id}) before metadata save, discarding")
                            
                            # CRITICAL FIX: Clean up orphaned files that were downloaded but validation failed
                            # Delete only newly downloaded files (not existing ones) to prevent data loss
                            cleanup_count = 0
                            for file_path, filename in newly_downloaded_files:
                                try:
                                    if file_path.exists():
                                        file_path.unlink()
                                        cleanup_count += 1
                                        logger.debug(f"Cleaned up orphaned file: {filename}")
                                except Exception as e:
                                    logger.debug(f"Failed to clean up orphaned file {filename}: {e}")
                            
                            if cleanup_count > 0:
                                logger.info(f"Cleaned up {cleanup_count} orphaned image file(s) after validation failure")
                            
                            return []  # Don't save metadata for wrong artist
                except Exception as e:
                    logger.debug(f"Failed to verify artist before metadata save: {e}")
                
                # OPTIMIZATION: Only save metadata if it actually changed OR if file doesn't exist
                # This prevents unnecessary disk writes when ensure_artist_image_db runs but finds no new images
                # Same optimization pattern as album art to reduce metadata.json writes
                if metadata_changed or not metadata_path.exists():
                    # Save Metadata
                    metadata = {
                        "artist": artist,
                        "type": "artist_images",
                        "last_accessed": datetime.utcnow().isoformat() + "Z",
                        "images": saved_images
                    }
                    
                    await loop.run_in_executor(None, save_album_db_metadata, folder, metadata)
                else:
                    # Commented out to reduce log spam - this is internal optimization feedback, not actionable debugging info
                    # logger.debug(f"Skipping metadata save for {artist} - no changes detected")
                    pass
                
                # Return list of LOCAL paths for the frontend
                # We return paths relative to the DB root for the API to serve
                # URL encode folder name and filename to handle special characters safely
                from urllib.parse import quote
                encoded_folder = quote(folder.name, safe='')
                result_paths = [
                    f"/api/album-art/image/{encoded_folder}/{quote(img.get('filename', ''), safe='')}" 
                    for img in saved_images 
                    if img.get('downloaded') and img.get('filename')
                ]
                
                # Update cache
                _artist_db_check_cache[artist] = (time.time(), result_paths)
                return result_paths

            except Exception as e:
                logger.error(f"Error ensuring artist image DB: {e}")
                return []
    finally:
        # Always remove from tracker, even if error occurred
        # Use composite key for removal (use original values stored at start)
        try:
            request_key = f"{original_artist}::{original_spotify_id or 'no_id'}"
            _artist_download_tracker.discard(request_key)
        except:
            # Fallback if original_artist not defined (shouldn't happen, but defensive)
            _artist_download_tracker.discard(artist)

def _perform_debug_art_update(result: Dict[str, Any]):
    """
    Helper to update current_art.jpg in a background thread.
    This function runs in a thread executor, so it must be synchronous.
    The async lock (_art_update_lock) is acquired by the caller before
    submitting this function to the executor, ensuring no concurrent writes.
    """
    try:
        # We need get_cached_art_path. It's available in module scope.
        target_path = get_cached_art_path()
        if not target_path:
            return

        source_path = result.get("album_art_path")
        source_url = result.get("album_art_url")

        # Determine what to write
        # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
        # This prevents race conditions when multiple debug art updates happen simultaneously
        temp_filename = f"{target_path.stem}_{uuid.uuid4().hex}{target_path.suffix}.tmp"
        temp_path = target_path.parent / temp_filename
        
        # 1. If we have a local path (Thumb or DB), copy it
        if source_path:
            src = Path(source_path)
            if src.exists():
                # Avoid self-copy
                if src.resolve() == target_path.resolve():
                    return
                    
                shutil.copy2(src, temp_path)
                # Use threading lock to coordinate with other threads doing file operations
                # (The async lock is already held by caller, but we need thread-level coordination too)
                with _art_update_thread_lock:
                    try:
                        os.replace(temp_path, target_path)
                    except OSError:
                        # File might be locked by server or user (e.g. open in viewer)
                        pass
                return

        # 2. If we have a remote URL (Spotify), download it
        if source_url and source_url.startswith('http'):
            try:
                # Use a short timeout for debug updates to avoid hanging
                response = requests.get(source_url, timeout=3)
                if response.status_code == 200:
                    with open(temp_path, 'wb') as f:
                        f.write(response.content)
                    # Use threading lock to coordinate with other threads doing file operations
                    # (The async lock is already held by caller, but we need thread-level coordination too)
                    with _art_update_thread_lock:
                        try:
                            os.replace(temp_path, target_path)
                        except OSError:
                            # File might be locked by server or user (e.g. open in viewer)
                            pass
            except Exception:
                pass

        # Cleanup temp if it exists
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except: pass

    except Exception:
        # Fail silently in debug update
        pass

async def _update_debug_art(result: Dict[str, Any]):
    """
    Updates current_art.jpg in the cache folder to match the current song's art.
    This restores the behavior of having a 'current_art.jpg' file for debugging
    and external tools, even though the server now uses direct paths/URLs.
    """
    if not result:
        return

    try:
        # Optimization: Only update if source changed
        current_source = result.get('album_art_path') or result.get('album_art_url')
        last_source = getattr(_update_debug_art, 'last_source', None)
        
        if current_source != last_source:
            _update_debug_art.last_source = current_source
            
            # Acquire lock before calling executor to prevent concurrent writes (prevents flickering)
            async with _art_update_lock:
                # Don't block the main thread
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _perform_debug_art_update, result)
            
    except Exception as e:
        logger.debug(f"Failed to schedule debug art update: {e}")