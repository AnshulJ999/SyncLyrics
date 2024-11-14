"""Test track info display functionality"""
import logging
from providers.spotify_lyrics import SpotifyLyrics

logging.basicConfig(level=logging.DEBUG)

def test_ui_elements():
    """Test if track info elements are working"""
    spotify = SpotifyLyrics()
    track = spotify.spotify.get_current_track()
    
    print("\nTesting Track Info Display:")
    print("1. Track data fetched:", "✓" if track else "✗")
    if track:
        print(f"- Title: {track.get('title')}")
        print(f"- Artist: {track.get('artist')}")
        print(f"- Album Art: {'✓' if track.get('album_art') else '✗'}")
        print(f"- URL: {'✓' if track.get('url') else '✗'}")

if __name__ == "__main__":
    test_ui_elements() 