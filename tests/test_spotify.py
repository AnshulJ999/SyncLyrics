"""
Test script for Spotify API integration
"""
import sys
import time
import os
import threading
from functools import wraps

# Add parent directory to path to import providers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    SpotifyLyrics,
    NetEaseProvider,
    LRCLIBProvider,
    QQMusicProvider
)

from providers.spotify_api import SpotifyAPI

class TimeoutError(Exception):
    """Raised when operation times out"""
    pass

def with_timeout(seconds=10):
    """Windows-compatible timeout decorator using threading"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = []
            error = []
            
            def target():
                try:
                    result.append(func(*args, **kwargs))
                except Exception as e:
                    error.append(e)
                    
            thread = threading.Thread(target=target)
            thread.daemon = True
            thread.start()
            thread.join(seconds)
            
            if thread.is_alive():
                raise TimeoutError(f"Operation timed out after {seconds} seconds")
            if error:
                raise error[0]
            return result[0] if result else None
            
        return wrapper
    return decorator

@with_timeout(10)
def test_spotify():
    # Initialize API
    print("Initializing Spotify API...")
    spotify = SpotifyAPI()
    
    # Test current track info
    print("\nTesting current track retrieval...")
    print("Please make sure you're playing something on Spotify")
    
    # Try 3 times with delays
    for i in range(3):
        print(f"\nAttempt {i+1}:")
        try:
            track = spotify.get_current_track()
            if track:
                print("✓ Successfully retrieved track info:")
                print(f"Title: {track['title']}")
                print(f"Artist: {track['artist']}")
                print(f"Album: {track['album']}")
                print(f"Progress: {track['progress_ms']/1000:.1f}s / {track['duration_ms']/1000:.1f}s")
                print(f"Track URL: {track['url']}")
                print(f"Album Art: {track['album_art']}")
            else:
                print("✗ No track currently playing")
        except TimeoutError:
            print("⚠ Operation timed out")
        except Exception as e:
            print(f"❌ Error: {e}")
        time.sleep(2)

if __name__ == "__main__":
    try:
        test_spotify()
    except KeyboardInterrupt:
        print("\nTest interrupted by user")
    except Exception as e:
        print(f"Test failed: {e}") 