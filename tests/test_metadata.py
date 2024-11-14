import sys
import os
import asyncio
import logging

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from system_utils import get_current_song_meta_data
from state_manager import get_state, set_state

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_metadata_sources():
    """Test different metadata sources and priorities"""
    
    # Test default behavior
    print("\nTesting default behavior:")
    data = await get_current_song_meta_data()
    print("Default source:", data)
    
    # Test Spotify priority
    print("\nTesting Spotify priority:")
    state = get_state()
    state["MEDIA_SOURCE"] = {
        "sources": [
            {"name": "spotify", "enabled": True, "priority": 1},
            {"name": "windows_media", "enabled": True, "priority": 2}
        ]
    }
    set_state(state)
    data = await get_current_song_meta_data()
    print("Spotify priority:", data)
    
    # Test Windows Media priority
    print("\nTesting Windows Media priority:")
    state["MEDIA_SOURCE"] = {
        "sources": [
            {"name": "spotify", "enabled": True, "priority": 2},
            {"name": "windows_media", "enabled": True, "priority": 1}
        ]
    }
    set_state(state)
    data = await get_current_song_meta_data()
    print("Windows Media priority:", data)

if __name__ == "__main__":
    asyncio.run(test_metadata_sources())