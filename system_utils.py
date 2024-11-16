import subprocess
import platform
# import os
import re
import logging
from time import time
# from PIL import Image

import config
from config import DEBUG
from state_manager import get_state, set_state
from providers.spotify_api import SpotifyAPI
from logging_config import get_logger

ACTIVE_INTERVAL = config.LYRICS["display"]["update_interval"]  # 0.1s
IDLE_INTERVAL = config.LYRICS["display"]["idle_interval"]     # 3.0s
IDLE_WAIT_TIME = config.LYRICS["display"]["idle_wait_time"]   # 2.5s

# logger = logging.getLogger(__name__)
logger = get_logger(__name__)

# Initialize Spotify client once
spotify_client = SpotifyAPI()

# Add near the top with other globals
_last_state_log_time = 0
STATE_LOG_INTERVAL = 240  # Log state every 240 seconds

def _log_app_state() -> None:
    """Log key application state and details. Only logs once every STATE_LOG_INTERVAL seconds."""
    global _last_state_log_time
    current_time = time()
    
    # Check if it's time to log
    if current_time - _last_state_log_time < STATE_LOG_INTERVAL:
        return
        
    _last_state_log_time = current_time
    
    # Collect state information
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')
    
    # Create state summary using ASCII characters
    state_summary = (
        "\nApplication State Summary:\n"
        f"|- Mode: {'Active' if is_active else 'Idle'}\n"
        f"|- Current Song: {last_song}\n"
        f"|- Active Source: {last_source}\n"
        f"|- Update Interval: {ACTIVE_INTERVAL if is_active else IDLE_INTERVAL:.1f}s\n"
        f"`- Desktop Environment: {DESKTOP}"
    )
    
    logger.info(state_summary)

def _remove_text_inside_parentheses_and_brackets(text: str) -> str:
    """
    This function removes text inside parentheses and brackets from the given text.
    """
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", '', text)

def _get_current_song_meta_data_gnome() -> dict[str, str | int] | None:
    """
    This function returns the current song's metadata if a song is playing, otherwise it returns None.
    It only works on Linux.
    """
    command = "playerctl metadata --format '{{artist}}`{{title}}`{{album}}`{{position}}'"
    process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    process.wait()
    output = process.stdout.read().decode("utf-8").split("`")
    if len(output) == 1: return None # No song is playing
    artist, title, album, position = output
    if album == "": 
        title = _remove_text_inside_parentheses_and_brackets(title)
        artist = "" 

    return {"artist": artist, "title": title, "position": int(position)/1000000}

async def _get_current_song_meta_data_windows() -> dict[str, str | int | tuple[str, str]] | None:
    """
    This function returns the current song's metadata if a song is playing, otherwise it returns None.
    Checks for actual playback status, not just if media session exists.
    """
    try:
        sessions = await MediaManager.request_async()
        current_session = sessions.get_current_session()
        
        if not current_session:
            return None
            
        playback_info = current_session.get_playback_info()
        
        # Check if actually playing (not just open)
        # 4 = PLAYING, 5 = PAUSED, others = STOPPED/CLOSED
        if playback_info.playback_status != 4:  
            if DEBUG["enabled"]:
                logger.debug(f"Media session exists but not playing (status: {playback_info.playback_status})")
            return None
        
        # Get artist and title
        info = await current_session.try_get_media_properties_async()
        artist, title, album = info.artist, info.title, info.album_title

        if album == "":
            title = _remove_text_inside_parentheses_and_brackets(title)
            artist = ""

        info = current_session.get_timeline_properties()
        seconds = info.position.total_seconds()
        not_update_time = time() - info.last_updated_time.timestamp()
        position = seconds + not_update_time
        
        metadata = {
            "artist": artist,
            "title": title,
            "position": position,
            "colors": ("#24273a", "#363b54")  # Default colors
        }
        _get_current_song_meta_data_windows.last_returned_data = metadata
        return metadata
            
    except Exception as e:
        logger.error(f"Error getting Windows media metadata: {e}")
        return None

_get_current_song_meta_data_windows.last_returned_data = None

async def _get_current_song_meta_data_spotify() -> dict[str, str | int | tuple[str, str]] | None:
    """Get current song metadata from Spotify in the expected format"""
    try:
        track = spotify_client.get_current_track()
        if not track:
            return None
            
        # Check if actually playing (not just open)
        if not track.get("is_playing", False):
            if DEBUG["enabled"]:
                logger.info("Spotify session exists but not playing")
            return None
            
        return {
            "artist": track["artist"],
            "title": track["title"],
            "position": track["progress_ms"] / 1000,  # Convert ms to seconds
            "colors": ("#24273a", "#363b54")  # Using default colors for now
        }
    except Exception as e:
        logger.error(f"Error getting Spotify metadata: {e}")
        return None

