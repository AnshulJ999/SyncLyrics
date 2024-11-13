"""
System utilities for SyncLyrics
Handles OS-specific operations and media detection
"""

import subprocess
import platform
import os
import re
import ctypes
import logging
from time import time
from typing import Optional, Dict, Any
from pathlib import Path

from PIL import Image
import matplotlib.font_manager as fm
from colorthief import ColorThief
import io

from state_manager import get_state, set_state
from config import SYSTEM, DEBUG

# Configure logging
logger = logging.getLogger(__name__)

def _remove_text_inside_parentheses(text: str) -> str:
    """Clean up song titles"""
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", '', text)

async def _get_colors_from_art(image_data) -> tuple[str, str]:
    """Get colors from album art for background"""
    try:
        # Process image data
        stream = io.BytesIO(image_data if isinstance(image_data, bytes) else image_data)
        image = Image.open(stream)
        temp_stream = io.BytesIO()
        image.save(temp_stream, format='PNG')
        temp_stream.seek(0)
        
        # Get colors
        thief = ColorThief(temp_stream)
        colors = thief.get_palette(color_count=2, quality=1)
        return tuple('#{:02x}{:02x}{:02x}'.format(*c) for c in colors[:2])
    except:
        return "#24273a", "#363b54"  # Default colors

def _get_current_song_meta_data_gnome() -> Optional[Dict[str, Any]]:
    """Get current song info on Linux/Gnome"""
    try:
        command = "playerctl metadata --format '{{artist}}`{{title}}`{{album}}`{{position}}'"
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        
        output = process.stdout.read().decode("utf-8").split("`")
        if len(output) == 1:
            return None
            
        artist, title, album, position = output
        if not album:
            title = _remove_text_inside_parentheses(title)
            artist = ""
            
        return {"artist": artist, "title": title, "position": int(position)/1000000}
    except:
        return None

# Cache for Windows media detection
_last_check = 0
_last_data = None

async def _get_current_song_meta_data_windows() -> Optional[Dict[str, Any]]:
    """Get current song info on Windows"""
    global _last_check, _last_data
    
    # Return cached data if checked recently
    if time() - _last_check < 0.1:  # 100ms cache
        return _last_data
        
    try:
        # Get media session
        sessions = await MediaManager.request_async()
        current_session = sessions.get_current_session()
        
        if not current_session:
            return None
            
        # Return cached data if paused
        if current_session.get_playback_info().playback_status == 5:  # PAUSED
            return _last_data
            
        # Get basic metadata
        info = await current_session.try_get_media_properties_async()
        artist, title = info.artist, info.title
        
        # Clean up title if needed
        if not info.album_title:
            title = _remove_text_inside_parentheses(title)
            artist = ""
        
        # Get playback position
        timeline = current_session.get_timeline_properties()
        position = timeline.position.total_seconds()
        
        # Get Spotify info if available
        spotify_info = None
        if "Spotify" in str(current_session.source_app_user_model_id):
            try:
                track_id = None
                source = getattr(info, 'playback_source', '')
                logger.debug(f"Playback source: {source}")
                
                if 'spotify:track:' in source:
                    track_id = source.split('spotify:track:')[-1][:22]
                elif '/track/' in source:
                    track_id = source.split('/track/')[-1][:22]
                
                if track_id:
                    spotify_info = {
                        "track_id": track_id,
                        "url": f"https://open.spotify.com/track/{track_id}"
                    }
            except Exception as e:
                logger.error(f"Error getting Spotify info: {e}")
        
        # Get album art colors
        colors = ("#24273a", "#363b54")  # Default colors
        try:
            if info.thumbnail:
                stream = await info.thumbnail.open_read_async()
                buffer = bytearray()
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                colors = await _get_colors_from_art(bytes(buffer))
        except:
            pass
            
        # Create and cache result
        _last_data = {
            "artist": artist,
            "title": title,
            "position": position,
            "colors": colors,
            "spotify": spotify_info
        }
        _last_check = time()
        
        return _last_data
        
    except Exception as e:
        logger.error(f"Error getting song info: {e}")
        return None

def set_wallpaper(path: str):
    """Set system wallpaper"""
    try:
        path = os.path.abspath(path)
        if DESKTOP == "Windows":
            path = path.replace("\\", "/")
            ctypes.windll.user32.SystemParametersInfoW(0x0014, 0, path, 0x01 | 0x02)
        else:  # Gnome
            path = f"'{path}'"
            uri = f"picture-uri{'-dark' if GNOME_THEME == 'dark' else ''}"
            subprocess.Popen(f"gsettings set org.gnome.desktop.background {uri} file:{path}", shell=True)
    except Exception as e:
        logger.error(f"Error setting wallpaper: {e}")

def get_current_wallpaper() -> Image.Image:
    """Get current wallpaper"""
    try:
        if DESKTOP == "Windows":
            buffer = ctypes.create_unicode_buffer(260)
            ctypes.windll.user32.SystemParametersInfoW(0x0073, 260, buffer, 0)
            path = buffer.value
        else:  # Gnome
            uri = f"picture-uri{'-dark' if GNOME_THEME == 'dark' else ''}"
            process = subprocess.Popen(
                f"gsettings get org.gnome.desktop.background {uri}", 
                shell=True, stdout=subprocess.PIPE
            )
            process.wait()
            path = process.stdout.read().decode("utf-8").replace("file:", "").replace("'", "").strip()
        
        # Update state if path changed
        state = get_state()
        if state["currentWallpaper"] != path:
            state["currentWallpaper"] = path
            set_state(state)
            
        return Image.open(path)
    except Exception as e:
        logger.error(f"Error getting wallpaper: {e}")
        raise

def get_available_fonts() -> list[str]:
    """Get available system fonts"""
    return fm.get_font_names()

def get_font_path(font_name: str) -> str:
    """Get path for specified font"""
    return fm.findfont(font_name)

async def get_current_song_meta_data() -> Optional[Dict[str, Any]]:
    """Get current song info"""
    if DESKTOP == "Gnome":
        return _get_current_song_meta_data_gnome()
    elif DESKTOP == "Windows":
        return await _get_current_song_meta_data_windows()
    return None

# Detect desktop environment
DESKTOP = platform.system()
GNOME_THEME = None

if DESKTOP == "Linux":
    process = subprocess.Popen(
        "gsettings get org.gnome.desktop.interface color-scheme",
        shell=True, stdout=subprocess.PIPE
    )
    if process.wait() == 0:
        DESKTOP = "Gnome"
        GNOME_THEME = "dark" if "prefer-dark" in process.stdout.read().decode() else "light"
elif DESKTOP == "Darwin":
    raise NotImplementedError("MacOS not supported")
elif DESKTOP == "Windows":
    from winsdk.windows.media.control import \
        GlobalSystemMediaTransportControlsSessionManager as MediaManager

# Disable PIL debug logging if not in debug mode
if not DEBUG['enabled']:
    logging.getLogger('PIL').setLevel(logging.WARNING)