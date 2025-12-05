"""
System Utils Package - Backward Compatible Facade

This package refactors the monolithic system_utils.py into focused modules
while maintaining full backward compatibility through re-exports.

External code can continue using:
    from system_utils import get_current_song_meta_data
    from system_utils import _art_update_lock

The internal structure is:
    state.py      - Shared locks, caches, trackers
    helpers.py    - Pure utility functions
    image.py      - Image I/O and color extraction
    gnome.py      - GNOME/Linux metadata
    album_art.py  - Album art database
    artist_image.py - Artist image database
    windows.py    - Windows Media Session
    spotify.py    - Spotify metadata
    metadata.py   - Main orchestrator
"""
# Temporary minimal init - will be updated with full re-exports
from .state import *
