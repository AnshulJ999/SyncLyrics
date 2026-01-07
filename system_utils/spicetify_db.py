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

# Log throttling to avoid spam (only log first access per track per 5s)
_last_logged_track: str = ""
_last_log_time: float = 0


# =============================================================================
# DATA VALIDATION HELPERS
# =============================================================================

def _has_valid_colors(colors: dict) -> bool:
    """
    Check if colors dict contains actual hex color values.
    
    Returns False for None, empty dict, or dict with only null/empty values.
    This prevents empty color data from overwriting previously extracted colors.
    
    Args:
        colors: Color palette dict (e.g., {'VIBRANT': '#ff5500', ...})
        
    Returns:
        True if at least one valid hex color exists
    """
    if not colors or not isinstance(colors, dict):
        return False
    return any(
        isinstance(v, str) and v.startswith('#') and len(v) >= 4
        for v in colors.values()
    )


def _merge_metadata(existing: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Merge track metadata field-by-field, preserving non-null values.
    
    Strategy: Start with existing data, overlay new non-null values.
    This ensures we never lose data - only add or update fields.
    
    Args:
        existing: Previously saved track metadata (may be None)
        new: Incoming track metadata (may be None)
        
    Returns:
        Merged metadata dict, or None if both inputs are None
    """
    if not existing and not new:
        return None
    
    # Start with existing data as base
    merged = dict(existing) if existing else {}
    
    # Overlay new values (but only if they're not None/empty)
    if new:
        for key, value in new.items():
            # Only update if new value is meaningful
            # Handles None, empty string, empty list
            if value is not None and value != '' and value != []:
                merged[key] = value
            elif key not in merged:
                # Key doesn't exist in merged, add even if None (for completeness)
                merged[key] = value
    
    return merged if merged else None


def _get_db_path(artist: str, title: str) -> Optional[str]:
    """
    Generate safe filename for spicetify data.
    
    Preserves original case (like Lyrics DB). Windows filesystem handles
    case-insensitive matching automatically.
    
    Args:
        artist: Artist name
        title: Track title
        
    Returns:
        Full path to JSON file, or None if invalid
    """
    try:
        # Remove illegal characters for filenames (preserve original case like lyrics.py)
        safe_artist = "".join([c for c in artist if c.isalnum() or c in " -_"]).strip()
        safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
        
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
        
        # Throttle logging - only log first access per track per 5s
        global _last_logged_track, _last_log_time
        import time
        track_key = f"{artist} - {title}"
        current_time = time.time()
        if track_key != _last_logged_track or (current_time - _last_log_time) > 5:
            logger.debug(f"Loaded Spicetify data from cache: {track_key}")
            _last_logged_track = track_key
            _last_log_time = current_time
        
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
    track_metadata: Optional[Dict[str, Any]] = None,
    # Extended metadata
    canvas: Optional[Dict[str, Any]] = None,
    player_state: Optional[Dict[str, Any]] = None,
    playback_quality: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    collection: Optional[Dict[str, Any]] = None,
    raw_metadata: Optional[Dict[str, Any]] = None,
    context_metadata: Optional[Dict[str, Any]] = None,
    page_metadata: Optional[Dict[str, Any]] = None,
    artist_visuals: Optional[Dict[str, Any]] = None  # GraphQL header/gallery images
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
        canvas: Canvas data (animated video loops)
        player_state: Player state (shuffle, repeat, volume, etc.)
        playback_quality: Playback quality info (bitrate, hifi, etc.)
        context: Context info (playlist/album/radio)
        collection: Collection status (in library, can add, etc.)
        raw_metadata: Raw Spicetify metadata object
        context_metadata: Context metadata from Spicetify
        page_metadata: Page metadata from Spicetify
        artist_visuals: GraphQL artist header/gallery images from Spicetify
        
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
        
        # Merge audio analysis (preserve existing if new has no actual data)
        # Check for actual segments, not just a dict with empty arrays
        new_has_data = audio_analysis and audio_analysis.get('segments')
        existing_has_data = existing.get('audio_analysis', {}).get('segments')
        
        if new_has_data:
            # New data has actual segments - use it
            data["audio_analysis"] = audio_analysis
        elif existing_has_data:
            # Preserve existing good data (don't overwrite with empty)
            data["audio_analysis"] = existing["audio_analysis"]
            logger.info(f"Preserved existing audio analysis for: {artist} - {title} (new data was empty)")
        elif audio_analysis is not None:
            # Both are empty, use new (for consistency)
            data["audio_analysis"] = audio_analysis
        
        # Merge colors (only update if new has actual hex values)
        new_has_colors = _has_valid_colors(colors)
        existing_has_colors = _has_valid_colors(existing.get("colors"))
        
        if new_has_colors:
            data["colors"] = colors
        elif existing_has_colors:
            data["colors"] = existing["colors"]
            if colors is not None:
                # New data was sent but was empty/invalid - log preservation
                logger.debug(f"Preserved existing colors for: {artist} - {title} (new colors were empty)")
        elif colors is not None:
            # Neither has valid colors, but new was explicitly sent - save it
            data["colors"] = colors
        
        # Merge track metadata (field-by-field merge to preserve data)
        merged_metadata = _merge_metadata(
            existing.get("track_metadata"),
            track_metadata
        )
        if merged_metadata:
            data["track_metadata"] = merged_metadata
        
        # === EXTENDED METADATA ===
        # Canvas (animated video loops) - only save if has actual URL
        if canvas and canvas.get('url'):
            data["canvas"] = canvas
        elif "canvas" in existing and existing["canvas"].get('url'):
            data["canvas"] = existing["canvas"]
        
        # Player state (shuffle, repeat, volume, etc.) - always use latest
        if player_state is not None:
            data["player_state"] = player_state
        
        # Playback quality - always use latest
        if playback_quality is not None:
            data["playback_quality"] = playback_quality
        
        # Context (playlist/album info)
        if context is not None:
            data["context"] = context
        elif "context" in existing:
            data["context"] = existing["context"]
        
        # Collection status - always use latest
        if collection is not None:
            data["collection"] = collection
        
        # Raw metadata - always use latest (for future-proofing)
        if raw_metadata is not None:
            data["raw_metadata"] = raw_metadata
        
        # Context metadata
        if context_metadata is not None:
            data["context_metadata"] = context_metadata
        elif "context_metadata" in existing:
            data["context_metadata"] = existing["context_metadata"]
        
        # Page metadata
        if page_metadata is not None:
            data["page_metadata"] = page_metadata
        elif "page_metadata" in existing:
            data["page_metadata"] = existing["page_metadata"]
        
        # Artist visuals (GraphQL header/gallery) - preserve existing if new is empty
        if artist_visuals and (artist_visuals.get('header_image') or artist_visuals.get('gallery')):
            data["artist_visuals"] = artist_visuals
        elif "artist_visuals" in existing and existing["artist_visuals"]:
            data["artist_visuals"] = existing["artist_visuals"]
        
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
            logger.info(f"Saved Spicetify data to cache: {artist} - {title}")
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
