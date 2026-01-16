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

# Background connection management (non-blocking)
_connection_lock: Optional[asyncio.Lock] = None  # Created lazily for event loop safety
_connecting = False  # Fast check to avoid duplicate connection tasks
_listener_task: Optional[asyncio.Task] = None  # Track listener to prevent duplicates

# State cache (updated by WebSocket events)
_current_player_id: Optional[str] = None
_current_queue_id: Optional[str] = None
_last_active_time: float = 0
_last_active_player_id: Optional[str] = None  # Track player that was last playing/paused
_metadata_cache: Optional[Dict[str, Any]] = None
_cache_time: float = 0

# Log rate limiting - prevent spam in logs
_last_no_player_log: float = 0
_last_player_not_found_log: float = 0
_last_queue_error_log: float = 0
_last_metadata_error_log: float = 0
_last_disconnect_log: float = 0
_connection_attempt_count: int = 0  # Track consecutive connection attempts
LOG_THROTTLE_INTERVAL = 60.0  # Only log repeated messages once per minute

# Favorites cache - avoids repeated API calls for same track
_favorite_cache: Dict[str, bool] = {}  # item_id -> is_favorite
_favorite_cache_time: Dict[str, float] = {}  # item_id -> timestamp
FAVORITE_CACHE_TTL = 30.0  # Cache favorite status for 30 seconds

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


def is_ready() -> bool:
    """Check if MA is connected and listening (non-blocking)."""
    return _connected and _client is not None and _listening


def _get_connection_lock() -> asyncio.Lock:
    """Get or create the connection lock (lazy init for event loop safety)."""
    global _connection_lock
    if _connection_lock is None:
        _connection_lock = asyncio.Lock()
    return _connection_lock


async def _connect() -> bool:
    """
    Connect to Music Assistant server.
    
    Uses exponential backoff for reconnection attempts.
    Returns True if connected, False otherwise.
    """
    global _client, _connected, _listening, _last_connect_attempt, _reconnect_delay
    global _connection_attempt_count
    
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
    _connection_attempt_count += 1
    
    server_url = _get_config_value("system.music_assistant.server_url", "")
    token = _get_config_value("system.music_assistant.token", "")
    
    try:
        from music_assistant_client import MusicAssistantClient
        
        # Log INFO on first attempt, DEBUG on retries to reduce spam
        if _connection_attempt_count == 1:
            logger.info(f"Connecting to Music Assistant: {server_url}")
        else:
            logger.debug(f"Reconnecting to Music Assistant (attempt {_connection_attempt_count})")
        
        # Create client (token may be optional for older schema versions)
        _client = MusicAssistantClient(
            server_url=server_url,
            aiohttp_session=None,
            token=token if token else None,
        )
        
        # Connect with timeout
        await asyncio.wait_for(_client.connect(), timeout=4.0)
        
        _connected = True
        _reconnect_delay = 1  # Reset backoff on success
        _connection_attempt_count = 0  # Reset on success
        
        logger.info("Connected to Music Assistant")
        
        # Start listening in background to receive player/queue updates
        # This populates _client.players.players and _client.player_queues.player_queues
        # Cancel any existing listener first to prevent duplicates
        global _listener_task
        if _listener_task and not _listener_task.done():
            _listener_task.cancel()
        _listener_task = asyncio.create_task(_start_listening())
        
        return True
        
    except ImportError:
        logger.error("music-assistant-client not installed. Run: pip install music-assistant-client")
        _reconnect_delay = MAX_RECONNECT_DELAY  # Don't retry frequently
        return False
    except asyncio.TimeoutError:
        # Rate limit timeout warnings - log first 3, then every 5th attempt
        if _connection_attempt_count <= 3 or _connection_attempt_count % 5 == 0:
            logger.debug(f"Music Assistant connection timed out (attempt {_connection_attempt_count})")
        _reconnect_delay = min(_reconnect_delay * 2, MAX_RECONNECT_DELAY)
        await _cleanup_failed_client()
        return False
    except Exception as e:
        # Rate limit connection failure logs - log first 3, then every 5th attempt
        if _connection_attempt_count <= 3 or _connection_attempt_count % 5 == 0:
            logger.debug(f"Music Assistant connection failed (attempt {_connection_attempt_count}): {e}")
        _reconnect_delay = min(_reconnect_delay * 2, MAX_RECONNECT_DELAY)
        await _cleanup_failed_client()
        return False


