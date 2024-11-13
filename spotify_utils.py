"""Spotify Web API integration and token management"""
import base64
import time
import json
import logging
import aiohttp
from typing import Optional, Dict, Any
from pathlib import Path

from config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    ROOT_DIR
)

logger = logging.getLogger(__name__)

class SpotifyTokenManager:
    def __init__(self):
        self.token_file = ROOT_DIR / ".spotify_token.json"
        self.client_id = SPOTIFY_CLIENT_ID
        self.client_secret = SPOTIFY_CLIENT_SECRET
        self.access_token = None
        self.token_expiry = 0
        self._load_cached_token()

    def _load_cached_token(self):
        """Load cached token if available and valid"""
        try:
            if self.token_file.exists():
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    if data['expiry'] > time.time():
                        self.access_token = data['token']
                        self.token_expiry = data['expiry']
                        logger.debug("Loaded cached Spotify token")
        except Exception as e:
            logger.error(f"Error loading cached token: {e}")

    def _save_token(self, token: str, expires_in: int):
        """Save token with expiry"""
        try:
            data = {
                'token': token,
                'expiry': time.time() + expires_in - 60  # 60 second buffer
            }
            with open(self.token_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Error saving token: {e}")

    async def get_token(self) -> Optional[str]:
        """Get valid access token, refreshing if necessary"""
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token

        try:
            auth = base64.b64encode(
                f"{self.client_id}:{self.client_secret}".encode()
            ).decode()

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://accounts.spotify.com/api/token',
                    headers={
                        'Authorization': f'Basic {auth}',
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    data={'grant_type': 'client_credentials'}
                ) as response:
                    
                    if response.status != 200:
                        logger.error("Failed to get Spotify token")
                        return None

                    data = await response.json()
                    self.access_token = data['access_token']
                    self.token_expiry = time.time() + data['expires_in'] - 60
                    self._save_token(self.access_token, data['expires_in'])
                    return self.access_token

        except Exception as e:
            logger.error(f"Error getting Spotify token: {e}")
            return None

class SpotifyWebAPI:
    def __init__(self):
        self.token_manager = SpotifyTokenManager()
        self.base_url = "https://api.spotify.com/v1"

    async def _make_request(self, endpoint: str) -> Optional[Dict]:
        """Make authenticated request to Spotify API"""
        token = await self.token_manager.get_token()
        if not token:
            logger.error("No valid token available")
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/{endpoint}",
                    headers={'Authorization': f'Bearer {token}'}
                ) as response:
                    
                    if response.status == 401:
                        # Token might be invalid, try to refresh
                        token = await self.token_manager.get_token()
                        if not token:
                            return None
                        # Retry request
                        async with session.get(
                            f"{self.base_url}/{endpoint}",
                            headers={'Authorization': f'Bearer {token}'}
                        ) as retry_response:
                            if retry_response.status != 200:
                                return None
                            return await retry_response.json()
                    
                    elif response.status != 200:
                        return None
                    
                    return await response.json()

        except Exception as e:
            logger.error(f"Error making Spotify API request: {e}")
            return None

    async def get_track(self, track_id: str) -> Optional[Dict]:
        """Get track information from Spotify"""
        return await self._make_request(f"tracks/{track_id}")

    async def search_track(self, artist: str, title: str) -> Optional[Dict]:
        """Search for a track"""
        query = f"track:{title} artist:{artist}"
        response = await self._make_request(
            f"search?q={query}&type=track&limit=1"
        )
        if response and response.get('tracks', {}).get('items'):
            return response['tracks']['items'][0]
        return None

spotify_api = SpotifyWebAPI()