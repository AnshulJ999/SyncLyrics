"""
Now Playing Input (push) Metadata Source

Other devices send what they are playing to POST /api/now-playing (phones via
Tasker/MacroDroid, Home Assistant automations, scripts). Among pushing devices,
the most recent playing one wins; a paused device drops out after the paused
timeout. Disabled by default - enable it under Media settings.

Payload (JSON): device_id (required), device_name, title (required), artist,
album, album_art_url, position + duration in seconds (or position_ms +
duration_ms), is_playing (required). {"device_id": ..., "stopped": true}
removes a device. Unknown fields are ignored.
"""
import math
import time
from typing import Optional, Tuple

from .base import BaseMetadataSource, SourceConfig, SourceCapability
from ..helpers import _normalize_track_id
from logging_config import get_logger

logger = get_logger(__name__)

# A "playing" device that stops sending (phone died, app killed) is treated as
# paused after this long. Senders are expected to heartbeat every ~5s.
_STALE_AFTER = 30.0
_MAX_DEVICES = 20
_MAX_TEXT = 500
_MAX_URL = 2048


def _text(value, limit: int = _MAX_TEXT) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError
    text = str(value).strip()
    return text[:limit] if text else None


def _seconds(data: dict, name: str) -> Optional[float]:
    """Read `name` (seconds) or `name_ms` (milliseconds)."""
    if data.get(name) is not None:
        value, scale = data[name], 1.0
    elif data.get(f"{name}_ms") is not None:
        value, scale = data[f"{name}_ms"], 0.001
    else:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    value = float(value) * scale
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be zero or more")
    return value


class NowPlayingInputSource(BaseMetadataSource):

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="now_playing_input",
            display_name="Now Playing Input",
            platforms=["Windows", "Linux", "Darwin"],
            default_enabled=False,
            default_priority=9,
            paused_timeout=600,
            skip_platform_check=True,
        )

    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (SourceCapability.METADATA |
                SourceCapability.ALBUM_ART |
                SourceCapability.DURATION)

    def __init__(self):
        super().__init__()
        self._devices: dict = {}

    def is_available(self) -> bool:
        return True

    def ingest(self, data) -> Tuple[int, str]:
        """Apply one pushed update. Returns (HTTP status, message)."""
        if not isinstance(data, dict):
            return 400, "Send a JSON object."
        try:
            device_id = _text(data.get("device_id"), 100)
        except ValueError:
            device_id = None
        if not device_id:
            return 400, "device_id is required."

        if data.get("stopped") is True:
            self._devices.pop(device_id, None)
            return 200, "ok"

        try:
            title = _text(data.get("title"))
            if not title:
                return 400, "title is required."
            is_playing = data.get("is_playing")
            if not isinstance(is_playing, bool):
                return 400, "is_playing is required (true or false)."
            art = _text(data.get("album_art_url"), _MAX_URL)
            if art and not art.lower().startswith(("http://", "https://")):
                return 400, "album_art_url must be an http(s) URL."
            record = {
                "name": _text(data.get("device_name"), 100),
                "title": title,
                "artist": _text(data.get("artist")) or "",
                "album": _text(data.get("album")) or "",
                "art": art,
                "position": _seconds(data, "position"),
                "duration": _seconds(data, "duration"),
                "is_playing": is_playing,
            }
        except ValueError as e:
            return 400, str(e) or "Invalid field value."

        now = time.time()
        previous = self._devices.get(device_id)
        record["received_at"] = now
        record["last_active"] = now if is_playing else (previous or {}).get("last_active", now)
        self._devices[device_id] = record

        if len(self._devices) > _MAX_DEVICES:
            oldest = min(self._devices, key=lambda d: self._devices[d]["received_at"])
            self._devices.pop(oldest, None)
        return 200, "ok"

    def _pick(self, now: float) -> Optional[dict]:
        timeout = self.paused_timeout
        playing, paused = [], []
        for device_id, rec in list(self._devices.items()):
            live = rec["is_playing"] and now - rec["received_at"] < _STALE_AFTER
            if live:
                playing.append(rec)
                continue
            last_active = rec["received_at"] if rec["is_playing"] else rec["last_active"]
            if timeout and now - last_active >= timeout:
                self._devices.pop(device_id, None)
            else:
                paused.append((last_active, rec))
        if playing:
            return max(playing, key=lambda r: r["received_at"])
        if paused:
            return max(paused, key=lambda p: p[0])[1]
        return None

    async def get_metadata(self) -> Optional[dict]:
        now = time.time()
        rec = self._pick(now)
        if not rec:
            return None

        live = rec["is_playing"] and now - rec["received_at"] < _STALE_AFTER
        if live:
            last_active = now
        elif rec["is_playing"]:  # stopped sending while playing
            last_active = rec["received_at"]
        else:
            last_active = rec["last_active"]
        self._last_active_time = last_active

        metadata = {
            "track_id": _normalize_track_id(rec["artist"], rec["title"]),
            "artist": rec["artist"],
            "artist_name": rec["artist"],
            "title": rec["title"],
            "album": rec["album"],
            "album_art_url": rec["art"],
            "is_playing": live,
            "source": "now_playing_input",
            "source_label": rec["name"],
            "colors": ("#24273a", "#363b54"),  # Default, will be enriched
            "last_active_time": last_active,
        }
        if rec["duration"]:
            metadata["duration_ms"] = int(rec["duration"] * 1000)
        if rec["position"] is not None:
            position = rec["position"]
            if live:
                position += now - rec["received_at"]
            if rec["duration"]:
                position = min(position, rec["duration"])
            metadata["position"] = position
        return metadata
