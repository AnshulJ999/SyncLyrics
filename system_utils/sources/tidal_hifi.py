"""
tidal-hifi (TIDAL) Metadata Source

Polls the local REST API of tidal-hifi, the unofficial TIDAL desktop client
(https://github.com/Mastermindzh/tidal-hifi). Its API listens on
http://127.0.0.1:47836 by default; to read it from another machine (e.g. a
Docker/HA server), set tidal-hifi's API hostname to 0.0.0.0 in its settings.
Disabled by default - enable it under Media settings.
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

# Connect fast, read patiently: a closed port on Windows is dropped, not refused
_TIMEOUT = (0.5, 5.0)

# get_metadata() runs without an outer timeout; back off after a failure
_RETRY_BACKOFF = 5.0


class TidalHifiSource(BaseMetadataSource):

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="tidal_hifi",
            display_name="tidal-hifi (TIDAL)",
            platforms=["Windows", "Linux", "Darwin"],
            default_enabled=False,
            default_priority=7,
            paused_timeout=600,
        )

    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (SourceCapability.METADATA |
                SourceCapability.ALBUM_ART |
                SourceCapability.DURATION)

    def __init__(self):
        super().__init__()
        base = conf("media_source.tidal_hifi.base_url", "http://127.0.0.1:47836")
        self.base_url = str(base).rstrip("/")
        self._next_attempt = 0.0
        self._session = requests.Session()

    def is_available(self) -> bool:
        """Connectivity is verified during fetch."""
        return True

    def _fetch_current(self) -> Optional[dict]:
        """GET /current. Blocking. Raises on connection failure."""
        resp = self._session.get(f"{self.base_url}/current", timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        return None

    async def get_metadata(self) -> Optional[dict]:
        if time.time() < self._next_attempt:
            return None

        try:
            data = await asyncio.to_thread(self._fetch_current)
        except Exception as e:
            logger.debug(f"tidal-hifi fetch failed: {e}")
            self._next_attempt = time.time() + _RETRY_BACKOFF
            return None
        self._next_attempt = 0.0

        if not data:
            return None
        title = data.get("title") or ""
        artist = data.get("artists") or ""
        if not title and not artist:
            return None

        is_playing = data.get("status") == "playing"
        if is_playing:
            self._last_active_time = time.time()

        metadata = {
            "track_id": _normalize_track_id(artist, title),
            "artist": artist,
            "artist_name": artist,
            "title": title,
            "album": data.get("album") or "",
            "album_art_url": data.get("image") or None,
            "is_playing": is_playing,
            "source": "tidal_hifi",
            "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            "last_active_time": self._last_active_time,
        }
        if data.get("durationInSeconds"):
            metadata["duration_ms"] = int(data["durationInSeconds"] * 1000)
        if data.get("currentInSeconds") is not None:
            metadata["position"] = data["currentInSeconds"]
        return metadata
