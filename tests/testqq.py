"""
QQ Music Lyrics Provider Test Script
Tests the QQ Music provider with user input
"""

import logging
from providers.qq import QQMusicProvider

def format_time(seconds: float) -> str:
    """Format seconds into MM:SS.mm"""
    minutes = int(seconds // 60)
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:05.2f}"

def test_qq_lyrics():
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    provider = QQMusicProvider()
    
    while True:
        print("\n=== QQ Music Lyrics Test ===")
        artist = input("\nEnter artist name (or 'quit' to exit): ").strip()
        
        if artist.lower() == 'quit':
            break
            
        title = input("Enter song title: ").strip()
        
        print(f"\nSearching for: {artist} - {title}")
        lyrics = provider.get_lyrics(artist, title)
        
        if lyrics:
            print("\nFound lyrics! First 10 lines:")
            print("-" * 50)
            for time, text in lyrics[:10]:
                print(f"[{format_time(time)}] {text}")
            print("-" * 50)
            print(f"Total lines: {len(lyrics)}")
        else:
            print("No lyrics found")
        
        print("\nPress Enter to try another song...")
        input()

if __name__ == "__main__":
    try:
        test_qq_lyrics()
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"An error occurred: {e}")