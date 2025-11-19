"""
Test script to verify server endpoints
"""
import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock

# Mock system_utils and other dependencies to test server routes in isolation
sys.modules['system_utils'] = MagicMock()
sys.modules['lyrics'] = MagicMock()
sys.modules['state_manager'] = MagicMock()
sys.modules['config'] = MagicMock()
sys.modules['settings'] = MagicMock()
sys.modules['logging_config'] = MagicMock()
sys.modules['providers.spotify_api'] = MagicMock()

# Setup mocks
# IMPORTANT: get_current_song_meta_data must be an AsyncMock because server.py awaits it
sys.modules['system_utils'].get_current_song_meta_data = AsyncMock()
sys.modules['system_utils'].spotify_client = MagicMock()

# Import server after mocking
try:
    from server import app
except ImportError:
    print("⚠️  Could not import server app")
    sys.exit(1)

async def test_server():
    print("Testing server endpoints...\n")
    
    # Create test client
    client = app.test_client()
    
    # 1. Test /current-track
    print("1. Testing /current-track...")
    
    # Mock metadata response
    mock_meta = {
        "artist": "Test Artist",
        "title": "Test Title",
        "album_art_url": "http://example.com/art.jpg",
        "duration_ms": 180000,
        "is_playing": True
    }
    sys.modules['system_utils'].get_current_song_meta_data.return_value = mock_meta
    
    response = await client.get('/current-track')
    data = await response.get_json()
    
    if data.get('album_art_url') == "http://example.com/art.jpg":
        print("✅ /current-track returned album_art_url")
    else:
        print(f"❌ /current-track failed: {data}")

    # 2. Test /api/playback/next
    print("\n2. Testing /api/playback/next...")
    
    # Mock Spotify client
    mock_spotify = MagicMock()
    mock_spotify.initialized = True
    # These methods are awaited in server.py, so they must be AsyncMocks
    mock_spotify.next_track = AsyncMock()
    mock_spotify.get_current_track = AsyncMock(return_value={'is_playing': True})
    
    # Patch the get_spotify_client helper
    with patch('server.get_spotify_client', return_value=mock_spotify):
        response = await client.post('/api/playback/next')
        data = await response.get_json()
        
        if data.get('status') == 'success':
            print("✅ /api/playback/next returned success")
        else:
            print(f"❌ /api/playback/next failed: {data}")

if __name__ == "__main__":
    try:
        asyncio.run(test_server())
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