async def get_current_song_meta_data() -> dict[str, str | int | tuple[str, str]] | None:
    """Get song metadata from configured sources in priority order"""
    current_time = time()
    
    # Get last check time and state
    last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
    
    # Determine if we should check now
    time_since_check = current_time - last_check
    required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
    
    if DEBUG["enabled"]:
        logger.debug(f"Time since last check: {time_since_check:.2f}s, Required interval: {required_interval:.2f}s")
        logger.debug(f"Current mode: {'active' if is_active else 'idle'}")
    
    if time_since_check < required_interval:
        return getattr(get_current_song_meta_data, '_last_result', None)
    
    # Update last check time
    get_current_song_meta_data._last_check_time = current_time
    
    # 1. Get configuration - Use config.MEDIA_SOURCE instead of state
    sources = config.MEDIA_SOURCE.get("sources", [])
    
    # 2. Track state and last song for logging
    last_song = getattr(get_current_song_meta_data, '_last_song', None)
    last_source = getattr(get_current_song_meta_data, '_last_source', None)
    
    # 3. Handle default behavior (no config)
    if not sources:
        if DESKTOP == "Gnome":
            logger.info("Using default source: Gnome")
            return _get_current_song_meta_data_gnome()
        elif DESKTOP == "Windows":
            logger.info("Using default source: Windows Media")
            return await _get_current_song_meta_data_windows()
    
    # Sort sources by priority and filter enabled ones (lower number = higher priority)
    sorted_sources = [s for s in sorted(sources, key=lambda x: int(x.get("priority", 999))) 
                     if s.get("enabled", False)]
    
    # Log sources order only when it changes
    sources_order = [s['name'] for s in sorted_sources]
    if sources_order != getattr(get_current_song_meta_data, '_last_sources_order', None):
        logger.info(f"Media sources (in priority order): {sources_order}")
        get_current_song_meta_data._last_sources_order = sources_order

    # Debug log the priority order
    logger.debug(f"Source priorities: {[(s['name'], s.get('priority')) for s in sorted_sources]}")

    # 5. Try each source
    result = None
    for source in sorted_sources:
        try:
            logger.debug(f"Attempting to get metadata from {source['name']}")
            # 6. Get data from appropriate source
            data = None
            if source["name"] == "spotify":
                data = await _get_current_song_meta_data_spotify()
            elif source["name"] == "windows_media" and DESKTOP == "Windows":
                data = await _get_current_song_meta_data_windows()
            elif source["name"] == "gnome" and DESKTOP == "Gnome":
                data = _get_current_song_meta_data_gnome()
            else:
                continue
                
            if data:
                data["source"] = source["name"]
                current_song = f"{data['artist']} - {data['title']}"
                
                # Log only on changes
                if current_song != last_song or source["name"] != last_source:
                    logger.info(f"Now playing: {current_song} (Source: {source['name']})")
                    get_current_song_meta_data._last_song = current_song
                
                if source["name"] != last_source:
                    logger.info(f"Active source: {source['name']}")
                    get_current_song_meta_data._last_source = source["name"]
                
                result = data
                break
            else:
                logger.info(f"No data from {source['name']}")
                
        except Exception as e:
            logger.error(f"Error with {source['name']}: {str(e)}")
            continue
    
    if result:
        # Song is playing, stay in or switch to active mode
        get_current_song_meta_data._is_active = True
        get_current_song_meta_data._last_active_time = current_time
        if DEBUG["enabled"]:
            logger.debug("Song detected - maintaining/switching to active mode")
    else:
        # No song playing, check if we should switch to idle mode
        time_since_active = current_time - last_active_time
        if time_since_active > IDLE_WAIT_TIME:
            get_current_song_meta_data._is_active = False
            if DEBUG["enabled"]:
                logger.debug(f"No song for {time_since_active:.1f}s - switching to idle mode")
    
    # Store result for rate limiting
    get_current_song_meta_data._last_result = result
    
    # Debug logging for state changes
    if DEBUG["enabled"] and hasattr(get_current_song_meta_data, '_prev_state'):
        if get_current_song_meta_data._prev_state != get_current_song_meta_data._is_active:
            logger.debug(f"Polling mode changed to: {'active' if get_current_song_meta_data._is_active else 'idle'}")
    get_current_song_meta_data._prev_state = get_current_song_meta_data._is_active
    
    # Log if no metadata found (only when we had a song before)
    if not result and last_song:
        logger.debug("No metadata available from any source")
    
    # Log application state
    _log_app_state()
    
    return result

# Find out which desktop environment is being used
DESKTOP = platform.system()
if DESKTOP == "Linux":
    process = subprocess.Popen("gsettings get org.gnome.desktop.interface color-scheme",
        shell=True, stdout=subprocess.PIPE)
    process.wait()
    if process.returncode == 0:
        DESKTOP = "Gnome"
        if process.stdout.read().decode("utf-8").replace("\n", "") == "'prefer-dark'":
            GNOME_THEME = "dark"
        else:
            GNOME_THEME = "light"
elif DESKTOP == "Darwin":
    raise NotImplementedError("MacOS not supported")

if DESKTOP == "Windows":
    from winsdk.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as MediaManager