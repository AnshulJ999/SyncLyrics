"""
Lyrics Providers Package
This package contains different providers for fetching synchronized lyrics.
"""

from .base import LyricsProvider
from .lrclib import LRCLIBProvider
from .netease import NetEaseProvider
from .spotify_lyrics import SpotifyLyrics

# List of all available providers
available_providers = [
    LRCLIBProvider,
    NetEaseProvider,
    SpotifyLyrics
]

__all__ = [
    'LyricsProvider',
    'LRCLIBProvider',
    'NetEaseProvider',
    'SpotifyLyrics',
    'available_providers'
] 