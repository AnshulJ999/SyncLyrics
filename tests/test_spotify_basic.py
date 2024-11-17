import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

import pytest
from providers.spotify_api import SpotifyAPI
from providers.spotify_sync import SpotifyLyricsSync
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_spotify_basic():
    """Basic test to verify Spotify API and sync are working"""
    try:
        # 1. Test Spotify API
        logger.info("Testing Spotify API...")
        spotify = SpotifyAPI()
        track = spotify.get_current_track()
        
        if track:
            logger.info(f"[OK] Current track: {track['title']} - {track['artist']}")
            logger.info(f"Track progress: {track['progress_ms']/1000:.1f}s / {track['duration_ms']/1000:.1f}s")
        else:
            logger.warning("No track playing")
            
        # 2. Test Spotify Sync
        logger.info("\nTesting Spotify Sync...")
        sync = SpotifyLyricsSync(spotify)
        await sync.initialize()
        
        # Get position and compare with track progress
        position = sync.get_position()
        expected_position = track['progress_ms'] / 1000  # Convert to seconds
        
        logger.info(f"Expected position: {expected_position:.2f}s")
        logger.info(f"Actual position: {position:.2f}s")
        
        # Check if position is within 1 second of expected
        if abs(position - expected_position) > 1:
            logger.error(f"Position mismatch! Difference: {abs(position - expected_position):.2f}s")
            return False
            
        logger.info("[OK] Position tracking working correctly")
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_spotify_basic()) 