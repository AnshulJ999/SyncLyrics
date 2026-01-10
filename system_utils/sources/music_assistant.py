"""
Music Assistant metadata source plugin.

This plugin provides metadata from Music Assistant (MA), a music server
commonly used with Home Assistant. It uses WebSockets for real-time
updates and supports full playback controls.

Requirements:
- Music Assistant server (standalone or Home Assistant add-on)
- API token (generate in MA web UI)

Features:
- Real-time metadata via WebSocket
- Playback controls (play, pause, next, previous, seek)
- Queue support
- Auto-reconnection with exponential backoff
- Multi-player support (auto-detect or user-specified)
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any, List
from .base import BaseMetadataSource, SourceConfig, SourceCapability
from ..helpers import _normalize_track_id
from logging_config import get_logger

logger = get_logger(__name__)

# SECURITY: Suppress MA client loggers that log sensitive data (tokens, full messages)
# The connection logger logs full WebSocket messages including auth tokens at DEBUG level
logging.getLogger("music_assistant_client.connection").setLevel(logging.WARNING)
logging.getLogger("music_assistant_client").setLevel(logging.WARNING)


# Connection state
_client = None
_connection_task = None
_connected = False
_listening = False
_last_connect_attempt = 0
_reconnect_delay = 1  # Start at 1 second, exponential backoff

# State cache (updated by WebSocket events)
_current_player_id: Optional[str] = None
_current_queue_id: Optional[str] = None
_last_active_time: float = 0
_last_active_player_id: Optional[str] = None  # Track player that was last playing/paused
_metadata_cache: Optional[Dict[str, Any]] = None
_cache_time: float = 0

# Log rate limiting
_last_no_player_log: float = 0
NO_PLAYER_LOG_INTERVAL = 60.0  # Only log "no player" once every 30 seconds

# Constants
MAX_RECONNECT_DELAY = 60  # Max 60 seconds between reconnection attempts
CACHE_TTL = 1.0  # Cache TTL in seconds (MA updates come via events)


def _get_config_value(key: str, default: Any = None) -> Any:
    """Get config value with proper type handling."""
    from config import conf
    return conf(key, default)


def is_configured() -> bool:
    """Check if Music Assistant is configured (server URL provided)."""
    server_url = _get_config_value("system.music_assistant.server_url", "")
    return bool(server_url and server_url.strip())


def is_connected() -> bool:
    """Check if connected to Music Assistant server."""
    return _connected and _client is not None


async def _connect() -> bool:
    """
    Connect to Music Assistant server.
    
    Uses exponential backoff for reconnection attempts.
    Returns True if connected, False otherwise.
    """
    global _client, _connected, _listening, _last_connect_attempt, _reconnect_delay
    
    if _connected and _client and _listening:
        return True
    
    # Check if configured
    if not is_configured():
        return False
    
    # Rate limit connection attempts
    now = time.time()
    if now - _last_connect_attempt < _reconnect_delay:
        return False
    
    _last_connect_attempt = now
    
    server_url = _get_config_value("system.music_assistant.server_url", "")
    token = _get_config_value("system.music_assistant.token", "")
    
    try:
        from music_assistant_client import MusicAssistantClient
        
        logger.info(f"Connecting to Music Assistant: {server_url}")
        
        # Create client (token may be optional for older schema versions)
        _client = MusicAssistantClient(
            server_url=server_url,
            aiohttp_session=None,
            token=token if token else None,
        )
        
        # Connect with timeout
        await asyncio.wait_for(_client.connect(), timeout=10.0)
        
        _connected = True
        _reconnect_delay = 1  # Reset backoff on success
        
        logger.info("Connected to Music Assistant")
        
        # Start listening in background to receive player/queue updates
        # This populates _client.players.players and _client.player_queues.player_queues
        asyncio.create_task(_start_listening())
        
        return True
        
    except ImportError:
        logger.error("music-assistant-client not installed. Run: pip install music-assistant-client")
        _reconnect_delay = MAX_RECONNECT_DELAY  # Don't retry frequently
        return False
    except asyncio.TimeoutError:
        logger.warning("Music Assistant connection timed out")
        _reconnect_delay = min(_reconnect_delay * 2, MAX_RECONNECT_DELAY)
        return False
    except Exception as e:
        logger.debug(f"Music Assistant connection failed: {e}")
        _reconnect_delay = min(_reconnect_delay * 2, MAX_RECONNECT_DELAY)
        _client = None
        _connected = False
        _listening = False
        return False


async def _start_listening():
    """
    Start the WebSocket listener to receive player/queue events.
    
    This runs in the background and keeps the player list updated.
    """
    global _listening, _connected, _client
    
    if not _client:
        return
    
    try:
        _listening = True
        logger.debug("Starting Music Assistant event listener")
        await _client.start_listening()
    except Exception as e:
        logger.debug(f"Music Assistant listener stopped: {e}")
    finally:
        _listening = False
        _connected = False
        logger.info("Music Assistant disconnected")


async def _ensure_connected() -> bool:
    """Ensure we're connected, attempt reconnection if needed."""
    if _connected and _client and _listening:
        return True
    return await _connect()


