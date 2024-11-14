"""Test the UI components and track info display"""
import logging
from providers.spotify_lyrics import SpotifyLyrics

logging.basicConfig(level=logging.DEBUG)

def test_track_info():
    """Test getting current track info"""
    spotify = SpotifyLyrics()
    track = spotify.spotify.get_current_track()
    print("\nCurrent Track Info:")
    print(f"Title: {track.get('title')}")
    print(f"Artist: {track.get('artist')}")
    print(f"Album Art: {track.get('album_art')}")
    print(f"URL: {track.get('url')}")

if __name__ == "__main__":
    test_track_info() 