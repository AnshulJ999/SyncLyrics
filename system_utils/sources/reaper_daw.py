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

# ─── UDP / Timing ─────────────────────────────────────────────────────────────
REAPER_UDP_PORT = 9064           # Port we listen on for telemetry from companion
COMMAND_UDP_PORT = 9065          # Port we send transport commands to
SAFETY_TIMEOUT = 2.5             # Seconds without heartbeat before we consider REAPER gone

# ─── Auto-Calibration ─────────────────────────────────────────────────────────
AUTO_CALIBRATION_CYCLES = 3                      # Number of Shazam cycles to average
CALIBRATION_AGREEMENT_TOLERANCE_SEC = 1.0        # Max spread of offsets (seconds) to accept
CALIBRATION_FAIL_COOLDOWN_SEC = 300              # Re-attempt failed calibrations after this many seconds
CALIBRATION_MIN_AGREEING_CYCLES = 2              # At least this many cycles must agree on song identity

# ─── REAPER Actions (editable) ────────────────────────────────────────────────
PLAY_PAUSE_ACTION = 40044 # 40044 is Play/Stop. 40073 is Play/Pause.
NEXT_MARKER_ACTION = 40173
PREV_MARKER_ACTION = 40172

# ─── External Paths ───────────────────────────────────────────────────────────
# Absolute path to ReaLauncher's metadata-cache.json (sibling repo on dev machine).
# Edit this if the ReaLauncher repo lives elsewhere.
METADATA_CACHE_PATH = r"G:\GitHub\Personal-Stuff\ReaLauncher\metadata-cache.json"


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
        self._listener_started = False  # Lazy start on first get_metadata() call
        self._cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        self._projects_db_path = os.path.join(base_dir, "reaper_integration", "reaper_projects.json")
        self._metadata_cache_path = METADATA_CACHE_PATH

        self._projects_db = self._load_json(self._projects_db_path, {})
        self._metadata_cache = self._load_json(self._metadata_cache_path, {})

        self._calibration_task: Optional[asyncio.Task] = None
        # Negative cache: proj_key -> last-failure timestamp. Prevents hammering failed projects.
        self._calibration_negative_cache: Dict[str, float] = {}

        self._current_offset_sec = 0.0
        self._current_song_meta: Dict[str, Any] = {}
        # B2: Track project to detect switches and clear stale state immediately.
        self._last_seen_project: str = ""

    @classmethod
    def get_config(cls) -> SourceConfig:
        return SourceConfig(
            name="reaper_daw",
            display_name="REAPER DAW",
            platforms=["Windows"],
            default_enabled=False,   # Opt-in until UI is stable
            default_priority=5,      # High (lower = higher); plugin returns None when not active, so fallthrough is automatic
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

    async def _ensure_listener_started(self):
        """Lazy, event-loop-safe UDP listener start. Called on first get_metadata()."""
        if self._listener_started:
            return
        self._listener_started = True  # Set early to prevent duplicate start races
        try:
            loop = asyncio.get_running_loop()
            self._transport, _ = await loop.create_datagram_endpoint(
                lambda: ReaperUDPProtocol(self),
                local_addr=("127.0.0.1", REAPER_UDP_PORT)
            )
            logger.info(f"REAPER DAW plugin listening on port {REAPER_UDP_PORT}")
        except Exception as e:
            logger.error(f"Failed to start REAPER UDP listener: {e}")
            self._listener_started = False  # Allow retry on next get_metadata()

    def handle_telemetry(self, payload: dict):
        """Called by the DatagramProtocol whenever a packet arrives from REAPER."""
        # Companion cleanup payload: state=0 AND no project. Clear state.
        if payload.get("state") == 0 and not payload.get("project"):
            self._telemetry = {}
            self._last_seen_project = ""
            self._current_offset_sec = 0.0
            self._current_song_meta = {}
            return

        self._telemetry = payload
        self._last_heartbeat = time.time()

        # B2: Detect project change — clear stale offset/meta immediately so we don't
        # serve lyrics from the previous project while calibration runs for the new one.
        incoming_project = self._get_project_key(payload.get("project", ""))
        if incoming_project and incoming_project != self._last_seen_project:
            self._last_seen_project = incoming_project
            self._current_offset_sec = 0.0
            self._current_song_meta = {}
            # Also cancel any in-flight calibration for the old project.
            if self._calibration_task and not self._calibration_task.done():
                self._calibration_task.cancel()
                self._calibration_task = None
            logger.info(f"REAPER project changed -> '{incoming_project}': cleared offset/meta")

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
        # NOTE: "project" is the REAPER project basename (e.g. "505 New.rpp").
        # "file" is the currently active *video* file path and is None for audio-only projects.
        if not self._telemetry.get("project"):
            return

        # Only calibrate while actually playing (state 1=play, 4=record count as playing for get_metadata,
        # but for calibration we want definite playback — state 1 only).
        if self._telemetry.get("state") != 1:
            return

        proj_key = self._get_project_key(self._telemetry.get("project"))
        pos = self._telemetry.get("pos", 0.0)

        # 1. Check if we already have an offset mapped in reaper_projects.json
        proj_data = self._projects_db.get(proj_key, {"songs": {}})

        active_song = None
        # Sort by offset descending to find the section we're currently in
        sorted_songs = sorted(
            proj_data.get("songs", {}).items(),
            key=lambda x: x[1].get("offset_sec", 0.0),
            reverse=True,
        )
        for song_title, song_data in sorted_songs:
            if pos >= song_data.get("offset_sec", 0.0):
                active_song = dict(song_data)  # copy — don't mutate DB
                active_song["title"] = song_title
                break

        if active_song is not None:
            # We have a valid offset mapped
            self._current_offset_sec = active_song.get("offset_sec", 0.0)
            self._current_song_meta = active_song
            return

        # 2. No offset found in DB. Check metadata cache for artist/title identity.
        # IMPORTANT: We do NOT return here. The cache gives us artist/title for immediate display,
        # but calibration MUST still run to find the real timeline offset. Assuming 0.0 is wrong
        # for any project where the song doesn't start at the very beginning of the timeline.
        if proj_key in self._metadata_cache:
            cache_data = self._metadata_cache[proj_key]
            if cache_data.get("matchedArtist") and cache_data.get("matchedTitle"):
                # Only update meta if not already set (avoid re-creating dict every 50ms)
                if not self._current_song_meta.get("title"):
                    self._current_song_meta = {
                        "artist": cache_data.get("matchedArtist"),
                        "title": cache_data.get("matchedTitle"),
                    }
                # Fall through to calibration — offset still unknown

        # 3. No offset and no metadata. Trigger Audio Recognition (with cooldown + single-flight guard).
        if self._calibration_task is not None and not self._calibration_task.done():
            return  # Already calibrating

        last_fail = self._calibration_negative_cache.get(proj_key, 0.0)
        if time.time() - last_fail < CALIBRATION_FAIL_COOLDOWN_SEC:
            return  # In cooldown window after prior failure

        self._calibration_task = asyncio.create_task(self._run_auto_calibration(proj_key, pos))

    async def _run_auto_calibration(self, proj_key: str, initial_pos: float):
        """
        Runs Audio Recognition to calculate the timeline offset for the current song.

        Correctness rules:
        - Does NOT call engine.start() (which would spawn a competing recognition loop).
          Just uses engine.recognize_once() for controlled one-shot recognition.
        - Requires at least CALIBRATION_MIN_AGREEING_CYCLES cycles to agree on SAME song identity.
        - Requires offset spread within CALIBRATION_AGREEMENT_TOLERANCE_SEC.
        - On failure, adds proj_key to negative cache to prevent hammering.
        """
        logger.info(f"Starting auto-calibration for '{proj_key}' at REAPER pos {initial_pos:.2f}s")

        from audio_recognition.engine import RecognitionEngine

        engine = RecognitionEngine(
            recognition_interval=5.0,
            capture_duration=5.0,
        )

        # List of (artist, title, offset_sec). We DO NOT call engine.start() — that would
        # launch _run_loop which competes for the audio device with our recognize_once calls.
        samples: List[Dict[str, Any]] = []

        try:
            for i in range(AUTO_CALIBRATION_CYCLES):
                logger.debug(f"Calibration cycle {i + 1}/{AUTO_CALIBRATION_CYCLES}")
                result = await engine.recognize_once()
                if result and result.artist and result.title:
                    # NOTE: get_current_position() = offset + (now - capture_start_time), i.e. song-time NOW.
                    # We pair it with the latest REAPER pos telemetry, which is also "now" within one UDP tick (~50ms).
                    # This is good enough for v1; v2 can back-interpolate pos to capture_start_time for sub-50ms precision.
                    current_reaper_pos = self._telemetry.get("pos", initial_pos)
                    offset = current_reaper_pos - result.get_current_position()
                    samples.append({
                        "artist": result.artist,
                        "title": result.title,
                        "offset": offset,
                    })
                    logger.debug(f"  Recognized: {result.artist} - {result.title} (offset {offset:.2f}s)")
                else:
                    logger.debug(f"  Cycle {i + 1}: no recognition")
                await asyncio.sleep(1.0)
        except Exception as e:
            logger.error(f"Auto-calibration error during recognition: {e}", exc_info=True)
        finally:
            # Clean up aiohttp session held by the recognizer to prevent resource leak.
            try:
                if engine.recognizer:
                    await engine.recognizer.close()
            except Exception:
                pass

        if not samples:
            logger.warning(f"Auto-calibration: no recognitions for '{proj_key}'. Backing off for {CALIBRATION_FAIL_COOLDOWN_SEC}s.")
            self._calibration_negative_cache[proj_key] = time.time()
            return

        # Group samples by normalized song identity
        groups: Dict[str, Dict[str, Any]] = {}
        for s in samples:
            key = _normalize_track_id(s["artist"], s["title"])
            if key not in groups:
                groups[key] = {"artist": s["artist"], "title": s["title"], "offsets": []}
            groups[key]["offsets"].append(s["offset"])

        # Pick the most agreed-upon song
        best = max(groups.values(), key=lambda g: len(g["offsets"]))

        if len(best["offsets"]) < CALIBRATION_MIN_AGREEING_CYCLES:
            logger.warning(
                f"Auto-calibration inconclusive for '{proj_key}': "
                f"cycles disagree on song (got {[g['title'] for g in groups.values()]}). "
                f"Backing off for {CALIBRATION_FAIL_COOLDOWN_SEC}s."
            )
            self._calibration_negative_cache[proj_key] = time.time()
            return

        offset_spread = max(best["offsets"]) - min(best["offsets"])
        if offset_spread > CALIBRATION_AGREEMENT_TOLERANCE_SEC:
            logger.warning(
                f"Auto-calibration offset disagreement for '{proj_key}' ({best['artist']} - {best['title']}): "
                f"spread {offset_spread:.2f}s > {CALIBRATION_AGREEMENT_TOLERANCE_SEC}s tolerance. "
                f"Offsets: {[f'{o:.2f}' for o in best['offsets']]}. Backing off."
            )
            self._calibration_negative_cache[proj_key] = time.time()
            return

        # Passed agreement checks — save to DB
        avg_offset = sum(best["offsets"]) / len(best["offsets"])
        artist = best["artist"]
        title = best["title"]

        logger.info(
            f"Auto-Calibration SUCCESS: {artist} - {title} | "
            f"offset={avg_offset:.2f}s (avg of {len(best['offsets'])}/{AUTO_CALIBRATION_CYCLES} cycles, spread {offset_spread:.2f}s)"
        )

        if proj_key not in self._projects_db:
            self._projects_db[proj_key] = {"songs": {}}
        self._projects_db[proj_key]["songs"][title] = {
            "artist": artist,
            "offset_sec": avg_offset,
        }
        self._save_json(self._projects_db_path, self._projects_db)

        # Apply immediately + clear any prior failure record
        self._current_offset_sec = avg_offset
        self._current_song_meta = {"artist": artist, "title": title}
        self._calibration_negative_cache.pop(proj_key, None)

    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        # Lazy start of UDP listener on first poll (safe from any event-loop context)
        await self._ensure_listener_started()

        # 1. Safety Timeout: If REAPER hasn't sent data, it's likely closed or stopped
        if time.time() - self._last_heartbeat > SAFETY_TIMEOUT:
            return None

        if not self._telemetry:
            return None

        # Use "project" field (REAPER project basename), NOT "file" (video file path)
        proj_key = self._get_project_key(self._telemetry.get("project"))
        if not proj_key:
            return None

        is_playing = self._telemetry.get("state") in (1, 4)  # 1=play, 4=record
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
