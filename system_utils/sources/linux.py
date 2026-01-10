"""
Linux MPRIS metadata source via playerctl.

This source provides metadata from any MPRIS-compatible media player on Linux,
including Spotify, VLC, Firefox, Rhythmbox, and many others.

Requirements:
- Linux operating system
- playerctl installed: sudo apt install playerctl

Features:
- Metadata from any MPRIS player (Spotify, VLC, Firefox, etc.)
- Playback controls (play, pause, next, previous, seek)
- Position and duration tracking
- Auto-enrichment with album art, colors, artist images
"""
import asyncio
import subprocess
import time
import platform
from typing import Optional, Dict, Any
from .base import BaseMetadataSource, SourceConfig, SourceCapability
from ..helpers import _normalize_track_id
from logging_config import get_logger

logger = get_logger(__name__)


class LinuxSource(BaseMetadataSource):
    """
    Linux MPRIS integration via playerctl.
    
    This source uses playerctl to get metadata from any MPRIS-compatible
    media player running on Linux (Spotify, VLC, Firefox, etc.).
    
    Supports:
    - Metadata retrieval (artist, title, album, art, position, duration)
    - Playback controls (play, pause, next, previous)
    - Seek to position
    
    Configuration:
    - media_source.linux.enabled: Enable/disable this source
    - media_source.linux.priority: Priority (lower = checked first)
    - system.linux.paused_timeout: Seconds before paused source expires
    """
    
    def __init__(self):
        super().__init__()
        self._playerctl_available: Optional[bool] = None
    
    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="linux",
            display_name="Linux (MPRIS)",
            platforms=["Linux"],  # Only available on Linux
            default_enabled=True,  # Enabled by default on Linux
            default_priority=1,    # High priority (main source on Linux)
            paused_timeout=600,    # 10 minutes
        )
    
    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (
            SourceCapability.METADATA |
            SourceCapability.PLAYBACK_CONTROL |
            SourceCapability.SEEK |
            SourceCapability.DURATION
        )
    
    def is_available(self) -> bool:
        """
        Check if we're on Linux and playerctl is installed.
        
        Returns False on non-Linux platforms or if playerctl is not found.
        Caches the result to avoid repeated subprocess calls.
        """
        # Platform check first (fast)
        if platform.system() != "Linux":
            return False
        
        # Check playerctl installation (cache result)
        if self._playerctl_available is None:
            try:
                result = subprocess.run(
                    ["playerctl", "--version"],
                    capture_output=True,
                    timeout=2
                )
                self._playerctl_available = result.returncode == 0
                if self._playerctl_available:
                    version = result.stdout.decode().strip()
                    logger.debug(f"playerctl found: {version}")
                else:
                    logger.warning("playerctl not available (command failed)")
            except FileNotFoundError:
                self._playerctl_available = False
                logger.warning("playerctl not installed. Install with: sudo apt install playerctl")
            except subprocess.TimeoutExpired:
                self._playerctl_available = False
                logger.warning("playerctl check timed out")
        
        return self._playerctl_available
    
    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata from playerctl.
        
        Runs blocking subprocess in executor to avoid blocking event loop.
        Returns None if no player is active or an error occurs.
        """
        loop = asyncio.get_running_loop()
        
        try:
            # Run blocking subprocess in executor
            result = await loop.run_in_executor(None, self._fetch_playerctl_metadata)
            
            if result:
                # Update last active time if playing
                if result.get("is_playing"):
                    self._last_active_time = time.time()
                result["last_active_time"] = self._last_active_time
            
            return result
            
        except Exception as e:
            logger.debug(f"Linux metadata fetch failed: {e}")
            return None
    
    def _fetch_playerctl_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Blocking playerctl call (run in executor).
        
        This is the actual subprocess call that gets metadata from MPRIS.
        """
        try:
            # Get status first
            status_result = subprocess.run(
                ["playerctl", "status"],
                capture_output=True,
                text=True,
                timeout=2
            )
            status = status_result.stdout.strip().lower()
            
            # Only proceed if playing or paused
            if status not in ("playing", "paused"):
                return None
            
            # Get metadata in one call using format string
            # Fields: artist, title, album, art URL, position (μs), duration (μs)
            metadata_result = subprocess.run(
                ["playerctl", "metadata", "--format",
                 "{{artist}}\n{{title}}\n{{album}}\n{{mpris:artUrl}}\n{{position}}\n{{mpris:length}}"],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            lines = metadata_result.stdout.strip().split("\n")
            if len(lines) < 2:
                return None
            
            # Parse fields (some may be empty)
            artist = lines[0] if lines[0] else ""
            title = lines[1] if len(lines) > 1 else ""
            album = lines[2] if len(lines) > 2 and lines[2] else None
            art_url = lines[3] if len(lines) > 3 and lines[3] else None
            
            # Position in microseconds → seconds
            position = 0
            if len(lines) > 4 and lines[4]:
                try:
                    position = int(lines[4]) / 1_000_000
                except ValueError:
                    pass
            
            # Duration in microseconds → milliseconds
            duration_ms = None
            if len(lines) > 5 and lines[5]:
                try:
                    duration_ms = int(lines[5]) // 1000
                except ValueError:
                    pass
            
            # Must have at least artist or title
            if not artist and not title:
                return None
            
            return {
                "track_id": _normalize_track_id(artist, title),
                "artist": artist,
                "artist_name": artist,  # For display consistency with other sources
                "title": title,
                "album": album,
                "album_art_url": art_url,
                "position": position,
                "duration_ms": duration_ms,
                "is_playing": status == "playing",
                "source": "linux",
                "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            }
            
        except subprocess.TimeoutExpired:
            logger.debug("playerctl timed out")
            return None
        except FileNotFoundError:
            # playerctl not installed
            self._playerctl_available = False
            return None
        except Exception as e:
            logger.debug(f"playerctl error: {e}")
            return None
    
    # === Playback Controls ===
    
    async def toggle_playback(self) -> bool:
        """Toggle play/pause via playerctl."""
        return await self._run_playerctl("play-pause")
    
    async def play(self) -> bool:
        """Resume playback via playerctl."""
        return await self._run_playerctl("play")
    
    async def pause(self) -> bool:
        """Pause playback via playerctl."""
        return await self._run_playerctl("pause")
    
    async def next_track(self) -> bool:
        """Skip to next track via playerctl."""
        return await self._run_playerctl("next")
    
    async def previous_track(self) -> bool:
        """Skip to previous track via playerctl."""
        return await self._run_playerctl("previous")
    
    async def seek(self, position_ms: int) -> bool:
        """
        Seek to position via playerctl.
        
        Args:
            position_ms: Target position in milliseconds
            
        Note: playerctl position command expects seconds for absolute seek.
        """
        # Convert milliseconds to seconds (playerctl uses seconds)
        position_seconds = position_ms / 1000
        return await self._run_playerctl("position", str(position_seconds))
    
    async def _run_playerctl(self, *args) -> bool:
        """
        Run a playerctl command asynchronously.
        
        Runs in executor to avoid blocking event loop.
        
        Args:
            *args: Command arguments (e.g., "play-pause", "next")
            
        Returns:
            True if command succeeded, False otherwise
        """
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["playerctl", *args],
                    capture_output=True,
                    timeout=2
                )
            )
            success = result.returncode == 0
            if not success:
                logger.debug(f"playerctl {args[0]} failed: {result.stderr.decode().strip()}")
            return success
        except subprocess.TimeoutExpired:
            logger.debug(f"playerctl {args[0]} timed out")
            return False
        except subprocess.SubprocessError as e:
            # Expected subprocess errors (CalledProcessError, etc.)
            logger.debug(f"playerctl {args[0]} subprocess error: {e}")
            return False
        except Exception as e:
            # Unexpected error - log at warning level for visibility
            logger.warning(f"playerctl {args[0]} unexpected error: {e}", exc_info=True)
            return False
