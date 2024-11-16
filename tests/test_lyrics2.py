
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider
from providers.spotify_lyrics import SpotifyLyrics
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger('providers.spotify_lyrics').setLevel(logging.DEBUG)

def test_provider(provider, artist: str, title: str) -> bool:
    """Test a single provider"""
    try:
        lyrics = provider.get_lyrics(artist, title)
        success = lyrics is not None
        logger.info(f"{provider.name}: {'✓' if success else '✗'}")
        if success:
            logger.info(f"First line: {lyrics[0][1]}")
        return success
    except Exception as e:
        logger.error(f"Error testing {provider.name}: {e}")
        return False

def test_providers():
    """Test all providers with a known song"""
    
    # Test song
    artist = "Guiding Lights"
    title = "Skyharbor"
    
    providers = [
        LRCLIBProvider(),    # Priority 1
        NetEaseProvider(),    # Priority 2
        SpotifyLyrics()       # Priority 3
    ]
    
    results = []
    print(f"\nTesting providers with: {artist} - {title}\n")
    
    for provider in sorted(providers, key=lambda x: x.priority):
        success = test_provider(provider, artist, title)
        results.append(success)
    
    print(f"\nResults Summary:")
    print(f"Providers working: {sum(results)}/{len(results)}")

if __name__ == "__main__":
    test_providers()