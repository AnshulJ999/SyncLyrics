"""
macOS Now Playing metadata source via nowplaying-cli or AppleScript fallback.

This source provides metadata from any app that reports to macOS Control Center,
including Spotify, Apple Music, Firefox, VLC, and many others.

Primary method (nowplaying-cli):
- Install via: brew install nowplaying-cli
- Works with ALL media players that report to Control Center
- Uses private MediaRemote framework (may break on future macOS versions)

Fallback method (AppleScript):
- Works without any additional dependencies
- Limited to Music.app and Spotify only
- More stable across macOS updates

Features:
- Metadata from any Now Playing source (via nowplaying-cli)
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


class MacOSSource(BaseMetadataSource):
    """
    macOS Now Playing integration via nowplaying-cli or AppleScript fallback.
    
    This source uses nowplaying-cli to get metadata from any app that reports
    to macOS Control Center (Spotify, Apple Music, Firefox, VLC, etc.).
    
    When nowplaying-cli is not installed, falls back to AppleScript for
    Music.app and Spotify only.
    
    Supports:
    - Metadata retrieval (artist, title, album, position, duration)
    - Playback controls (play, pause, next, previous)
    - Seek to position
    
    Configuration:
    - media_source.macos.enabled: Enable/disable this source
    - media_source.macos.priority: Priority (lower = checked first)
    - system.macos.paused_timeout: Seconds before paused source expires
    """
    
    def __init__(self):
        super().__init__()
        self._nowplaying_cli_available: Optional[bool] = None
        self._applescript_available: Optional[bool] = None
    
    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="macos",
            display_name="macOS (Now Playing)",
            platforms=["Darwin"],  # Only available on macOS
            default_enabled=True,  # Enabled by default on macOS
            default_priority=1,    # High priority (main source on macOS)
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
        Check if we're on macOS and have either nowplaying-cli or AppleScript.
        
        Returns False on non-macOS platforms.
        AppleScript is always available on macOS, so this effectively just
        checks the platform.
        """
        # Platform check first (fast)
        if platform.system() != "Darwin":
            return False
        
        # Check nowplaying-cli installation (cache result)
        if self._nowplaying_cli_available is None:
            self._check_nowplaying_cli()
        
        # AppleScript is always available on macOS
        return True
    
    def _check_nowplaying_cli(self) -> None:
        """Check if nowplaying-cli is installed and cache the result."""
        try:
            result = subprocess.run(
                ["nowplaying-cli", "get", "title"],
                capture_output=True,
                timeout=2
            )
            # Even if nothing is playing, the command should succeed
            self._nowplaying_cli_available = result.returncode == 0
            if self._nowplaying_cli_available:
                logger.debug("nowplaying-cli found and available")
            else:
                logger.debug("nowplaying-cli returned non-zero exit code")
        except FileNotFoundError:
            self._nowplaying_cli_available = False
            logger.info("nowplaying-cli not installed. Install with: brew install nowplaying-cli")
        except subprocess.TimeoutExpired:
            self._nowplaying_cli_available = False
            logger.warning("nowplaying-cli check timed out")
        except Exception as e:
            self._nowplaying_cli_available = False
            logger.debug(f"nowplaying-cli check failed: {e}")
    
    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata from nowplaying-cli or AppleScript fallback.
        
        Runs blocking subprocess in executor to avoid blocking event loop.
        Returns None if no player is active or an error occurs.
        """
        loop = asyncio.get_running_loop()
        
        try:
            # Check nowplaying-cli availability if not cached
            if self._nowplaying_cli_available is None:
                await loop.run_in_executor(None, self._check_nowplaying_cli)
            
            # Try nowplaying-cli first if available
            if self._nowplaying_cli_available:
                result = await loop.run_in_executor(None, self._fetch_nowplaying_cli)
                if result:
                    # Update last active time if playing
                    if result.get("is_playing"):
                        self._last_active_time = time.time()
                    result["last_active_time"] = self._last_active_time
                    return result
            
            # Fall back to AppleScript
            result = await loop.run_in_executor(None, self._fetch_applescript)
            if result:
                if result.get("is_playing"):
                    self._last_active_time = time.time()
                result["last_active_time"] = self._last_active_time
            
            return result
            
        except Exception as e:
            logger.debug(f"macOS metadata fetch failed: {e}")
            return None
    
    def _fetch_nowplaying_cli(self) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata via nowplaying-cli (run in executor).
        
        Uses a single command to get all properties at once for efficiency.
        """
        try:
            # Get all needed properties in one call
            result = subprocess.run(
                ["nowplaying-cli", "get", "title", "artist", "album", 
                 "duration", "elapsedTime", "playbackRate"],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode != 0:
                return None
            
            lines = result.stdout.strip().split("\n")
            if len(lines) < 6:
                # Not enough data - probably nothing playing
                return None
            
            # Parse fields (empty strings for missing values)
            title = lines[0].strip() if lines[0].strip() else ""
            artist = lines[1].strip() if len(lines) > 1 and lines[1].strip() else ""
            album = lines[2].strip() if len(lines) > 2 and lines[2].strip() else None
            
            # Duration and elapsed time are in seconds (float)
            duration_sec = 0
            if len(lines) > 3 and lines[3].strip():
                try:
                    duration_sec = float(lines[3].strip())
                except ValueError:
                    pass
            
            position_sec = 0
            if len(lines) > 4 and lines[4].strip():
                try:
                    position_sec = float(lines[4].strip())
                except ValueError:
                    pass
            
            # playbackRate: 1.0 = playing, 0.0 = paused
            is_playing = False
            if len(lines) > 5 and lines[5].strip():
                try:
                    playback_rate = float(lines[5].strip())
                    is_playing = playback_rate > 0
                except ValueError:
                    pass
            
            # Must have at least artist or title
            if not artist and not title:
                return None
            
            # Convert duration to milliseconds for consistency with other sources
            duration_ms = int(duration_sec * 1000) if duration_sec > 0 else None
            
            return {
                "track_id": _normalize_track_id(artist, title),
                "artist": artist,
                "artist_name": artist,  # For display consistency
                "title": title,
                "album": album if album else None,
                "position": position_sec,
                "duration_ms": duration_ms,
                "is_playing": is_playing,
                "source": "macos",
                "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            }
            
        except subprocess.TimeoutExpired:
            logger.debug("nowplaying-cli timed out")
            return None
        except FileNotFoundError:
            # nowplaying-cli not installed
            self._nowplaying_cli_available = False
            return None
        except Exception as e:
            logger.debug(f"nowplaying-cli error: {e}")
            return None
    
    def _fetch_applescript(self) -> Optional[Dict[str, Any]]:
        """
        Fetch metadata via AppleScript fallback for Music.app and Spotify.
        
        Tries Music.app first, then Spotify.
        """
        # Try Music.app first
        result = self._fetch_music_app()
        if result:
            return result
        
        # Try Spotify
        return self._fetch_spotify()
    
    def _fetch_music_app(self) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Apple Music.app via AppleScript."""
        script = '''
        tell application "Music"
            if player state is playing or player state is paused then
                set trackName to name of current track
                set trackArtist to artist of current track
                set trackAlbum to album of current track
                set trackDuration to duration of current track
                set trackPosition to player position
                set isPlaying to (player state is playing)
                return trackName & "\\n" & trackArtist & "\\n" & trackAlbum & "\\n" & trackDuration & "\\n" & trackPosition & "\\n" & isPlaying
            end if
        end tell
        '''
        return self._run_applescript(script, "music")
    
    def _fetch_spotify(self) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Spotify via AppleScript."""
        script = '''
        tell application "Spotify"
            if player state is playing or player state is paused then
                set trackName to name of current track
                set trackArtist to artist of current track
                set trackAlbum to album of current track
                set trackDuration to duration of current track
                set trackPosition to player position
                set isPlaying to (player state is playing)
                return trackName & "\\n" & trackArtist & "\\n" & trackAlbum & "\\n" & trackDuration & "\\n" & trackPosition & "\\n" & isPlaying
            end if
        end tell
        '''
        return self._run_applescript(script, "spotify")
    
    def _run_applescript(self, script: str, app_name: str) -> Optional[Dict[str, Any]]:
        """
        Execute AppleScript and parse the result.
        
        Args:
            script: AppleScript code to execute
            app_name: Name of the app for logging
            
        Returns:
            Parsed metadata dict or None
        """
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3
            )
            
            if result.returncode != 0:
                # App not running or no track playing
                return None
            
            output = result.stdout.strip()
            if not output:
                return None
            
            lines = output.split("\n")
            if len(lines) < 6:
                return None
            
            title = lines[0].strip()
            artist = lines[1].strip()
            album = lines[2].strip() if lines[2].strip() else None
            
            # Apple Music duration is in seconds, Spotify in milliseconds
            duration_ms = None
            try:
                duration_val = float(lines[3].strip())
                # Spotify returns duration in ms, Music.app in seconds
                # Heuristic: if > 10000, it's probably milliseconds
                if duration_val > 10000:
                    duration_ms = int(duration_val)
                else:
                    duration_ms = int(duration_val * 1000)
            except ValueError:
                pass
            
            # Position is in seconds for both
            position_sec = 0
            try:
                position_sec = float(lines[4].strip())
            except ValueError:
                pass
            
            # Parse boolean
            is_playing = lines[5].strip().lower() == "true"
            
            if not artist and not title:
                return None
            
            return {
                "track_id": _normalize_track_id(artist, title),
                "artist": artist,
                "artist_name": artist,
                "title": title,
                "album": album,
                "position": position_sec,
                "duration_ms": duration_ms,
                "is_playing": is_playing,
                "source": "macos",
                "colors": ("#24273a", "#363b54"),
            }
            
        except subprocess.TimeoutExpired:
            logger.debug(f"AppleScript ({app_name}) timed out")
            return None
        except Exception as e:
            logger.debug(f"AppleScript ({app_name}) error: {e}")
            return None
    
    # === Playback Controls ===
    
    async def toggle_playback(self) -> bool:
        """Toggle play/pause via nowplaying-cli or AppleScript."""
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("togglePlayPause")
        return await self._run_applescript_control("playpause")
    
    async def play(self) -> bool:
        """Resume playback."""
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("play")
        return await self._run_applescript_control("play")
    
    async def pause(self) -> bool:
        """Pause playback."""
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("pause")
        return await self._run_applescript_control("pause")
    
    async def next_track(self) -> bool:
        """Skip to next track."""
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("next")
        return await self._run_applescript_control("next track")
    
    async def previous_track(self) -> bool:
        """Skip to previous track."""
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("previous")
        return await self._run_applescript_control("previous track")
    
    async def seek(self, position_ms: int) -> bool:
        """
        Seek to position.
        
        Args:
            position_ms: Target position in milliseconds
            
        Note: nowplaying-cli expects seconds, so we convert.
        """
        position_seconds = position_ms / 1000
        
        if self._nowplaying_cli_available:
            return await self._run_nowplaying_cli("seek", str(position_seconds))
        
        # AppleScript fallback for seek
        return await self._run_applescript_seek(position_seconds)
    
    async def _run_nowplaying_cli(self, *args) -> bool:
        """
        Run a nowplaying-cli command asynchronously.
        
        Args:
            *args: Command arguments (e.g., "togglePlayPause", "next")
            
        Returns:
            True if command succeeded, False otherwise
        """
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["nowplaying-cli", *args],
                    capture_output=True,
                    timeout=2
                )
            )
            success = result.returncode == 0
            if not success:
                logger.debug(f"nowplaying-cli {args[0]} failed: {result.stderr.decode().strip()}")
            return success
        except subprocess.TimeoutExpired:
            logger.debug(f"nowplaying-cli {args[0]} timed out")
            return False
        except subprocess.SubprocessError as e:
            logger.debug(f"nowplaying-cli {args[0]} subprocess error: {e}")
            return False
        except Exception as e:
            logger.warning(f"nowplaying-cli {args[0]} unexpected error: {e}", exc_info=True)
            return False
    
    async def _run_applescript_control(self, command: str) -> bool:
        """
        Run a playback control command via AppleScript.
        
        Tries Music.app first, then Spotify.
        """
        loop = asyncio.get_running_loop()
        
        # Try Music.app
        script_music = f'tell application "Music" to {command}'
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e", script_music],
                    capture_output=True,
                    timeout=2
                )
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
        
        # Try Spotify
        script_spotify = f'tell application "Spotify" to {command}'
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e", script_spotify],
                    capture_output=True,
                    timeout=2
                )
            )
            return result.returncode == 0
        except Exception:
            return False
    
    async def _run_applescript_seek(self, position_seconds: float) -> bool:
        """Seek via AppleScript (Music.app or Spotify)."""
        loop = asyncio.get_running_loop()
        
        # Try Music.app
        script_music = f'tell application "Music" to set player position to {position_seconds}'
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e", script_music],
                    capture_output=True,
                    timeout=2
                )
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
        
        # Try Spotify
        script_spotify = f'tell application "Spotify" to set player position to {position_seconds}'
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e", script_spotify],
                    capture_output=True,
                    timeout=2
                )
            )
            return result.returncode == 0
        except Exception:
            return False
