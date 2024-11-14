"""
Integration Test for All Lyrics Providers
Tests all available providers with a set of known songs
"""

import logging
import sys
import os
from typing import List, Dict, Any
from datetime import datetime

# Add parent directory to path to import providers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    SpotifyLyrics,
    NetEaseProvider,
    LRCLIBProvider,
    QQMusicProvider
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test cases - known songs with lyrics
TEST_SONGS = [
    {
        "artist": "Erra",
        "title": "Snowblood",
        "expected_providers": ["QQ Music", "LRCLIB"]  # Providers expected to have lyrics
    },
    {
        "artist": "周杰伦",
        "title": "稻香",
        "expected_providers": ["NetEase", "QQ Music"]
    },
    {
        "artist": "Taylor Swift",
        "title": "Shake It Off",
        "expected_providers": ["Spotify", "LRCLIB", "NetEase", "QQ Music"]
    }
]

def format_time(seconds: float) -> str:
    """Format seconds to MM:SS.mm"""
    minutes = int(seconds // 60)
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:05.2f}"

def test_provider(provider_class: Any, song: Dict[str, str]) -> Dict[str, Any]:
    """Test a single provider with a song"""
    provider = provider_class()
    provider_name = provider.name
    start_time = datetime.now()
    
    result = {
        "provider": provider_name,
        "success": False,
        "time_taken": 0,
        "lyrics_count": 0,
        "error": None
    }
    
    try:
        lyrics = provider.get_lyrics(song["artist"], song["title"])
        end_time = datetime.now()
        result["time_taken"] = (end_time - start_time).total_seconds()
        
        if lyrics:
            result["success"] = True
            result["lyrics_count"] = len(lyrics)
            # Sample first few lyrics
            logger.info(f"\nFirst few lines from {provider_name}:")
            for time, text in lyrics[:3]:
                logger.info(f"[{format_time(time)}] {text}")
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Error testing {provider_name}: {e}")
    
    return result

def run_integration_test():
    """Run integration test on all providers with all test songs"""
    providers = [SpotifyLyrics, NetEaseProvider, LRCLIBProvider, QQMusicProvider]
    results = []
    
    for song in TEST_SONGS:
        logger.info(f"\n=== Testing: {song['artist']} - {song['title']} ===")
        song_results = []
        
        for provider_class in providers:
            result = test_provider(provider_class, song)
            song_results.append(result)
            
            status = "✓" if result["success"] else "✗"
            logger.info(
                f"{status} {result['provider']}: "
                f"Found {result['lyrics_count']} lyrics in {result['time_taken']:.2f}s"
            )
            
            if result["error"]:
                logger.error(f"Error: {result['error']}")
        
        results.append({
            "song": f"{song['artist']} - {song['title']}",
            "results": song_results
        })
    
    return results

def print_summary(results: List[Dict[str, Any]]):
    """Print summary of all test results"""
    logger.info("\n=== Test Summary ===")
    
    for song_result in results:
        logger.info(f"\n{song_result['song']}:")
        successful_providers = [
            r["provider"] for r in song_result["results"] 
            if r["success"]
        ]
        logger.info(f"Found lyrics in: {', '.join(successful_providers)}")

if __name__ == "__main__":
    try:
        logger.info("Starting integration test of all providers...")
        results = run_integration_test()
        print_summary(results)
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}") 