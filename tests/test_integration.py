import asyncio
from pathlib import Path
import sys

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from lyrics import LyricsManager

async def test_lyrics_sync():
    """Test lyrics sync with Spotify"""
    lyrics_manager = LyricsManager()
    await lyrics_manager.initialize()
    
    # Test lyrics update
    lyrics = await lyrics_manager.update_lyrics()
    assert lyrics is not None
    
    # Test position tracking
    initial_position = lyrics_manager.spotify_sync.get_position()
    await asyncio.sleep(1)
    new_position = lyrics_manager.spotify_sync.get_position()
    assert new_position > initial_position 