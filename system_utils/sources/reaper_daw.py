import asyncio
import json
import socket
import os
import time
from typing import Optional, Dict, Any, List

from logging_config import get_logger
from system_utils.sources.base import BaseMetadataSource, SourceConfig, SourceCapability
from system_utils.helpers import _normalize_track_id

logger = get_logger("reaper_daw")

REAPER_UDP_PORT = 9064
COMMAND_UDP_PORT = 9065
SAFETY_TIMEOUT = 2.5
AUTO_CALIBRATION_CYCLES = 3

PLAY_PAUSE_ACTION = 40044
NEXT_MARKER_ACTION = 40173
PREV_MARKER_ACTION = 40172


class ReaperUDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, source_ref):
        self.source = source_ref
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            payload = json.loads(data.decode("utf-8"))
            self.source.handle_telemetry(payload)
        except Exception as e:
            pass  # Ignore malformed packets


class ReaperDAWSource(BaseMetadataSource):
    """
    REAPER DAW Media Source
    
    Receives real-time UDP telemetry from the Anshul - Video Companion.py ReaScript.
    Features an Auto-Calibration pipeline using Audio Recognition to establish
    timeline offsets for songs within projects.
    """
    
    def __init__(self):
        super().__init__()
        self._telemetry = {}
        self._last_heartbeat = 0.0
        self._transport = None
        self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self._projects_db_path = os.path.join(base_dir, "reaper_integration", "reaper_projects.json")
        self._metadata_cache_path = os.path.join(os.path.dirname(base_dir), "ReaLauncher", "metadata-cache.json")
        
        self._projects_db = self._load_json(self._projects_db_path, {})
        self._metadata_cache = self._load_json(self._metadata_cache_path, {})
        
        self._calibration_task = None
        self._current_offset_sec = 0.0
        self._current_song_meta = {}
        
        # Start UDP listener
        asyncio.create_task(self._start_udp_listener())

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="reaper_daw",
            display_name="REAPER DAW",
            platforms=["Windows"],
            default_enabled=True,
            default_priority=5,  # High priority when active
            config_keys=["reaper_daw.split_filename"]
        )

    @classmethod
    def capabilities(cls) -> SourceCapability:
        return (SourceCapability.METADATA | 
                SourceCapability.PLAYBACK_CONTROL)

    def _load_json(self, path: str, default: Any) -> Any:
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return default

    def _save_json(self, path: str, data: Any):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error saving {path}: {e}")

    async def _start_udp_listener(self):
        loop = asyncio.get_running_loop()
        try:
            self._transport, protocol = await loop.create_datagram_endpoint(
                lambda: ReaperUDPProtocol(self),
                local_addr=("127.0.0.1", REAPER_UDP_PORT)
            )
            logger.info(f"REAPER DAW plugin listening on port {REAPER_UDP_PORT}")
        except Exception as e:
            logger.error(f"Failed to start REAPER UDP listener: {e}")

    def handle_telemetry(self, payload: dict):
        """Called by the DatagramProtocol whenever a packet arrives from REAPER."""
        # If we receive a stop signal from cleanup, clear telemetry
        if payload.get("state") == 0 and payload.get("pos") == 0.0 and not payload.get("file"):
            self._telemetry = {}
            return
            
        self._telemetry = payload
        self._last_heartbeat = time.time()
        
        # Check if we need to auto-calibrate offsets
        self._check_auto_calibration()

    def _send_command(self, cmd_val: int):
        try:
            payload = json.dumps({"cmd": cmd_val}).encode("utf-8")
            self._cmd_sock.sendto(payload, ("127.0.0.1", COMMAND_UDP_PORT))
        except Exception as e:
            logger.error(f"Failed to send REAPER command: {e}")

    async def play(self) -> bool:
        self._send_command(PLAY_PAUSE_ACTION)
        return True

    async def pause(self) -> bool:
        self._send_command(PLAY_PAUSE_ACTION)
        return True

    async def toggle_playback(self) -> bool:
        self._send_command(PLAY_PAUSE_ACTION)
        return True

    async def next_track(self) -> bool:
        self._send_command(NEXT_MARKER_ACTION)
        return True

    async def previous_track(self) -> bool:
        self._send_command(PREV_MARKER_ACTION)
        return True

    def _get_project_key(self, filepath: str) -> str:
        if not filepath:
            return ""
        basename = os.path.basename(filepath)
        if basename.lower().endswith(".rpp"):
            basename = basename[:-4]
        return basename

    def _check_auto_calibration(self):
        """Determines if we need to run Audio Recognition to find the timeline offset."""
        if not self._telemetry.get("file"):
            return
            
        is_playing = self._telemetry.get("state") == 1
        if not is_playing:
            return
            
        proj_key = self._get_project_key(self._telemetry.get("file"))
        pos = self._telemetry.get("pos", 0.0)
        
        # 1. Check if we already have an offset mapped in reaper_projects.json
        proj_data = self._projects_db.get(proj_key, {"songs": {}})
        
        active_song = None
        # Sort by offset descending to find the section we're currently in
        sorted_songs = sorted(proj_data.get("songs", {}).items(), key=lambda x: x[1].get("offset_sec", 0.0), reverse=True)
        for song_title, song_data in sorted_songs:
            if pos >= song_data.get("offset_sec", 0.0):
                active_song = song_data
                active_song["title"] = song_title
                break
                
        if active_song is not None:
            # We have a valid offset mapped
            self._current_offset_sec = active_song.get("offset_sec", 0.0)
            self._current_song_meta = active_song
            return
            
        # 2. No offset found. Do we have cached metadata with NO offset requirement?
        # (For simple 1-song projects, offset might just be 0)
        if proj_key in self._metadata_cache:
            cache_data = self._metadata_cache[proj_key]
            if cache_data.get("matchedArtist") and cache_data.get("matchedTitle"):
                # Use 0 offset by default for cached projects without explicit offsets
                self._current_offset_sec = 0.0
                self._current_song_meta = {
                    "artist": cache_data.get("matchedArtist"),
                    "title": cache_data.get("matchedTitle")
                }
                return
                
        # 3. No offset and no metadata cache. Trigger Audio Recognition
        if self._calibration_task is None or self._calibration_task.done():
            self._calibration_task = asyncio.create_task(self._run_auto_calibration(proj_key, pos))

    async def _run_auto_calibration(self, proj_key: str, initial_pos: float):
        """Runs the Audio Recognition engine to calculate the track offset."""
        logger.info(f"Starting auto-calibration for {proj_key} at REAPER pos {initial_pos:.2f}s")
        try:
            from audio_recognition.engine import RecognitionEngine
            
            engine = RecognitionEngine(
                recognition_interval=5.0,
                capture_duration=5.0
            )
            await engine.start()
            
            offsets = []
            recognized_artist = None
            recognized_title = None
            
            for i in range(AUTO_CALIBRATION_CYCLES):
                logger.debug(f"Calibration cycle {i+1}/{AUTO_CALIBRATION_CYCLES}")
                result = await engine.recognize_once()
                if result and result.artist and result.title:
                    current_reaper_pos = self._telemetry.get("pos", initial_pos)
                    # Offset calculation: song_time = pos - offset => offset = pos - song_time
                    offset = current_reaper_pos - result.get_current_position()
                    offsets.append(offset)
                    recognized_artist = result.artist
                    recognized_title = result.title
                    logger.debug(f"Recognized: {recognized_artist} - {recognized_title} (Calc Offset: {offset:.2f}s)")
                else:
                    logger.debug("Recognition failed this cycle")
                    
                await asyncio.sleep(1.0)
                
            await engine.stop()
            
            if offsets:
                # Calculate average offset
                avg_offset = sum(offsets) / len(offsets)
                logger.info(f"Auto-Calibration SUCCESS: {recognized_artist} - {recognized_title} | Offset: {avg_offset:.2f}s")
                
                # Save to database
                if proj_key not in self._projects_db:
                    self._projects_db[proj_key] = {"songs": {}}
                    
                self._projects_db[proj_key]["songs"][recognized_title] = {
                    "artist": recognized_artist,
                    "offset_sec": avg_offset
                }
                
                self._save_json(self._projects_db_path, self._projects_db)
                
                # Apply immediately
                self._current_offset_sec = avg_offset
                self._current_song_meta = {"artist": recognized_artist, "title": recognized_title}
            else:
                logger.warning(f"Auto-calibration failed to recognize audio for {proj_key}")
                
        except Exception as e:
            logger.error(f"Auto-calibration error: {e}")

    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        # 1. Safety Timeout: If REAPER hasn't sent data, it's likely closed or stopped
        if time.time() - self._last_heartbeat > SAFETY_TIMEOUT:
            return None
            
        if not self._telemetry:
            return None
            
        proj_key = self._get_project_key(self._telemetry.get("file"))
        if not proj_key:
            return None
            
        is_playing = self._telemetry.get("state") == 1
        pos = self._telemetry.get("pos", 0.0)
        
        # 2. Offset Math
        song_time = pos - self._current_offset_sec
        if song_time < 0:
            song_time = 0
            
        artist = self._current_song_meta.get("artist")
        title = self._current_song_meta.get("title")
        
        # 3. Fallback: Filename Splitting
        from config import conf
        if conf("reaper_daw.split_filename", False) and (not artist or not title):
            if " - " in proj_key:
                parts = proj_key.split(" - ", 1)
                artist = parts[0].strip()
                title = parts[1].strip()

        # Final Fallbacks
        if not artist: artist = "Unknown Artist"
        if not title: title = proj_key

        return {
            "artist": artist,
            "title": title,
            "is_playing": is_playing,
            "position": song_time,
            "duration_ms": 0,  # REAPER doesn't provide track duration inherently
            "source": self.name,
            "track_id": _normalize_track_id(artist, title),
            "_reaper_project": proj_key,
            "_reaper_pos": pos,
            "_reaper_offset": self._current_offset_sec,
            "_reaper_state": self._telemetry.get("state")
        }
