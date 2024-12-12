import subprocess
import platform
# import os
import re
import logging
import time
# from PIL import Image
from typing import Optional, Dict
import asyncio

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

# Add near the top with other globals
_last_state_log_time = 0
STATE_LOG_INTERVAL = 100  # Log state every 90 seconds
_request_counters = {
    'spotify': 0,
    'windows_media': 0
}

# Initialize the SpotifyAPI client as None first
spotify_client = None

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
        # Increment counter
        if DEBUG["enabled"]:
            _request_counters['windows_media'] += 1
            
        sessions = await MediaManager.request_async()
        if not sessions:  # Add null check for sessions
            return None
            
        current_session = sessions.get_current_session()
        if not current_session:
            return None
            
        try:  # Add nested try-except for better error handling
            playback_info = current_session.get_playback_info()
            
            # Check if actually playing (not just open)
            # 4 = PLAYING, 5 = PAUSED, others = STOPPED/CLOSED
            if playback_info.playback_status != 4:  
                if DEBUG["enabled"]:
                    logger.debug(f"Media session exists but not playing (status: {playback_info.playback_status})")
                return None
                
            # Get artist and title
            info = await current_session.try_get_media_properties_async()
            if not info:  # Add null check for media properties
                return None
                
            artist, title, album = info.artist, info.title, info.album_title

            if album == "":
                title = _remove_text_inside_parentheses_and_brackets(title)
                artist = ""

            info = current_session.get_timeline_properties()
            if not info:  # Add null check for timeline properties
                return None
                
            seconds = info.position.total_seconds()
            not_update_time = time.time() - info.last_updated_time.timestamp()
            position = seconds + not_update_time
            
            metadata = {
                "artist": artist,
                "title": title,
                "position": position,
                "colors": ("#24273a", "#363b54")  # Default colors
            }
            _get_current_song_meta_data_windows.last_returned_data = metadata
            return metadata
            
        except asyncio.CancelledError:  # Handle cancellation gracefully
            logger.debug("Windows Media request cancelled")
            return None
            
    except Exception as e:
        logger.error(f"Error getting Windows media metadata: {e}")
        return None

_get_current_song_meta_data_windows.last_returned_data = None

async def _get_current_song_meta_data_spotify() -> dict[str, str | int | tuple[str, str]] | None:
    """Get current song metadata from Spotify."""
    global spotify_client # Access the global variable

    try:
        if spotify_client is None:
            spotify_client = SpotifyAPI()
            
        if not spotify_client.initialized:
            logger.error("Failed to initialize Spotify client")
            return None

        if DEBUG["enabled"]:
            _request_counters['spotify'] += 1

        track = await spotify_client.get_current_track()
        if not track:
            return None
            
        # Check if actually playing (not just open)
        if not track.get("is_playing", False):
            if DEBUG["enabled"]:
                logger.debug("Spotify session exists but not playing")
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
    current_time = time.time()
        
    # Get last check time and state
    last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
    
    # Determine if we should check now
    time_since_check = current_time - last_check
    required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
    
    if DEBUG["enabled"]:
    #  logger.debug(f"Time since last check: {time_since_check:.2f}s, Required interval: {required_interval:.2f}s")
    #  logger.debug(f"Current mode: {'active' if is_active else 'idle'}")
   
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
    # logger.debug(f"Source priorities: {[(s['name'], s.get('priority')) for s in sorted_sources]}")

    # 5. Try each source
    result = None
    primary_source_error = False
    
    # Get the primary source (first in priority)
    primary_source = sorted_sources[0] if sorted_sources else None
    
    for source in sorted_sources:
        try:
            # logger.debug(f"Attempting to get metadata from {source['name']}")
            
            if source["name"] == "windows_media" and DESKTOP == "Windows":
                try:
                    data = await _get_current_song_meta_data_windows()
                    if data:
                        result = data
                        data["source"] = source["name"]
                        break
                    elif source == primary_source:
                        # No music playing is not an error state
                        logger.debug("No music playing in Windows Media")
                except Exception as e:
                    logger.error(f"Windows Media error: {e}")
                    if source == primary_source:
                        # Only mark as error if there's an actual error
                        primary_source_error = True
                        
            elif source["name"] == "spotify":
                # Try Spotify if it's primary OR if primary source failed
                if source == primary_source or primary_source_error:
                    try:
                        data = await _get_current_song_meta_data_spotify()
                        if data:
                            result = data
                            data["source"] = source["name"]
                            break
                        elif source == primary_source:
                            # No music playing is not an error state
                            logger.debug("No music playing in Spotify")
                    except Exception as e:
                        logger.error(f"Spotify error: {e}")
                        if source == primary_source:
                            # Only mark as error if there's an actual error
                            primary_source_error = True
                            
            elif source["name"] == "gnome" and DESKTOP == "Gnome":
                if source == primary_source or primary_source_error:
                    data = _get_current_song_meta_data_gnome()
                    if data:
                        result = data
                        data["source"] = source["name"]
                        break
                    elif source == primary_source:
                        primary_source_error = True
                        
        except Exception as e:
            logger.error(f"Error with {source['name']}: {str(e)}")
            if source == primary_source:
                primary_source_error = True
            continue
    
    if result:
        # Song is playing, stay in or switch to active mode
        get_current_song_meta_data._is_active = True
        get_current_song_meta_data._last_active_time = current_time
       # if DEBUG["enabled"]:
            # logger.debug("Song detected - maintaining/switching to active mode")
    else:
        # No song playing, check if we should switch to idle mode
        time_since_active = current_time - last_active_time
        if time_since_active > IDLE_WAIT_TIME:
            get_current_song_meta_data._is_active = False
            if DEBUG["enabled"]:
                logger.debug(f"No song for {time_since_active:.1f}s - switching to idle mode")
    
    # Store last known good result for logging
    if result:
        # Song state changed - new song playing
        get_current_song_meta_data._last_song = f"{result.get('artist', 'Unknown')} - {result.get('title', 'Unknown')}"
        get_current_song_meta_data._last_source = result.get('source', 'Unknown')
        _log_app_state()  # Log when new song detected
    
    # Log if no metadata found (only when we had a song before)
    if not result and last_song:
        # Song state changed - song ended
        logger.debug("No metadata available from any source")
        _log_app_state()  # Log when song ends
    
    # Store result for rate limiting
    get_current_song_meta_data._last_result = result
    
    # Debug logging for state changes
    if DEBUG["enabled"] and hasattr(get_current_song_meta_data, '_prev_state'):
        if get_current_song_meta_data._prev_state != get_current_song_meta_data._is_active:
            # Mode state changed - active/idle transition
            logger.debug(f"Polling mode changed to: {'active' if get_current_song_meta_data._is_active else 'idle'}")
            _log_app_state()  # Log when mode changes
    get_current_song_meta_data._prev_state = get_current_song_meta_data._is_active
    
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

