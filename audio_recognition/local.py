"""
Local Audio Fingerprinting Module

Uses SoundFingerprinting (via sfp-cli) for instant, offline recognition
of songs in the user's local music library.

This module is ENV-guarded and only loaded if LOCAL_FP_ENABLED=true.
"""

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
    
    def __init__(self, db_path: Optional[Path] = None, cli_path: Optional[Path] = None, min_confidence: float = 0.5):
        """
        Initialize local fingerprint recognizer.
        
        Args:
            db_path: Path to fingerprint database (default: from config)
            cli_path: Path to sfp-cli directory (default: from config)
            min_confidence: Minimum confidence threshold (default: 0.5)
        """
        # Lazy load config to avoid circular imports
        from config import LOCAL_FINGERPRINT
        
        self._db_path = db_path or LOCAL_FINGERPRINT["db_path"]
        self._cli_path = cli_path or LOCAL_FINGERPRINT["cli_path"]
        self._min_confidence = min_confidence or LOCAL_FINGERPRINT["min_confidence"]
        self._available = None  # Lazy check
        self._exe_path = None  # Path to built executable
        
        logger.info(f"LocalRecognizer initialized: db={self._db_path}, min_conf={self._min_confidence}")
    
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
        - Database has at least 1 indexed song
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
            
            # Check if database has songs
            result = self._run_cli_command("stats")
            if result.get("error"):
                logger.warning(f"sfp-cli stats failed: {result.get('error')}")
                self._available = False
                return False
            
            song_count = result.get("songCount", 0)
            if song_count == 0:
                logger.info("Local fingerprint database is empty")
                self._available = False
                return False
            
            logger.info(f"Local fingerprinting available: {song_count} songs indexed")
            self._available = True
            return True
            
        except Exception as e:
            logger.warning(f"Local fingerprinting check failed: {e}")
            self._available = False
            return False
    
    def _run_cli_command(self, command: str, *args) -> Dict[str, Any]:
        """Run sfp-cli command and return JSON result."""
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
            # Convert AudioChunk to WAV file for sfp-cli
            with tempfile.NamedTemporaryFile(suffix="_raw.wav", delete=False) as raw_file:
                raw_path = Path(raw_file.name)
                
                # Write raw WAV
                with wave.open(str(raw_path), 'wb') as wf:
                    wf.setnchannels(audio.channels)
                    wf.setsampwidth(2)  # int16
                    wf.setframerate(audio.sample_rate)
                    wf.writeframes(audio.data.tobytes())
            
            # Convert to SFP format (5512Hz mono)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as sfp_file:
                sfp_path = Path(sfp_file.name)
            
            # Hide console window on Windows
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            
            ffmpeg_result = subprocess.run(
                ["ffmpeg", "-i", str(raw_path)] + self.FFMPEG_ARGS + [str(sfp_path), "-y"],
                capture_output=True,
                timeout=10,
                creationflags=creationflags
            )
            
            # Clean up raw file
            raw_path.unlink()
            
            if ffmpeg_result.returncode != 0:
                logger.warning("FFmpeg conversion failed for local recognition")
                sfp_path.unlink()
                return None
            
            # Query sfp-cli
            duration = int(audio.duration)
            result = self._run_cli_command("query", str(sfp_path), str(duration), "0")
            
            # Clean up
            sfp_path.unlink()
            
            recognition_time = time.time()
            
            if not result.get("matched"):
                logger.debug(f"Local: No match")
                return None
            
            # Check confidence threshold
            confidence = result.get("confidence", 0)
            if confidence < self._min_confidence:
                # Log with song details for easier debugging
                artist = result.get("artist", "Unknown")
                title = result.get("title", "Unknown")
                offset = result.get("trackMatchStartsAt", 0)
                logger.debug(
                    f"Local: Match below threshold | "
                    f"{artist} - {title} | "
                    f"Offset: {offset:.1f}s | "
                    f"Conf: {confidence:.2f} < {self._min_confidence}"
                )
                # return None
            
            # Build RecognitionResult
            offset = result.get("trackMatchStartsAt", 0)
            
            recognition = RecognitionResult(
                title=result.get("title", "Unknown"),
                artist=result.get("artist", "Unknown"),
                offset=float(offset),
                capture_start_time=audio.capture_start_time,
                recognition_time=recognition_time,
                confidence=confidence,
                time_skew=0.0,
                frequency_skew=0.0,
                track_id=result.get("songId"),
                album=result.get("album"),
                album_art_url=None,  # Will be enriched later
                isrc=None,
                shazam_url=None,
                spotify_url=None,
                background_image_url=None,
                genre=None,
                shazam_lyrics_text=None,
                recognition_provider="local_fingerprint",
                duration=result.get("duration")
            )
            
            latency = recognition.get_latency()
            current_pos = recognition.get_current_position()
            
            logger.info(
                f"Local: {recognition.artist} - {recognition.title} | "
                f"Offset: {offset:.1f}s | Latency: {latency:.1f}s | "
                f"Current: {current_pos:.1f}s | Conf: {confidence:.2f}"
            )
            
            return recognition
            
        except Exception as e:
            logger.error(f"Local recognition failed: {e}")
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        return self._run_cli_command("stats")
