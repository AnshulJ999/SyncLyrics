"""
Integration Test for All Lyrics Providers
Tests all available providers with a set of known songs
"""
import sys
import os
from pathlib import Path
import logging
from datetime import datetime
from typing import List, Dict, Any
import threading
from functools import wraps

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

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

def test_single_provider(provider_class: Any, song: Dict[str, str], timeout: int = 10) -> Dict[str, Any]:
    """Test a single provider with timeout"""
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
    
    def run_test():
        try:
            lyrics = provider.get_lyrics(song["artist"], song["title"])
            if lyrics:
                result["success"] = True
                result["lyrics_count"] = len(lyrics)
                logger.info(f"✓ {provider_name}: Found {len(lyrics)} lyrics")
            else:
                logger.info(f"✗ {provider_name}: No lyrics found")
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"❌ {provider_name} error: {e}")
    
    # Run test with timeout
    thread = threading.Thread(target=run_test)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    
    if thread.is_alive():
        result["error"] = f"Timeout after {timeout}s"
        logger.error(f"⚠ {provider_name}: Operation timed out")
        return result
        
    result["time_taken"] = (datetime.now() - start_time).total_seconds()
    return result

def run_tests():
    """Run tests for all providers and songs"""
    providers = [
        LRCLIBProvider,    # Try LRCLIB first
        NetEaseProvider,   # Then NetEase
        QQMusicProvider,   # Then QQ Music
        SpotifyLyrics      # Spotify as last resort
    ]
    
    all_results = []
    
    for song in TEST_SONGS:
        logger.info(f"\n=== Testing: {song['artist']} - {song['title']} ===")
        song_results = []
        
        for provider_class in providers:
            result = test_single_provider(provider_class, song)
            song_results.append(result)
            
            # Print immediate feedback
            status = "✓" if result["success"] else "✗"
            logger.info(
                f"{status} {result['provider']}: "
                f"Time: {result['time_taken']:.2f}s"
            )
            if result["error"]:
                logger.error(f"  Error: {result['error']}")
                
        all_results.append({
            "song": f"{song['artist']} - {song['title']}",
            "results": song_results
        })
        
    return all_results

if __name__ == "__main__":
    try:
        results = run_tests()
        
        # Print summary
        logger.info("\n=== Test Summary ===")
        for song_result in results:
            logger.info(f"\n{song_result['song']}:")
            working_providers = [
                r["provider"] for r in song_result["results"] 
                if r["success"]
            ]
            if working_providers:
                logger.info(f"Working providers: {', '.join(working_providers)}")
            else:
                logger.info("No working providers found")
                
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
    except Exception as e:
        logger.error(f"Test framework error: {e}") 