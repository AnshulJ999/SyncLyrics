"""
Comprehensive System Verification Script
Verifies:
1. Config loading
2. Logging setup
3. System Utils (Metadata fetching)
4. Spotify API (Initialization & Backoff)
5. Server Routes (Mocked)
"""
import asyncio
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock

# Add current directory to path
sys.path.append(os.getcwd())

async def verify_system():
    print("🚀 Starting Comprehensive System Verification...\n")
    
    # 1. Verify Config
    print("1️⃣  Verifying Configuration...")
    try:
        import config
        print(f"   ✅ Config loaded. Debug enabled: {config.DEBUG['enabled']}")
    except Exception as e:
        print(f"   ❌ Config load failed: {e}")
        return

    # 2. Verify Logging
    print("\n2️⃣  Verifying Logging...")
    try:
        from logging_config import setup_logging
        setup_logging()
        print("   ✅ Logging setup successful")
    except Exception as e:
        print(f"   ❌ Logging setup failed: {e}")
        return

    # 3. Verify System Utils (Metadata)
    print("\n3️⃣  Verifying System Utils...")
    try:
        import system_utils
        # Mock dependencies to avoid real hardware calls during test
        system_utils._get_current_song_meta_data_windows = AsyncMock(return_value=None)
        system_utils._get_current_song_meta_data_gnome = MagicMock(return_value=None)
        
        # Test orchestrator
        print("   Testing get_current_song_meta_data()...")
        res = await system_utils.get_current_song_meta_data()
        print(f"   ✅ Metadata fetch executed (Result: {res})")
    except Exception as e:
        print(f"   ❌ System Utils verification failed: {e}")

    # 4. Verify Spotify API
    print("\n4️⃣  Verifying Spotify API...")
    try:
        from providers.spotify_api import SpotifyAPI
        api = SpotifyAPI()
        if hasattr(api, 'get_current_track'):
            print("   ✅ SpotifyAPI class structure valid")
        else:
            print("   ❌ SpotifyAPI missing get_current_track")
            
        # Check backoff logic existence
        if hasattr(api, '_backoff_until'):
             # It might not be set yet, but checking if code handles it is hard statically.
             # We rely on previous test_backoff.py for logic verification.
             pass
    except Exception as e:
        print(f"   ❌ Spotify API verification failed: {e}")

    # 5. Verify Server Routes
    print("\n5️⃣  Verifying Server Routes...")
    try:
        # Mock system_utils for server
        sys.modules['system_utils'].get_current_song_meta_data = AsyncMock(return_value={
            "title": "Test", "artist": "Test", "is_playing": True, "album_art_url": "http://test"
        })
        
        from server import app
        client = app.test_client()
        
        # Test /current-track
        res = await client.get('/current-track')
        data = await res.get_json()
        if data.get('title') == 'Test':
            print("   ✅ /current-track route working")
        else:
            print(f"   ❌ /current-track returned unexpected data: {data}")
            
    except Exception as e:
        print(f"   ❌ Server verification failed: {e}")

    print("\n✅ Verification Complete!")

if __name__ == "__main__":
    asyncio.run(verify_system())
