import subprocess
import platform
import re
import time
import asyncio
from typing import Optional
import config
from config import DEBUG
from state_manager import get_state, set_state
from providers.spotify_api import get_shared_spotify_client
from logging_config import get_logger
from config import CACHE_DIR
import os
from functools import lru_cache
from PIL import Image
from pathlib import Path
import requests
import logging

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
STATE_LOG_INTERVAL = 100  # Log app state every 100 seconds
# Track metadata fetch calls (not the same as API calls - one fetch may use cache)
_metadata_fetch_counters = {'spotify': 0, 'windows_media': 0}
_last_windows_track_id = None  # Track ID to avoid re-reading thumbnail

# Cache for color extraction to avoid re-processing the same image
# Key: file_path, Value: (color1, color2)
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
    
    # Check cache first
    if path_str in _color_cache:
        return _color_cache[path_str]
    
    # Prevent cache from growing indefinitely - remove oldest entry if too large
    # Using pop(next(iter(...))) removes the oldest entry (first inserted)
    # This is better than clear() which would cause a performance spike
    if len(_color_cache) > _MAX_CACHE_SIZE:
        oldest_key = next(iter(_color_cache))
        _color_cache.pop(oldest_key)
        logger.debug(f"Color cache: removed oldest entry (size was {_MAX_CACHE_SIZE + 1})")
    
    # Run CPU-bound task in thread executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    final_colors = await loop.run_in_executor(None, extract_dominant_colors_sync, image_path)
    
    # Cache the result
    _color_cache[path_str] = final_colors
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
    """
    for ext in ['.jpg', '.png', '.bmp', '.gif']:
        path = CACHE_DIR / f"current_art{ext}"
        if path.exists():
            return path
    return None

def cleanup_old_art() -> None:
    """
    Removes previous album art files to prevent conflicts.
    
    When switching songs, Windows might provide a different image format (e.g., PNG instead of JPG).
    If we don't delete the old file, get_cached_art_path() might return the stale file
    because it checks extensions in order (.jpg first, then .png, etc.).
    This function ensures only the current song's art exists.
    """
    for ext in ['.jpg', '.png', '.bmp', '.gif']:
        try:
            path = CACHE_DIR / f"current_art{ext}"
            if path.exists():
                os.remove(path)
                logger.debug(f"Cleaned up old album art: {path.name}")
        except Exception as e:
            # Silently ignore errors (file might be in use or already deleted)
            logger.debug(f"Could not remove old art file {ext}: {e}")

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
        
        # Get thumbnail if available
        album_art_url = None
        try:
            # Create track ID to check if we need to re-fetch thumbnail
            global _last_windows_track_id
            current_track_id = f"{artist}:{title}"
            
            thumbnail_ref = info.thumbnail
            # Only read thumbnail if track has changed or we don't have cached art
            if thumbnail_ref and current_track_id != _last_windows_track_id:
                # Open the stream
                stream = await thumbnail_ref.open_read_async()
                if stream:
                    # Create DataReader
                    reader = DataReader(stream)
                    await reader.load_async(stream.size)
                    
                    # Read bytes directly into bytearray
                    byte_data = bytearray(stream.size)
                    reader.read_bytes(byte_data)
                    
                    # Detect extension
                    ext = get_image_extension(byte_data)
                    
                    # Clean up old art files before saving new one to prevent stale art bug
                    cleanup_old_art()
                    
                    # Save to cache
                    art_path = CACHE_DIR / f"current_art{ext}"
                    with open(art_path, "wb") as f:
                        f.write(byte_data)
                    
                    # Update last track ID
                    _last_windows_track_id = current_track_id
                    
                    # Set URL to local server route
                    # Use track ID as cache buster instead of timestamp
                    # This prevents image flickering - URL only changes when track changes
                    album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
            elif thumbnail_ref:
                # Track hasn't changed, use existing cached art
                # Use same track-based cache buster to maintain URL stability
                album_art_url = f"/cover-art?t={hash(current_track_id) % 100000}"
        except Exception as e:
            # logger.debug(f"Failed to extract Windows thumbnail: {e}")
            pass

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
        
        if album_art_url:
            try:
                # Check if we need to download new art (track changed)
                if not hasattr(_get_current_song_meta_data_spotify, '_last_spotify_art_url') or \
                   _get_current_song_meta_data_spotify._last_spotify_art_url != album_art_url:
                    
                    # Download album art in thread executor to avoid blocking event loop
                    # This prevents lyrics animation from freezing during slow network requests
                    loop = asyncio.get_running_loop()
                    response = await loop.run_in_executor(
                        None, 
                        lambda: requests.get(album_art_url, timeout=5)
                    )
                    
                    if response.status_code == 200:
                        # Save to cache
                        art_path = CACHE_DIR / "spotify_art.jpg"
                        with open(art_path, "wb") as f:
                            f.write(response.content)
                        
                        # Invalidate cache for this path because the content changed
                        # Since we reuse the same filename for all Spotify art, we need to
                        # clear the old cached colors when a new image is downloaded
                        str_path = str(art_path)
                        if str_path in _color_cache:
                            del _color_cache[str_path]
                        
                        # Extract colors (now async, so we await it)
                        colors = await extract_dominant_colors(art_path)
                        
                        # Cache the URL to avoid re-downloading
                        _get_current_song_meta_data_spotify._last_spotify_art_url = album_art_url
                        _get_current_song_meta_data_spotify._last_spotify_colors = colors
                else:
                    # Use cached colors
                    if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_colors'):
                        colors = _get_current_song_meta_data_spotify._last_spotify_colors
                        
            except Exception as e:
                logger.debug(f"Failed to extract Spotify colors: {e}")
                # Fall back to defaults
            
        # Return standardized structure with all fields
        return {
            "artist": track["artist"],
            "title": track["title"],
            "album": track.get("album"),
            "position": track["progress_ms"] / 1000,
            "duration_ms": track.get("duration_ms"),
            "colors": colors,
            "album_art_url": album_art_url,
            "is_playing": True,
            "source": "spotify"
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
                    # Steal Album Art (Prefer Spotify as it is usually higher quality)
                    if spotify_data.get("album_art_url"):
                        result["album_art_url"] = spotify_data.get("album_art_url")
                    
                    # Steal Colors from Spotify (now properly extracted!)
                    if spotify_data.get("colors"):
                        result["colors"] = spotify_data.get("colors")

                    # Enable Controls by marking as hybrid
                    # Frontend will allow controls for this source type
                    result["source"] = "spotify_hybrid"
                    
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
