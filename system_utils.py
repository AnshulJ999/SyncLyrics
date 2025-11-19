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
ACTIVE_INTERVAL = config.LYRICS["display"]["update_interval"]
IDLE_INTERVAL = config.LYRICS["display"]["idle_interval"]
IDLE_WAIT_TIME = config.LYRICS["display"]["idle_wait_time"]

# Globals
spotify_client = None
_last_state_log_time = 0
STATE_LOG_INTERVAL = 100
_request_counters = {'spotify': 0, 'windows_media': 0}

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
    
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')

    # Update state file
    state = get_state()
    state['current_song'] = last_song
    state['active_source'] = last_source
    set_state(state)

    request_stats = ""
    if DEBUG["enabled"]:
        request_stats = (
            f"|- API Requests:\n"
            f"|  |- Spotify: {_request_counters['spotify']}\n"
            f"|  `- Windows Media: {_request_counters['windows_media']}\n"
        )
    
    current_time_str = time.strftime("%I:%M %p - %b %d, %Y")
    
    # Base state summary
    state_summary = (
        f"\nApplication State Summary:\n"
        f"|- Time: {current_time_str}\n"
        f"|- Mode: {'Active' if is_active else 'Idle'}\n"
        f"|- Current Song: {last_song}\n"
        f"|- Active Source: {last_source}\n"
        f"{request_stats}"
    )
    logger.info(state_summary)

    # --- RESTORED: Detailed Spotify Stats ---
    if DEBUG["enabled"] and spotify_client and spotify_client.initialized:
        try:
            stats = spotify_client.get_request_stats()
            spotify_stats = (
                "\nSpotify API Statistics:\n"
                f"|- Total Requests: {stats['Total Requests']}\n"
                f"|- Cached Responses: {stats['Cached Responses']} ({stats['Cache Hit Rate']})\n"
                "|- API Calls:\n"
            )
            
            for endpoint, count in stats['API Calls'].items():
                spotify_stats += f"|  |- {endpoint}: {count}\n"
                
            spotify_stats += "|- Errors:\n"
            for error_type, count in stats['Errors'].items():
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

async def _get_current_song_meta_data_windows() -> Optional[dict]:
    """Windows Media metadata fetcher with standardized output."""
    global _win_media_manager
    if not MediaManager: return None

    try:
        if DEBUG["enabled"]: _request_counters['windows_media'] += 1
            
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
        elapsed = time.time() - timeline.last_updated_time.timestamp()
        position = seconds + elapsed
        
        # Get duration if available
        duration_ms = None
        try:
            duration_ms = int(timeline.end_time.total_seconds() * 1000)
        except:
            pass
        
        return {
            "artist": artist,
            "title": title,
            "album": album if album else None,
            "position": position,
            "duration_ms": duration_ms,
            "colors": ("#24273a", "#363b54"),
            "album_art_url": None,  # Windows thumbnail retrieval is unstable in Python
            "is_playing": True,
            "source": "windows_media"
        }
            
    except Exception as e:
        logger.error(f"Windows Media Error: {e}")
        _win_media_manager = None
        return None

async def _get_current_song_meta_data_spotify() -> Optional[dict]:
    """Spotify API metadata fetcher with standardized output."""
    global spotify_client
    try:
        if spotify_client is None:
            spotify_client = SpotifyAPI()
            
        if not spotify_client.initialized:
            return None

        if DEBUG["enabled"]: _request_counters['spotify'] += 1

        track = await spotify_client.get_current_track()
        if not track or not track.get("is_playing", False):
            return None
            
        # Return standardized structure with all fields
        return {
            "artist": track["artist"],
            "title": track["title"],
            "album": track.get("album"),
            "position": track["progress_ms"] / 1000,
            "duration_ms": track.get("duration_ms"),
            "colors": track.get("colors", ("#24273a", "#363b54")),
            "album_art_url": track.get("album_art"),
            "is_playing": True,
            "source": "spotify"
        }
    except Exception as e:
        logger.error(f"Spotify API Error: {e}")
        return None

# --- Main Function ---

async def get_current_song_meta_data() -> Optional[dict]:
    """Main orchestrator to get song data from configured sources with hybrid enrichment."""
    current_time = time.time()
    last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
    
    required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
    if (current_time - last_check) < required_interval:
        return getattr(get_current_song_meta_data, '_last_result', None)
    
    get_current_song_meta_data._last_check_time = current_time
    
    sources = config.MEDIA_SOURCE.get("sources", [])
    sorted_sources = [s for s in sorted(sources, key=lambda x: int(x.get("priority", 999))) 
                     if s.get("enabled", False)]

    result = None
    
    # 1. Fetch Primary Data from sorted sources
    for source in sorted_sources:
        try:
            if source["name"] == "windows_media" and DESKTOP == "Windows":
                result = await _get_current_song_meta_data_windows()
            elif source["name"] == "spotify":
                result = await _get_current_song_meta_data_spotify()
            elif source["name"] == "gnome" and DESKTOP == "Gnome":
                result = _get_current_song_meta_data_gnome()
                
            if result:
                # Source is already set in the function
                break
        except Exception:
            continue
    
    # 2. HYBRID ENRICHMENT - Merge Spotify data if primary source lacks album art/controls
    if result and result.get("source") == "windows_media":
        try:
            spotify_data = await _get_current_song_meta_data_spotify()
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
                    # Steal Album Art if Windows doesn't have it
                    if not result.get("album_art_url") and spotify_data.get("album_art_url"):
                        result["album_art_url"] = spotify_data.get("album_art_url")
                    
                    # Steal Colors if Windows has default colors
                    if result.get("colors") == ("#24273a", "#363b54"):
                        result["colors"] = spotify_data.get("colors", ("#24273a", "#363b54"))

                    # Enable Controls by marking as hybrid
                    # Frontend will allow controls for this source type
                    result["source"] = "spotify_hybrid"
                    
                    if DEBUG["enabled"]:
                        logger.info(f"Hybrid mode: Enriched Windows Media data with Spotify album art and controls")
        except Exception as e:
            logger.error(f"Hybrid enrichment failed: {e}")
    
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
