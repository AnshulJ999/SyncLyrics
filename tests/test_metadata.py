"""
Test script to verify standardized metadata structure
"""
import asyncio
import sys
sys.path.insert(0, '.')

from system_utils import get_current_song_meta_data

async def test_metadata():
    print("Testing standardized metadata structure...\n")
    
    metadata = await get_current_song_meta_data()
    
    if metadata:
        print("✅ Successfully retrieved metadata\n")
        print("Metadata structure:")
        for key, value in metadata.items():
            print(f"  - {key}: {value}")
        
        # Verify all expected fields exist
        expected_fields = ['artist', 'title', 'album', 'position', 'duration_ms', 
                          'colors', 'album_art_url', 'is_playing', 'source']
        
        missing_fields = [field for field in expected_fields if field not in metadata]
        
        if missing_fields:
            print(f"\n❌ Missing fields: {missing_fields}")
        else:
            print(f"\n✅ All expected fields present")
            
        print(f"\n📊 Source: {metadata.get('source')}")
        print(f"🎵 Track: {metadata.get('artist')} - {metadata.get('title')}")
        if metadata.get('album'):
            print(f"💿 Album: {metadata.get('album')}")
        if metadata.get('album_art_url'):
            print(f"🖼️  Album Art: {metadata.get('album_art_url')[:50]}...")
        if metadata.get('duration_ms'):
            duration_sec = metadata.get('duration_ms') / 1000
            print(f"⏱️  Duration: {duration_sec:.1f}s")
            
    else:
        print("⚠️  No track currently playing")

if __name__ == "__main__":
    asyncio.run(test_metadata())
