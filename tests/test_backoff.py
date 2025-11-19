"""
Test script to verify Spotify API backoff logic
"""
import asyncio
import sys
import time
from unittest.mock import MagicMock, patch, AsyncMock

# Mock dependencies
sys.modules['system_utils'] = MagicMock()
sys.modules['config'] = MagicMock()
sys.modules['logging_config'] = MagicMock()

# Create a real-looking SpotifyException class for mocking
class MockSpotifyException(Exception):
    def __init__(self, http_status, code, msg, headers=None):
        self.http_status = http_status
        self.code = code
        self.msg = msg
        self.headers = headers or {}

# Mock spotipy module structure
mock_spotipy = MagicMock()
mock_spotipy.exceptions.SpotifyException = MockSpotifyException
sys.modules['spotipy'] = mock_spotipy
sys.modules['spotipy.oauth2'] = MagicMock()

# Import SpotifyAPI after mocking
from providers.spotify_api import SpotifyAPI

async def test_backoff():
    print("Testing Spotify API backoff logic...\n")
    
    # Initialize API
    api = SpotifyAPI()
    api.initialized = True
    api.sp = MagicMock()
    api._cache_enabled = False # Disable cache to force API calls
    
    # Mock current_playback to raise 429 exception
    error_429 = MockSpotifyException(
        http_status=429,
        code=429,
        msg="Rate limit exceeded",
        headers={'Retry-After': '2'}
    )
    
    # Configure the mock to raise the exception when called
    api.sp.current_playback.side_effect = error_429
    
    print("1. Triggering 429 Rate Limit...")
    await api.get_current_track()
    
    if hasattr(api, '_backoff_until'):
        wait_time = api._backoff_until - time.time()
        print(f"✅ Backoff triggered. Waiting for ~{wait_time:.1f}s")
        
        if 1.5 < wait_time < 2.5:
            print("✅ Retry-After header respected (2s)")
        else:
            print(f"❌ Incorrect wait time: {wait_time}")
    else:
        print("❌ Backoff NOT triggered")
        # Debug: check if exception was caught
        print(f"Request stats: {api.request_stats}")
        return

    print("\n2. Attempting request during backoff...")
    # Reset mock to verify it's NOT called
    api.sp.current_playback.reset_mock()
    
    await api.get_current_track()
    
    if not api.sp.current_playback.called:
        print("✅ Request skipped during backoff")
    else:
        print("❌ Request was made despite backoff!")

    print("\n3. Waiting for backoff to expire...")
    await asyncio.sleep(2.1)
    
    print("4. Attempting request after backoff...")
    # Remove side effect to allow success
    api.sp.current_playback.side_effect = None
    api.sp.current_playback.return_value = {
        'item': {'name': 'Test Song', 'artists': [{'name': 'Test Artist'}], 'album': {'name': 'Test Album', 'images': []}, 'id': '1', 'external_urls': {'spotify': 'url'}, 'duration_ms': 1000},
        'is_playing': True,
        'progress_ms': 0
    }
    
    result = await api.get_current_track()
    
    if api.sp.current_playback.called and result:
        print("✅ Request resumed successfully")
    else:
        print("❌ Request failed to resume")

if __name__ == "__main__":
    asyncio.run(test_backoff())
