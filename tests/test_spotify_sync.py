import asyncio
from pathlib import Path
import sys
from time import time
import pytest
from unittest.mock import Mock, patch
# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from providers.spotify_sync import SpotifyLyricsSync

@pytest.fixture
async def sync_manager():
    spotify_mock = Mock()
    manager = SpotifyLyricsSync(spotify_mock)
    await manager.initialize()
    return manager

async def test_position_tracking():
    """Test position interpolation"""
    manager = await sync_manager()
    
    # Mock initial state
    manager.state.position = 10.0
    manager.state.timestamp = time()
    manager.state.is_playing = True
    
    # Wait 100ms
    await asyncio.sleep(0.1)
    
    # Position should be ~10.1
    position = manager.get_position()
    assert 10.09 <= position <= 10.11

async def test_track_end():
    """Test track end handling"""
    manager = await sync_manager()
    
    # Set near end of track
    manager.state.position = 199.9
    manager.state.duration = 200.0
    manager.state.is_playing = True
    
    # Wait for end
    await asyncio.sleep(0.2)
    
    # Should trigger sync
    assert manager.spotify.current_playback.called 