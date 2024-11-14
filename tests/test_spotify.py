"""
Test script for Spotify API integration
"""
import sys
import time
import os

# from providers.spotify_api import SpotifyAPI

# Add parent directory to path to import providers
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import (
    SpotifyLyrics,
    NetEaseProvider,
    LRCLIBProvider,
    QQMusicProvider
)

from providers.spotify_api import SpotifyAPI

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
        
        time.sleep(2)

if __name__ == "__main__":
    test_spotify() 