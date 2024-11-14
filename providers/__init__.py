"""
Lyrics Providers Package
This package contains different providers for fetching synchronized lyrics.
"""

from .base import LyricsProvider
from .lrclib import LRCLIBProvider
from .netease import NetEaseProvider

# List of all available providers
available_providers = [
    LRCLIBProvider,
    NetEaseProvider
]

__all__ = [
    'LyricsProvider',
    'LRCLIBProvider',
    'NetEaseProvider',
    'available_providers'
] 