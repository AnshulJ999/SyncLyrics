import subprocess
import platform
# import os
import re
# import ctypes
from time import time
# from PIL import Image

from state_manager import get_state, set_state

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

async def get_current_song_meta_data() -> dict[str, str | int | tuple[str, str]] | None:
    """
    This function returns the current song's metadata if a song is playing, otherwise it returns None.
    """
    if DESKTOP == "Gnome":
        return _get_current_song_meta_data_gnome()
    elif DESKTOP == "Windows":
        return await _get_current_song_meta_data_windows()

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