"""
Pear Desktop (YouTube Music) Metadata Source

Fetches currently playing track info from Pear Desktop's API server.
Supports authentication via Bearer token and polls the song-info endpoint.
"""
import time
from typing import Optional

import httpx

from .base import BaseMetadataSource, SourceConfig, SourceCapability
from config import conf

import logging
logger = logging.getLogger(__name__)


class PearDesktopSource(BaseMetadataSource):

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="pear_desktop",
            display_name="Pear Desktop (YouTube Music)",
            platforms=["Windows", "Linux", "Darwin"],
            default_enabled=False,
            default_priority=5,
            paused_timeout=600,
        )

    @classmethod
    def capabilities(cls) -> SourceCapability:
        return SourceCapability.METADATA

    def __init__(self):
        super().__init__()
        self.base_url = conf("media_source.pear_desktop.base_url", "http://localhost:26538")
        self.client_id = "SyncLyrics-pear"
        self.token: Optional[str] = None
        self._last_active_time = 0.0
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0),
            limits=httpx.Limits(max_connections=5)
        )

    async def close(self):
        """Cleanup async resources."""
        await self._http_client.aclose()

    def is_available(self) -> bool:
        """Check if the source is available on this platform."""
        # Pear Desktop runs locally, so we assume available if configured.
        # Actual connectivity is verified during fetch.
        return True

    async def _get_token(self) -> Optional[str]:
        """Authenticate with Pear Desktop API and return the JWT token."""
        try:
            resp = await self._http_client.post(f"{self.base_url}/auth/{self.client_id}")
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("accessToken")
                return self.token
            logger.warning(f"Pear Desktop auth failed: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Pear Desktop auth request failed: {e}")
        return None

    async def _fetch_song_info(self) -> Optional[dict]:
        """Fetch current song info from Pear Desktop API."""
        if not self.token:
            if not await self._get_token():
                return None

        try:
            resp = await self._http_client.get(
                f"{self.base_url}/api/v1/song-info",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                self.token = None
                # Retry once with a fresh token
                if not await self._get_token():
                    return None
                resp = await self._http_client.get(
                    f"{self.base_url}/api/v1/song-info",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                if resp.status_code == 200:
                    return resp.json()
            return None
        except Exception as e:
            logger.debug(f"Pear Desktop fetch failed: {e}")
            return None

    async def get_metadata(self) -> Optional[dict]:
        """Get current track metadata from Pear Desktop."""
        data = await self._fetch_song_info()
        if not data:
            return None

        is_playing = not data.get("isPaused", True)
        if is_playing:
            self._last_active_time = time.time()

        title = data.get("title") or data.get("alternativeTitle", "")
        artist = data.get("artist", "")

        # If no title or artist, treat as no track
        if not title and not artist:
            return None

        track_id = f"{artist}_{title}"

        metadata = {
            "artist": artist,
            "title": title,
            "is_playing": is_playing,
            "track_id": track_id,
            "source": "pear_desktop",
            "last_active_time": self._last_active_time,
        }

        # Map optional fields from API response
        if "imageSrc" in data:
            metadata["album_art_url"] = data["imageSrc"]
        if "songDuration" in data:
            metadata["duration_ms"] = int(data["songDuration"] * 1000)
        if "elapsedSeconds" in data and "songDuration" in data:
            metadata["position"] = data["elapsedSeconds"]

        return metadata
