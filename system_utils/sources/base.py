"""
Base class for plugin metadata sources.

To create a new source:
1. Create a new file in system_utils/sources/
2. Subclass BaseMetadataSource
3. Implement get_config(), capabilities(), get_metadata()
4. Add settings to settings.py for enabled/priority
5. Restart SyncLyrics - your source will auto-register!

See docs/Adding-New-Metadata-Sources.md for full documentation.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Flag, auto
import platform


class SourceCapability(Flag):
    """
    Capabilities a source can declare.
    
    Use bitwise OR to combine: METADATA | PLAYBACK_CONTROL | SEEK
    Check with bitwise AND: if source.capabilities() & SourceCapability.SEEK
    """
    NONE = 0
    METADATA = auto()           # Can fetch track metadata
    PLAYBACK_CONTROL = auto()   # Can play/pause/next/prev
    SEEK = auto()               # Can seek to position
    ALBUM_ART = auto()          # Provides album art URL directly
    ARTIST_ID = auto()          # Provides Spotify-compatible artist ID
    DURATION = auto()           # Provides track duration
    QUEUE = auto()              # Can provide playback queue


@dataclass
class SourceConfig:
    """
    Static configuration for a metadata source.
    
    This defines the source's identity and default settings.
    Runtime settings (enabled, priority) are read from settings.json.
    """
    name: str                              # Internal ID (lowercase, underscores, e.g., "music_assistant")
    display_name: str                      # Human-readable name for UI (e.g., "Music Assistant")
    platforms: List[str] = field(default_factory=lambda: ["Windows", "Linux", "Darwin"])
    default_enabled: bool = False          # Disabled by default (most plugins need configuration)
    default_priority: int = 10             # Lower number = higher priority (checked first)
    paused_timeout: int = 600              # Seconds before paused source expires (0 = never)
    requires_auth: bool = False            # Whether authentication/API keys are needed
    config_keys: List[str] = field(default_factory=list)  # Settings keys this source needs


class BaseMetadataSource(ABC):
    """
    Abstract base class for plugin metadata sources.
    
    Subclass this and implement the required methods to create a new source.
    Drop the file in system_utils/sources/ and it auto-registers on startup.
    
    Required methods:
        get_config() - Return static SourceConfig
        capabilities() - Return SourceCapability flags
        get_metadata() - Fetch current track info
        
    Optional methods:
        is_available() - Check if source can run (platform, dependencies)
        play(), pause(), toggle_playback() - Playback controls
        next_track(), previous_track() - Track navigation
        seek(position_ms) - Seek to position
        get_queue() - Get playback queue
    """
    
    def __init__(self):
        self._config = self.get_config()
        self._last_active_time: float = 0
    
    @classmethod
    @abstractmethod
    def get_config(cls) -> SourceConfig:
        """
        Return static source configuration.
        
        This is called once during discovery to get the source's identity
        and default settings.
        
        Returns:
            SourceConfig with name, display_name, platforms, etc.
        """
        pass
    
    @classmethod
    @abstractmethod
    def capabilities(cls) -> SourceCapability:
        """
        Return capabilities this source provides.
        
        Used by the frontend to show/hide controls (Like button, queue, etc.)
        and by server.py to route playback commands.
        
        Examples:
            # Metadata only (most basic)
            return SourceCapability.METADATA
            
            # Full controls
            return (SourceCapability.METADATA | 
                    SourceCapability.PLAYBACK_CONTROL | 
                    SourceCapability.SEEK)
        """
        pass
    
    @property
    def name(self) -> str:
        """Source name (convenience property)."""
        return self._config.name
    
    @property
    def enabled(self) -> bool:
        """
        Check if source is enabled.
        
        Reads from settings.json, falls back to default_enabled from config.
        """
        from config import conf
        result = conf(f"media_source.{self.name}.enabled")
        if result is None:
            return self._config.default_enabled
        # Handle string "true"/"false" from settings
        if isinstance(result, str):
            return result.lower() == "true"
        return bool(result)
    
    @property
    def priority(self) -> int:
        """
        Get source priority.
        
        Lower number = higher priority (checked first).
        Reads from settings.json, falls back to default_priority from config.
        """
        from config import conf
        result = conf(f"media_source.{self.name}.priority")
        if result is None:
            return self._config.default_priority
        try:
            return int(result)
        except (ValueError, TypeError):
            return self._config.default_priority
    
    @property
    def paused_timeout(self) -> int:
        """
        Get paused timeout in seconds.
        
        After this many seconds of being paused, the source is considered
        expired and other sources will be checked.
        0 = never expire.
        """
        from config import conf
        result = conf(f"system.{self.name}.paused_timeout")
        if result is None:
            return self._config.paused_timeout
        try:
            return int(result)
        except (ValueError, TypeError):
            return self._config.paused_timeout
    
    def is_available(self) -> bool:
        """
        Check if this source is currently available.
        
        Override to add:
        - Platform checks (e.g., Linux-only sources)
        - Dependency checks (e.g., is playerctl installed?)
        - Service availability (e.g., is Music Assistant running?)
        
        Default implementation checks if current platform is in config.platforms.
        
        Returns:
            True if source can be used, False otherwise
        """
        current_platform = platform.system()
        return current_platform in self._config.platforms
    
    @abstractmethod
    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Fetch current track metadata.
        
        This is the main method that SyncLyrics calls to get track info.
        Called every polling interval when the source is enabled and available.
        
        Returns:
            Dict with track info, or None if nothing playing / unavailable.
            
        Required fields:
            - artist: str - Artist name
            - title: str - Track title  
            - is_playing: bool - True if actively playing
            - source: str - Your source name (must match config.name)
            
        Recommended fields:
            - track_id: str - Normalized "Artist_Title" for change detection
                              (auto-generated if missing)
            - album: str - Album name (can be None for singles)
            - position: float - Seconds into track
            - duration_ms: int - Track duration in milliseconds
            - album_art_url: str - Remote URL (will be cached to local DB)
            - colors: tuple - e.g., ("#24273a", "#363b54"), extracted if missing
            
        Optional fields (for enhanced features):
            - id: str - Spotify track ID (enables Like button)
            - artist_id: str - Spotify artist ID (enables Visual Mode)
            - url: str - Web URL to open track externally
            - last_active_time: float - Timestamp of last activity (for timeout)
        """
        pass
    
    # === Optional Playback Controls ===
    # Implement these if your source supports them and include
    # PLAYBACK_CONTROL in capabilities()
    
    async def play(self) -> bool:
        """
        Resume playback.
        
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def pause(self) -> bool:
        """
        Pause playback.
        
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def toggle_playback(self) -> bool:
        """
        Toggle play/pause.
        
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def next_track(self) -> bool:
        """
        Skip to next track.
        
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def previous_track(self) -> bool:
        """
        Skip to previous track.
        
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def seek(self, position_ms: int) -> bool:
        """
        Seek to position.
        
        Args:
            position_ms: Target position in milliseconds
            
        Returns:
            True if successful, False otherwise
        """
        return False
    
    async def get_queue(self) -> Optional[Dict]:
        """
        Get playback queue.
        
        Returns:
            Queue data dict, or None if not supported
        """
        return None
