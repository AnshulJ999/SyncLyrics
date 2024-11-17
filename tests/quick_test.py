import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 
import asyncio
from providers.spotify_api import SpotifyAPI
from providers.spotify_sync import SpotifyLyricsSync
import logging
from time import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test():
    print("\n=== Testing Spotify Integration ===")
    
    # 1. Test API
    spotify = SpotifyAPI()
    track = spotify.get_current_track()
    
    if not track:
        print("\n⚠️ No track detected! Please start playing something in Spotify")
        return
        
    print(f"\nCurrent Track: {track['artist']} - {track['title']}")
    print(f"Progress: {track['progress_ms']/1000:.1f}s / {track['duration_ms']/1000:.1f}s")
    print(f"Playback State: {'Playing' if track.get('is_playing') else 'Paused'}")
    
    if not track.get('is_playing'):
        print("\n⚠️ Track is paused! Please press play in Spotify")
        return
    
    # 2. Test Sync
    sync = SpotifyLyricsSync(spotify)
    await sync.initialize()
    
    # 3. Monitor position for 10 seconds
    start_time = time()
    initial_position = sync.get_position()
    print(f"\nStarting position: {initial_position:.2f}s")
    
    for _ in range(5):  # Check 5 times over 10 seconds
        await asyncio.sleep(2)
        position = sync.get_position()
        elapsed = time() - start_time
        print(f"Position after {elapsed:.1f}s: {position:.2f}s (delta: {position - initial_position:.2f}s)")
    
    if position > initial_position:
        print("\n✓ Position tracking working correctly!")
        print(f"Total position change: {position - initial_position:.2f}s")
    else:
        print("\n⚠️ Position not updating correctly")
        print("Please check if a song is actually playing")

if __name__ == "__main__":
    asyncio.run(test()) 