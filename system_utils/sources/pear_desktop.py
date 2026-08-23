"""
Pear Desktop (YouTube Music) Metadata Source

Fetches currently playing track info from Pear Desktop's API server.
Authenticates with a Bearer token and polls the song-info endpoint.

Pear Desktop's API plugin must be enabled and reachable (default
http://localhost:26538). Disabled by default - enable it under Media settings.
"""
import asyncio
import time
from typing import Optional

import requests

from .base import BaseMetadataSource, SourceConfig, SourceCapability
from ..helpers import _normalize_track_id
from config import conf
from logging_config import get_logger

logger = get_logger(__name__)

# Connect fast, read patiently. A closed port on Windows is dropped rather
# than refused, so the connect timeout is what we actually pay when Pear
# isn't running - keep it short.
_TIMEOUT = (0.5, 5.0)

# metadata.py calls plugin.get_metadata() with no timeout around it, so a
# stalled source blocks the whole dispatch. Back off after a failure instead
# of paying the connect timeout on every single poll.
_RETRY_BACKOFF = 5.0


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
        return (SourceCapability.METADATA |
                SourceCapability.ALBUM_ART |
                SourceCapability.DURATION)

    def __init__(self):
        super().__init__()
        # 127.0.0.1 rather than localhost: localhost resolves to both ::1 and
        # 127.0.0.1, and a failed connect pays the timeout once per family.
        base = conf("media_source.pear_desktop.base_url", "http://127.0.0.1:26538")
        self.base_url = str(base).rstrip("/")
        self.client_id = "SyncLyrics-pear"
        self.token: Optional[str] = None
        self._next_attempt = 0.0
        # Session keeps the connection alive across polls
        self._session = requests.Session()

    def is_available(self) -> bool:
        """Pear Desktop runs locally; real connectivity is verified during fetch."""
        return True

    def _get_token(self) -> Optional[str]:
        """Authenticate and cache the bearer token. Blocking."""
        try:
            resp = self._session.post(
                f"{self.base_url}/auth/{self.client_id}", timeout=_TIMEOUT
            )
            if resp.status_code == 200:
                self.token = resp.json().get("accessToken")
                return self.token
            logger.warning(f"Pear Desktop auth failed: {resp.status_code}")
        except Exception as e:
            logger.debug(f"Pear Desktop auth request failed: {e}")
        return None

    def _fetch_song_info(self) -> Optional[dict]:
        """Fetch current song info, refreshing the token once on 401. Blocking."""
        if not self.token and not self._get_token():
            return None

        try:
            resp = self._session.get(
                f"{self.base_url}/api/v1/song-info",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=_TIMEOUT,
            )
            if resp.status_code == 401:
                self.token = None
                if not self._get_token():
                    return None
                resp = self._session.get(
                    f"{self.base_url}/api/v1/song-info",
                    headers={"Authorization": f"Bearer {self.token}"},
                    timeout=_TIMEOUT,
                )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            logger.debug(f"Pear Desktop fetch failed: {e}")
            return None

    async def get_metadata(self) -> Optional[dict]:
        """Get current track metadata from Pear Desktop."""
        if time.time() < self._next_attempt:
            return None

        data = await asyncio.to_thread(self._fetch_song_info)
        if not data:
            self._next_attempt = time.time() + _RETRY_BACKOFF
            return None
        self._next_attempt = 0.0

        title = data.get("title") or data.get("alternativeTitle", "")
        artist = data.get("artist", "")

        # No title and no artist means nothing is loaded
        if not title and not artist:
            return None

        is_playing = not data.get("isPaused", True)
        if is_playing:
            self._last_active_time = time.time()

        metadata = {
            "track_id": _normalize_track_id(artist, title),
            "artist": artist,
            "artist_name": artist,  # For display consistency with other sources
            "title": title,
            "album": data.get("album", ""),
            "album_art_url": data.get("imageSrc"),
            "is_playing": is_playing,
            "source": "pear_desktop",
            "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            "last_active_time": self._last_active_time,
        }

        if data.get("songDuration") is not None:
            metadata["duration_ms"] = int(data["songDuration"] * 1000)
        if data.get("elapsedSeconds") is not None:
            metadata["position"] = data["elapsedSeconds"]

        return metadata
