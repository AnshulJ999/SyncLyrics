"""
Spicetify Database - Cache audio analysis and colors from Spicetify.

Stores per-song JSON files for quick loading without re-fetching.
Enables waveform/spectrum visualizers to work for previously-played songs
and across application restarts.

Pattern follows lyrics.py for consistency:
- Atomic writes (tempfile + os.replace)
- Async lock for concurrent access protection
- Safe filenames (strip illegal chars)
- Feature flag control

Level 0 - No internal imports (self-contained)
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from config import SPICETIFY_DB_DIR, FEATURES
from logging_config import get_logger

logger = get_logger(__name__)

# Async lock protects read-modify-write cycles
_db_lock = asyncio.Lock()


def _get_db_path(artist: str, title: str) -> Optional[str]:
    """
    Generate safe filename for spicetify data.
    
    Uses lowercase for case-insensitive matching:
    - "ERRA" and "Erra" both resolve to "erra - shadow autonomous.json"
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        Full path to JSON file, or None if invalid
    """
    try:
        # Lowercase for case-insensitive matching, then remove illegal characters
        safe_artist = "".join([c for c in artist.lower() if c.isalnum() or c in " -_"]).strip()
        safe_title = "".join([c for c in title.lower() if c.isalnum() or c in " -_"]).strip()
        
        if not safe_artist or not safe_title:
            return None
            
        filename = f"{safe_artist} - {safe_title}.json"
        return str(SPICETIFY_DB_DIR / filename)
    except Exception:
        return None


def load_from_db(artist: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Load cached Spicetify data (audio analysis, colors) for a song.
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        Cached data dict or None if not found/disabled
    """
    if not FEATURES.get("spicetify_database", True):
        return None
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return None
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.debug(f"Loaded Spicetify data from cache: {artist} - {title}")
        return data
    except Exception as e:
        logger.debug(f"Failed to load Spicetify cache: {e}")
        return None


def has_cached(artist: str, title: str) -> bool:
    """
    Check if song has cached Spicetify data.
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        True if cache file exists
    """
    if not FEATURES.get("spicetify_database", True):
        return False
    db_path = _get_db_path(artist, title)
    return db_path is not None and os.path.exists(db_path)


def has_audio_analysis_cached(artist: str, title: str) -> bool:
    """
    Check if song has audio analysis data cached.
    
    More specific than has_cached() - verifies audio_analysis field exists.
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        True if audio_analysis data is cached
    """
    if not FEATURES.get("spicetify_database", True):
        return False
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path):
        return False
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('audio_analysis') is not None
    except Exception:
        return False


async def save_to_db(
    artist: str,
    title: str,
    track_uri: str,
    audio_analysis: Optional[Dict[str, Any]] = None,
    colors: Optional[Dict[str, Any]] = None,
    track_metadata: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Save Spicetify data to disk with atomic writes.
    
    Uses merge mode: updates existing files without overwriting other fields.
    File I/O runs in thread pool to avoid blocking the event loop.
    
    Args:
        artist: Artist name
        title: Track title  
        track_uri: Spotify track URI (e.g., spotify:track:xxx)
        audio_analysis: Audio analysis data (tempo, segments, beats, etc.)
        colors: Extracted color palette from album art
        track_metadata: Basic track info (name, artist, album, etc.)
        
    Returns:
        True if save successful
    """
    if not FEATURES.get("spicetify_database", True):
        return False
    
    db_path = _get_db_path(artist, title)
    if not db_path:
        return False
    
    # Prepare data outside the blocking section
    now_iso = datetime.utcnow().isoformat() + "Z"
    
    def _do_file_io():
        """Blocking file I/O - runs in thread pool."""
        # Load existing data (merge mode)
        existing = {}
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                pass  # Start fresh if corrupt
        
        # Build/update data structure
        data = {
            "artist": artist,
            "title": title,
            "track_uri": track_uri,
            "saved_at": existing.get("saved_at", now_iso),
            "last_updated": now_iso,
        }
        
        # Merge audio analysis (preserve existing if new is None)
        if audio_analysis is not None:
            data["audio_analysis"] = audio_analysis
        elif "audio_analysis" in existing:
            data["audio_analysis"] = existing["audio_analysis"]
        
        # Merge colors
        if colors is not None:
            data["colors"] = colors
        elif "colors" in existing:
            data["colors"] = existing["colors"]
        
        # Merge track metadata
        if track_metadata is not None:
            data["track_metadata"] = track_metadata
        elif "track_metadata" in existing:
            data["track_metadata"] = existing["track_metadata"]
        
        # Atomic write pattern (same as lyrics.py):
        # 1. Write to temp file in same directory
        # 2. Atomic replace (os.replace is atomic on all platforms)
        dir_path = os.path.dirname(db_path)
        fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, db_path)
        except Exception as write_err:
            # Cleanup temp file on error
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            raise write_err
        
        return True
    
    async with _db_lock:
        try:
            # Run blocking file I/O in thread pool
            result = await asyncio.to_thread(_do_file_io)
            logger.debug(f"Saved Spicetify data to cache: {artist} - {title}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to save Spicetify cache: {e}")
            return False


def get_cached_colors(artist: str, title: str) -> Optional[Dict[str, str]]:
    """
    Get cached colors for a song.
    
    Convenience function for color extraction fallback.
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        Color palette dict or None
    """
    data = load_from_db(artist, title)
    if data:
        return data.get("colors")
    return None
