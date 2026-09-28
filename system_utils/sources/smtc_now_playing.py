"""
smtc-now-playing (Windows) Metadata Source

Polls smtc-now-playing (https://github.com/soarqin/smtc-now-playing), a small
Windows app that serves whatever Windows media controls (SMTC) report - any
player on that PC. Lets a SyncLyrics running in Docker/HA follow a Windows
desktop. Default URL http://127.0.0.1:11451; point it at the Windows PC's IP
when SyncLyrics runs elsewhere. Disabled by default - enable it under Media settings.
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

_TIMEOUT = (0.5, 5.0)
_RETRY_BACKOFF = 5.0

# smtc-now-playing progress.status values (SMTC playback status)
_STATUS_PLAYING = 4


class SmtcNowPlayingSource(BaseMetadataSource):

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="smtc_now_playing",
            display_name="smtc-now-playing (Windows)",
            platforms=["Windows", "Linux", "Darwin"],
            default_enabled=False,
            default_priority=8,
            paused_timeout=600,
        )

    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (SourceCapability.METADATA |
                SourceCapability.ALBUM_ART |
                SourceCapability.DURATION)

    def __init__(self):
        super().__init__()
        base = conf("media_source.smtc_now_playing.base_url", "http://127.0.0.1:11451")
        self.base_url = str(base).rstrip("/")
        self._next_attempt = 0.0
        self._session = requests.Session()

    def is_available(self) -> bool:
        """Connectivity is verified during fetch."""
        return True

    def _fetch_now_playing(self) -> Optional[dict]:
        """GET /api/now-playing (404 = no active session). Blocking. Raises on connection failure."""
        resp = self._session.get(f"{self.base_url}/api/now-playing", timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        return None

    @staticmethod
    def _live_position(progress: dict, is_playing: bool) -> float:
        """
        Position is raw SMTC data: seconds as of lastUpdatedTime (Unix ms, sender's clock).
        Extrapolate while playing; ignore implausible deltas (clock skew, stale timestamp).
        """
        position = progress.get("position") or 0
        duration = progress.get("duration") or 0
        last_updated_ms = progress.get("lastUpdatedTime") or 0
        if is_playing and last_updated_ms > 0:
            delta = time.time() - last_updated_ms / 1000
            if 0 <= delta <= max(duration, 1):
                position += delta * (progress.get("playbackRate") or 1.0)
        return min(position, duration) if duration else position

    async def get_metadata(self) -> Optional[dict]:
        if time.time() < self._next_attempt:
            return None

        try:
            data = await asyncio.to_thread(self._fetch_now_playing)
        except Exception as e:
            logger.debug(f"smtc-now-playing fetch failed: {e}")
            self._next_attempt = time.time() + _RETRY_BACKOFF
            return None
        self._next_attempt = 0.0

        if not data:
            return None
        info = data.get("info") or {}
        progress = data.get("progress") or {}
        title = info.get("title") or ""
        artist = info.get("artist") or ""
        if not title and not artist:
            return None

        is_playing = progress.get("status") == _STATUS_PLAYING
        if is_playing:
            self._last_active_time = time.time()

        art = info.get("albumArt") or None
        if art and art.startswith("/"):
            art = f"{self.base_url}{art}"  # served by smtc-now-playing itself

        metadata = {
            "track_id": _normalize_track_id(artist, title),
            "artist": artist,
            "artist_name": artist,
            "title": title,
            "album": info.get("albumTitle") or "",
            "album_art_url": art,
            "is_playing": is_playing,
            "source": "smtc_now_playing",
            "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            "last_active_time": self._last_active_time,
            "position": self._live_position(progress, is_playing),
        }
        if progress.get("duration"):
            metadata["duration_ms"] = int(progress["duration"] * 1000)
        return metadata
