"""
GNOME/Linux metadata fetcher for system_utils package.

Dependencies: state, helpers
"""
from __future__ import annotations
import subprocess
from typing import Optional

from .helpers import _remove_text_inside_parentheses_and_brackets, _normalize_track_id


def _get_current_song_meta_data_gnome() -> Optional[dict]:
    """Gnome/Linux metadata fetcher with standardized output."""
    try:
        command = "playerctl metadata --format '{{artist}}`{{title}}`{{album}}`{{position}}`{{mpris:artUrl}}'"
        process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.wait()
        output = process.stdout.read().decode("utf-8").split("`")
        if len(output) < 4: 
            return None 
        
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