def _get_target_player_id() -> Optional[str]:
    """
    Get the player ID to monitor.
    
    Priority:
    1. User-configured player_id setting
    2. First player with state PLAYING
    3. First player with state PAUSED (recently active)
    4. Last active player (if still exists)
    5. First available player
    """
    global _current_player_id, _last_active_player_id
    
    if not _client:
        return None
    
    # Check user preference
    preferred_id = _get_config_value("system.music_assistant.player_id", "")
    if preferred_id and preferred_id.strip():
        player = _client.players.get(preferred_id.strip())
        if player:
            return player.player_id
        logger.debug(f"Configured player_id '{preferred_id}' not found")
    
    # Find first playing player
    for player in _client.players.players:
        if player.playback_state and player.playback_state.value == "playing":
            _last_active_player_id = player.player_id  # Remember active player
            return player.player_id
    
    # Find first paused player (recently active)
    for player in _client.players.players:
        if player.playback_state and player.playback_state.value == "paused":
            return player.player_id
    
    # Use last active player if still exists
    if _last_active_player_id:
        player = _client.players.get(_last_active_player_id)
        if player:
            return player.player_id
    
    # Fall back to first available player
    players = list(_client.players.players)
    if players:
        return players[0].player_id
    
    return None


async def _get_active_queue_id(player_id: str) -> Optional[str]:
    """Get the active queue ID for a player."""
    global _current_queue_id
    
    if not _client:
        return None
    
    try:
        queue = await _client.player_queues.get_active_queue(player_id)
        if queue:
            _current_queue_id = queue.queue_id
            return queue.queue_id
    except Exception as e:
        logger.debug(f"Failed to get active queue: {e}")
    
    # Fallback: queue_id often equals player_id
    return player_id


