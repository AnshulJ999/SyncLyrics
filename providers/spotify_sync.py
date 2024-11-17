"""
Spotify synchronization for lyrics timing
Handles playback state and position tracking
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

from typing import Optional, Callable, Dict, Any
from time import time
import asyncio
from dataclasses import dataclass
from providers.spotify_api import SpotifyAPI
from logging_config import get_logger

logger = get_logger(__name__)

@dataclass
class TrackState:
    """Track state data structure"""
    id: Optional[str] = None
    position: float = 0
    timestamp: float = 0
    is_playing: bool = False
    duration: float = 0

class SpotifyLyricsSync:
    def __init__(self, spotify_client: SpotifyAPI) -> None:
        """Initialize sync with Spotify client"""
        # Ensure spotify_client is an instance of SpotifyAPI
        # from providers.spotify_api import SpotifyAPI
        if not isinstance(spotify_client, SpotifyAPI):
            raise ValueError("Invalid Spotify client - must be SpotifyAPI instance")
            
        self.spotify = spotify_client
        self.state = TrackState()
        self._last_update = time()
        self._sync_task = None
        logger.info("SpotifyLyricsSync initialized")
        
    async def initialize(self):
        """Initialize sync system"""
        try:
            # Initialize state from current track
            current_track = await self.spotify.get_current_track()
            if current_track:
                self.state = TrackState(
                    id=current_track.get('track_id'),
                    position=current_track['progress_ms'] / 1000,
                    timestamp=time(),
                    is_playing=current_track.get('is_playing', False),
                    duration=current_track['duration_ms'] / 1000
                )
                logger.info(f"Initial position: {self.state.position:.2f}s")
                
            # Start sync loop
            await self._setup_tracker()
            logger.info("Using API polling for position updates")
        except Exception as e:
            logger.error(f"Sync init error: {e}")
            raise
            
    async def _setup_tracker(self):
        """Setup position tracking"""
        await self._sync_state()  # Initial sync
        self._sync_task = asyncio.create_task(self._sync_loop())
        
    async def _sync_loop(self):
        """Periodic state sync"""
        while True:
            try:
                await self._sync_state()
                await asyncio.sleep(3)  # Sync every 3s
            except Exception as e:
                logger.error(f"Sync error: {e}")
                await asyncio.sleep(5)  # Back off on error
                
    async def _sync_state(self):
        """Sync with Spotify API"""
        try:
            current_track = await self.spotify.get_current_track()
            if not current_track:
                self.state = TrackState()
                return
                
            self.state = TrackState(
                id=current_track.get('track_id'),
                position=current_track['progress_ms'] / 1000,
                timestamp=time(),
                is_playing=current_track.get('is_playing', False),
                duration=current_track['duration_ms'] / 1000
            )
            self._last_update = time()
            
            logger.debug(f"State synced - Position: {self.state.position:.2f}s (Playing: {self.state.is_playing})")
            
        except Exception as e:
            logger.error(f"State sync failed: {e}")
        
    def get_position(self) -> float:
        """Get current position with time tracking"""
        if not self.state.is_playing:
            return self.state.position
            
        # Calculate elapsed time since last update
        now = time()
        elapsed = now - self._last_update
        current_position = self.state.position + elapsed
        
        # Don't exceed track duration
        if current_position >= self.state.duration:
            asyncio.create_task(self._sync_state())
            return self.state.position
            
        return current_position        