"""Pytest configuration and shared fixtures"""
import pytest
from unittest.mock import Mock
from providers.spotify_api import SpotifyAPI
from providers.spotify_sync import SpotifyLyricsSync

@pytest.fixture
async def spotify_client():
    """Create a mock Spotify client"""
    client = Mock(spec=SpotifyAPI)
    client.get_current_track.return_value = {
        'id': 'test_track',
        'position': 0,
        'is_playing': True,
        'duration': 200
    }
    return client

@pytest.fixture
async def sync_manager(spotify_client):
    """Create a SpotifyLyricsSync instance"""
    manager = SpotifyLyricsSync(spotify_client)
    await manager.initialize()
    return manager 