def _log_app_state() -> None:
    """Log key application state and details. Only logs once every STATE_LOG_INTERVAL seconds."""
    global _last_state_log_time
    current_time = time.time()
    global spotify_client # Access the global variable
    
    api_requests = {} # Initialize api_requests here

    # Check if it's time to log
    if current_time - _last_state_log_time < STATE_LOG_INTERVAL:
        return
        
    _last_state_log_time = current_time
    
    # Collect state information
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')

    # Use the last known metadata instead of making a new call
    current_song = last_song
    current_source = last_source
    
    # Update state
    state = get_state()
    state['current_song'] = current_song
    state['active_source'] = current_source
    set_state(state)

    # Add request counts if debug is enabled
    request_stats = ""
    if DEBUG["enabled"]:
        request_stats = (
            f"|- API Requests:\n"
            f"|  |- Spotify: {_request_counters['spotify']}\n"
            f"|  `- Windows Media: {_request_counters['windows_media']}\n"
        )
    
    # Create state summary using ASCII characters
    current_time_str = time.strftime("%I:%M %p - %b %d, %Y")
    state_summary = (
        "\nApplication State Summary:\n"
        f"|- Time: {current_time_str}\n"
        f"|- Mode: {'Active' if is_active else 'Idle'}\n"
        f"|- Current Song: {current_song or 'None'}\n"
        f"|- Active Source: {current_source or 'None'}\n"
        f"|- Update Interval: {ACTIVE_INTERVAL if is_active else IDLE_INTERVAL:.1f}s\n"
        f"{request_stats}"
        f"`- Desktop Environment: {DESKTOP}"
    )
    
    logger.info(state_summary)
    
    # Access spotify_client safely
    if spotify_client and spotify_client.initialized:
        spotify_requests = _request_counters['spotify']
    else:
        spotify_requests = 0

    # Add detailed Spotify stats if enabled
    if DEBUG["enabled"] and spotify_client:
        stats = spotify_client.get_request_stats()
        spotify_stats = (
            "\nSpotify API Statistics:\n"
            f"|- Total Requests: {stats['Total Requests']}\n"
            f"|- Cached Responses: {stats['Cached Responses']} ({stats['Cache Hit Rate']})\n"
            "|- API Calls:\n"
        )
        
        # Add detailed API call counts
        for endpoint, count in stats['API Calls'].items():
            spotify_stats += f"|  |- {endpoint}: {count}\n"
            
        # Add error counts
        spotify_stats += "|- Errors:\n"
        for error_type, count in stats['Errors'].items():
            spotify_stats += f"|  |- {error_type}: {count}\n"
            
        # Add cache age
        spotify_stats += f"`- Cache Age: {stats['Cache Age']}"
        
        logger.info(spotify_stats)