class MusicAssistantSource(BaseMetadataSource):
    """
    Music Assistant integration.
    
    Provides real-time metadata and playback controls from any Music Assistant
    server (standalone or Home Assistant add-on).
    
    Configuration:
    - system.music_assistant.server_url: MA server URL (e.g., http://192.168.1.100:8095)
    - system.music_assistant.token: API token (generate in MA web UI)
    - system.music_assistant.player_id: Specific player to monitor (optional)
    - system.music_assistant.paused_timeout: Seconds before paused state expires
    """
    
    def __init__(self):
        super().__init__()
        self._last_active_time = 0
    
    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="music_assistant",
            display_name="Music Assistant",
            platforms=["Windows", "Linux", "Darwin"],  # Cross-platform
            default_enabled=True,  # Enabled by default (requires server_url to work)
            default_priority=1,    # High priority (same as Windows Media)
            paused_timeout=600,    # 10 minutes
            requires_auth=True,    # Needs server_url and optional token
            config_keys=[
                "system.music_assistant.server_url",
                "system.music_assistant.token",
                "system.music_assistant.player_id",
            ],
        )
    
    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (
            SourceCapability.METADATA |
            SourceCapability.PLAYBACK_CONTROL |
            SourceCapability.SEEK |
            SourceCapability.DURATION |
            SourceCapability.ALBUM_ART |
            SourceCapability.QUEUE
        )
    
    def is_available(self) -> bool:
        """
        Check if Music Assistant is available.
        
        Returns True if:
        - Server URL is configured
        - Platform is supported (all platforms)
        """
        return is_configured()
    
    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata from Music Assistant.
        
        Gets current track info from the active player's queue.
        Uses cached data if fresh enough, otherwise fetches from server.
        """
        global _metadata_cache, _cache_time, _current_player_id, _last_active_time
        
        # Ensure connected
        if not await _ensure_connected():
            return None
        
        try:
            # Get target player
            player_id = _get_target_player_id()
            if not player_id:
                # Rate limit this log to avoid spam
                global _last_no_player_log
                now = time.time()
                if now - _last_no_player_log >= NO_PLAYER_LOG_INTERVAL:
                    logger.debug("No Music Assistant player available")
                    _last_no_player_log = now
                return None
            
            _current_player_id = player_id
            
            # Get player state
            player = _client.players.get(player_id)
            if not player:
                return None
            
            # Get active queue
            queue_id = await _get_active_queue_id(player_id)
            if not queue_id:
                return None
            
            queue = _client.player_queues.get(queue_id)
            if not queue:
                return None
            
            # Check queue state (use queue.state for consistency with corrected_elapsed_time)
            # queue.state is what corrected_elapsed_time uses to decide whether to interpolate
            queue_state = queue.state.value if queue.state else "idle"
            
            # Return None if IDLE - don't show stale metadata from last session
            if queue_state == "idle":
                return None
            
            is_playing = queue_state == "playing"
            
            # Get current item from queue
            current_item = queue.current_item
            if not current_item:
                return None
            
            # Extract metadata
            media_item = current_item.media_item
            if not media_item:
                # Use queue item directly if no media_item
                artist = current_item.name or ""
                title = ""
                album = None
            else:
                # Get from media_item (more detailed)
                artist = ""
                if hasattr(media_item, 'artists') and media_item.artists:
                    artist = media_item.artists[0].name if media_item.artists else ""
                elif hasattr(media_item, 'artist'):
                    artist = str(media_item.artist) if media_item.artist else ""
                
                title = media_item.name or ""
                album = media_item.album.name if hasattr(media_item, 'album') and media_item.album else None
            
            # Handle case where title is empty but name exists on current_item
            if not title and current_item.name:
                title = current_item.name
            
            # Get image URL
            album_art_url = None
            try:
                # Try to get image from the client's helper
                album_art_url = _client.get_media_item_image_url(current_item, size=640)
            except Exception:
                pass
            
            # Calculate position
            # IMPORTANT: Only use corrected_elapsed_time when PLAYING
            # When paused/stopped, use raw elapsed_time to avoid infinite interpolation
            # (corrected_elapsed_time interpolates based on queue.state, which can get stuck)
            if is_playing:
                position = queue.corrected_elapsed_time if queue.corrected_elapsed_time is not None else 0
            else:
                # Paused - use raw elapsed_time (no interpolation)
                position = queue.elapsed_time if queue.elapsed_time is not None else 0
            
            # Get duration
            duration_ms = None
            if current_item.duration:
                duration_ms = int(current_item.duration * 1000)
            
            # Update last active time
            if is_playing:
                self._last_active_time = time.time()
            
            # Build result
            result = {
                "track_id": _normalize_track_id(artist, title),
                "artist": artist,
                "title": title,
                "album": album,
                "album_art_url": album_art_url,
                "position": position,
                "duration_ms": duration_ms,
                "is_playing": is_playing,
                "source": "music_assistant",
                "colors": ("#24273a", "#363b54"),  # Default, will be enriched
                "last_active_time": self._last_active_time,
            }
            
            return result
            
        except Exception as e:
            logger.debug(f"Music Assistant metadata fetch failed: {e}")
            # Don't set _connected = False here - that causes reconnect spam
            # Connection errors are handled by start_listening task
            return None
    
    # === Playback Controls ===
    
    async def toggle_playback(self) -> bool:
        """Toggle play/pause on the active queue."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            # Use the built-in play_pause() method which handles toggle
            await _client.player_queues.play_pause(queue_id)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant toggle_playback failed: {e}")
            return False
    
    async def play(self) -> bool:
        """Resume playback."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            await _client.player_queues.play(queue_id)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant play failed: {e}")
            return False
    
    async def pause(self) -> bool:
        """Pause playback."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            await _client.player_queues.pause(queue_id)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant pause failed: {e}")
            return False
    
    async def next_track(self) -> bool:
        """Skip to next track."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            await _client.player_queues.next(queue_id)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant next_track failed: {e}")
            return False
    
    async def previous_track(self) -> bool:
        """Skip to previous track."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            await _client.player_queues.previous(queue_id)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant previous_track failed: {e}")
            return False
    
    async def seek(self, position_ms: int) -> bool:
        """Seek to position in milliseconds."""
        if not await _ensure_connected():
            return False
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            # MA seek expects seconds
            position_seconds = position_ms // 1000
            await _client.player_queues.seek(queue_id, position_seconds)
            return True
        except Exception as e:
            logger.debug(f"Music Assistant seek failed: {e}")
            return False
    
    async def get_queue(self) -> Optional[Dict]:
        """
        Get playback queue.
        
        Returns queue in Spotify-compatible format for frontend compatibility.
        """
        if not await _ensure_connected():
            return None
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return None
            
            # Get queue items
            items = await _client.player_queues.get_queue_items(queue_id, limit=20, offset=0)
            
            # Convert to Spotify-compatible format
            queue_items = []
            for item in items:
                # Skip current item (compare queue_item_id strings)
                queue_obj = _client.player_queues.get(queue_id)
                if queue_obj and queue_obj.current_item:
                    if item.queue_item_id == queue_obj.current_item.queue_item_id:
                        continue
                
                media = item.media_item
                if not media:
                    continue
                
                # Get artist name
                artist_name = ""
                if hasattr(media, 'artists') and media.artists:
                    artist_name = media.artists[0].name
                elif hasattr(media, 'artist'):
                    artist_name = str(media.artist) if media.artist else ""
                
                # Get album art
                art_url = None
                try:
                    art_url = _client.get_media_item_image_url(item, size=64)
                except Exception:
                    pass
                
                queue_items.append({
                    "name": media.name or item.name,
                    "artists": [{"name": artist_name}],
                    "album": {
                        "images": [{"url": art_url}] if art_url else []
                    }
                })
            
            return {
                "queue": queue_items,
                "source": "music_assistant"
            }
            
        except Exception as e:
            logger.debug(f"Music Assistant get_queue failed: {e}")
            return None
