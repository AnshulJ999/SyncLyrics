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

logger = get_logger(__name__)

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
    'colors': None,             # May be null (Spotify blocks API)
}

# Freshness thresholds
POSITION_STALE_MS = 1000    # Position older than 1s is stale
METADATA_STALE_MS = 5000    # Track metadata older than 5s is stale

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
        if not _spicetify_state['connected']:
            return None
        
        # Check staleness
        age_ms = (time.time() * 1000) - _spicetify_state['last_update']
        if age_ms > METADATA_STALE_MS:
            logger.debug(f"Spicetify data stale ({age_ms:.0f}ms > {METADATA_STALE_MS}ms)")
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
        
        return {
            'track_id': current_track_id,
            'source': 'spicetify',
            'title': title,
            'artist': artist,
            'album': album,
            'position': _spicetify_state['position_ms'] / 1000,  # Convert to seconds
            'duration_ms': _spicetify_state['duration_ms'],
            'is_playing': is_playing,
            'is_buffering': _spicetify_state['is_buffering'],
            'colors': colors,
            'album_art_url': track.get('album_art_url'),
            'background_image_url': track.get('album_art_url'),  # Default bg to album art
            'audio_analysis': _spicetify_state.get('audio_analysis'),
            'last_active_time': _spicetify_last_active_time,
        }


# =============================================================================
# WEBSOCKET HANDLER (Quart style)
# =============================================================================

async def handle_spicetify_connection():
    """
    Handle Spicetify WebSocket connection.
    
    Called from server.py's @app.websocket('/ws/spicetify') endpoint.
    Uses Quart's global `websocket` object for receive/send.
    """
    global _spicetify_state, _spicetify_last_active_time
    
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
                        await _handle_position_update(msg)
                    elif msg_type == 'track_data':
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
        # Reset state on disconnect to prevent stale data on reconnect
        _spicetify_state['connected'] = False
        _spicetify_state['track'] = None
        _spicetify_state['audio_analysis'] = None
        _spicetify_state['colors'] = None
        _spicetify_state['track_uri'] = None
        logger.info("Spicetify bridge disconnected")


# =============================================================================
# MESSAGE HANDLERS
# =============================================================================

async def _handle_position_update(data: dict):
    """Handle position update message from Spicetify."""
    async with state._spicetify_state_lock:
        _spicetify_state['position_ms'] = data.get('position_ms', 0)
        _spicetify_state['duration_ms'] = data.get('duration_ms', 0)
        _spicetify_state['is_playing'] = data.get('is_playing', False)
        _spicetify_state['is_buffering'] = data.get('is_buffering', False)
        _spicetify_state['track_uri'] = data.get('track_uri')
        
        # Use server time for freshness (more reliable than client timestamp)
        _spicetify_state['last_update'] = time.time() * 1000


async def _handle_track_data(data: dict):
    """Handle track metadata + audio analysis from Spicetify."""
    async with state._spicetify_state_lock:
        _spicetify_state['track'] = data.get('track')
        _spicetify_state['audio_analysis'] = data.get('audio_analysis')
        _spicetify_state['colors'] = data.get('colors')
        _spicetify_state['track_uri'] = data.get('track_uri')
        
        # Also update last_update timestamp for freshness
        _spicetify_state['last_update'] = time.time() * 1000
    
    # Log track change (outside lock)
    track = data.get('track', {})
    logger.debug(f"Spicetify track: {track.get('artist')} - {track.get('name')}")
