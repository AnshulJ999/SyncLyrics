"""
Spicetify Bridge - Metadata from Spotify Desktop via Spicetify extension.

This module handles WebSocket connections from the Spicetify browser extension
and provides metadata to the main metadata orchestrator.

Dependencies: state, helpers
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Optional, Dict, Any
from quart import websocket
from . import state
from .helpers import _normalize_track_id
from logging_config import get_logger
from providers.spotify_api import enhance_spotify_image_url_async

logger = get_logger(__name__)


def _convert_spotify_image_uri(url: str) -> str:
    """
    Convert spotify:image:xxx URI to HTTPS URL.
    
    Spicetify sometimes sends spotify:image:xxx format instead of HTTPS URLs.
    Format: spotify:image:ab67616d00001e02xxx -> https://i.scdn.co/image/ab67616d00001e02xxx
    
    This is defense-in-depth (bridge also converts, but this catches edge cases).
    """
    if url and url.startswith('spotify:image:'):
        image_id = url.replace('spotify:image:', '')
        return f'https://i.scdn.co/image/{image_id}'
    return url

# =============================================================================
# SHARED STATE
# =============================================================================

_spicetify_state: Dict[str, Any] = {
    'connected': False,
    'last_update': 0,           # Server timestamp (ms)
    'position_ms': 0,
    'duration_ms': 0,
    'is_playing': False,
    'is_buffering': False,
    'track_uri': None,
    'track': None,              # {name, artist, artists, album, album_art_url}
    'audio_analysis': None,     # For future visualizer features
    'audio_analysis_track_id': None,  # Normalized track ID for validation
    'colors': None,             # May be null (Spotify blocks API)
}

# Freshness thresholds
POSITION_STALE_MS = 1000    # Position older than 1s is stale
METADATA_STALE_MS = 4000    # Track metadata older than 4s is stale (fast fallback to SMTC)

# Track when Spicetify was last actively playing (for paused timeout)
_spicetify_last_active_time: float = 0


# =============================================================================
# PUBLIC API
# =============================================================================

def is_connected() -> bool:
    """Check if Spicetify bridge is connected and data is fresh."""
    if not _spicetify_state['connected']:
        return False
    
    age_ms = (time.time() * 1000) - _spicetify_state['last_update']
    return age_ms < METADATA_STALE_MS


async def get_current_song_meta_data_spicetify() -> Optional[dict]:
    """
    Get metadata from Spicetify bridge.
    
    Follows existing source pattern (windows.py, spotify.py, gnome.py).
    Returns standardized dict or None if not connected/stale.
    """
    global _spicetify_last_active_time
    
    # Use lock to prevent torn reads during multi-field updates
    async with state._spicetify_state_lock:
        # Track metadata fetch (consistent with other sources)
        state._metadata_fetch_counters['spicetify'] += 1
        
        if not _spicetify_state['connected']:
            return None
        
        # Check staleness
        age_ms = (time.time() * 1000) - _spicetify_state['last_update']
        if age_ms > METADATA_STALE_MS:
            # Throttle stale log to once per 120 seconds to avoid spam
            if not hasattr(get_current_song_meta_data_spicetify, '_last_stale_log'):
                get_current_song_meta_data_spicetify._last_stale_log = 0
            now = time.time()
            if now - get_current_song_meta_data_spicetify._last_stale_log > 120:
                logger.debug(f"Spicetify data stale ({age_ms:.0f}ms > {METADATA_STALE_MS}ms)")
                get_current_song_meta_data_spicetify._last_stale_log = now
            return None
        
        track = _spicetify_state.get('track')
        if not track:
            return None
        
        artist = track.get('artist') or ''
        title = track.get('name') or ''
        album = track.get('album')
        
        # Generate normalized track ID for change detection
        current_track_id = _normalize_track_id(artist, title)
        
        # Update active time if playing
        is_playing = _spicetify_state['is_playing']
        if is_playing:
            _spicetify_last_active_time = time.time()
        
        # Get colors (may be null if Spotify blocks API)
        colors = _spicetify_state.get('colors')
        if colors:
            # Normalize to tuple format if dict
            if isinstance(colors, dict):
                colors = (
                    colors.get('VIBRANT') or colors.get('DARK_VIBRANT') or "#24273a",
                    colors.get('DARK_VIBRANT') or colors.get('DESATURATED') or "#363b54"
                )
        else:
            colors = ("#24273a", "#363b54")  # Default theme colors
        
        # Extract Spotify track ID from URI (spotify:track:xxx -> xxx)
        # Needed for like button functionality
        track_uri = _spicetify_state.get('track_uri')
        spotify_id = None
        if track_uri and ':' in track_uri:
            parts = track_uri.split(':')
            if len(parts) >= 3 and parts[1] == 'track':
                spotify_id = parts[2]
        
        # Position interpolation (matches Windows pattern)
        # When playing, estimate position based on elapsed time since last update
        # This prevents stale positions during brief WebSocket reconnections
        position_ms = _spicetify_state['position_ms']
        if is_playing:
            elapsed_ms = (time.time() * 1000) - _spicetify_state['last_update']
            # Cap interpolation at 5 seconds to prevent runaway drift
            elapsed_ms = min(elapsed_ms, 5000)
            position_ms = position_ms + elapsed_ms
        
        # Convert spotify:image: URI -> HTTPS, then enhance to 1400px (same as Spotify API source)
        # This ensures high-res album art even when album_art_db is OFF
        # Compute once and reuse for both album_art_url and background_image_url
        raw_album_art_url = _convert_spotify_image_uri(track.get('album_art_url'))
        enhanced_album_art_url = await enhance_spotify_image_url_async(raw_album_art_url)
        
        return {
            'track_id': current_track_id,
            'id': spotify_id,  # Spotify track ID for like button
            'source': 'spicetify',
            'title': title,
            'artist': artist,
            'artist_name': artist,  # For display purposes (same as artist)
            'album': album,
            'position': position_ms / 1000,  # Convert to seconds
            'duration_ms': _spicetify_state['duration_ms'],
            'is_playing': is_playing,
            'is_buffering': _spicetify_state['is_buffering'],
            'colors': colors,
            'album_art_url': enhanced_album_art_url,
            'album_art_path': None,  # Set during enrichment in metadata.py
            'background_image_url': enhanced_album_art_url,  # Default bg to album art
            'background_image_path': None,  # Set during enrichment in metadata.py
            'audio_analysis': _spicetify_state.get('audio_analysis'),
            'last_active_time': _spicetify_last_active_time,
            # Spotify-specific fields for Visual Mode and UI features
            'artist_id': track.get('artist_id'),  # For Visual Mode artist slideshow
            'url': track.get('url'),  # For 'open in Spotify' feature
            'artist_visuals': _spicetify_state.get('artist_visuals'),  # GraphQL header/gallery images
        }


# =============================================================================
# WEBSOCKET HANDLER (Quart style)
# =============================================================================

# Pending position update task (for debounce - only one at a time)
_pending_position_task: asyncio.Task = None


async def handle_spicetify_connection():
    """
    Handle Spicetify WebSocket connection.
    
    Called from server.py's @app.websocket('/ws/spicetify') endpoint.
    Uses Quart's global `websocket` object for receive/send.
    
    Architecture Notes:
    - Position updates use fire-and-forget (asyncio.create_task) to avoid blocking receive loop
    - Only one position update task runs at a time (debounce) to prevent task pileup
    - No locks used for position updates - dict assignments are atomic in Python
    - Track data still awaited since it's less frequent and needs ordering
    """
    global _spicetify_state, _spicetify_last_active_time, _pending_position_task
    
    _spicetify_state['connected'] = True
    logger.info("Spicetify bridge connected")
    
    try:
        while True:
            # Quart WebSocket uses receive() not async for
            data = await websocket.receive()
            
            if isinstance(data, str):
                try:
                    msg = json.loads(data)
                    msg_type = msg.get('type')
                    
                    if msg_type == 'position':
                        # Fire-and-forget with debounce: cancel old task if still pending
                        if _pending_position_task and not _pending_position_task.done():
                            _pending_position_task.cancel()
                        _pending_position_task = asyncio.create_task(_handle_position_update(msg))
                        
                    elif msg_type == 'track_data':
                        # Await track data (less frequent, needs ordering)
                        await _handle_track_data(msg)
                        
                    elif msg_type == 'ping':
                        # Respond to keepalive
                        await websocket.send_json({'type': 'pong'})
                        
                except json.JSONDecodeError:
                    logger.debug("Spicetify: Invalid JSON received")
                    
    except asyncio.CancelledError:
        logger.debug("Spicetify WebSocket cancelled")
    except Exception as e:
        logger.warning(f"Spicetify connection error: {e}")
    finally:
        # Cancel any pending position task
        if _pending_position_task and not _pending_position_task.done():
            _pending_position_task.cancel()
        
        # Reset state on disconnect to prevent stale data on reconnect
        _spicetify_state['connected'] = False
        _spicetify_state['track'] = None
        _spicetify_state['audio_analysis'] = None
        _spicetify_state['colors'] = None
        _spicetify_state['track_uri'] = None
        _spicetify_state['position_ms'] = 0
        _spicetify_state['duration_ms'] = 0
        _spicetify_state['is_playing'] = False
        _spicetify_state['is_buffering'] = False
        logger.info("Spicetify bridge disconnected")


# =============================================================================
# MESSAGE HANDLERS
# =============================================================================

async def _handle_position_update(data: dict):
    """
    Handle position update message from Spicetify.
    
    Note: No lock used - Python dict item assignment is atomic.
    This handler is called via fire-and-forget to avoid blocking the WebSocket receive loop.
    """
    # Simple dict updates are atomic in Python - no lock needed
    _spicetify_state['position_ms'] = data.get('position_ms', 0)
    _spicetify_state['duration_ms'] = data.get('duration_ms', 0)
    _spicetify_state['is_playing'] = data.get('is_playing', False)
    _spicetify_state['is_buffering'] = data.get('is_buffering', False)
    _spicetify_state['track_uri'] = data.get('track_uri')
    
    # Use server time for freshness (more reliable than client timestamp)
    _spicetify_state['last_update'] = time.time() * 1000
    
    # Debug log throttled to once per 60 seconds to reduce spam
    if not hasattr(_handle_position_update, '_last_pos_log'):
        _handle_position_update._last_pos_log = 0
    now = time.time()
    if now - _handle_position_update._last_pos_log > 60:
        logger.debug(f"Spicetify position: {data.get('position_ms', 0)}ms, playing={data.get('is_playing')}")
        _handle_position_update._last_pos_log = now


async def _handle_track_data(data: dict):
    """Handle track metadata + audio analysis from Spicetify."""
    # Track data is less frequent, can use lock for safety during multi-key update
    async with state._spicetify_state_lock:
        _spicetify_state['track'] = data.get('track')
        _spicetify_state['audio_analysis'] = data.get('audio_analysis')
        _spicetify_state['colors'] = data.get('colors')
        _spicetify_state['track_uri'] = data.get('track_uri')
        _spicetify_state['artist_visuals'] = data.get('artist_visuals')  # GraphQL header/gallery
        
        # Store normalized track ID for audio analysis validation
        # This allows frontend to verify analysis matches current track
        track = data.get('track', {})
        if track and data.get('audio_analysis'):
            analysis_track_id = _normalize_track_id(
                track.get('artist', ''),
                track.get('name', '')
            )
            _spicetify_state['audio_analysis_track_id'] = analysis_track_id
        else:
            # Clear stale track ID if no analysis provided
            _spicetify_state['audio_analysis_track_id'] = None
        
        # Also update last_update timestamp for freshness
        _spicetify_state['last_update'] = time.time() * 1000
    
    # Log track change (outside lock)
    track = data.get('track', {})
    artist = track.get('artist', '')
    title = track.get('name', '')
    
    # Detailed logging for track data
    # Check if audio_analysis has ACTUAL data (not just empty arrays)
    audio_analysis = data.get('audio_analysis') or {}
    has_analysis = bool(audio_analysis.get('segments'))  # segments is the key field
    has_colors = bool(data.get('colors'))
    
    if has_analysis:
        # INFO level for tracks with audio analysis (saved)
        logger.info(f"Spicetify track data: {artist} - {title} (analysis: ✓, colors: {'✓' if has_colors else '✗'})")
    else:
        # INFO level for tracks without audio analysis (still saves metadata)
        logger.info(f"Spicetify track: {artist} - {title} (no waveform data from Spotify)")
    
    # Save to database (background, non-blocking)
    # Always save track metadata, even without audio analysis - other fields are still useful
    if artist and title:
        from .spicetify_db import save_to_db
        from . import create_tracked_task
        create_tracked_task(save_to_db(
            artist=artist,
            title=title,
            track_uri=data.get('track_uri', ''),
            audio_analysis=data.get('audio_analysis'),
            colors=data.get('colors'),
            track_metadata=track,
            # New extended metadata
            canvas=data.get('canvas'),
            player_state=data.get('player_state'),
            playback_quality=data.get('playback_quality'),
            context=data.get('context'),
            collection=data.get('collection'),
            raw_metadata=data.get('raw_metadata'),
            context_metadata=data.get('context_metadata'),
            page_metadata=data.get('page_metadata'),
            artist_visuals=data.get('artist_visuals')  # GraphQL header/gallery images
        ))

