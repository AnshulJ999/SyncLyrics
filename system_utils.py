import subprocess
import platform
import re
import time
import asyncio
from typing import Optional
import config
from config import DEBUG
from state_manager import get_state, set_state
from providers.spotify_api import SpotifyAPI
from logging_config import get_logger

# Initialize Logger
logger = get_logger(__name__)

# Intervals
ACTIVE_INTERVAL = config.LYRICS["display"]["update_interval"]  # 0.1s
IDLE_INTERVAL = config.LYRICS["display"]["idle_interval"]     # 3.0s
IDLE_WAIT_TIME = config.LYRICS["display"]["idle_wait_time"]   # 2.5s

# Globals
spotify_client = None
_last_state_log_time = 0
STATE_LOG_INTERVAL = 100
_request_counters = {
    'spotify': 0,
    'windows_media': 0
}

# --- Helper Functions ---

def _remove_text_inside_parentheses_and_brackets(text: str) -> str:
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", '', text)

def _log_app_state() -> None:
    """Log key application state periodically."""
    global _last_state_log_time, spotify_client
    current_time = time.time()
    
    if current_time - _last_state_log_time < STATE_LOG_INTERVAL:
        return
        
    _last_state_log_time = current_time
    
    # Get current status
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')

    # Update state file
    state = get_state()
    state['current_song'] = last_song
    state['active_source'] = last_source
    set_state(state)

    # Log summary
    request_stats = ""
    if DEBUG["enabled"]:
        request_stats = (
            f"|- API Requests:\n"
            f"|  |- Spotify: {_request_counters['spotify']}\n"
            f"|  `- Windows Media: {_request_counters['windows_media']}\n"
        )
    
    current_time_str = time.strftime("%I:%M %p - %b %d, %Y")
    logger.info(
        f"\nApplication State Summary:\n"
        f"|- Time: {current_time_str}\n"
        f"|- Mode: {'Active' if is_active else 'Idle'}\n"
        f"|- Current Song: {last_song}\n"
        f"|- Active Source: {last_source}\n"
        f"{request_stats}"
    )

# --- Platform Specific Logic ---

DESKTOP = platform.system()
_win_media_manager = None  # Cache for Windows Media Manager

if DESKTOP == "Linux":
    # Detect Gnome theme
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
    except ImportError:
        logger.error("Winsdk not installed. Windows Media integration will not work.")
        MediaManager = None

# --- Metadata Fetching Functions ---

def _get_current_song_meta_data_gnome() -> Optional[dict]:
    """Linux Gnome metadata fetcher."""
    try:
        command = "playerctl metadata --format '{{artist}}`{{title}}`{{album}}`{{position}}'"
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        output = process.stdout.read().decode("utf-8").split("`")
        if len(output) < 4: return None 
        
        artist, title, album, position = output
        if not album: 
            title = _remove_text_inside_parentheses_and_brackets(title)
            artist = "" 

        return {
            "artist": artist.strip(), 
            "title": title.strip(), 
            "position": int(position)/1000000,
            "colors": ("#24273a", "#363b54")
        }
    except Exception:
        return None

async def _get_current_song_meta_data_windows() -> Optional[dict]:
    """Windows Media metadata fetcher (Optimized)."""
    global _win_media_manager
    
    if not MediaManager: return None

    try:
        if DEBUG["enabled"]: _request_counters['windows_media'] += 1
            
        # Get Manager (Cached)
        if _win_media_manager is None:
            _win_media_manager = await MediaManager.request_async()
            
        if not _win_media_manager: return None

        # Get Session
        current_session = _win_media_manager.get_current_session()
        if not current_session: return None
            
        # Check Playback Status (4=Playing, 5=Paused)
        playback_info = current_session.get_playback_info()
        if not playback_info or playback_info.playback_status != 4:
            return None
            
        # Get Song Info
        info = await current_session.try_get_media_properties_async()
        if not info: return None
            
        artist = info.artist
        title = info.title
        album = info.album_title

        # Cleanup Title
        if not album:
            title = _remove_text_inside_parentheses_and_brackets(title)
            artist = ""

        # Calculate Position
        timeline = current_session.get_timeline_properties()
        if not timeline: return None
            
        # Calculate live position based on last update time
        seconds = timeline.position.total_seconds()
        elapsed = time.time() - timeline.last_updated_time.timestamp()
        position = seconds + elapsed
        
        return {
            "artist": artist,
            "title": title,
            "position": position,
            "colors": ("#24273a", "#363b54")
        }
            
    except Exception as e:
        logger.error(f"Windows Media Error: {e}")
        # If manager breaks, force reload next time
        _win_media_manager = None
        return None

async def _get_current_song_meta_data_spotify() -> Optional[dict]:
    """Spotify API metadata fetcher."""
    global spotify_client

    try:
        if spotify_client is None:
            spotify_client = SpotifyAPI()
            
        if not spotify_client.initialized:
            return None

        if DEBUG["enabled"]: _request_counters['spotify'] += 1

        track = await spotify_client.get_current_track()
        
        # Ensure track exists and is playing
        if not track or not track.get("is_playing", False):
            return None
            
        return {
            "artist": track["artist"],
            "title": track["title"],
            "position": track["progress_ms"] / 1000,
            "colors": track.get("colors", ("#24273a", "#363b54")),
            "source": "spotify"
        }
    except Exception as e:
        logger.error(f"Spotify API Error: {e}")
        return None

# --- Main Function ---

async def get_current_song_meta_data() -> Optional[dict]:
    """Main orchestrator to get song data from configured sources."""
    current_time = time.time()
        
    # Rate Limiting Logic
    last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
    
    required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
    if (current_time - last_check) < required_interval:
        return getattr(get_current_song_meta_data, '_last_result', None)
    
    get_current_song_meta_data._last_check_time = current_time
    
    # Get Sources from Config
    sources = config.MEDIA_SOURCE.get("sources", [])
    sorted_sources = [s for s in sorted(sources, key=lambda x: int(x.get("priority", 999))) 
                     if s.get("enabled", False)]

    result = None
    
    # Try sources in order
    for source in sorted_sources:
        try:
            if source["name"] == "windows_media" and DESKTOP == "Windows":
                result = await _get_current_song_meta_data_windows()
            elif source["name"] == "spotify":
                result = await _get_current_song_meta_data_spotify()
            elif source["name"] == "gnome" and DESKTOP == "Gnome":
                result = _get_current_song_meta_data_gnome()
                
            if result:
                result["source"] = source["name"]
                break
        except Exception:
            continue
    
    # State Management (Active vs Idle)
    if result:
        get_current_song_meta_data._is_active = True
        get_current_song_meta_data._last_active_time = current_time
        
        # Log change
        last_song = getattr(get_current_song_meta_data, '_last_song', None)
        current_song_name = f"{result.get('artist')} - {result.get('title')}"
        
        if last_song != current_song_name:
            get_current_song_meta_data._last_song = current_song_name
            get_current_song_meta_data._last_source = result.get('source')
            _log_app_state()
    else:
        # Go idle if no song for a while
        if (current_time - last_active_time) > IDLE_WAIT_TIME:
            get_current_song_meta_data._is_active = False

    get_current_song_meta_data._last_result = result
    _log_app_state()
    
    return result
