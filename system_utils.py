import subprocess
import platform
# import os
import re
import logging
from time import time
# from PIL import Image

import config
from state_manager import get_state, set_state
from providers.spotify_api import SpotifyAPI
from logging_config import get_logger

# logger = logging.getLogger(__name__)
logger = get_logger(__name__)

# Initialize Spotify client once
spotify_client = SpotifyAPI()

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
    """
    PLABACK_PAUSED = 5

    sessions = await MediaManager.request_async()
    current_session = sessions.get_current_session()
    if current_session: 
        if current_session.get_playback_info().playback_status == PLABACK_PAUSED:
            return _get_current_song_meta_data_windows.last_returned_data
        
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

_get_current_song_meta_data_windows.last_returned_data = None

async def _get_current_song_meta_data_spotify() -> dict[str, str | int | tuple[str, str]] | None:
    """Get current song metadata from Spotify in the expected format"""
    try:
        track = spotify_client.get_current_track()
        if not track:
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
                
                return data
            else:
                logger.debug(f"No data from {source['name']}")
                
        except Exception as e:
            logger.error(f"Error with {source['name']}: {str(e)}")
            continue
    
    # 8. No data available
    if last_song:  # Only log when we had a song before
        logger.debug("No metadata available from any source")
    return None

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