import asyncio
import os
import json
import shutil
from unittest.mock import MagicMock, AsyncMock
import sys

# Add parent directory to path to import lyrics
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import LYRICS, FEATURES, DATABASE_DIR
import lyrics

# Mock Providers
class MockProvider:
    def __init__(self, name, priority, delay=0, result="Lyrics"):
        self.name = name
        self.priority = priority
        self.delay = delay
        self.result = result
        self.enabled = True

    async def get_lyrics(self, artist, title):
        await asyncio.sleep(self.delay)
        return [self.result]

async def test_local_db():
    print("--- Testing Local DB ---")
    artist = "Test Artist"
    title = "Test Title"
    
    # Ensure clean state
    db_path = lyrics._get_db_path(artist, title)
    if os.path.exists(db_path):
        os.remove(db_path)
        
    # 1. Save Low Priority Provider
    print("Saving Low Priority (netease)...")
    lyrics._save_to_db(artist, title, ["NetEase Lyrics"], "netease")
    
    # Verify file exists and content
    with open(db_path, 'r') as f:
        data = json.load(f)
        assert "saved_lyrics" in data
        assert "netease" in data["saved_lyrics"]
        assert data["saved_lyrics"]["netease"] == ["NetEase Lyrics"]
        
    # 2. Save High Priority Provider (Merge)
    print("Saving High Priority (lrclib)...")
    lyrics._save_to_db(artist, title, ["LRCLib Lyrics"], "lrclib")
    
    # Verify merge
    with open(db_path, 'r') as f:
        data = json.load(f)
        assert "netease" in data["saved_lyrics"]
        assert "lrclib" in data["saved_lyrics"]
        assert data["saved_lyrics"]["lrclib"] == ["LRCLib Lyrics"]
        
    # 3. Load Best Provider
    # Mock providers list in lyrics module to include our mock providers
    # We need to mock the 'providers' list in lyrics.py to test _load_from_db priority logic
    # But _load_from_db uses the global 'providers' list which contains real instances.
    # We can just rely on the fact that LRCLib is priority 1 and NetEase is priority 3 in the real app.
    # The real providers list has: LRCLib(1), Spotify(2), NetEase(3), QQ(4)
    
    print("Loading from DB (Should get LRCLib)...")
    loaded = lyrics._load_from_db(artist, title)
    assert loaded == ["LRCLib Lyrics"]
    print("Local DB Test Passed!")

async def test_smart_race():
    print("\n--- Testing Smart Race ---")
    
    # Setup Mock Providers
    # Scenario 1: High Priority finishes fast -> Returns immediately
    print("Scenario 1: High Priority Fast")
    lyrics.providers = [
        MockProvider("lrclib", 1, delay=0.1, result="HQ Lyrics"),
        MockProvider("netease", 3, delay=0.5, result="LQ Lyrics")
    ]
    res = await lyrics._get_lyrics("A", "B")
    assert res == ["HQ Lyrics"]
    print("Scenario 1 Passed")
    
    # Scenario 2: Low Priority finishes fast, High Priority finishes within timeout
    print("Scenario 2: Low Priority Fast, High Priority within Grace Period")
    LYRICS["smart_race_timeout"] = 1.0
    lyrics.providers = [
        MockProvider("lrclib", 1, delay=0.5, result="HQ Lyrics"),
        MockProvider("netease", 3, delay=0.1, result="LQ Lyrics")
    ]
    res = await lyrics._get_lyrics("A", "B")
    assert res == ["HQ Lyrics"]
    print("Scenario 2 Passed")
    
    # Scenario 3: Low Priority finishes fast, High Priority times out
    print("Scenario 3: Low Priority Fast, High Priority Timeout")
    LYRICS["smart_race_timeout"] = 0.2
    lyrics.providers = [
        MockProvider("lrclib", 1, delay=1.0, result="HQ Lyrics"),
        MockProvider("netease", 3, delay=0.1, result="LQ Lyrics")
    ]
    res = await lyrics._get_lyrics("A", "B")
    assert res == ["LQ Lyrics"]
    print("Scenario 3 Passed")

async def main():
    # Enable features
    FEATURES["save_lyrics_locally"] = True
    FEATURES["parallel_provider_fetch"] = True
    
    await test_local_db()
    await test_smart_race()
    
    # Cleanup
    artist = "Test Artist"
    title = "Test Title"
    db_path = lyrics._get_db_path(artist, title)
    if os.path.exists(db_path):
        os.remove(db_path)
    print("\nAll Tests Passed!")

if __name__ == "__main__":
    asyncio.run(main())