async def _cleanup_failed_client():
    """Clean up client resources after failed connection."""
    global _client, _connected, _listening
    
    if _client:
        try:
            # Properly close the client to avoid unclosed session warnings
            await _client.disconnect()
        except Exception:
            pass
    
    _client = None
    _connected = False
    _listening = False


async def _start_listening():
    """
    Start the WebSocket listener to receive player/queue events.
    
    This runs in the background and keeps the player list updated.
    """
    global _listening, _connected, _client, _last_disconnect_log
    
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
        # Rate limit disconnected log to prevent spam on reconnect cycles
        now = time.time()
        if now - _last_disconnect_log >= LOG_THROTTLE_INTERVAL:
            logger.info("Music Assistant disconnected")
            _last_disconnect_log = now


async def _ensure_connected() -> bool:
    """Ensure we're connected, attempt reconnection if needed (BLOCKING - legacy)."""
    if _connected and _client and _listening:
        return True
    return await _connect()


async def _ensure_connected_nonblocking() -> bool:
    """
    Check connection, trigger background reconnect if needed.
    
    NEVER BLOCKS - returns immediately.
    If not connected, schedules background connection task.
    Use this in get_metadata() and frequent poll paths.
    """
    global _connection_task, _connecting
    
    if is_ready():
        return True
    
    # Not connected - trigger background connection if not already running
    if not _connecting and (_connection_task is None or _connection_task.done()):
        _connecting = True
        _connection_task = asyncio.create_task(_background_connect())
    
    return False  # Not ready yet - caller should return None


