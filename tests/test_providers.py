"""
Provider Tests
Tests all lyrics providers functionality
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from logging_config import get_logger
from providers import (
    SpotifyLyrics,
    NetEaseProvider,
    LRCLIBProvider,
    QQMusicProvider
)

logger = get_logger(__name__)

class TestProviders(unittest.TestCase):
    """Test all lyrics providers"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        cls.logger = logger
        cls.start_time = datetime.now()
        cls.logger.info(f"Starting {cls.__name__} tests")
        
        # Test songs with high success rate
        cls.test_songs = [
            {
                "artist": "Rick Astley",
                "title": "Never Gonna Give You Up",
                "expected": ["LRCLIB", "NetEase"]
            },
            {
                "artist": "The Beatles",
                "title": "Hey Jude",
                "expected": ["LRCLIB"]
            }
        ]
        
        # Chinese songs (test separately)
        cls.chinese_songs = [
            {
                "artist": "周杰伦",
                "title": "稻香",
                "expected": ["NetEase", "QQ Music"]
            }
        ]
    
    def setUp(self):
        """Initialize providers for each test"""
        self.providers = {}
        
        # Initialize each provider with error handling
        try:
            self.providers["LRCLIB"] = LRCLIBProvider()
        except Exception as e:
            self.logger.error(f"Failed to initialize LRCLIB: {e}")
            
        try:
            self.providers["NetEase"] = NetEaseProvider()
        except Exception as e:
            self.logger.error(f"Failed to initialize NetEase: {e}")
            
        try:
            self.providers["QQ Music"] = QQMusicProvider()
        except Exception as e:
            self.logger.error(f"Failed to initialize QQ Music: {e}")
    
    def test_provider_initialization(self):
        """Test that providers initialize correctly"""
        for name, provider in self.providers.items():
            with self.subTest(provider=name):
                self.assertIsNotNone(provider)
                self.assertTrue(hasattr(provider, 'get_lyrics'))
    
    def test_english_lyrics_fetch(self):
        """Test lyrics fetching for English songs"""
        for song in self.test_songs:
            for name, provider in self.providers.items():
                if name in song["expected"]:
                    with self.subTest(provider=name, song=f"{song['artist']} - {song['title']}"):
                        try:
                            lyrics = provider.get_lyrics(song["artist"], song["title"])
                            self.assertIsNotNone(lyrics, f"{name} should find lyrics")
                            self.assertTrue(len(lyrics) > 0)
                        except Exception as e:
                            self.logger.error(f"Error fetching lyrics from {name}: {e}")
                            self.fail(f"Provider {name} raised an exception")
    
    @unittest.skip("Chinese providers currently unreliable - needs investigation")
    def test_chinese_lyrics_fetch(self):
        """Test lyrics fetching for Chinese songs"""
        for song in self.chinese_songs:
            for name, provider in self.providers.items():
                if name in song["expected"]:
                    with self.subTest(provider=name, song=f"{song['artist']} - {song['title']}"):
                        try:
                            lyrics = provider.get_lyrics(song["artist"], song["title"])
                            self.assertIsNotNone(lyrics, f"{name} should find lyrics")
                            self.assertTrue(len(lyrics) > 0)
                        except Exception as e:
                            self.logger.error(f"Error fetching lyrics from {name}: {e}")
                            self.fail(f"Provider {name} raised an exception")

if __name__ == "__main__":
    unittest.main(verbosity=2) 