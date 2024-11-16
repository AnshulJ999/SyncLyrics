"""
Debug Test Script for Spotify API
Tests each Spotify API function individually with detailed logging
"""
import sys
import os
from pathlib import Path
import logging
import time
import json
from typing import Dict, Any, Optional

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from providers.spotify_api import SpotifyAPI

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_api_health(spotify: SpotifyAPI) -> bool:
    """Test basic API connectivity"""
    try:
        logger.info("Testing API health...")
        response = spotify.sp._get("me/player", timeout=3)
        logger.debug(f"Health check response code: {response.status_code}")
        return response.status_code in [200, 204]
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return False

def test_current_track(spotify: SpotifyAPI) -> Optional[Dict[str, Any]]:
    """Test current track retrieval"""
    logger.info("\nTesting current track retrieval...")
    try:
        track = spotify.get_current_track()
        if track:
            logger.info("Current track data:")
            for key, value in track.items():
                logger.info(f"{key}: {value}")
            return track
        else:
            logger.warning("No track currently playing")
            return None
    except Exception as e:
        logger.error(f"Current track error: {e}")
        return None

def test_search(spotify: SpotifyAPI, artist: str, title: str) -> Optional[Dict[str, Any]]:
    """Test track search"""
    logger.info(f"\nTesting track search for: {artist} - {title}")
    try:
        track = spotify.search_track(artist, title)
        if track:
            logger.info("Search results:")
            for key, value in track.items():
                logger.info(f"{key}: {value}")
            return track
        else:
            logger.warning("No track found")
            return None
    except Exception as e:
        logger.error(f"Search error: {e}")
        return None

def main():
    """Run all tests"""
    try:
        # Initialize API
        logger.info("Initializing Spotify API...")
        spotify = SpotifyAPI()
        
        # Test API health
        if not test_api_health(spotify):
            logger.error("API health check failed - skipping remaining tests")
            return
            
        # Test current track
        current = test_current_track(spotify)
        
        # Test search with known songs
        test_songs = [
            ("Taylor Swift", "Shake It Off"),
            ("Ed Sheeran", "Shape of You"),
            ("Rick Astley", "Never Gonna Give You Up")
        ]
        
        for artist, title in test_songs:
            test_search(spotify, artist, title)
            time.sleep(1)  # Avoid rate limiting
            
        # If current track exists, test search with it
        if current:
            test_search(spotify, current['artist'], current['title'])
            
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}")

if __name__ == "__main__":
    main() 