async def _background_connect():
    """
    Background connection task with exponential backoff.
    
    Runs until connected or cancelled. Uses lock to prevent
    multiple simultaneous connection attempts.
    """
    global _connecting
    
    async with _get_connection_lock():
        try:
            while not _connected:
                success = await _connect()
                if success:
                    break
                
                # Wait before retry (exponential backoff already handled in _connect)
                await asyncio.sleep(_reconnect_delay)
        except asyncio.CancelledError:
            logger.debug("Background connection task cancelled")
        finally:
            _connecting = False


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
        # Rate limit this log to prevent spam when player is misconfigured
        global _last_player_not_found_log
        now = time.time()
        if now - _last_player_not_found_log >= LOG_THROTTLE_INTERVAL:
            logger.debug(f"Configured player_id '{preferred_id}' not found")
            _last_player_not_found_log = now
    
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
        # Rate limit queue error log
        global _last_queue_error_log
        now = time.time()
        if now - _last_queue_error_log >= LOG_THROTTLE_INTERVAL:
            logger.debug(f"Failed to get active queue: {e}")
            _last_queue_error_log = now
    
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
            SourceCapability.QUEUE |
            SourceCapability.FAVORITES
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
        if not await _ensure_connected_nonblocking():
            return None
        
        try:
            # Get target player
            player_id = _get_target_player_id()
            if not player_id:
                # Rate limit this log to avoid spam
                global _last_no_player_log
                now = time.time()
                if now - _last_no_player_log >= LOG_THROTTLE_INTERVAL:
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
            player_state = player.playback_state.value if player.playback_state else "idle"
            
            # DEBUG: Uncomment to diagnose state detection issues
            # logger.debug(f"MA state check - queue.state={queue_state}, player.playback_state={player_state}, "
            #             f"current_item={'present' if queue.current_item else 'None'}, "
            #             f"elapsed_time_last_updated={queue.elapsed_time_last_updated}")
            
            # Determine staleness: how long since MA last updated the position
            # This distinguishes "just paused" from "stale session data from hours ago"
            time_since_update = time.time() - queue.elapsed_time_last_updated
            paused_timeout = self.get_config().paused_timeout  # Default 600s (10 min)
            is_stale = time_since_update > paused_timeout
            
            # Only return None if IDLE and STALE (prevents 688min bug from old sessions)
            # When just paused, queue.state=idle but elapsed_time_last_updated is fresh
            if queue_state == "idle" and is_stale:
                return None
            
            # Use player.playback_state for is_playing (it updates faster than queue.state)
            # Logs showed player goes playing→idle→playing faster than queue
            is_playing = player_state == "playing"
            
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
            #
            # TODO: MA Server PR #2959 (Jan 2026) adds elapsed_time_updated_at from server
            # This will eliminate clock drift between MA server and client by providing
            # the server's timestamp when elapsed_time was measured.
            # Track: https://github.com/music-assistant/server/pull/2959
            # When merged, update to use server timestamp instead of client time.time()
            # Current workaround: users adjust music_assistant_latency_compensation setting
            #
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
            
            # Get MA item ID for favorites support
            # Use media_item.item_id if available, fallback to uri
            ma_item_id = None
            if media_item:
                if hasattr(media_item, 'item_id') and media_item.item_id:
                    ma_item_id = str(media_item.item_id)
                elif hasattr(media_item, 'uri') and media_item.uri:
                    ma_item_id = str(media_item.uri)
            
            # Build result
            result = {
                "track_id": _normalize_track_id(artist, title),
                "artist": artist,
                "artist_name": artist,  # For display consistency with other sources
                "title": title,
                "album": album,
                "album_art_url": album_art_url,
                "position": position,
                "duration_ms": duration_ms,
                "is_playing": is_playing,
                "source": "music_assistant",
                "colors": ("#24273a", "#363b54"),  # Default, will be enriched
                "last_active_time": self._last_active_time,
                "ma_item_id": ma_item_id,  # For favorites/like functionality
            }
            
            return result
            
        except Exception as e:
            # Rate limit metadata error log
            global _last_metadata_error_log
            now = time.time()
            if now - _last_metadata_error_log >= LOG_THROTTLE_INTERVAL:
                logger.debug(f"Music Assistant metadata fetch failed: {e}")
                _last_metadata_error_log = now
            # Don't set _connected = False here - that causes reconnect spam
            # Connection errors are handled by start_listening task
            return None
    
    # === Playback Controls ===
    
    async def toggle_playback(self) -> bool:
        """Toggle play/pause on the active queue."""
        if not await _ensure_connected_nonblocking():
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
        if not await _ensure_connected_nonblocking():
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
        if not await _ensure_connected_nonblocking():
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
        if not await _ensure_connected_nonblocking():
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
        if not await _ensure_connected_nonblocking():
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
        if not await _ensure_connected_nonblocking():
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
        Get playback queue (upcoming songs only).
        
        Returns queue in Spotify-compatible format for frontend compatibility.
        Only returns songs AFTER the current playing song, not history.
        """
        if not await _ensure_connected_nonblocking():
            return None
        
        try:
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return None
            
            # Get queue object to find current position
            queue_obj = _client.player_queues.get(queue_id)
            if not queue_obj:
                return None
            
            # Get current index - this is where we are in the queue
            # MA queue includes history (played songs) at the beginning
            # We want to start AFTER the current song to get only upcoming
            current_index = getattr(queue_obj, 'current_index', 0) or 0
            
            # Get items starting AFTER the current item
            # offset = current_index + 1 skips history AND current song
            items = await _client.player_queues.get_queue_items(
                queue_id, 
                limit=20, 
                offset=current_index + 1
            )
            
            # Convert to Spotify-compatible format
            queue_items = []
            for item in items:
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

    # === Favorites (Like) Support ===
    
    async def is_favorite(self, item_id: str) -> bool:
        """
        Check if the current track is in favorites.
        
        Uses a cache to avoid spamming the API. Cache is invalidated
        when add_to_favorites or remove_from_favorites is called.
        
        Args:
            item_id: MA media item ID
            
        Returns:
            True if track is in favorites, False otherwise
        """
        global _favorite_cache, _favorite_cache_time
        
        if not item_id:
            return False
        
        # Check cache first
        now = time.time()
        if item_id in _favorite_cache:
            cache_age = now - _favorite_cache_time.get(item_id, 0)
            if cache_age < FAVORITE_CACHE_TTL:
                return _favorite_cache[item_id]
        
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            # Get the current queue to check the playing item's favorite status
            queue_id = _current_queue_id or _current_player_id
            if not queue_id:
                return False
            
            queue = _client.player_queues.get(queue_id)
            if not queue or not queue.current_item:
                return False
            
            # Check if the current item's media_item has favorite property
            media_item = queue.current_item.media_item
            is_fav = False
            if media_item and hasattr(media_item, 'favorite'):
                is_fav = media_item.favorite
            
            # Cache the result
            _favorite_cache[item_id] = is_fav
            _favorite_cache_time[item_id] = now
            
            return is_fav
            
        except Exception as e:
            logger.debug(f"Music Assistant is_favorite check failed: {e}")
            return False
    
    async def add_to_favorites(self, item_id: str) -> bool:
        """
        Add a track to favorites.
        
        Args:
            item_id: MA media item ID
            
        Returns:
            True if successful, False otherwise
        """
        if not item_id:
            logger.debug(f"Music Assistant add_to_favorites: item_id is empty/None")
            return False
        
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            # Get the current queue to access the media_item object
            # The add_item command needs the actual media item, not just the ID
            queue_id = _current_queue_id or _current_player_id
            if queue_id:
                queue = _client.player_queues.get(queue_id)
                if queue and queue.current_item and queue.current_item.media_item:
                    media_item = queue.current_item.media_item
                    # Use the media item directly
                    await _client.send_command(
                        "music/favorites/add_item",
                        item=media_item
                    )
                    # Invalidate cache - set to True so next check returns correct state
                    _favorite_cache[item_id] = True
                    _favorite_cache_time[item_id] = time.time()
                    logger.debug(f"Added track {item_id} to MA favorites")
                    return True
            
            logger.debug(f"Music Assistant add_to_favorites: no current media item available")
            return False
            
        except Exception as e:
            logger.debug(f"Music Assistant add_to_favorites failed: {e}")
            return False
    
    async def remove_from_favorites(self, item_id: str) -> bool:
        """
        Remove a track from favorites.
        
        Args:
            item_id: MA media item ID
            
        Returns:
            True if successful, False otherwise
        """
        if not item_id:
            logger.debug(f"Music Assistant remove_from_favorites: item_id is empty/None")
            return False
        
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            # remove_item uses library_item_id parameter
            await _client.send_command(
                "music/favorites/remove_item",
                library_item_id=item_id,
                media_type="track"
            )
            # Invalidate cache - set to False so next check returns correct state
            _favorite_cache[item_id] = False
            _favorite_cache_time[item_id] = time.time()
            logger.debug(f"Removed track {item_id} from MA favorites")
            return True
            
        except Exception as e:
            logger.debug(f"Music Assistant remove_from_favorites failed: {e}")
            return False
    
    # === Volume Control ===
    
    async def get_volume(self) -> Optional[int]:
        """Get current player volume (0-100).
        
        Returns:
            Volume percentage (0-100) or None if unavailable
        """
        if not await _ensure_connected_nonblocking():
            return None
        
        try:
            player_id = _get_target_player_id()
            if not player_id:
                return None
            
            player = _client.players.get(player_id)
            if player and hasattr(player, 'volume_level'):
                # MA volume is 0-100
                return int(player.volume_level)
            return None
        except Exception as e:
            logger.debug(f"Music Assistant get_volume failed: {e}")
            return None
    
    async def set_volume(self, volume: int) -> bool:
        """Set player volume (0-100).
        
        Args:
            volume: Volume percentage (0-100)
            
        Returns:
            True if successful, False otherwise
        """
        if not await _ensure_connected_nonblocking():
            return False
        
        # Clamp to valid range
        volume = max(0, min(100, volume))
        
        try:
            player_id = _get_target_player_id()
            if not player_id:
                return False
            
            await _client.players.volume_set(player_id, volume)
            logger.debug(f"Set MA player volume to {volume}%")
            return True
        except Exception as e:
            logger.debug(f"Music Assistant set_volume failed: {e}")
            return False

    # === Shuffle Control ===
    
    async def get_shuffle(self) -> Optional[bool]:
        """Get current shuffle state.
        
        Returns:
            True if shuffle enabled, False if disabled, None if unavailable
        """
        if not await _ensure_connected_nonblocking():
            return None
        
        try:
            player_id = _get_target_player_id()
            if not player_id:
                return None
            
            queue_id = await _get_active_queue_id(player_id)
            if not queue_id:
                return None
            
            queue = _client.player_queues.get(queue_id)
            if queue and hasattr(queue, 'shuffle_enabled'):
                return bool(queue.shuffle_enabled)
            return None
        except Exception as e:
            logger.debug(f"Music Assistant get_shuffle failed: {e}")
            return None
    
    async def set_shuffle(self, enabled: bool) -> bool:
        """Set shuffle mode.
        
        Args:
            enabled: True to enable shuffle, False to disable
            
        Returns:
            True if successful, False otherwise
        """
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            player_id = _get_target_player_id()
            if not player_id:
                return False
            
            queue_id = await _get_active_queue_id(player_id)
            if not queue_id:
                return False
            
            await _client.player_queues.shuffle(queue_id, enabled)
            logger.info(f"Set MA shuffle to {enabled}")
            return True
        except Exception as e:
            logger.debug(f"Music Assistant set_shuffle failed: {e}")
            return False
    
    # === Repeat Control ===
    
    async def get_repeat(self) -> Optional[str]:
        """Get current repeat mode.
        
        Returns:
            'off', 'all', or 'one' (mapped from MA's RepeatMode enum), None if unavailable
        """
        if not await _ensure_connected_nonblocking():
            return None
        
        try:
            player_id = _get_target_player_id()
            if not player_id:
                return None
            
            queue_id = await _get_active_queue_id(player_id)
            if not queue_id:
                return None
            
            queue = _client.player_queues.get(queue_id)
            if queue and hasattr(queue, 'repeat_mode'):
                # Map MA's RepeatMode to our string format
                # MA uses: OFF, ONE, ALL
                # We use: 'off', 'track' (for ONE), 'context' (for ALL)
                mode_value = queue.repeat_mode.value if hasattr(queue.repeat_mode, 'value') else str(queue.repeat_mode)
                mode_map = {'off': 'off', 'one': 'track', 'all': 'context'}
                return mode_map.get(mode_value.lower(), 'off')
            return None
        except Exception as e:
            logger.debug(f"Music Assistant get_repeat failed: {e}")
            return None
    
    async def set_repeat(self, mode: str) -> bool:
        """Set repeat mode.
        
        Args:
            mode: 'off', 'context' (all), or 'track' (one)
            
        Returns:
            True if successful, False otherwise
        """
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            from music_assistant_models.enums import RepeatMode
            
            # Map our mode names to MA's RepeatMode
            mode_map = {'off': RepeatMode.OFF, 'context': RepeatMode.ALL, 'track': RepeatMode.ONE}
            ma_mode = mode_map.get(mode)
            if not ma_mode:
                logger.warning(f"Invalid repeat mode: {mode}")
                return False
            
            player_id = _get_target_player_id()
            if not player_id:
                return False
            
            queue_id = await _get_active_queue_id(player_id)
            if not queue_id:
                return False
            
            await _client.player_queues.repeat(queue_id, ma_mode)
            logger.info(f"Set MA repeat to {mode}")
            return True
        except ImportError:
            logger.debug("music_assistant_models not available for repeat mode")
            return False
        except Exception as e:
            logger.debug(f"Music Assistant set_repeat failed: {e}")
            return False
    
    # === Device/Player Selection ===
    
    async def get_devices(self) -> List[Dict[str, Any]]:
        """Get list of available players/devices.
        
        Returns:
            List of player dicts with id, name, type, is_active, state
        """
        if not await _ensure_connected_nonblocking():
            return []
        
        try:
            devices = []
            current_player_id = _get_target_player_id()
            
            for player in _client.players:
                devices.append({
                    'id': player.player_id,
                    'name': player.display_name or player.name or player.player_id,
                    'type': player.type.value if hasattr(player.type, 'value') else str(player.type),
                    'is_active': player.player_id == current_player_id,
                    'state': player.playback_state.value if player.playback_state else 'idle',
                    'volume': int(player.volume_level) if hasattr(player, 'volume_level') else None,
                })
            
            return devices
        except Exception as e:
            logger.debug(f"Music Assistant get_devices failed: {e}")
            return []
    
    async def transfer_playback(self, target_player_id: str) -> bool:
        """Transfer playback to another player.
        
        Args:
            target_player_id: ID of the player to transfer to
            
        Returns:
            True if successful, False otherwise
        """
        if not await _ensure_connected_nonblocking():
            return False
        
        try:
            source_player_id = _get_target_player_id()
            if not source_player_id:
                return False
            
            source_queue_id = await _get_active_queue_id(source_player_id)
            if not source_queue_id:
                return False
            
            # For transfer, target queue ID is usually same as player ID
            target_queue_id = target_player_id
            
            await _client.player_queues.transfer(source_queue_id, target_queue_id)
            logger.info(f"Transferred MA playback from {source_player_id} to {target_player_id}")
            return True
        except Exception as e:
            logger.debug(f"Music Assistant transfer_playback failed: {e}")
            return False


# =============================================================================
# Lifecycle Management (Non-blocking connection startup/shutdown)
# =============================================================================

def start_background_connection():
    """
    Start background connection task at app startup.
    
    Call this when MA is configured to begin connection in background.
    Non-blocking - connection happens asynchronously.
    """
    global _connection_task, _connecting
    
    if not is_configured():
        return
    
    if _connecting or (_connection_task and not _connection_task.done()):
        return  # Already connecting
    
    _connecting = True
    _connection_task = asyncio.create_task(_background_connect())
    logger.debug("Started Music Assistant background connection task")


def stop_background_connection():
    """
    Cancel background connection task and disconnect client on shutdown.
    
    Call this on app exit for clean shutdown.
    """
    global _connection_task, _connecting, _listener_task, _client, _connected, _listening
    
    _connecting = False
    
    # Cancel background connection task
    if _connection_task and not _connection_task.done():
        _connection_task.cancel()
        _connection_task = None
        logger.debug("Cancelled Music Assistant background connection task")
    
    # Cancel listener task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        _listener_task = None
        logger.debug("Cancelled Music Assistant listener task")
    
    # Disconnect client (sync wrapper - schedules async disconnect)
    if _client:
        try:
            # Try to get running loop and schedule disconnect
            loop = asyncio.get_running_loop()
            loop.create_task(_disconnect_client())
        except RuntimeError:
            # No running loop - we're in sync context during shutdown
            # Client will be garbage collected, which triggers cleanup
            pass
        _client = None
        _connected = False
        _listening = False
        logger.debug("Music Assistant client marked for disconnect")


async def _disconnect_client():
    """Helper to disconnect client asynchronously."""
    global _client
    if _client:
        try:
            await _client.disconnect()
            logger.debug("Music Assistant client disconnected")
        except Exception as e:
            logger.debug(f"Error disconnecting MA client: {e}")

