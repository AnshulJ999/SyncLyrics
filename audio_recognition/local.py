"""
Local Audio Fingerprinting Module

Uses SoundFingerprinting (via sfp-cli) for instant, offline recognition
of songs in the user's local music library.

This module is ENV-guarded and only loaded if LOCAL_FP_ENABLED=true.
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Optional, Dict, Any

from logging_config import get_logger
from .shazam import RecognitionResult
from .capture import AudioChunk
from .daemon import DaemonManager

logger = get_logger(__name__)


class LocalRecognizer:
    """
    Local audio fingerprinting using SoundFingerprinting CLI.
    
    Acts as first-pass recognition before Shazamio/ACRCloud.
    Uses the user's own FLAC library as the fingerprint database.
    
    Features:
    - Instant recognition (no network latency)
    - Works offline
    - Returns offset for lyrics synchronization
    - Integrates with existing RecognitionResult format
    
    NOTE: This class is only imported if LOCAL_FP_ENABLED=true.
    """
    
    # FFmpeg args for converting to SFP format (5512Hz mono)
    FFMPEG_ARGS = ["-ac", "1", "-ar", "5512", "-loglevel", "error"]
    
    def __init__(self, db_path: Optional[Path] = None, cli_path: Optional[Path] = None, min_confidence: Optional[float] = None):
        """
        Initialize local fingerprint recognizer.
        
        Args:
            db_path: Path to fingerprint database (default: from config)
            cli_path: Path to sfp-cli directory (default: from config)
            min_confidence: Minimum confidence threshold (default: from config)
        """
        # Lazy load config to avoid circular imports
        from config import LOCAL_FINGERPRINT
        
        self._db_path = db_path or LOCAL_FINGERPRINT["db_path"]
        self._cli_path = cli_path or LOCAL_FINGERPRINT["cli_path"]
        # Use config value if not explicitly passed (None check, not truthy check)
        self._min_confidence = min_confidence if min_confidence is not None else LOCAL_FINGERPRINT["min_confidence"]
        self._available = None  # Lazy check
        self._exe_path = None  # Path to built executable
        self._daemon: Optional[DaemonManager] = None  # Lazy initialized
        
        logger.info(f"LocalRecognizer initialized: db={self._db_path}, min_conf={self._min_confidence}")
    
    def _get_daemon(self) -> Optional[DaemonManager]:
        """Get or create daemon manager (lazy initialization)."""
        if self._daemon is None:
            exe_path = self._get_exe_path()
            if exe_path:
                self._daemon = DaemonManager(exe_path, Path(self._db_path))
        return self._daemon
    
    def stop_daemon(self) -> None:
        """Stop the daemon process if running. Called when engine stops."""
        if self._daemon:
            self._daemon.stop()
            self._daemon = None
    
    async def prewarm_daemon(self) -> bool:
        """
        Pre-warm the daemon in background to eliminate cold-start latency.
        
        Called when engine starts to load FFmpeg and fingerprint database
        before the first recognition request. This reduces first-query
        latency from ~30s to <1s.
        
        Returns:
            True if daemon started successfully, False otherwise
        """
        if not self.is_available():
            logger.debug("Local FP not available, skipping daemon prewarm")
            return False
        
        daemon = self._get_daemon()
        if daemon is None:
            logger.debug("Could not create daemon manager")
            return False
        
        logger.info("Pre-warming local fingerprint daemon...")
        success = await daemon.start()
        if success:
            logger.info("Local fingerprint daemon pre-warmed and ready")
        else:
            logger.warning("Daemon prewarm failed - will retry on first query")
        return success
    
    def _get_exe_path(self) -> Optional[Path]:
        """Get path to pre-built sfp-cli executable, building if needed."""
        if self._exe_path is not None:
            return self._exe_path
        
        # Check for existing published executable
        publish_dir = self._cli_path / "bin" / "publish"
        exe_name = "sfp-cli.exe" if sys.platform == "win32" else "sfp-cli"
        exe_path = publish_dir / exe_name
        
        if exe_path.exists():
            self._exe_path = exe_path
            logger.debug(f"Using pre-built sfp-cli: {exe_path}")
            return exe_path
        
        # Build the executable
        logger.info("Building sfp-cli executable (one-time)...")
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            result = subprocess.run(
                ["dotnet", "publish", "-c", "Release", "-o", str(publish_dir)],
                cwd=str(self._cli_path),
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=creationflags
            )
            
            if result.returncode != 0:
                logger.error(f"Failed to build sfp-cli: {result.stderr}")
                return None
            
            if exe_path.exists():
                self._exe_path = exe_path
                logger.info(f"Built sfp-cli executable: {exe_path}")
                return exe_path
            else:
                logger.error(f"Build succeeded but exe not found at {exe_path}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to build sfp-cli: {e}")
            return None
    
    def is_available(self) -> bool:
        """
        Check if local fingerprinting is available.
        
        Returns True if:
        - sfp-cli executable exists (or can be built)
        - Database files exist (fingerprints folder + metadata.json)
        
        NOTE: We don't run sfp-cli stats here to avoid loading the database twice
        (once for the check, once for the daemon). The daemon will verify on startup.
        """
        if self._available is not None:
            return self._available
        
        try:
            # Check if CLI exists
            if not (self._cli_path / "sfp-cli.csproj").exists():
                logger.warning(f"sfp-cli not found at {self._cli_path}")
                self._available = False
                return False
            
            # Ensure executable is built
            if self._get_exe_path() is None:
                logger.warning("sfp-cli executable not available")
                self._available = False
                return False
            
            # Fast check: verify database files exist (no CLI call needed)
            # This avoids loading the entire database just to check availability
            db_path = Path(self._db_path)
            fingerprint_path = db_path / "fingerprints"
            metadata_path = db_path / "metadata.json"
            
            if not fingerprint_path.exists():
                logger.info(f"Local fingerprint database not found: {fingerprint_path}")
                self._available = False
                return False
            
            if not metadata_path.exists():
                logger.info(f"Local fingerprint metadata not found: {metadata_path}")
                self._available = False
                return False
            
            # Quick check: metadata.json should have content (not empty)
            try:
                metadata_size = metadata_path.stat().st_size
                if metadata_size < 10:  # Empty JSON {} is ~2 bytes
                    logger.info("Local fingerprint database is empty (no metadata)")
                    self._available = False
                    return False
            except OSError:
                self._available = False
                return False
            
            logger.info(f"Local fingerprinting available (database exists at {db_path})")
            self._available = True
            return True
            
        except Exception as e:
            logger.warning(f"Local fingerprinting check failed: {e}")
            self._available = False
            return False
    
    async def _query_via_daemon(self, wav_path: str, duration: int, offset: int = 0) -> Optional[Dict[str, Any]]:
        """
        Query via daemon (fast path, async-safe).
        
        Returns None if daemon is not available, requiring fallback to subprocess.
        """
        daemon = self._get_daemon()
        if not daemon:
            return None
        
        # If daemon is in fallback mode, skip it
        if daemon.in_fallback_mode:
            return None
        
        result = await daemon.send_command({
            "cmd": "query",
            "path": wav_path,
            "duration": duration,
            "offset": offset
        })
        
        return result
    
    async def _run_cli_command_async(self, command: str, *args) -> Dict[str, Any]:
        """
        Run sfp-cli command and return JSON result (async version).
        
        For 'query' commands, tries daemon first (fast), falls back to subprocess (slow).
        """
        # For query commands, try daemon first (fast path)
        if command == "query" and len(args) >= 2:
            wav_path = args[0]
            duration = int(args[1])
            offset = int(args[2]) if len(args) > 2 else 0
            
            daemon_result = await self._query_via_daemon(wav_path, duration, offset)
            if daemon_result is not None:
                return daemon_result
            # Daemon unavailable or failed, fall through to subprocess
        
        # Subprocess fallback (slow path) - run in thread to avoid blocking
        return await asyncio.get_running_loop().run_in_executor(
            None, self._run_cli_command_sync, command, *args
        )
    
    def _run_cli_command_sync(self, command: str, *args) -> Dict[str, Any]:
        """Run sfp-cli command synchronously (for subprocess fallback)."""
        exe_path = self._get_exe_path()
        if exe_path is None:
            return {"error": "sfp-cli executable not available"}
        
        cmd = [
            str(exe_path),
            "--db-path", str(self._db_path.absolute()),
            command
        ] + list(args)
        
        try:
            # Hide console window on Windows
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=creationflags
            )
            
            # Parse JSON from stdout
            stdout = result.stdout.strip()
            for line in stdout.split('\n'):
                line = line.strip()
                if line.startswith('{'):
                    return json.loads(line)
            
            return {"error": f"No JSON output: {stdout[:200]}"}
            
        except subprocess.TimeoutExpired:
            return {"error": "CLI timeout"}
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON: {e}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def recognize(self, audio: AudioChunk, wav_bytes: Optional[bytes] = None) -> Optional[RecognitionResult]:
        """
        Recognize audio against local fingerprint database.
        
        Args:
            audio: AudioChunk with capture timing info
            wav_bytes: Optional WAV bytes (not used - we convert AudioChunk directly)
            
        Returns:
            RecognitionResult or None if no match
        """
        import time
        
        if not self.is_available():
            return None
        
        try:
            # Write AudioChunk to WAV file for sfp-cli
            # FFmpegAudioService handles downsampling internally, so we just need standard WAV
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
                wav_path = Path(wav_file.name)
                
                # Write standard WAV (FFmpegAudioService will handle conversion to 5512Hz mono)
                with wave.open(str(wav_path), 'wb') as wf:
                    wf.setnchannels(audio.channels)
                    wf.setsampwidth(2)  # int16
                    wf.setframerate(audio.sample_rate)
                    wf.writeframes(audio.data.tobytes())
            
            # NOTE: FFmpegAudioService now handles format conversion internally
            # Old FFmpeg conversion code commented out for reference:
            # with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as sfp_file:
            #     sfp_path = Path(sfp_file.name)
            # creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            # ffmpeg_result = subprocess.run(
            #     ["ffmpeg", "-i", str(raw_path)] + self.FFMPEG_ARGS + [str(sfp_path), "-y"],
            #     capture_output=True,
            #     timeout=10,
            #     creationflags=creationflags
            # )
            # raw_path.unlink()
            # if ffmpeg_result.returncode != 0:
            #     logger.warning("FFmpeg conversion failed for local recognition")
            #     sfp_path.unlink()
            #     return None
            
            # Query sfp-cli (async to not block event loop)
            # FFmpegAudioService handles the downsampling to 5512Hz mono internally
            duration = int(audio.duration)
            result = await self._run_cli_command_async("query", str(wav_path), str(duration), "0")
            
            # Clean up temp file
            wav_path.unlink()

            
            recognition_time = time.time()
            
            if not result.get("matched"):
                logger.debug(f"Local: No match")
                return None
            
            # Extract best match from multi-match response format
            # New format: {"matched": true, "bestMatch": {...}, "matches": [...]}
            matches = result.get("matches", [])
            
            # Use multi-match position verification if we have multiple matches
            if len(matches) > 1:
                # Import select_best_match helper
                from .audio_buffer import select_best_match, PositionTracker
                
                # Get expected position from position tracker (if available)
                # The tracker is managed by the engine and passed via class attribute
                expected_position = None
                if hasattr(self, '_position_tracker') and self._position_tracker:
                    expected_position = self._position_tracker.get_expected_position()
                
                best, selection_reason = select_best_match(matches, expected_position)
                logger.debug(f"Multi-match selection: {selection_reason} ({len(matches)} candidates)")
            else:
                # Single match or backward compatibility
                best = result.get("bestMatch", result)
                selection_reason = "single match"
            
            # Log confidence for debugging (engine handles threshold for acceptance)
            confidence = best.get("confidence", 0)
            if confidence < self._min_confidence:
                # Log with song details for easier debugging
                artist = best.get("artist", "Unknown")
                title = best.get("title", "Unknown")
                offset = best.get("trackMatchStartsAt", 0)
                logger.debug(
                    f"Local: Match below threshold | "
                    f"{artist} - {title} | "
                    f"Offset: {offset:.1f}s | "
                    f"Conf: {confidence:.2f} < {self._min_confidence}"
                )
                # NOTE: We still return the match - engine handles validation/verification
                # for low confidence matches via Reaper validation or multi-match
            
            # Build RecognitionResult from best match
            track_offset = best.get("trackMatchStartsAt", 0)
            
            # CRITICAL: Adjust capture_start_time for buffered audio
            # queryMatchStartsAt tells us where in OUR QUERY the match was found
            # This allows correct latency compensation when using rolling buffer
            query_match_offset = best.get("queryMatchStartsAt", 0)
            adjusted_capture_start = audio.capture_start_time + query_match_offset
            
            recognition = RecognitionResult(
                title=best.get("title", "Unknown"),
                artist=best.get("artist", "Unknown"),
                offset=float(track_offset),
                capture_start_time=adjusted_capture_start,  # Adjusted for buffer
                recognition_time=recognition_time,
                confidence=confidence,
                time_skew=0.0,
                frequency_skew=0.0,
                track_id=best.get("songId"),
                album=best.get("album"),
                album_art_url=None,  # Will be enriched later
                isrc=best.get("isrc"),  # Now provided by sfp-cli
                shazam_url=None,
                spotify_url=None,
                background_image_url=None,
                genre=best.get("genre"),  # Now provided by sfp-cli
                shazam_lyrics_text=None,
                recognition_provider="local_fingerprint",
                duration=best.get("duration")
            )
            
            latency = recognition.get_latency()
            current_pos = recognition.get_current_position()
            
            # Update position tracker for next recognition
            if hasattr(self, '_position_tracker') and self._position_tracker:
                self._position_tracker.update(current_pos, best.get("songId", ""))
            
            logger.info(
                f"Local: {recognition.artist} - {recognition.title} | "
                f"Offset: {track_offset:.1f}s | QueryOffset: {query_match_offset:.1f}s | "
                f"Current: {current_pos:.1f}s | Conf: {confidence:.2f}"
            )
            
            return recognition
            
        except Exception as e:
            logger.error(f"Local recognition failed: {e}")
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        return self._run_cli_command_sync("stats")
