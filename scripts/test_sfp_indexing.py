"""
SoundFingerprinting Database Manager v2.1

Comprehensive tool for local audio fingerprinting using sfp-cli.
Now with:
- Interactive CLI mode with daemon reuse
- Full metadata extraction (all tags)
- Content hash deduplication (90-sec audio hash)
- Database verification and repair
- indexed_files.json tracking
- skip_log.json for skipped files
- Configurable database path

Usage:
    python scripts/test_sfp_indexing.py --cli              Interactive CLI mode (recommended)
    python scripts/test_sfp_indexing.py --index <folder>   Index all songs in folder
    python scripts/test_sfp_indexing.py --test <folder>    Test recognition accuracy
    python scripts/test_sfp_indexing.py --live             Test live audio capture
    python scripts/test_sfp_indexing.py --verify           Verify database integrity
    python scripts/test_sfp_indexing.py --repair           Repair database discrepancies
    python scripts/test_sfp_indexing.py --stats            Show database stats
    python scripts/test_sfp_indexing.py --clear            Clear database
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mutagen import File as MutagenFile
from logging_config import get_logger

logger = get_logger(__name__)

# Configuration
SFP_CLI_DIR = Path(__file__).parent.parent / "audio_recognition" / "sfp-cli"
SFP_PUBLISH_DIR = SFP_CLI_DIR / "bin" / "publish"

# Default database path (can be overridden via --db-path or SFP_DB_PATH env)
DEFAULT_DB_PATH = Path(__file__).parent.parent / "local_fingerprint_database"

# FFmpeg conversion settings for SoundFingerprinting
# Uses 8000 Hz mono to match sfp-cli's FingerprintConfig.SampleRate
FFMPEG_ARGS = ["-ac", "1", "-ar", "8000", "-loglevel", "warning"]

# File filtering
MAX_DURATION_MINUTES = 20  # Skip files longer than this
SUPPORTED_EXTENSIONS = ['.flac', '.mp3', '.wav', '.m4a', '.ogg']


def get_db_path() -> Path:
    """Get database path from environment or default."""
    env_path = os.getenv("SFP_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_sfp_exe() -> Optional[Path]:
    """Get path to pre-built sfp-cli executable, building if needed."""
    exe_name = "sfp-cli.exe" if sys.platform == "win32" else "sfp-cli"
    exe_path = SFP_PUBLISH_DIR / exe_name
    
    if exe_path.exists():
        return exe_path
    
    # Build the executable
    print("Building sfp-cli executable (one-time)...")
    try:
        result = subprocess.run(
            ["dotnet", "publish", "-c", "Release", "-o", str(SFP_PUBLISH_DIR)],
            cwd=str(SFP_CLI_DIR),
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            print(f"Build failed: {result.stderr}")
            return None
        
        if exe_path.exists():
            print(f"Built: {exe_path}")
            return exe_path
        else:
            print(f"Build succeeded but exe not found at {exe_path}")
            return None
            
    except Exception as e:
        print(f"Build failed: {e}")
        return None


def run_sfp_command(db_path: Path, command: str, *args) -> Dict[str, Any]:
    """Run sfp-cli command and return JSON result."""
    exe_path = get_sfp_exe()
    if exe_path is None:
        return {"error": "sfp-cli executable not available"}
    
    # Ensure db_path is absolute
    abs_db_path = db_path.absolute()
    cmd = [str(exe_path), "--db-path", str(abs_db_path), command] + list(args)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        # Parse JSON from stdout (ignore stderr which has progress messages)
        stdout = result.stdout.strip()
        
        # Find JSON in output (may have progress messages before it)
        for line in stdout.split('\n'):
            line = line.strip()
            if line.startswith('{'):
                return json.loads(line)
        
        # If no JSON found, return error
        return {"error": f"No JSON output. stdout: {stdout[:200]}, stderr: {result.stderr[:200]}"}
        
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out (5 min)"}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
    except Exception as e:
        return {"error": str(e)}


class IndexingDaemon:
    """
    Daemon-based indexing for 8x faster fingerprinting.
    
    Supports two connection modes:
    1. TCP: Connect to existing daemon (started by SyncLyrics) on port 9123
    2. Subprocess: Start own daemon via stdin/stdout
    
    Features:
    - Auto-detect existing daemon via TCP
    - Retry logic (max 3 attempts)
    - Auto-restart on crash
    - Save every N files
    
    Usage:
        daemon = IndexingDaemon(db_path)
        daemon.start()  # Will connect via TCP if SyncLyrics daemon running
        for file in audio_files:
            result = daemon.fingerprint(file, metadata)
            if i % 5 == 0:  # Save every 5 files
                daemon.save()
        daemon.stop()
    """
    
    MAX_RESTART_ATTEMPTS = 3
    STARTUP_TIMEOUT = 120  # seconds
    COMMAND_TIMEOUT = 60   # seconds per file
    TCP_PORT = 9123        # Port to connect to existing daemon
    
    def __init__(self, db_path: Path):
        self.db_path = db_path.absolute()
        self.exe_path = get_sfp_exe()
        self.process: Optional[subprocess.Popen] = None
        self._ready = False
        self._song_count = 0
        self._restart_count = 0
        
        # TCP connection (when connecting to existing daemon)
        self._tcp_socket: Optional['socket.socket'] = None
        self._using_tcp = False
    
    @property
    def is_running(self) -> bool:
        """Check if daemon is available (via TCP or subprocess)."""
        if self._using_tcp and self._tcp_socket:
            return True
        return self.process is not None and self.process.poll() is None
    
    def start(self) -> bool:
        """
        Start daemon connection with TCP-first strategy.
        
        1. First, try to connect to existing daemon via TCP (port 9123)
        2. If no daemon running, start our own via subprocess
        
        Returns True if successful.
        """
        # First, try TCP connection to existing daemon
        if self._try_tcp_connect():
            self._using_tcp = True
            print(f"✅ Connected to existing daemon via TCP (port {self.TCP_PORT})")
            return True
        
        # No existing daemon, start our own via subprocess
        self._using_tcp = False
        return self._start_subprocess()
    
    def _try_tcp_connect(self) -> bool:
        """Try to connect to existing daemon via TCP. Returns True if successful."""
        import socket
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)  # Quick timeout for connection attempt
            print(f"   Trying TCP connection to 127.0.0.1:{self.TCP_PORT}...")
            sock.connect(('127.0.0.1', self.TCP_PORT))
            print(f"   TCP connected! Waiting for handshake...")
            sock.settimeout(30)  # Normal timeout for operations
            
            # Read ready/connected message with proper buffering
            data = b''
            while b'\n' not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    print(f"   TCP: Server closed connection during handshake")
                    sock.close()
                    return False
                data += chunk
            
            # Extract first line, preserve any remainder for future reads
            line_bytes, remainder = data.split(b'\n', 1)
            # Use utf-8-sig to strip BOM that C# StreamWriter adds
            line = line_bytes.decode('utf-8-sig').strip()
            print(f"   TCP handshake received: {line[:100]}...")
            response = json.loads(line)
            
            if response.get('status') == 'connected':
                self._tcp_socket = sock
                self._tcp_buffer = remainder  # Initialize buffer with any leftover bytes
                self._ready = True
                self._song_count = response.get('songs', 0)
                print(f"   ✅ TCP connected to daemon ({self._song_count} songs)")
                return True
            else:
                print(f"   TCP: Unexpected status: {response.get('status')}")
            
            sock.close()
            return False
        
        except ConnectionRefusedError:
            print(f"   TCP: Connection refused (daemon not listening on port {self.TCP_PORT})")
            return False
        except socket.timeout:
            print(f"   TCP: Connection timed out")
            return False
        except json.JSONDecodeError as e:
            print(f"   TCP: Invalid JSON in handshake: {e}")
            return False
        except Exception as e:
            print(f"   TCP: Failed with {type(e).__name__}: {e}")
            return False
    
    def _start_subprocess(self) -> bool:
        """Start own daemon via subprocess."""
        if self.exe_path is None:
            print("❌ sfp-cli executable not available")
            return False
        
        if self.is_running:
            print("⚠️  Daemon already running")
            return True
        
        # Retry loop
        while self._restart_count < self.MAX_RESTART_ATTEMPTS:
            self._restart_count += 1
            print(f"🚀 Starting indexing daemon (attempt {self._restart_count}/{self.MAX_RESTART_ATTEMPTS})...")
            
            if self._try_start():
                self._restart_count = 0  # Reset on success
                return True
            
            print(f"   Retry in 2 seconds...")
            time.sleep(2)
        
        print(f"❌ Failed to start daemon after {self.MAX_RESTART_ATTEMPTS} attempts")
        return False
    
    def _try_start(self) -> bool:
        """Single attempt to start daemon subprocess. Returns True if successful."""
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            
            self.process = subprocess.Popen(
                [str(self.exe_path), "--db-path", str(self.db_path), "serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,  # Line buffered
                creationflags=creationflags
            )
            
            # Wait for ready signal
            start_time = time.time()
            while time.time() - start_time < self.STARTUP_TIMEOUT:
                if self.process.poll() is not None:
                    print(f"   ❌ Daemon exited during startup")
                    return False
                
                line = self.process.stdout.readline()
                if line:
                    try:
                        data = json.loads(line.strip())
                        if data.get("status") == "ready":
                            self._ready = True
                            self._song_count = data.get("songs", 0)
                            print(f"✅ Daemon ready: {self._song_count} songs indexed")
                            return True
                    except json.JSONDecodeError:
                        pass  # Ignore non-JSON output
            
            print("   ❌ Daemon startup timeout")
            self._kill_process()
            return False
            
        except Exception as e:
            print(f"   ❌ Failed to start daemon: {e}")
            self._kill_process()
            return False
    
    def _kill_process(self):
        """Kill daemon subprocess if running."""
        if self.process is not None:
            try:
                self.process.kill()
            except:
                pass
            self.process = None
            self._ready = False
    
    def _send_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Send command via TCP or subprocess, return response."""
        if self._using_tcp and self._tcp_socket:
            return self._send_tcp_command(cmd)
        else:
            return self._send_subprocess_command(cmd)
    
    def _send_tcp_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Send command via TCP socket with proper buffering."""
        try:
            cmd_json = json.dumps(cmd) + '\n'
            self._tcp_socket.sendall(cmd_json.encode('utf-8'))
            
            # Read response with persistent buffer (preserves extra bytes)
            # Initialize buffer if not exists
            if not hasattr(self, '_tcp_buffer'):
                self._tcp_buffer = b''
            
            # Read until we have at least one complete line
            while b'\n' not in self._tcp_buffer:
                chunk = self._tcp_socket.recv(65536)
                if not chunk:
                    return {"success": False, "error": "Connection closed"}
                self._tcp_buffer += chunk
            
            # Extract first line, keep remainder in buffer
            line_bytes, self._tcp_buffer = self._tcp_buffer.split(b'\n', 1)
            line = line_bytes.decode('utf-8').strip()
            return json.loads(line)
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _send_subprocess_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Send command via subprocess stdin/stdout."""
        if not self._ready or self.process is None:
            return {"success": False, "error": "Daemon not ready"}
        
        try:
            self.process.stdin.write(json.dumps(cmd) + "\n")
            self.process.stdin.flush()
            
            response = self.process.stdout.readline()
            if response:
                return json.loads(response.strip())
            else:
                return {"success": False, "error": "No response from daemon"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def fingerprint(self, audio_path: Path, metadata: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
        """
        Send fingerprint command to daemon.
        
        Args:
            audio_path: Path to audio file (FLAC/MP3/WAV)
            metadata: Dictionary with songId, title, artist, etc.
            force: If True, overwrite existing entry (for re-indexing)
        
        Returns:
            Result dict with success/error info
        """
        if not self._ready:
            return {"success": False, "error": "Daemon not ready"}
        
        cmd = {
            "cmd": "fingerprint",
            "path": str(audio_path.absolute()),
            "metadata": metadata,
            "force": force
        }
        
        return self._send_command(cmd)
    
    def fingerprint_batch(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send batch fingerprint command for parallel processing (8 concurrent in C#).
        
        Args:
            files: List of dicts with 'path' (Path) and 'metadata' (dict) keys
        
        Returns:
            Result dict with processed count and individual results
        """
        if not self._ready:
            return {"success": False, "error": "Daemon not ready"}
        
        # Convert paths to strings
        files_json = []
        for f in files:
            files_json.append({
                "path": str(f["path"].absolute()) if isinstance(f["path"], Path) else str(f["path"]),
                "metadata": f["metadata"]
            })
        
        cmd = {
            "cmd": "fingerprint-batch",
            "files": files_json
        }
        
        result = self._send_command(cmd)
        if result.get("success"):
            self._song_count = result.get("successCount", 0) + self._song_count
        return result
    
    def save(self) -> Dict[str, Any]:
        """Tell daemon to save database to disk."""
        if not self._ready:
            return {"status": "error", "error": "Daemon not ready"}
        
        result = self._send_command({"cmd": "save"})
        if result.get("success"):
            self._song_count = result.get("songCount", self._song_count)
        return result
    
    def list_fp(self) -> Dict[str, Any]:
        """Get list of song IDs from fingerprint database."""
        if not self._ready:
            return {"error": "Daemon not ready"}
        return self._send_command({"cmd": "list-fp"})
    
    def delete(self, song_id: str) -> Dict[str, Any]:
        """Delete a song from fingerprint DB and daemon's metadata."""
        if not self._ready:
            return {"success": False, "error": "Daemon not ready"}
        return self._send_command({"cmd": "delete", "songId": song_id})
    
    def reload(self) -> Dict[str, Any]:
        """Full reload: fingerprint DB + metadata from disk."""
        if not self._ready:
            return {"error": "Daemon not ready"}
        return self._send_command({"cmd": "reload"})
    
    def refresh(self) -> Dict[str, Any]:
        """Refresh metadata only (lighter than full reload)."""
        if not self._ready:
            return {"error": "Daemon not ready"}
        return self._send_command({"cmd": "refresh"})
    
    def stats(self) -> Dict[str, Any]:
        """Get database statistics from daemon."""
        if not self._ready:
            return {"error": "Daemon not ready"}
        return self._send_command({"cmd": "stats"})
    
    def query(self, audio_path: Path, duration: int = 10, offset: int = 0) -> Dict[str, Any]:
        """
        Query audio file for recognition.
        
        Args:
            audio_path: Path to audio file (WAV)
            duration: Duration of clip in seconds
            offset: Start offset in seconds
        
        Returns:
            Recognition result from daemon
        """
        if not self._ready:
            return {"error": "Daemon not ready"}
        return self._send_command({
            "cmd": "query",
            "path": str(audio_path),
            "duration": str(duration),
            "offset": str(offset)
        })
    
    @property
    def connection_mode(self) -> str:
        """Return current connection mode: 'TCP', 'Subprocess', or 'Disconnected'."""
        if self._using_tcp and self._tcp_socket:
            return "TCP"
        elif self.process is not None and self.process.poll() is None:
            return "Subprocess"
        else:
            return "Disconnected"
    
    def stop(self):
        """
        Shutdown/disconnect from daemon.
        
        - TCP mode: Just close socket (doesn't shutdown daemon)
        - Subprocess mode: Send shutdown command
        """
        if self._using_tcp and self._tcp_socket:
            # TCP mode: just disconnect (don't shutdown SyncLyrics' daemon!)
            try:
                self._tcp_socket.close()
            except:
                pass
            self._tcp_socket = None
            self._ready = False
            print(f"✅ Disconnected from shared daemon")
            return
        
        # Subprocess mode: send shutdown
        if self.process is None:
            return
        
        try:
            self.process.stdin.write('{"cmd": "shutdown"}\n')
            self.process.stdin.flush()
            self.process.wait(timeout=30)
            print(f"✅ Daemon shutdown complete")
        except:
            self.process.kill()
        finally:
            self.process = None
            self._ready = False
    
    @property
    def song_count(self) -> int:
        return self._song_count


def convert_to_wav(input_path: Path, output_path: Path, start_sec: float = 0, duration_sec: float = 0) -> bool:
    """
    Convert audio file to WAV using ffmpeg.
    
    Args:
        input_path: Source audio file (FLAC, MP3, etc.)
        output_path: Destination WAV file
        start_sec: Start time in seconds (0 = from beginning)
        duration_sec: Duration in seconds (0 = entire file)
    
    Returns:
        True if successful
    """
    cmd = ["ffmpeg", "-i", str(input_path), "-loglevel", "error"]
    
    if start_sec > 0:
        cmd.extend(["-ss", str(start_sec)])
    
    if duration_sec > 0:
        cmd.extend(["-t", str(duration_sec)])
    
    cmd.extend(FFMPEG_ARGS)
    cmd.extend([str(output_path), "-y"])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"FFmpeg conversion failed: {e}")
        return False


def compute_content_hash(file_path: Path, duration_seconds: int = 90) -> Optional[str]:
    """
    Compute content hash from first N seconds of decoded audio.
    
    This is used for deduplication - same audio content will have same hash
    regardless of file format, bitrate, or metadata differences.
    """
    try:
        # Use ffmpeg to extract first N seconds as raw PCM
        cmd = [
            "ffmpeg", "-i", str(file_path), "-loglevel", "error",
            "-t", str(duration_seconds),
            "-ac", "1", "-ar", "8000",  # Low quality for hashing
            "-f", "s16le", "-"  # Output raw PCM to stdout
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return None
        
        # Hash the raw audio bytes
        return hashlib.sha256(result.stdout).hexdigest()[:16]
        
    except Exception as e:
        logger.warning(f"Could not compute content hash for {file_path}: {e}")
        return None


def normalize_song_id(artist: str, title: str) -> str:
    """
    Generate a normalized song ID from artist and title.
    Matches the _normalize_track_id function in system_utils/helpers.py
    """
    if not artist:
        artist = ""
    if not title:
        title = ""
    
    # Lowercase and keep only alphanumeric
    norm_artist = "".join(c for c in artist.lower() if c.isalnum())
    norm_title = "".join(c for c in title.lower() if c.isalnum())
    return f"{norm_artist}_{norm_title}"


def extract_full_metadata(file_path: Path, filename_fallback: bool = False) -> Dict[str, Any]:
    """
    Extract all available metadata from audio file using mutagen.
    
    Args:
        file_path: Path to audio file
        filename_fallback: If True, parse filename when tags unavailable
    
    Returns dict with all fields needed by sfp-cli.
    """
    metadata = {
        'title': None,
        'artist': None,
        'album': None,
        'albumArtist': None,
        'duration': None,
        'trackNumber': None,
        'discNumber': None,
        'genre': None,
        'year': None,
        'isrc': None,
        'originalFilepath': str(file_path.absolute()),
    }
    
    try:
        audio = MutagenFile(file_path)
        if audio is None:
            # File format not recognized by mutagen
            if filename_fallback:
                parsed = parse_filename(file_path)
                metadata['title'] = parsed.get('title')
                metadata['artist'] = parsed.get('artist')
            # If not filename_fallback, return with None title/artist - caller will skip
            return metadata
        
        # Get duration
        if hasattr(audio.info, 'length'):
            metadata['duration'] = round(audio.info.length, 2)
        
        # Try common tag formats
        if hasattr(audio, 'tags') and audio.tags:
            tags = audio.tags
            
            # FLAC/Vorbis style (case-insensitive dict-like)
            def get_tag(names):
                for name in names:
                    if name in tags:
                        val = tags[name]
                        if isinstance(val, list) and val:
                            return str(val[0])
                        elif val:
                            return str(val)
                return None
            
            metadata['title'] = get_tag(['title', 'TITLE', 'TIT2'])
            metadata['artist'] = get_tag(['artist', 'ARTIST', 'TPE1'])
            metadata['album'] = get_tag(['album', 'ALBUM', 'TALB'])
            metadata['albumArtist'] = get_tag(['albumartist', 'album_artist', 'ALBUMARTIST', 'TPE2'])
            metadata['genre'] = get_tag(['genre', 'GENRE', 'TCON'])
            metadata['year'] = get_tag(['date', 'DATE', 'year', 'YEAR', 'TDRC'])
            metadata['isrc'] = get_tag(['isrc', 'ISRC', 'TSRC'])
            
            # Track number
            track = get_tag(['tracknumber', 'TRACKNUMBER', 'TRCK'])
            if track:
                # Handle "1/12" format
                if '/' in track:
                    track = track.split('/')[0]
                try:
                    metadata['trackNumber'] = int(track)
                except ValueError:
                    pass
            
            # Disc number
            disc = get_tag(['discnumber', 'DISCNUMBER', 'TPOS'])
            if disc:
                if '/' in disc:
                    disc = disc.split('/')[0]
                try:
                    metadata['discNumber'] = int(disc)
                except ValueError:
                    pass
        
        # NO FALLBACK TO FILENAME - per plan, files without tags should be skipped
        # The calling code will check for missing title/artist and skip the file
        
    except Exception as e:
        logger.warning(f"Could not read metadata from {file_path}: {e}")
        # Return with None title/artist - caller will skip this file
    
    return metadata


def parse_filename(file_path: Path) -> Dict[str, str]:
    """
    Parse metadata from filename.
    
    Expected formats:
    - "01. Artist - Title.flac"
    - "Artist - Title.flac"
    """
    name = file_path.stem
    
    # Remove track number prefix like "01. " or "01 - "
    name = re.sub(r'^\d+[\.\-\s]+', '', name)
    
    # Split by " - "
    if ' - ' in name:
        parts = name.split(' - ', 1)
        return {
            'artist': parts[0].strip(),
            'title': parts[1].strip(),
        }
    
    # Fallback: use filename as title
    return {
        'artist': None,
        'title': name,
    }


def load_json_file(path: Path) -> Dict:
    """Load JSON file or return empty dict."""
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_json_file(path: Path, data: Dict):
    """Save data to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================================
# SESSION LOGGING - Track index/reindex operations for undo
# ============================================================================

def save_session_log(db_path: Path, action: str, folder: Path, 
                     added_songs: List[Dict], flags: List[str] = None) -> Path:
    """
    Save a session log after index/reindex operation.
    
    Args:
        db_path: Database path
        action: 'index' or 'reindex'
        folder: Folder that was indexed
        added_songs: List of dicts with 'songId' and 'filepath'
        flags: Optional list of flags used (e.g., ['--force'])
    
    Returns:
        Path to the saved session log
    """
    sessions_dir = db_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now()
    session_id = timestamp.strftime("%Y%m%d_%H%M%S")
    
    session = {
        'id': session_id,
        'action': action,
        'folder': str(folder),
        'timestamp': timestamp.isoformat(),
        'flags': flags or [],
        'added_songs': added_songs,
        'count': len(added_songs),
        'undone': False
    }
    
    session_path = sessions_dir / f"session_{session_id}.json"
    save_json_file(session_path, session)
    return session_path


def load_session_logs(db_path: Path, limit: int = 20) -> List[Dict]:
    """
    Load recent session logs, most recent first.
    
    Args:
        db_path: Database path
        limit: Maximum number of sessions to return
    
    Returns:
        List of session dicts, sorted by timestamp descending
    """
    sessions_dir = db_path / "sessions"
    if not sessions_dir.exists():
        return []
    
    sessions = []
    for session_file in sessions_dir.glob("session_*.json"):
        try:
            session = load_json_file(session_file)
            session['_path'] = str(session_file)
            sessions.append(session)
        except:
            pass
    
    # Sort by timestamp descending (most recent first)
    sessions.sort(key=lambda s: s.get('timestamp', ''), reverse=True)
    return sessions[:limit]


def undo_session(db_path: Path, session_id: str, daemon) -> Dict:
    """
    Undo a session by purging all songs added in that session.
    
    Args:
        db_path: Database path
        session_id: Session ID to undo
        daemon: Active IndexingDaemon for deleting songs
    
    Returns:
        Dict with 'success', 'deleted', 'errors'
    """
    sessions_dir = db_path / "sessions"
    session_path = sessions_dir / f"session_{session_id}.json"
    
    if not session_path.exists():
        return {'success': False, 'error': f"Session not found: {session_id}"}
    
    session = load_json_file(session_path)
    
    if session.get('undone'):
        return {'success': False, 'error': "Session already undone"}
    
    added_songs = session.get('added_songs', [])
    if not added_songs:
        return {'success': False, 'error': "No songs to undo in this session"}
    
    # Delete each song
    deleted = 0
    errors = []
    indexed_files_path = db_path / "indexed_files.json"
    indexed_files = load_json_file(indexed_files_path)
    
    for song in added_songs:
        song_id = song.get('songId')
        filepath = song.get('filepath')
        
        if not song_id:
            continue
        
        try:
            # Delete from daemon (handles FP DB and metadata)
            if daemon and daemon.is_running:
                result = daemon.delete(song_id)
                if result.get('success'):
                    deleted += 1
                else:
                    errors.append(f"{song_id}: {result.get('error', 'Unknown')}")
            else:
                errors.append(f"{song_id}: Daemon not running")
            
            # Remove from indexed_files
            if filepath and filepath in indexed_files:
                del indexed_files[filepath]
        except Exception as e:
            errors.append(f"{song_id}: {str(e)}")
    
    # Save updated indexed_files
    save_json_file(indexed_files_path, indexed_files)
    
    # Mark session as undone
    session['undone'] = True
    session['undone_at'] = datetime.now(timezone.utc).isoformat()
    save_json_file(session_path, session)
    
    # Save daemon changes
    if daemon and daemon.is_running:
        daemon.save()
    
    return {
        'success': True,
        'deleted': deleted,
        'total': len(added_songs),
        'errors': errors
    }


def index_folder(folder_path: Path, db_path: Path, extensions: List[str] = None, 
                 required_tags: List[str] = None, dry_run: bool = False,
                 daemon: 'IndexingDaemon' = None, filename_fallback: bool = False,
                 force_include: bool = False) -> Dict[str, Any]:
    """
    Index all audio files in a folder.
    
    Args:
        folder_path: Path to folder containing audio files
        db_path: Path to database directory
        extensions: List of extensions to include
        required_tags: Optional list of additional required metadata fields
                       (e.g., ['album', 'genre']). Artist and title are always required.
        dry_run: If True, only show what would be indexed without making changes
        daemon: Optional existing IndexingDaemon to reuse (avoids creating duplicate)
        filename_fallback: If True, use filename parsing when tags are missing
        force_include: If True, ignore exclusion list (index all matching files)
    
    Returns:
        Summary dict with results
    """
    if extensions is None:
        extensions = SUPPORTED_EXTENSIONS
    
    # Ensure directories exist
    db_path.mkdir(parents=True, exist_ok=True)
    temp_dir = db_path / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Load tracking files
    indexed_files_path = db_path / "indexed_files.json"
    skip_log_path = db_path / "skip_log.json"
    
    indexed_files = load_json_file(indexed_files_path)
    skip_log = load_json_file(skip_log_path)  # Dict keyed by filepath
    
    # Track excluded files for dry-run display
    excluded_files = []  # List of {songId, artist, title, filepath, reason}
    
    # Find all audio files
    audio_files = []
    for ext in extensions:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    print(f"\n=== Indexing {len(audio_files)} files from {folder_path} ===\n")
    print(f"Database: {db_path}")
    print(f"Already indexed: {len(indexed_files)} files")
    print(f"Using batch mode (8 files parallel) for maximum speed...\n")
    
    # Load exclusion list for checking
    exclusions = load_exclusion_list(db_path)
    excluded_ids = set(exclusions.get('songIds', []))
    excluded_patterns = exclusions.get('patterns', [])
    if excluded_ids or excluded_patterns:
        print(f"📋 Exclusion list: {len(excluded_ids)} IDs, {len(excluded_patterns)} patterns")
    
    results = {
        'total': len(audio_files),
        'indexed': 0,
        'skipped': 0,
        'excluded': 0,
        'failed': 0,
        'songs': [],
        'errors': []
    }
    
    # Start daemon for fast fingerprinting (or reuse existing if running)
    own_daemon = daemon is None or not daemon.is_running
    if own_daemon:
        daemon = IndexingDaemon(db_path)
        if not daemon.start():
            print("❌ Failed to start indexing daemon")
            return {'error': 'Daemon startup failed', **results}
    else:
        print("✅ Using active CLI daemon")
    
    BATCH_SIZE = 8
    total_files = len(audio_files)
    
    # First pass: prepare all files (filter, extract metadata, compute hashes)
    print(f"Phase 1: Preparing files (metadata + content hash)...")
    prepared_files = []
    skipped_in_pass = 0
    
    for i, audio_file in enumerate(audio_files, 1):
        file_key = str(audio_file.absolute())
        
        # Progress output every 10 files or for small batches
        if i % 10 == 1 or total_files < 20:
            print(f"  [{i}/{total_files}] Scanning {audio_file.name[:50]}...")
        
        # Skip if already indexed by filepath
        if file_key in indexed_files:
            results['skipped'] += 1
            skipped_in_pass += 1
            continue
        
        # NOTE: skip_log check disabled - re-indexing now only requires removing from indexed_files.json
        # If you want to use skip_log, uncomment the following block:
        # if file_key in skip_log:
        #     results['skipped'] += 1
        #     skipped_in_pass += 1
        #     continue
        
        # Extract metadata (with optional filename fallback)
        metadata = extract_full_metadata(audio_file, filename_fallback=filename_fallback)
        
        # Skip if missing required tags
        if not metadata['title'] or not metadata['artist']:
            skip_log[file_key] = {
                'reason': 'Missing required tags (artist or title)',
                'skippedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }
            results['skipped'] += 1
            continue
        
        # Skip if missing additional required tags
        if required_tags:
            missing_tags = []
            for tag in required_tags:
                tag_lower = tag.lower()
                tag_key = tag_lower
                if tag_lower == 'year':
                    tag_key = 'year'
                value = metadata.get(tag_key)
                if not value:
                    missing_tags.append(tag)
            
            if missing_tags:
                reason = f"Missing required tags: {', '.join(missing_tags)}"
                skip_log[file_key] = {
                    'reason': reason,
                    'skippedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                }
                results['skipped'] += 1
                continue
        
        # Skip if too long
        if metadata['duration'] and metadata['duration'] > MAX_DURATION_MINUTES * 60:
            duration_min = metadata['duration'] / 60
            skip_log[file_key] = {
                'reason': f'Duration exceeds limit ({duration_min:.1f} min > {MAX_DURATION_MINUTES} min)',
                'skippedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }
            results['skipped'] += 1
            continue
        
        # Generate song ID and content hash
        song_id = normalize_song_id(metadata['artist'], metadata['title'])
        metadata['songId'] = song_id
        
        # Check exclusion list (both explicit IDs and patterns) - skip if force_include
        if not force_include and is_excluded(song_id, metadata['artist'], metadata['title'], exclusions):
            excluded_files.append({
                'songId': song_id,
                'artist': metadata['artist'],
                'title': metadata['title'],
                'filepath': file_key,
                'reason': 'Excluded by ID or pattern'
            })
            results['excluded'] += 1
            continue
        
        content_hash = compute_content_hash(audio_file)
        metadata['contentHash'] = content_hash
        metadata['originalFilepath'] = file_key
        
        prepared_files.append({
            'path': audio_file,
            'metadata': metadata,
            'file_key': file_key,
            'content_hash': content_hash
        })
    
    excluded_msg = f", excluded {results['excluded']}" if results['excluded'] > 0 else ""
    print(f"\nPhase 1 complete: {len(prepared_files)} files to index (skipped {results['skipped']}{excluded_msg})")
    
    # Show skipped files with reasons (always, not just dry-run) - limit to 50
    if results['skipped'] > 0:
        print(f"\n--- Skipped Files ({results['skipped']}) ---")
        skip_items = list(skip_log.items())
        for filepath, entry in skip_items[:50]:
            reason = entry.get('reason', 'Unknown')
            filename = Path(filepath).name
            print(f"  ❌ {filename}: {reason}")
        if len(skip_items) > 50:
            print(f"  ... and {len(skip_items) - 50} more")
    
    # Show excluded files with details (always, not just dry-run) - limit to 50
    if excluded_files:
        print(f"\n--- Excluded Files ({len(excluded_files)}) ---")
        for exc in excluded_files[:50]:
            print(f"  🚫 {exc['artist']} - {exc['title']} (ID: {exc['songId']})")
        if len(excluded_files) > 50:
            print(f"  ... and {len(excluded_files) - 50} more")
    
    # --- DRY-RUN: Exit after Phase 1 with detailed summary ---
    if dry_run:
        print(f"\n{'=' * 70}")
        print("DRY-RUN MODE - No changes will be made")
        print(f"{'=' * 70}\n")
        
        if prepared_files:
            print(f"Would index {len(prepared_files)} files:\n")
            for f in prepared_files:
                m = f['metadata']
                dur = m.get('duration', 0)
                dur_str = f" ({dur:.0f}s)" if dur else ""
                print(f"  • {m['artist']} - {m['title']}{dur_str}")
                print(f"    ID: {m['songId']}")
                print(f"    File: {f['file_key']}")
        else:
            print("No new files to index.")
        
        print(f"\n{'=' * 40}")
        print("Summary")
        print(f"{'=' * 40}")
        print(f"  Total files found: {results['total']}")
        print(f"  Would index: {len(prepared_files)}")
        print(f"  Skipped: {results['skipped']}")
        if results['excluded'] > 0:
            print(f"  Excluded: {results['excluded']}")
        
        # Show skipped files with reasons (from skip_log entries created this run) - limit to 50
        if results['skipped'] > 0:
            print(f"\n{'=' * 40}")
            print(f"Skipped Files ({results['skipped']})")
            print(f"{'=' * 40}")
            # Show entries that were added/modified in this run
            skip_items = list(skip_log.items())
            for filepath, entry in skip_items[:50]:
                reason = entry.get('reason', 'Unknown')
                filename = Path(filepath).name
                print(f"  ❌ {filename}")
                print(f"     Reason: {reason}")
            if len(skip_items) > 50:
                print(f"  ... and {len(skip_items) - 50} more")
        
        # Show excluded files with details - limit to 50
        if excluded_files:
            print(f"\n{'=' * 40}")
            print(f"Excluded Files ({len(excluded_files)})")
            print(f"{'=' * 40}")
            for exc in excluded_files[:50]:
                print(f"  🚫 {exc['artist']} - {exc['title']}")
                print(f"     ID: {exc['songId']}")
            if len(excluded_files) > 50:
                print(f"  ... and {len(excluded_files) - 50} more")
        
        # Add preview data to results for export
        results['dry_run'] = True
        results['would_index'] = [
            {
                'songId': f['metadata']['songId'],
                'artist': f['metadata']['artist'],
                'title': f['metadata']['title'],
                'duration': f['metadata'].get('duration', 0),
                'filepath': f['file_key']
            }
            for f in prepared_files
        ]
        # Include skipped and excluded in export
        results['skipped_files'] = [
            {'filepath': fp, **entry} for fp, entry in skip_log.items()
        ]
        results['excluded_files'] = excluded_files
        return results
    
    print(f"\nPhase 2: Batch fingerprinting ({BATCH_SIZE} parallel)...")
    
    # Second pass: process in batches of 8
    total_batches = (len(prepared_files) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for batch_num, batch_start in enumerate(range(0, len(prepared_files), BATCH_SIZE), 1):
        batch = prepared_files[batch_start:batch_start + BATCH_SIZE]
        
        print(f"\n[Batch {batch_num}/{total_batches}] Processing {len(batch)} files...")
        
        # Send batch to daemon for parallel processing
        batch_start_time = time.time()
        batch_result = daemon.fingerprint_batch(batch)
        batch_time = time.time() - batch_start_time
        
        if not batch_result.get('success'):
            error = batch_result.get('error', 'Unknown batch error')
            print(f"  ❌ Batch failed: {error}")
            for f in batch:
                results['failed'] += 1
                results['errors'].append({'file': f['file_key'], 'error': error})
            continue
        
        # Process individual results
        batch_results = batch_result.get('results', [])
        
        for file_info, result in zip(batch, batch_results):
            file_key = file_info['file_key']
            metadata = file_info['metadata']
            song_id = metadata['songId']
            content_hash = file_info['content_hash']
            
            if result.get('success'):
                print(f"  ✅ {metadata['artist']} - {metadata['title']} ({result.get('fingerprints', 0)} FPs)")
                results['indexed'] += 1
                results['songs'].append({
                    'song_id': song_id,
                    'title': metadata['title'],
                    'artist': metadata['artist'],
                    'source': file_key,
                    'fingerprints': result.get('fingerprints', 0)
                })
                
                indexed_files[file_key] = {
                    'songId': song_id,
                    'contentHash': content_hash,
                    'indexedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                }
                
            elif result.get('skipped'):
                reason = result.get('reason', 'Unknown')
                print(f"  ⏭️  {metadata['artist']} - {metadata['title']}: {reason}")
                results['skipped'] += 1
                
                if 'already' in reason.lower() or 'duplicate' in reason.lower():
                    indexed_files[file_key] = {
                        'songId': song_id,
                        'contentHash': content_hash,
                        'indexedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'skipped': reason
                    }
            else:
                error = result.get('error', 'Unknown error')
                print(f"  ❌ {metadata['artist']} - {metadata['title']}: {error}")
                results['failed'] += 1
                results['errors'].append({'file': file_key, 'error': error})
        
        # Print batch summary
        print(f"  Batch completed in {batch_time:.1f}s ({batch_time/len(batch):.2f}s/file avg)")
        
        # Save after each batch
        daemon.save()
        save_json_file(indexed_files_path, indexed_files)
        save_json_file(skip_log_path, skip_log)
    
    # Shutdown daemon (auto-saves database) - only if we own it
    if own_daemon:
        daemon.stop()
    else:
        daemon.save()  # Just save, don't stop the CLI's daemon
    
    # Final save
    save_json_file(indexed_files_path, indexed_files)
    save_json_file(skip_log_path, skip_log)
    
    # Summary
    print(f"\n=== Indexing Complete ===")
    print(f"Total files: {results['total']}")
    print(f"Indexed: {results['indexed']}")
    print(f"Skipped: {results['skipped']}")
    print(f"Failed: {results['failed']}")
    
    if results['songs']:
        total_fps = sum(s.get('fingerprints', 0) for s in results['songs'])
        print(f"Total fingerprints: {total_fps:,}")
    
    # Save session log for undo
    if results['indexed'] > 0:
        added_songs = [
            {'songId': s['song_id'], 'filepath': s['source']}
            for s in results['songs']
        ]
        flags = []
        if force_include:
            flags.append('--force')
        if filename_fallback:
            flags.append('--filename-fallback')
        if required_tags:
            flags.append(f"--require-tags {','.join(required_tags)}")
        
        session_path = save_session_log(db_path, 'index', folder_path, added_songs, flags)
        print(f"\n📝 Session logged: {session_path.name}")
        print("   Use 'undo' to reverse this action if needed.")
    
    return results

def reindex_songs(song_ids: List[str], db_path: Path, daemon: 'IndexingDaemon' = None,
                  force_include: bool = False) -> Dict[str, Any]:
    """
    Re-index specific songs by their songId.
    
    Useful for fixing songs with zero or low fingerprint counts.
    
    Args:
        song_ids: List of song IDs to re-index
        db_path: Path to database directory
        daemon: Optional daemon instance to reuse
        force_include: If True, bypasses exclusion list check (default: respects exclusions)
    
    Returns:
        Summary of re-indexed songs
    """
    print(f"\n=== Re-indexing {len(song_ids)} songs ===")
    if force_include:
        print("(--force: ignoring exclusion list)\n")
    else:
        print("(use --force to include excluded songs)\n")
    
    # Load metadata and indexed_files to find file paths
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    
    metadata = load_json_file(metadata_path)
    indexed_files = load_json_file(indexed_files_path)
    
    # Load exclusion list (unless force_include)
    exclusions = {} if force_include else load_exclusion_list(db_path)
    
    # Build songId -> filepath mapping from indexed_files
    song_to_files: Dict[str, List[str]] = {}
    for filepath, entry in indexed_files.items():
        sid = entry.get('songId')
        if sid:
            if sid not in song_to_files:
                song_to_files[sid] = []
            song_to_files[sid].append(filepath)
    
    results = {
        'total': len(song_ids),
        'reindexed': 0,
        'excluded': 0,
        'not_found': 0,
        'failed': 0,
        'songs': []
    }
    
    # Start daemon if needed
    own_daemon = daemon is None
    if own_daemon:
        daemon = IndexingDaemon(db_path)
        if not daemon.start():
            print("❌ Failed to start daemon")
            return results
    
    for song_id in song_ids:
        meta = metadata.get(song_id)
        
        if not meta:
            print(f"  ⚠ {song_id}: Not found in metadata")
            results['not_found'] += 1
            continue
        
        artist = meta.get('artist', '')
        title = meta.get('title', '')
        
        # Check exclusion list (unless force_include)
        if exclusions and is_excluded(song_id, artist, title, exclusions):
            print(f"  ⊘ {artist} - {title}: Excluded (use --force to override)")
            results['excluded'] += 1
            continue
        
        # Find file path
        files = song_to_files.get(song_id, [])
        if not files:
            # Try originalFilepath from metadata
            orig = meta.get('originalFilepath')
            if orig and Path(orig).exists():
                files = [orig]
        
        if not files:
            print(f"  ⚠ {artist} - {title}: No file found")
            results['not_found'] += 1
            continue
        
        # Use first available file
        audio_path = None
        for f in files:
            if Path(f).exists():
                audio_path = Path(f)
                break
        
        if not audio_path:
            print(f"  ⚠ {artist} - {title}: File(s) not found on disk")
            results['not_found'] += 1
            continue
        
        # Re-fingerprint this song
        try:
            # Re-extract metadata
            new_meta = extract_full_metadata(audio_path)
            new_meta['songId'] = song_id
            new_meta['originalFilepath'] = str(audio_path.absolute())
            new_meta['contentHash'] = compute_content_hash(audio_path)
            
            # Re-fingerprint with force=True (daemon will delete existing first)
            result = daemon.fingerprint(audio_path, new_meta, force=True)
            
            if result.get('success'):
                fp_count = result.get('fingerprints', 0)
                print(f"  ✓ {artist} - {title}: {fp_count} fingerprints")
                results['reindexed'] += 1
                results['songs'].append({
                    'songId': song_id,
                    'artist': artist,
                    'title': title,
                    'fingerprints': fp_count
                })
                
                # Update metadata
                metadata[song_id] = new_meta
                metadata[song_id]['fingerprintCount'] = fp_count
            else:
                print(f"  ✗ {artist} - {title}: {result.get('error', 'Unknown error')}")
                results['failed'] += 1
                
        except Exception as e:
            print(f"  ✗ {artist} - {title}: {e}")
            results['failed'] += 1
    
    # Save updated metadata
    if results['reindexed'] > 0:
        save_json_file(metadata_path, metadata)
        daemon.save()
        print(f"\n✓ Saved changes to database")
    
    if own_daemon:
        daemon.stop()
    
    print(f"\n=== Re-index complete ===")
    print(f"Re-indexed: {results['reindexed']}")
    print(f"Excluded: {results['excluded']}")
    print(f"Not found: {results['not_found']}")
    print(f"Failed: {results['failed']}")
    
    return results


def test_recognition(folder_path: Path, db_path: Path, clip_duration: int = 10, 
                     positions: List[int] = None, daemon: 'IndexingDaemon' = None) -> Dict[str, Any]:
    """
    Test recognition accuracy on indexed songs.
    
    Args:
        folder_path: Path to folder with audio files
        db_path: Path to database directory
        clip_duration: Duration of test clips in seconds
        positions: List of positions (in seconds) to test from
        daemon: Optional daemon for faster queries
    """
    if positions is None:
        positions = [10, 60, 120]
    
    # Get list of indexed songs
    stats = run_sfp_command(db_path, "list")
    if not stats.get('songs'):
        print("No songs indexed. Run --index first.")
        return {'error': 'No songs indexed'}
    
    indexed_songs = {s['songId']: s for s in stats['songs']}
    print(f"\n=== Testing {len(indexed_songs)} indexed songs ===")
    if daemon and daemon.is_running:
        print("Using active CLI daemon for queries")
    print()
    
    results = {
        'total_tests': 0,
        'passed': 0,
        'failed': 0,
        'tests': []
    }
    
    temp_dir = db_path / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Find source files
    audio_files = []
    for ext in SUPPORTED_EXTENSIONS:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    for audio_file in audio_files:
        metadata = extract_full_metadata(audio_file)
        if not metadata['title'] or not metadata['artist']:
            continue
            
        song_id = normalize_song_id(metadata['artist'], metadata['title'])
        
        if song_id not in indexed_songs:
            continue
        
        print(f"\n{metadata['artist']} - {metadata['title']}")
        
        for pos in positions:
            # Skip if position exceeds song duration
            if metadata['duration'] and pos >= metadata['duration']:
                print(f"  ⏭️  {pos}s → Skipped (song is {metadata['duration']:.0f}s)")
                continue
            
            clip_path = temp_dir / f"clip_{song_id}_{pos}.wav"
            
            # Extract clip
            if not convert_to_wav(audio_file, clip_path, start_sec=pos, duration_sec=clip_duration):
                print(f"  ⚠️  Could not extract clip at {pos}s")
                continue
            
            # Query - use daemon if available
            start_time = time.time()
            if daemon and daemon.is_running:
                result = daemon.query(clip_path, clip_duration, 0)
            else:
                result = run_sfp_command(db_path, "query", str(clip_path), str(clip_duration), "0")
            query_time = time.time() - start_time
            
            # Clean up clip
            try:
                clip_path.unlink()
            except:
                pass
            
            results['total_tests'] += 1
            
            # Extract best match from multi-match format
            best = result.get('bestMatch', result)
            
            if result.get('matched') and best.get('songId') == song_id:
                offset = best.get('trackMatchStartsAt', 0)
                confidence = best.get('confidence', 0)
                offset_error = abs(offset - pos)
                
                if offset_error < 2:  # Within 2 seconds
                    print(f"  ✅ {pos}s → Matched at {offset:.1f}s (error: {offset_error:.1f}s, conf: {confidence:.2f}, time: {query_time:.1f}s)")
                    results['passed'] += 1
                else:
                    print(f"  ⚠️  {pos}s → Matched at {offset:.1f}s (offset error: {offset_error:.1f}s)")
                    results['passed'] += 1  # Still a match
                
                results['tests'].append({
                    'song': song_id,
                    'position': pos,
                    'matched': True,
                    'offset': offset,
                    'offset_error': offset_error,
                    'confidence': confidence,
                    'query_time': query_time
                })
            else:
                print(f"  ❌ {pos}s → No match")
                results['failed'] += 1
                results['tests'].append({
                    'song': song_id,
                    'position': pos,
                    'matched': False,
                    'query_time': query_time
                })
    
    # Summary
    accuracy = (results['passed'] / results['total_tests'] * 100) if results['total_tests'] > 0 else 0
    print(f"\n=== Recognition Test Results ===")
    print(f"Total tests: {results['total_tests']}")
    print(f"Passed: {results['passed']}")
    print(f"Failed: {results['failed']}")
    print(f"Accuracy: {accuracy:.1f}%")
    
    return results


def test_live_capture(db_path: Path, duration: int = 10) -> Dict[str, Any]:
    """
    Test live audio capture and recognition.
    """
    print(f"\n=== Live Audio Test (capturing {duration}s) ===\n")
    
    try:
        from audio_recognition.capture import AudioCaptureManager
        import asyncio
        import wave
        import io
        
        temp_dir = db_path / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        async def capture_and_recognize():
            capture = AudioCaptureManager()
            
            device_id = await capture.resolve_device_async()
            if device_id is None:
                return {'error': 'No loopback device found'}
            
            print(f"Using device: {device_id}")
            print(f"Recording {duration} seconds...")
            
            audio = await capture.capture(duration)
            
            if audio is None:
                return {'error': 'Audio capture failed'}
            
            print(f"Captured {len(audio.data)} samples at {audio.sample_rate}Hz")
            
            # Save raw WAV
            temp_wav = temp_dir / "live_capture_raw.wav"
            buffer = io.BytesIO()
            with wave.open(buffer, 'wb') as wf:
                wf.setnchannels(audio.channels)
                wf.setsampwidth(2)
                wf.setframerate(audio.sample_rate)
                wf.writeframes(audio.data.tobytes())
            
            with open(temp_wav, 'wb') as f:
                f.write(buffer.getvalue())
            
            
            # Convert to 8000Hz mono (matches sfp-cli's SampleRate config)
            wav_path = temp_dir / "live_capture.wav"

            if not convert_to_wav(temp_wav, wav_path):
                return {'error': 'FFmpeg conversion failed'}
            
            # Query
            print("Querying...")
            result = run_sfp_command(db_path, "query", str(wav_path), str(duration), "0")
            
            return result
        
        result = asyncio.run(capture_and_recognize())
        
        if result.get('matched'):
            # Extract best match from multi-match format
            best = result.get('bestMatch', result)
            print(f"\n✅ MATCH FOUND!")
            print(f"   Song: {best.get('artist')} - {best.get('title')}")
            print(f"   Album: {best.get('album')}")
            print(f"   Position: {best.get('trackMatchStartsAt', 0):.1f}s")
            print(f"   Confidence: {best.get('confidence', 0):.2f}")
        else:
            print(f"\n❌ No match found")
            if result.get('error'):
                print(f"   Error: {result.get('error')}")
        
        return result
        
    except ImportError as e:
        return {'error': f'Import error: {e}. Run from project root.'}
    except Exception as e:
        return {'error': str(e)}


def show_stats(db_path: Path, daemon: 'IndexingDaemon' = None, show_songs: bool = False):
    """Show database statistics."""
    print(f"\n=== Database Statistics ===")
    print(f"DB Path: {db_path}\n")
    
    # Use daemon if available, otherwise subprocess
    if daemon and daemon.is_running:
        stats = daemon.stats()
        # Daemon returns different field names than subprocess
        # Daemon: songCount, fingerprintCount, status
        # Subprocess: songCount, totalFingerprints, metadataExists, fingerprintDbExists
        fp_count = stats.get('fingerprintCount', stats.get('totalFingerprints', 0))
        # Daemon doesn't return these, so check files directly
        metadata_exists = (db_path / "metadata.json").exists()
        fp_dir_exists = (db_path / "fingerprints").exists()
    else:
        stats = run_sfp_command(db_path, "stats")
        fp_count = stats.get('totalFingerprints', stats.get('fingerprintCount', 0))
        metadata_exists = stats.get('metadataExists', False)
        fp_dir_exists = stats.get('fingerprintDbExists', False)
    
    print(f"Songs indexed: {stats.get('songCount', 0)}")
    print(f"Total fingerprints: {fp_count:,}")
    print(f"Metadata exists: {metadata_exists}")
    print(f"Fingerprint DB exists: {fp_dir_exists}")
    
    # Show indexed files count
    indexed_files_path = db_path / "indexed_files.json"
    indexed_files = load_json_file(indexed_files_path)
    print(f"Tracked files: {len(indexed_files)}")
    
    # Show skip log count (dict keyed by filepath)
    skip_log_path = db_path / "skip_log.json"
    skip_log = load_json_file(skip_log_path)
    print(f"Skipped files: {len(skip_log)}")
    
    # Only show song list if requested
    if show_songs:
        print("\n=== Indexed Songs (sorted by artist) ===\n")
        songs_data = run_sfp_command(db_path, "list")
        songs = songs_data.get('songs', [])
        # Sort by artist, then title
        songs_sorted = sorted(songs, key=lambda s: (s.get('artist', '').lower(), s.get('title', '').lower()))
        for song in songs_sorted:
            duration = song.get('duration')
            dur_str = f" [{duration:.0f}s]" if duration else ""
            print(f"  • {song.get('artist')} - {song.get('title')}{dur_str} ({song.get('fingerprints', song.get('fingerprintCount', 0))} fp)")
    else:
        print("\nUse 'stats --songs' to list all indexed songs.")
    
    return stats


def clear_database(db_path: Path, force: bool = False):
    """Clear the entire database."""
    print(f"\n=== Clearing Database ===")
    print(f"DB Path: {db_path}\n")
    
    # Get stats first
    stats = run_sfp_command(db_path, "stats")
    song_count = stats.get('songCount', 0)
    
    if song_count == 0:
        print("Database is already empty.")
        return {"success": True, "cleared": 0}
    
    # Confirmation prompt
    if not force:
        print(f"⚠️  WARNING: This will permanently delete {song_count} songs!")
        print("   - All fingerprints will be removed")
        print("   - metadata.json will be cleared")
        print("   - indexed_files.json will be deleted")
        print("   - skip_log.json will be deleted")
        print()
        confirm = input("Type 'yes' to confirm: ").strip().lower()
        if confirm != 'yes':
            print("❌ Cancelled.")
            return {"cancelled": True}
    
    result = run_sfp_command(db_path, "clear")
    
    if result.get('success'):
        print(f"✅ Cleared {result.get('cleared', 0)} songs from fingerprint database")
        
        # Also clear tracking files
        indexed_files_path = db_path / "indexed_files.json"
        skip_log_path = db_path / "skip_log.json"
        
        if indexed_files_path.exists():
            indexed_files_path.unlink()
            print("✅ Cleared indexed_files.json")
        
        if skip_log_path.exists():
            skip_log_path.unlink()
            print("✅ Cleared skip_log.json")
    else:
        print(f"❌ Failed: {result.get('error', 'Unknown error')}")
    
    return result


# ============================================================================
# CLI MODE - Interactive Database Manager
# ============================================================================

def print_cli_header(db_path: Path, daemon: 'IndexingDaemon' = None):
    """Print the CLI header with current status."""
    print()
    print("=" * 70)
    print("  SyncLyrics Database Manager v1.0")
    print(f"  DB: {db_path}")
    if daemon and daemon.is_running:
        mode = daemon.connection_mode
        print(f"  Daemon: Running ({mode}) | Songs: {daemon.song_count}")
    else:
        print("  Daemon: Not running")
    print("=" * 70)
    print()


def print_cli_help():
    """Print available CLI commands."""
    commands = [
        ("help", "Show this help message"),
        ("status", "Quick sync status table"),
        ("verify", "Full verification with detailed discrepancies"),
        ("verify --low-fp [N]", "Find songs with < N fingerprints (default 100)"),
        ("repair", "Interactive repair wizard"),
        ("repair --batch", "Batch repair (confirm once for all)"),
        ("repair --auto", "Auto repair (no confirmation)"),
        ("repair --low-fp [N]", "Re-fingerprint songs with < N FPs (default 100)"),
        ("index <folder>", "Index new files in folder (respects exclusions)"),
        ("index <folder> --dry-run", "Preview what would be indexed"),
        ("index <folder> --force", "Index all files (ignores exclusions)"),
        ("index --filename-fallback", "Allow filename parsing when tags missing"),
        ("index --require-tags a,b", "Require additional tags (album,genre,year)"),
        ("reindex <folder>", "Re-index folder (respects exclusions)"),
        ("reindex <folder> --dry-run", "Preview what would be re-indexed"),
        ("reindex <folder> --force", "Re-index folder (ignores exclusions)"),
        ("reindex <songId> [id2]", "Re-index specific song(s)"),
        ("reindex <songId> --force", "Re-index song(s) ignoring exclusions"),
        ("search <query>", "Search songs by artist/title (shows 50)"),
        ("info <songId>", "Show details for a song"),
        ("delete <id> [id2...]", "Delete song(s) from all data sources"),
        ("purge <id> [id2...]", "Alias for delete"),
        ("purge --search <q>", "Delete songs matching search (interactive)"),
        ("list [page]", "List songs (100/page, sorted by artist)"),
        ("list all", "List all songs"),
        ("list --sort <type>", "Sort by: artist, title, path, original"),
        ("list --export", "Export all songs to file"),
        ("exclude <id> [id2]", "Add song(s) to exclusion list"),
        ("exclude --search <q>", "Exclude songs matching search"),
        ("exclude --pattern <p>", "Add title/artist pattern to exclusion"),
        ("exclude --list", "Show exclusion list"),
        ("include <id> [id2]", "Remove song(s) from exclusion list"),
        ("stats", "Show database statistics"),
        ("stats --songs", "Show stats with full song list"),
        ("undo", "Undo last index/reindex action"),
        ("undo --list", "Show history of recent actions"),
        ("undo <session_id>", "Undo a specific session"),
        ("clear", "Clear entire database (with confirmation)"),
        ("test <folder>", "Test recognition accuracy on folder"),
        ("test <folder> --positions", "Test at specific positions (default: 10,60,120)"),
        ("live [duration]", "Test live audio capture (default 10s)"),
        ("reload", "Full reload (FP DB + metadata from disk)"),
        ("refresh", "Refresh metadata only (lighter)"),
        ("exit / quit", "Exit the CLI"),
    ]
    
    print("\nAvailable Commands:")
    print("-" * 90)
    for cmd, desc in commands:
        print(f"  {cmd:28} {desc}")
    print()


def print_sync_table(fp_ids: set, metadata_ids: set, index_ids: set):
    """Print a visual sync status table."""
    # Calculate "in sync" counts (IDs present in ALL sources)
    all_synced = fp_ids & metadata_ids & index_ids
    synced_count = len(all_synced)
    
    fp_orphans = len(fp_ids - metadata_ids) + len(fp_ids - index_ids)
    meta_orphans = len(metadata_ids - fp_ids) + len(metadata_ids - index_ids)
    index_orphans = len(index_ids - fp_ids) + len(index_ids - metadata_ids)
    
    # Simplify: count IDs NOT in all 3
    fp_not_synced = len(fp_ids - all_synced)
    meta_not_synced = len(metadata_ids - all_synced)
    index_not_synced = len(index_ids - all_synced)
    
    print()
    print("+" + "-" * 26 + "+" + "-" * 8 + "+" + "-" * 12 + "+" + "-" * 10 + "+")
    print(f"| {'Data Source':<24} | {'Count':>6} | {'In Sync':>10} | {'Issues':>8} |")
    print("+" + "-" * 26 + "+" + "-" * 8 + "+" + "-" * 12 + "+" + "-" * 10 + "+")
    
    # Fingerprint DB row
    fp_status = "✓" if fp_not_synced == 0 else ""
    fp_issues = f"{fp_not_synced} ⚠" if fp_not_synced > 0 else "0"
    print(f"| {'Fingerprint DB':<24} | {len(fp_ids):>6} | {synced_count:>8} {fp_status:<1} | {fp_issues:>8} |")
    
    # Metadata row
    meta_status = "✓" if meta_not_synced == 0 else ""
    meta_issues = f"{meta_not_synced} ⚠" if meta_not_synced > 0 else "0"
    print(f"| {'metadata.json':<24} | {len(metadata_ids):>6} | {synced_count:>8} {meta_status:<1} | {meta_issues:>8} |")
    
    # Index row
    index_status = "✓" if index_not_synced == 0 else ""
    index_issues = f"{index_not_synced} ⚠" if index_not_synced > 0 else "0"
    print(f"| {'indexed_files.json':<24} | {len(index_ids):>6} | {synced_count:>8} {index_status:<1} | {index_issues:>8} |")
    
    print("+" + "-" * 26 + "+" + "-" * 8 + "+" + "-" * 12 + "+" + "-" * 10 + "+")
    print()


def print_discrepancy_table(discrepancies: Dict[str, List], fp_ids: set, metadata_ids: set, index_ids: set):
    """Print a table showing which songIds have issues."""
    # Collect all problematic songIds
    problem_ids = set()
    for items in discrepancies.values():
        for item in items:
            problem_ids.add(item.get('songId', ''))
    
    if not problem_ids:
        return
    
    print("\nDiscrepancy Details:")
    print("+" + "-" * 42 + "+" + "-" * 6 + "+" + "-" * 6 + "+" + "-" * 7 + "+")
    print(f"| {'Song ID':<40} | {'FP':^4} | {'Meta':^4} | {'Index':^5} |")
    print("+" + "-" * 42 + "+" + "-" * 6 + "+" + "-" * 6 + "+" + "-" * 7 + "+")
    
    for song_id in sorted(problem_ids)[:20]:  # Limit to 20 rows
        fp_mark = "✓" if song_id in fp_ids else "❌"
        meta_mark = "✓" if song_id in metadata_ids else "❌"
        index_mark = "✓" if song_id in index_ids else "❌"
        
        # Truncate long song IDs
        display_id = song_id[:40] if len(song_id) <= 40 else song_id[:37] + "..."
        print(f"| {display_id:<40} | {fp_mark:^4} | {meta_mark:^4} | {index_mark:^5} |")
    
    print("+" + "-" * 42 + "+" + "-" * 6 + "+" + "-" * 6 + "+" + "-" * 7 + "+")
    
    if len(problem_ids) > 20:
        print(f"  ... and {len(problem_ids) - 20} more")
    print()


def search_songs(query: str, db_path: Path, daemon: 'IndexingDaemon' = None) -> List[Dict]:
    """Search for songs by artist or title."""
    metadata_path = db_path / "metadata.json"
    metadata = load_json_file(metadata_path)
    
    query_lower = query.lower()
    results = []
    
    for song_id, meta in metadata.items():
        artist = (meta.get('artist') or '').lower()
        title = (meta.get('title') or '').lower()
        album = (meta.get('album') or '').lower()
        
        if query_lower in artist or query_lower in title or query_lower in album or query_lower in song_id.lower():
            results.append({
                'songId': song_id,
                'artist': meta.get('artist', '?'),
                'title': meta.get('title', '?'),
                'album': meta.get('album'),
                'duration': meta.get('duration'),
                'filepath': meta.get('originalFilepath')
            })
    
    return results


def show_song_info(song_id: str, db_path: Path, daemon: 'IndexingDaemon' = None):
    """Show detailed info for a specific song."""
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    
    metadata = load_json_file(metadata_path)
    indexed_files = load_json_file(indexed_files_path)
    
    print(f"\n{'=' * 60}")
    print(f"Song Info: {song_id}")
    print(f"{'=' * 60}")
    
    # Check metadata
    meta = metadata.get(song_id)
    if meta:
        print("\n[metadata.json] ✓ FOUND")
        print(f"  Artist:    {meta.get('artist', '?')}")
        print(f"  Title:     {meta.get('title', '?')}")
        print(f"  Album:     {meta.get('album', '-')}")
        print(f"  Duration:  {meta.get('duration', 0):.1f}s")
        print(f"  Year:      {meta.get('year', '-')}")
        print(f"  Genre:     {meta.get('genre', '-')}")
        print(f"  ISRC:      {meta.get('isrc', '-')}")
        print(f"  FP Count:  {meta.get('fingerprintCount', '?')}")
        print(f"  Indexed:   {meta.get('indexedAt', '-')}")
        print(f"  File:      {meta.get('originalFilepath', '-')}")
    else:
        print("\n[metadata.json] ❌ NOT FOUND")
    
    # Check indexed_files
    index_entries = [(fp, entry) for fp, entry in indexed_files.items() if entry.get('songId') == song_id]
    if index_entries:
        print(f"\n[indexed_files.json] ✓ FOUND ({len(index_entries)} entries)")
        for fp, entry in index_entries[:5]:
            print(f"  • {Path(fp).name}")
            print(f"    Hash: {entry.get('contentHash', '-')}")
    else:
        print("\n[indexed_files.json] ❌ NOT FOUND")
    
    # Check fingerprint DB (via daemon using list_fp method)
    if daemon and daemon.is_running:
        try:
            result = daemon.list_fp()
            fp_ids = set(result.get('songIds', []))
            if song_id in fp_ids:
                print(f"\n[Fingerprint DB] ✓ FOUND")
            else:
                print(f"\n[Fingerprint DB] ❌ NOT FOUND")
        except:
            print(f"\n[Fingerprint DB] ⚠ Could not check (daemon error)")
    else:
        print(f"\n[Fingerprint DB] ⚠ Could not check (daemon not running)")
    
    print()


# ============================================================================
# EXCLUSION SYSTEM
# ============================================================================

def load_exclusion_list(db_path: Path) -> Dict[str, Any]:
    """Load exclusion list from exclude.json."""
    exclude_path = db_path / "exclude.json"
    if exclude_path.exists():
        try:
            with open(exclude_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {
        'songIds': [],      # List of explicitly excluded song IDs
        'patterns': [],     # List of glob patterns for title/artist
        'addedAt': {}       # songId -> timestamp when added
    }


def save_exclusion_list(db_path: Path, exclusions: Dict[str, Any]):
    """Save exclusion list to exclude.json."""
    exclude_path = db_path / "exclude.json"
    with open(exclude_path, 'w', encoding='utf-8') as f:
        json.dump(exclusions, f, indent=2, ensure_ascii=False)


def is_excluded(song_id: str, artist: str, title: str, exclusions: Dict[str, Any]) -> bool:
    """Check if a song is excluded by ID or pattern."""
    import fnmatch
    
    # Check explicit ID exclusion
    if song_id in exclusions.get('songIds', []):
        return True
    
    # Check patterns
    for pattern in exclusions.get('patterns', []):
        pattern_lower = pattern.lower()
        if fnmatch.fnmatch(artist.lower(), pattern_lower):
            return True
        if fnmatch.fnmatch(title.lower(), pattern_lower):
            return True
        # Also check combined "artist - title"
        combined = f"{artist} - {title}".lower()
        if fnmatch.fnmatch(combined, pattern_lower):
            return True
    
    return False


def add_to_exclusion_list(db_path: Path, song_ids: List[str] = None, patterns: List[str] = None) -> Dict[str, Any]:
    """Add song IDs or patterns to exclusion list."""
    exclusions = load_exclusion_list(db_path)
    added_ids = []
    added_patterns = []
    
    if song_ids:
        for song_id in song_ids:
            if song_id not in exclusions['songIds']:
                exclusions['songIds'].append(song_id)
                exclusions['addedAt'][song_id] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                added_ids.append(song_id)
    
    if patterns:
        for pattern in patterns:
            if pattern not in exclusions['patterns']:
                exclusions['patterns'].append(pattern)
                added_patterns.append(pattern)
    
    save_exclusion_list(db_path, exclusions)
    return {'added_ids': added_ids, 'added_patterns': added_patterns}


def remove_from_exclusion_list(db_path: Path, song_ids: List[str] = None, patterns: List[str] = None) -> Dict[str, Any]:
    """Remove song IDs or patterns from exclusion list."""
    exclusions = load_exclusion_list(db_path)
    removed_ids = []
    removed_patterns = []
    
    if song_ids:
        for song_id in song_ids:
            if song_id in exclusions['songIds']:
                exclusions['songIds'].remove(song_id)
                exclusions['addedAt'].pop(song_id, None)
                removed_ids.append(song_id)
    
    if patterns:
        for pattern in patterns:
            if pattern in exclusions['patterns']:
                exclusions['patterns'].remove(pattern)
                removed_patterns.append(pattern)
    
    save_exclusion_list(db_path, exclusions)
    return {'removed_ids': removed_ids, 'removed_patterns': removed_patterns}


def show_exclusion_list(db_path: Path):
    """Display the current exclusion list."""
    exclusions = load_exclusion_list(db_path)
    
    print(f"\n{'=' * 60}")
    print("EXCLUSION LIST")
    print(f"{'=' * 60}")
    
    song_ids = exclusions.get('songIds', [])
    patterns = exclusions.get('patterns', [])
    
    if not song_ids and not patterns:
        print("\nNo exclusions configured.")
        print("Use 'exclude <songId>' or 'exclude --pattern <pattern>' to add.")
        return
    
    if song_ids:
        print(f"\nExcluded Song IDs ({len(song_ids)}):")
        for song_id in song_ids[:50]:
            added = exclusions.get('addedAt', {}).get(song_id, '?')
            print(f"  • {song_id}")
        if len(song_ids) > 50:
            print(f"  ... and {len(song_ids) - 50} more")
    
    if patterns:
        print(f"\nExclusion Patterns ({len(patterns)}):")
        for pattern in patterns:
            print(f"  • {pattern}")
    
    print()


def purge_multiple(song_ids: List[str], db_path: Path, daemon: 'IndexingDaemon' = None, 
                   mode: str = 'individual') -> Dict[str, Any]:
    """
    Purge multiple songs.
    
    Args:
        song_ids: List of song IDs to delete
        db_path: Database path
        daemon: Daemon instance
        mode: 'individual' (confirm each), 'all' (confirm once), 'auto' (no confirm)
    """
    if not song_ids:
        print("No songs to purge.")
        return {'deleted': 0, 'skipped': 0}
    
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    metadata = load_json_file(metadata_path)
    indexed_files = load_json_file(indexed_files_path)
    
    # Get FP IDs
    fp_ids = set()
    if daemon and daemon.is_running:
        try:
            result = daemon.list_fp()
            fp_ids = set(result.get('songIds', []))
        except:
            pass
    
    # Preview what will be deleted
    print(f"\n{'=' * 60}")
    print(f"MULTI-PURGE: {len(song_ids)} songs")
    print(f"{'=' * 60}\n")
    
    valid_songs = []
    for song_id in song_ids:
        in_fp = song_id in fp_ids
        in_meta = song_id in metadata
        in_index = any(e.get('songId') == song_id for e in indexed_files.values())
        
        if in_fp or in_meta or in_index:
            meta = metadata.get(song_id, {})
            valid_songs.append({
                'id': song_id,
                'artist': meta.get('artist', '?'),
                'title': meta.get('title', '?'),
                'in_fp': in_fp,
                'in_meta': in_meta,
                'in_index': in_index
            })
    
    if not valid_songs:
        print("None of the specified songs were found.")
        return {'deleted': 0, 'skipped': len(song_ids)}
    
    print(f"Found {len(valid_songs)} songs to delete:\n")
    for i, song in enumerate(valid_songs, 1):
        sources = []
        if song['in_fp']: sources.append('FP')
        if song['in_meta']: sources.append('Meta')
        if song['in_index']: sources.append('Index')
        print(f"  {i}. {song['artist']} - {song['title']}")
        print(f"     ID: {song['id']} | In: {', '.join(sources)}")
    
    # Get confirmation based on mode
    if mode == 'all':
        print()
        response = input(f"Delete all {len(valid_songs)} songs? (y/n): ").strip().lower()
        if response != 'y':
            print("Cancelled.")
            return {'deleted': 0, 'skipped': len(valid_songs)}
        songs_to_delete = valid_songs
    elif mode == 'auto':
        songs_to_delete = valid_songs
    else:  # individual
        songs_to_delete = []
        print("\nConfirm each deletion (y=yes, n=no, a=all remaining, q=quit):\n")
        for song in valid_songs:
            response = input(f"  Delete {song['artist']} - {song['title']}? ").strip().lower()
            if response == 'q':
                break
            elif response == 'a':
                songs_to_delete.extend(valid_songs[valid_songs.index(song):])
                break
            elif response == 'y':
                songs_to_delete.append(song)
    
    if not songs_to_delete:
        print("\nNo songs selected for deletion.")
        return {'deleted': 0, 'skipped': len(valid_songs)}
    
    # Execute deletions
    print(f"\nDeleting {len(songs_to_delete)} songs...")
    deleted = 0
    
    for song in songs_to_delete:
        song_id = song['id']
        
        # Delete from FP DB
        if song['in_fp'] and daemon and daemon.is_running:
            daemon.delete(song_id)
        
        # Delete from metadata
        if song_id in metadata:
            del metadata[song_id]
        
        # Delete from indexed_files
        keys_to_remove = [k for k, v in indexed_files.items() if v.get('songId') == song_id]
        for k in keys_to_remove:
            del indexed_files[k]
        
        deleted += 1
        print(f"  ✓ Deleted: {song['artist']} - {song['title']}")
    
    # Save changes
    save_json_file(metadata_path, metadata)
    save_json_file(indexed_files_path, indexed_files)
    
    if daemon and daemon.is_running:
        daemon.refresh()
    
    print(f"\n✓ Purge complete: {deleted} deleted, {len(valid_songs) - deleted} skipped")
    
    # Offer to add to exclusion list
    if deleted > 0:
        deleted_ids = [s['id'] for s in songs_to_delete]
        exclude_response = input(f"\nAdd {deleted} song(s) to exclusion list? (y/n): ").strip().lower()
        if exclude_response == 'y':
            result = add_to_exclusion_list(db_path, song_ids=deleted_ids)
            print(f"✅ Added {len(result['added_ids'])} songs to exclusion list")
    
    return {'deleted': deleted, 'skipped': len(valid_songs) - deleted}


def purge_song(song_id: str, db_path: Path, daemon: 'IndexingDaemon' = None) -> Dict[str, Any]:
    """
    Safely remove a song from all 3 data sources.
    Shows what will be deleted and asks for confirmation.
    """
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    
    metadata = load_json_file(metadata_path)
    indexed_files = load_json_file(indexed_files_path)
    
    # Check what exists
    in_metadata = song_id in metadata
    index_entries = [(fp, entry) for fp, entry in indexed_files.items() if entry.get('songId') == song_id]
    in_index = len(index_entries) > 0
    
    # Check fingerprint DB using daemon's list_fp method
    in_fp = False
    if daemon and daemon.is_running:
        try:
            result = daemon.list_fp()
            fp_ids = set(result.get('songIds', []))
            in_fp = song_id in fp_ids
        except:
            pass
    
    if not in_metadata and not in_index and not in_fp:
        print(f"\n❌ Song '{song_id}' not found in any data source.")
        return {'success': False, 'error': 'not found'}
    
    # Show what will be deleted
    print(f"\n{'=' * 60}")
    print(f"PURGE: {song_id}")
    print(f"{'=' * 60}")
    print("\nWill delete from:")
    
    if in_fp:
        print(f"  [✓] Fingerprint DB")
    else:
        print(f"  [ ] Fingerprint DB - NOT FOUND (already missing)")
    
    if in_metadata:
        meta = metadata[song_id]
        print(f"  [✓] metadata.json: {meta.get('artist', '?')} - {meta.get('title', '?')}")
    else:
        print(f"  [ ] metadata.json - NOT FOUND (already missing)")
    
    if in_index:
        print(f"  [✓] indexed_files.json: {len(index_entries)} file(s)")
        for fp, _ in index_entries[:3]:
            print(f"      • {Path(fp).name}")
        if len(index_entries) > 3:
            print(f"      ... and {len(index_entries) - 3} more")
    else:
        print(f"  [ ] indexed_files.json - NOT FOUND (already missing)")
    
    print()
    response = input("Proceed with deletion? (y/n): ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return {'success': False, 'cancelled': True}
    
    deleted_from = []
    
    # Delete from fingerprint DB and daemon's in-memory metadata using daemon.delete()
    if in_fp and daemon and daemon.is_running:
        try:
            result = daemon.delete(song_id)
            if result.get('success'):
                deleted_from.append('fingerprint_db')
                print(f"  ✓ Deleted from Fingerprint DB (and daemon memory)")
            else:
                print(f"  ⚠ Could not delete from Fingerprint DB: {result.get('error')}")
        except Exception as e:
            print(f"  ⚠ Error deleting from Fingerprint DB: {e}")
    
    # Delete from metadata file
    if in_metadata:
        del metadata[song_id]
        save_json_file(metadata_path, metadata)
        deleted_from.append('metadata')
        print(f"  ✓ Deleted from metadata.json")
    
    # Delete from indexed_files
    if in_index:
        for fp, _ in index_entries:
            del indexed_files[fp]
        save_json_file(indexed_files_path, indexed_files)
        deleted_from.append('indexed_files')
        print(f"  ✓ Deleted {len(index_entries)} entries from indexed_files.json")
    
    # Refresh daemon's metadata from disk (so it picks up our file changes)
    if daemon and daemon.is_running and ('metadata' in deleted_from):
        daemon.refresh()
    
    print(f"\n✓ Purge complete. Deleted from: {', '.join(deleted_from)}")
    
    # Offer to add to exclusion list
    if deleted_from:
        exclude_response = input(f"\nAdd to exclusion list? (y/n): ").strip().lower()
        if exclude_response == 'y':
            result = add_to_exclusion_list(db_path, song_ids=[song_id])
            if result['added_ids']:
                print(f"✅ Added to exclusion list")
            else:
                print(f"Already in exclusion list")
    
    return {'success': True, 'deleted_from': deleted_from}


def list_songs(db_path: Path, page: int = 1, page_size: int = 100, show_all: bool = False, 
               export_file: str = None, sort_by: str = 'artist'):
    """
    List all songs with pagination and sorting.
    
    Args:
        page: Page number (1-indexed)
        page_size: Songs per page (default 100)
        show_all: If True, show all songs without pagination
        export_file: If provided, export to file instead of printing
        sort_by: Sort order - 'artist' (default), 'title', 'original', 'path'
    """
    metadata_path = db_path / "metadata.json"
    metadata = load_json_file(metadata_path)
    
    songs = list(metadata.items())
    total = len(songs)
    
    # Apply sorting
    if sort_by == 'artist':
        songs = sorted(songs, key=lambda x: (x[1].get('artist', '').lower(), x[1].get('title', '').lower()))
        sort_label = "sorted by artist"
    elif sort_by == 'title':
        songs = sorted(songs, key=lambda x: x[1].get('title', '').lower())
        sort_label = "sorted by title"
    elif sort_by == 'path':
        songs = sorted(songs, key=lambda x: x[1].get('originalFilepath', '').lower())
        sort_label = "sorted by path"
    else:  # 'original' or any other value - keep dict order
        sort_label = "original order"
    
    # Build output lines
    lines = []
    
    if show_all or export_file:
        # Show/export all songs
        page_songs = songs
        lines.append(f"{'=' * 70}")
        lines.append(f"All Songs ({total} total, {sort_label})")
        lines.append(f"{'=' * 70}")
        lines.append("")
    else:
        # Paginated view
        total_pages = (total + page_size - 1) // page_size
        start = (page - 1) * page_size
        end = start + page_size
        page_songs = songs[start:end]
        
        lines.append(f"{'=' * 70}")
        lines.append(f"Songs (Page {page}/{total_pages}, {total} total, {sort_label})")
        lines.append(f"{'=' * 70}")
        lines.append("")
    
    for song_id, meta in page_songs:
        duration = meta.get('duration', 0)
        dur_str = f"[{duration:.0f}s]" if duration else ""
        fp_count = meta.get('fingerprintCount', '?')
        lines.append(f"  {meta.get('artist', '?')} - {meta.get('title', '?')} {dur_str}")
        lines.append(f"    ID: {song_id} | FPs: {fp_count}")
        lines.append("")
    
    if not show_all and not export_file:
        total_pages = (total + page_size - 1) // page_size
        if total_pages > 1 and page < total_pages:
            lines.append(f"Use 'list {page + 1}' for next page, or 'list all' to show all")
        lines.append("Sort options: --sort artist | --sort title | --sort path | --sort original")
    
    # Output
    if export_file:
        # Generate default filename if not specified
        if export_file == True or export_file == '':
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_file = db_path / f"songs_export_{timestamp}.txt"
        else:
            export_file = Path(export_file)
        
        with open(export_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"✅ Exported {total} songs to: {export_file}")
    else:
        print('\n' + '\n'.join(lines))


def cli_mode(db_path: Path):
    """
    Interactive CLI mode - stays running until user exits.
    Reuses a single daemon instance throughout the session.
    """
    print_cli_header(db_path)
    print("Starting daemon...")
    
    # Start daemon once for the entire session
    daemon = IndexingDaemon(db_path)
    if not daemon.start():
        print("⚠ Could not start daemon. Some commands may not work.")
        daemon = None
    
    print_cli_header(db_path, daemon)
    print("Type 'help' for available commands.\n")
    
    try:
        while True:
            try:
                cmd_input = input("> ").strip()
            except EOFError:
                break
            
            if not cmd_input:
                continue
            
            # Parse command and arguments
            parts = cmd_input.split(maxsplit=1)
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            
            # Handle commands
            if cmd in ('exit', 'quit', 'q'):
                print("\nShutting down...")
                break
            
            elif cmd == 'help':
                print_cli_help()
            
            elif cmd == 'status':
                # Quick status check
                result = verify_database(db_path, daemon=daemon, brief=True)
            
            elif cmd == 'verify':
                # Parse --low-fp [N] flag
                low_fp_threshold = None
                if '--low-fp' in args:
                    # Extract optional number after --low-fp
                    parts = args.split()
                    try:
                        idx = parts.index('--low-fp')
                        if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                            low_fp_threshold = int(parts[idx + 1])
                        else:
                            low_fp_threshold = 100  # Default threshold
                    except:
                        low_fp_threshold = 100
                verify_database(db_path, daemon=daemon, brief=False, low_fp_threshold=low_fp_threshold)
            
            elif cmd == 'repair':
                batch = '--batch' in args
                auto = '--auto' in args
                
                # Check for --low-fp [N] flag
                if '--low-fp' in args:
                    # Extract optional threshold
                    parts = args.split()
                    try:
                        idx = parts.index('--low-fp')
                        if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                            threshold = int(parts[idx + 1])
                        else:
                            threshold = 100  # Default
                    except:
                        threshold = 100
                    
                    # Find low-FP songs from metadata
                    metadata = load_json_file(db_path / "metadata.json")
                    low_fp_ids = []
                    for song_id, meta in metadata.items():
                        fp_count = meta.get('fingerprintCount', 0)
                        if fp_count < threshold:
                            low_fp_ids.append(song_id)
                    
                    if not low_fp_ids:
                        print(f"✅ No songs with < {threshold} fingerprints found.")
                    else:
                        print(f"\nFound {len(low_fp_ids)} songs with < {threshold} fingerprints.")
                        if not auto:
                            response = input(f"Re-fingerprint all {len(low_fp_ids)} songs? (y/n): ").strip().lower()
                            if response != 'y':
                                print("Cancelled.")
                                continue
                        reindex_songs(low_fp_ids, db_path, daemon=daemon)
                else:
                    repair_database(db_path, batch=batch, auto=auto, daemon=daemon)
            
            elif cmd == 'index':
                if not args:
                    print("Usage: index <folder> [--dry-run] [--require-tags tag1,tag2]")
                    continue
                # Check for flags
                dry_run = '--dry-run' in args
                filename_fallback = '--filename-fallback' in args
                force_include = '--force' in args
                clean_args = args.replace('--dry-run', '').replace('--filename-fallback', '').replace('--force', '')
                
                # Check for --require-tags flag
                required_tags = None
                if '--require-tags' in clean_args:
                    import re
                    match = re.search(r'--require-tags\s+([^\s]+)', clean_args)
                    if match:
                        required_tags = [t.strip() for t in match.group(1).split(',') if t.strip()]
                        clean_args = re.sub(r'--require-tags\s+[^\s]+', '', clean_args)
                        print(f"Requiring additional tags: {', '.join(required_tags)}")
                
                clean_args = clean_args.strip()
                if not clean_args:
                    print("Error: No folder specified")
                    continue
                
                # Strip surrounding quotes (single or double)
                if (clean_args.startswith('"') and clean_args.endswith('"')) or \
                   (clean_args.startswith("'") and clean_args.endswith("'")):
                    clean_args = clean_args[1:-1]
                
                folder = Path(clean_args.rstrip('/\\'))
                if not folder.exists():
                    print(f"Error: Folder not found: {folder}")
                    continue
                # Use the existing index_folder function with CLI's daemon
                if force_include:
                    print("🔓 Force mode: ignoring exclusion list")
                result = index_folder(folder, db_path, required_tags=required_tags, dry_run=dry_run, 
                             daemon=daemon, filename_fallback=filename_fallback, force_include=force_include)
                
                # Offer export for dry-run results
                if dry_run and result and not result.get('error'):
                    export_response = input("\nExport dry-run results to JSON? (y/N): ").strip().lower()
                    if export_response == 'y':
                        export_filename = input("Enter filename (default: dry_run_index.json): ").strip()
                        if not export_filename:
                            export_filename = "dry_run_index.json"
                        # Save to db_path folder (same as other JSON files)
                        export_path = db_path / export_filename
                        try:
                            save_json_file(export_path, result)
                            print(f"✅ Exported to {export_path}")
                        except Exception as e:
                            print(f"❌ Export failed: {e}")

            
            elif cmd == 'reindex':
                if not args:
                    print("Usage: reindex <folder> [--force] [--dry-run] | reindex <songId> [id2...] [--force]")
                    continue
                
                # Check for flags
                force_include = '--force' in args
                dry_run = '--dry-run' in args
                # Remove flags from args for further parsing
                clean_args = args.replace('--force', '').replace('--dry-run', '').strip()
                
                if not clean_args:
                    print("Error: No folder or songId specified")
                    continue
                
                # Strip surrounding quotes (single or double)
                if (clean_args.startswith('"') and clean_args.endswith('"')) or \
                   (clean_args.startswith("'") and clean_args.endswith("'")):
                    clean_args = clean_args[1:-1]
                
                # Check if the entire clean_args is a folder path (handles spaces in paths)
                folder = Path(clean_args.rstrip('/\\'))
                
                if folder.exists() and folder.is_dir():
                    # It's a folder - use reindex_folder with CLI's daemon
                    reindex_folder(folder, db_path, force_include=force_include, dry_run=dry_run, daemon=daemon)
                else:
                    # Treat as songId(s) - now we can split on spaces
                    if dry_run:
                        print("Error: --dry-run is only supported for folder reindexing")
                        print("Use 'info <songId>' to preview song details instead")
                        continue
                    song_ids = clean_args.split()
                    reindex_songs(song_ids, db_path, daemon=daemon, force_include=force_include)
            
            elif cmd == 'search':
                if not args:
                    print("Usage: search <query>")
                    continue
                results = search_songs(args, db_path, daemon)
                if results:
                    print(f"\nFound {len(results)} matches:\n")
                    for r in results[:50]:  # Show up to 50 results
                        dur = f"[{r['duration']:.0f}s]" if r.get('duration') else ""
                        print(f"  {r['artist']} - {r['title']} {dur}")
                        print(f"    ID: {r['songId']}")
                    if len(results) > 50:
                        print(f"\n  ... and {len(results) - 50} more")
                else:
                    print(f"No matches found for '{args}'")
                print()
            
            elif cmd == 'info':
                if not args:
                    print("Usage: info <songId>")
                    continue
                show_song_info(args, db_path, daemon)
            
            elif cmd in ('purge', 'delete'):
                if not args:
                    print("Usage: delete <songId> [songId2...] or delete --search <query>")
                    continue
                
                if args.startswith('--search '):
                    # Search-based purge with individual confirmation
                    query = args[9:].strip()
                    results = search_songs(query, db_path, daemon)
                    if not results:
                        print(f"No matches found for '{query}'")
                        continue
                    song_ids = [r['songId'] for r in results]
                    print(f"\nSearch matched {len(song_ids)} songs.")
                    mode_response = input("Delete mode: (i)ndividual confirm, (a)ll at once, (c)ancel: ").strip().lower()
                    if mode_response == 'c':
                        print("Cancelled.")
                        continue
                    mode = 'individual' if mode_response == 'i' else 'all'
                    purge_multiple(song_ids, db_path, daemon, mode=mode)
                else:
                    # Multiple song IDs (space-separated)
                    song_ids = args.split()
                    if len(song_ids) == 1:
                        purge_song(song_ids[0], db_path, daemon)
                    else:
                        mode_response = input("Delete mode: (i)ndividual confirm, (a)ll at once, (c)ancel: ").strip().lower()
                        if mode_response == 'c':
                            print("Cancelled.")
                            continue
                        mode = 'individual' if mode_response == 'i' else 'all'
                        purge_multiple(song_ids, db_path, daemon, mode=mode)
            
            elif cmd == 'list':
                # Parse --sort option
                sort_by = 'artist'  # Default
                if '--sort' in args:
                    import re
                    match = re.search(r'--sort\s+(\w+)', args)
                    if match:
                        sort_by = match.group(1).lower()
                        if sort_by not in ('artist', 'title', 'path', 'original'):
                            print(f"Unknown sort option: {sort_by}. Using 'artist'.")
                            sort_by = 'artist'
                    args = re.sub(r'--sort\s+\w+', '', args).strip()
                
                if args == 'all':
                    list_songs(db_path, show_all=True, sort_by=sort_by)
                elif args.startswith('--export'):
                    # Export to file
                    export_arg = args[8:].strip() if len(args) > 8 else ''
                    list_songs(db_path, export_file=export_arg if export_arg else True, sort_by=sort_by)
                elif args:
                    try:
                        page = int(args)
                        list_songs(db_path, page=page, sort_by=sort_by)
                    except ValueError:
                        print("Usage: list [page] [--sort artist|title|path|original] | list all | list --export [filename]")
                else:
                    list_songs(db_path, sort_by=sort_by)
            
            elif cmd == 'exclude':
                if not args:
                    print("Usage: exclude <songId> [id2...] | exclude --search <q> | exclude --pattern <p> | exclude --list")
                    continue
                
                if args == '--list':
                    show_exclusion_list(db_path)
                elif args.startswith('--search '):
                    query = args[9:].strip()
                    results = search_songs(query, db_path, daemon)
                    if not results:
                        print(f"No matches found for '{query}'")
                        continue
                    print(f"\nFound {len(results)} matches to exclude:\n")
                    for r in results[:20]:
                        print(f"  • {r['artist']} - {r['title']} ({r['songId']})")
                    if len(results) > 20:
                        print(f"  ... and {len(results) - 20} more")
                    response = input(f"\nExclude all {len(results)} songs? (y/n): ").strip().lower()
                    if response == 'y':
                        song_ids = [r['songId'] for r in results]
                        result = add_to_exclusion_list(db_path, song_ids=song_ids)
                        print(f"✅ Added {len(result['added_ids'])} songs to exclusion list")
                elif args.startswith('--pattern '):
                    pattern = args[10:].strip()
                    # Strip surrounding quotes if present
                    if (pattern.startswith('"') and pattern.endswith('"')) or \
                       (pattern.startswith("'") and pattern.endswith("'")):
                        pattern = pattern[1:-1]
                    result = add_to_exclusion_list(db_path, patterns=[pattern])
                    if result['added_patterns']:
                        print(f"✅ Added pattern: {pattern}")
                    else:
                        print(f"Pattern already exists: {pattern}")
                else:
                    # Multiple song IDs
                    song_ids = args.split()
                    result = add_to_exclusion_list(db_path, song_ids=song_ids)
                    if result['added_ids']:
                        print(f"✅ Added {len(result['added_ids'])} songs to exclusion list")
                    else:
                        print("All specified songs already in exclusion list")
            
            elif cmd == 'include':
                if not args:
                    print("Usage: include <songId> [id2...] | include --pattern <p>")
                    continue
                
                if args.startswith('--pattern '):
                    pattern = args[10:].strip()
                    # Strip surrounding quotes if present
                    if (pattern.startswith('"') and pattern.endswith('"')) or \
                       (pattern.startswith("'") and pattern.endswith("'")):
                        pattern = pattern[1:-1]
                    result = remove_from_exclusion_list(db_path, patterns=[pattern])
                    if result['removed_patterns']:
                        print(f"✅ Removed pattern: {pattern}")
                    else:
                        print(f"Pattern not found: {pattern}")
                else:
                    song_ids = args.split()
                    result = remove_from_exclusion_list(db_path, song_ids=song_ids)
                    if result['removed_ids']:
                        print(f"✅ Removed {len(result['removed_ids'])} songs from exclusion list")
                    else:
                        print("None of the specified songs were in exclusion list")
            
            elif cmd == 'stats':
                show_songs = '--songs' in args
                show_stats(db_path, daemon=daemon, show_songs=show_songs)
            
            elif cmd == 'reload':
                if daemon and daemon.is_running:
                    print("Reloading FP database and metadata from disk...")
                    result = daemon.reload()
                    if result.get('status') == 'reloaded':
                        print(f"✅ Reloaded: {result.get('previousSongs')} → {result.get('currentSongs')} songs")
                    else:
                        print(f"❌ Reload failed: {result.get('error', 'Unknown')}")
                else:
                    print("❌ Daemon not running")
            
            elif cmd == 'refresh':
                if daemon and daemon.is_running:
                    print("Refreshing metadata from disk...")
                    result = daemon.refresh()
                    if result.get('status') == 'refreshed':
                        print(f"✅ Refreshed: {result.get('previousSongs')} → {result.get('currentSongs')} songs")
                    else:
                        print(f"❌ Refresh failed: {result.get('error', 'Unknown')}")
                else:
                    print("❌ Daemon not running")
            
            elif cmd == 'undo':
                if args == '--list':
                    # Show session history
                    sessions = load_session_logs(db_path)
                    if not sessions:
                        print("No undo history found.")
                        continue
                    
                    print(f"\n=== Recent Sessions ({len(sessions)}) ===\n")
                    for i, session in enumerate(sessions, 1):
                        timestamp = session.get('timestamp', '')[:19].replace('T', ' ')
                        action = session.get('action', '?')
                        folder = Path(session.get('folder', '')).name
                        count = session.get('count', 0)
                        undone = " [UNDONE]" if session.get('undone') else ""
                        session_id = session.get('id', '?')
                        print(f"  {i}. [{timestamp}] {action} {folder} ({count} songs){undone}")
                        print(f"     ID: {session_id}")
                    print()
                    
                elif args:
                    # Undo specific session
                    session_id = args.strip()
                    result = undo_session(db_path, session_id, daemon)
                    if result.get('success'):
                        print(f"✅ Undo complete: deleted {result['deleted']}/{result['total']} songs")
                        if result.get('errors'):
                            print(f"   ⚠️  {len(result['errors'])} errors:")
                            for err in result['errors'][:5]:
                                print(f"      - {err}")
                    else:
                        print(f"❌ Undo failed: {result.get('error', 'Unknown')}")
                else:
                    # Undo last session (or specific session if provided without --list)
                    session_id = args.strip() if args and not args.startswith('--') else None
                    
                    if session_id:
                        # Load specific session
                        sessions_dir = db_path / "sessions"
                        session_path = sessions_dir / f"session_{session_id}.json"
                        if not session_path.exists():
                            print(f"❌ Session not found: {session_id}")
                            continue
                        session = load_json_file(session_path)
                    else:
                        # Load last undoable session
                        sessions = load_session_logs(db_path, limit=10)
                        active_sessions = [s for s in sessions if not s.get('undone')]
                        
                        if not active_sessions:
                            print("No undoable sessions found. Use 'undo --list' to see history.")
                            continue
                        
                        session = active_sessions[0]
                    
                    if session.get('undone'):
                        print(f"❌ Session already undone")
                        continue
                    
                    # Show session details
                    timestamp = session.get('timestamp', '')[:19].replace('T', ' ')
                    action = session.get('action', '?')
                    folder = Path(session.get('folder', '')).name
                    songs = session.get('added_songs', [])
                    
                    print(f"\n{'=' * 60}")
                    print(f"Session: {session.get('id')}")
                    print(f"Action: {action} {folder}")
                    print(f"When: {timestamp}")
                    print(f"Songs: {len(songs)}")
                    print(f"{'=' * 60}")
                    
                    # Show song list (limit to 100 for display)
                    print("\nSongs that will be DELETED:\n")
                    for i, song in enumerate(songs[:100], 1):
                        print(f"  {i}. {song.get('songId', '?')}")
                    if len(songs) > 100:
                        print(f"  ... and {len(songs) - 100} more")
                    
                    print(f"\n⚠️  This will DELETE {len(songs)} songs from the database!")
                    print("   Options:")
                    print("     y = Delete all songs")
                    print("     n = Cancel")
                    print("     s = Select specific songs to keep")
                    
                    response = input("\nYour choice (y/n/s): ").strip().lower()
                    
                    if response == 'n' or response == '':
                        print("Cancelled.")
                        continue
                    elif response == 's':
                        # Selective mode: let user pick songs to KEEP
                        print("\nEnter song numbers to KEEP (comma-separated, e.g., 1,3,5):")
                        print("Or press Enter to delete all.\n")
                        
                        for i, song in enumerate(songs, 1):
                            print(f"  {i}. {song.get('songId', '?')}")
                        
                        keep_input = input("\nSongs to keep: ").strip()
                        if keep_input:
                            try:
                                keep_indices = set(int(x.strip()) - 1 for x in keep_input.split(',') if x.strip())
                                songs_to_delete = [s for i, s in enumerate(songs) if i not in keep_indices]
                                songs_to_keep = [s for i, s in enumerate(songs) if i in keep_indices]
                                
                                print(f"\nWill DELETE {len(songs_to_delete)} songs, KEEP {len(songs_to_keep)} songs.")
                                confirm = input("Proceed? (y/N): ").strip().lower()
                                if confirm != 'y':
                                    print("Cancelled.")
                                    continue
                                
                                # Partial delete using purge
                                for song in songs_to_delete:
                                    song_id = song.get('songId')
                                    if daemon and daemon.is_running:
                                        daemon.delete(song_id)
                                    filepath = song.get('filepath')
                                    if filepath:
                                        indexed_files_path = db_path / "indexed_files.json"
                                        indexed_files = load_json_file(indexed_files_path)
                                        if filepath in indexed_files:
                                            del indexed_files[filepath]
                                        save_json_file(indexed_files_path, indexed_files)
                                
                                if daemon and daemon.is_running:
                                    daemon.save()
                                
                                # Update session to mark partial undo
                                session['partial_undo'] = True
                                session['kept_songs'] = songs_to_keep
                                session['deleted_songs'] = songs_to_delete
                                save_json_file(Path(session.get('_path', db_path / "sessions" / f"session_{session['id']}.json")), session)
                                
                                print(f"✅ Partial undo complete: deleted {len(songs_to_delete)} songs")
                                continue
                            except ValueError:
                                print("Invalid input. Cancelled.")
                                continue
                    elif response == 'y':
                        result = undo_session(db_path, session['id'], daemon)
                        if result.get('success'):
                            print(f"✅ Undo complete: deleted {result['deleted']}/{result['total']} songs")
                            if result.get('errors'):
                                print(f"\n⚠️  {len(result['errors'])} songs failed to delete:")
                                for err in result['errors'][:20]:
                                    print(f"   - {err}")
                                if len(result['errors']) > 20:
                                    print(f"   ... and {len(result['errors']) - 20} more errors")
                        else:
                            print(f"❌ Undo failed: {result.get('error', 'Unknown')}")
            
            elif cmd == 'clear':
                # CLI mode: daemon needs to be stopped before clearing, then restarted
                print("⚠️  Warning: This will clear the entire database!")
                print("   The daemon will be stopped, database cleared, then restarted.\n")
                response = input("Type 'yes' to confirm: ").strip().lower()
                if response != 'yes':
                    print("❌ Cancelled.")
                    continue
                
                # Stop daemon before clearing
                if daemon and daemon.is_running:
                    print("Stopping daemon...")
                    daemon.stop()
                
                # Clear the database
                result = clear_database(db_path, force=True)
                
                # Restart daemon
                print("\nRestarting daemon...")
                daemon = IndexingDaemon(db_path)
                if daemon.start():
                    print(f"✅ Daemon restarted")
                else:
                    print("⚠️  Could not restart daemon")
                    daemon = None
            
            elif cmd == 'test':
                if not args:
                    print("Usage: test <folder> [--positions 10,60,120] [--duration 10]")
                    continue
                
                # Parse arguments
                positions = [10, 60, 120]
                duration = 10
                clean_args = args
                
                if '--positions' in args:
                    import re
                    match = re.search(r'--positions\s+([0-9,]+)', args)
                    if match:
                        positions = [int(p) for p in match.group(1).split(',')]
                        clean_args = re.sub(r'--positions\s+[0-9,]+', '', clean_args)
                
                if '--duration' in args:
                    import re
                    match = re.search(r'--duration\s+(\d+)', args)
                    if match:
                        duration = int(match.group(1))
                        clean_args = re.sub(r'--duration\s+\d+', '', clean_args)
                
                clean_args = clean_args.strip()
                if not clean_args:
                    print("Error: No folder specified")
                    continue
                
                # Strip surrounding quotes (single or double)
                if (clean_args.startswith('"') and clean_args.endswith('"')) or \
                   (clean_args.startswith("'") and clean_args.endswith("'")):
                    clean_args = clean_args[1:-1]
                
                folder = Path(clean_args.rstrip('/\\'))
                if not folder.exists():
                    print(f"Error: Folder not found: {folder}")
                    continue
                
                test_recognition(folder, db_path, clip_duration=duration, positions=positions, daemon=daemon)
            
            elif cmd == 'live':
                # Parse optional duration
                duration = 10
                if args:
                    try:
                        duration = int(args.strip())
                    except ValueError:
                        print(f"Invalid duration: {args}. Using default 10s.")
                
                test_live_capture(db_path, duration=duration)
            
            else:
                print(f"Unknown command: {cmd}. Type 'help' for available commands.")

    
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    
    finally:
        if daemon:
            daemon.stop()
        print("Goodbye!")


def verify_database(db_path: Path, daemon: 'IndexingDaemon' = None, brief: bool = False, 
                    low_fp_threshold: int = None) -> Dict[str, Any]:
    """
    Verify database integrity by comparing all 3 data sources:
    - Fingerprint DB (via daemon list-fp command)
    - metadata.json
    - indexed_files.json
    
    Args:
        db_path: Path to database directory
        daemon: Optional daemon instance to reuse (avoids starting new one)
        brief: If True, only show sync table without detailed discrepancies
        low_fp_threshold: If set, report songs with fingerprint count below this threshold
    
    Reports ALL discrepancies with detailed logging.
    """
    if not brief:
        print(f"\n{'=' * 70}")
        print("DATABASE VERIFICATION REPORT")
        print(f"{'=' * 70}")
        print(f"Database: {db_path}\n")
    
    # Load all data sources
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    
    metadata = load_json_file(metadata_path)  # songId -> {metadata}
    indexed_files = load_json_file(indexed_files_path)  # filepath -> {songId, ...}
    
    # Extract songIds from each source
    metadata_ids = set(metadata.keys())
    
    # indexed_files: extract songId from each entry (excluding skipped entries if desired)
    index_id_to_files: Dict[str, List[str]] = {}  # songId -> [filepaths]
    for filepath, entry in indexed_files.items():
        song_id = entry.get('songId')
        if song_id:
            if song_id not in index_id_to_files:
                index_id_to_files[song_id] = []
            index_id_to_files[song_id].append(filepath)
    index_ids = set(index_id_to_files.keys())
    
    # Get fingerprint IDs from daemon
    fp_ids = set()
    own_daemon = daemon is None
    
    fp_check_incomplete = False  # Track if we couldn't query FP DB
    
    if own_daemon:
        print("Querying fingerprint database...")
        daemon = IndexingDaemon(db_path)
        if not daemon.start():
            print("❌ ERROR: Could not start daemon to query fingerprints")
            print("   Fingerprint DB verification INCOMPLETE")
            fp_ids = set()  # Empty = will show all songs as missing from FP
            fp_check_incomplete = True
            daemon = None
    
    if daemon and daemon.is_running:
        try:
            # Use list_fp method (works with both TCP and subprocess)
            result = daemon.list_fp()
            fp_ids = set(result.get('songIds', []))
        except Exception as e:
            print(f"❌ ERROR: Could not query fingerprint DB: {e}")
            print("   Fingerprint DB verification INCOMPLETE")
            fp_ids = set()  # Empty = will show all songs as missing from FP
            fp_check_incomplete = True
    
    # Stop daemon only if we started it ourselves
    if own_daemon and daemon:
        daemon.stop()
    
    # Print sync table (always shown)
    print_sync_table(fp_ids, metadata_ids, index_ids)
    
    # Show prominent warning if FP check was incomplete
    if fp_check_incomplete:
        print()
        print("⚠️ " + "=" * 60)
        print("⚠️  WARNING: Fingerprint DB verification INCOMPLETE")
        print("⚠️  The daemon could not be queried. META_NO_FP and INDEX_NO_FP")
        print("⚠️  discrepancies below may be FALSE - FP DB status unknown.")
        print("⚠️ " + "=" * 60)
        print()
    
    # Find discrepancies
    discrepancies: Dict[str, List] = {
        'FP_NO_META': [],      # In FP, not in metadata
        'FP_NO_INDEX': [],     # In FP, not in index
        'META_NO_FP': [],      # In metadata, not in FP
        'META_NO_INDEX': [],   # In metadata, not in index
        'INDEX_NO_META': [],   # In index, not in metadata
        'INDEX_NO_FP': [],     # In index, not in FP
    }
    
    # FP vs others
    for song_id in fp_ids:
        if song_id not in metadata_ids:
            discrepancies['FP_NO_META'].append({'songId': song_id})
        if song_id not in index_ids:
            discrepancies['FP_NO_INDEX'].append({'songId': song_id})
    
    # Metadata vs others
    for song_id in metadata_ids:
        meta = metadata[song_id]
        if song_id not in fp_ids:
            discrepancies['META_NO_FP'].append({
                'songId': song_id,
                'filepath': meta.get('originalFilepath', 'unknown'),
                'artist': meta.get('artist', '?'),
                'title': meta.get('title', '?')
            })
        if song_id not in index_ids:
            discrepancies['META_NO_INDEX'].append({
                'songId': song_id,
                'filepath': meta.get('originalFilepath', 'unknown')
            })
    
    # Index vs others
    for song_id in index_ids:
        filepaths = index_id_to_files.get(song_id, [])
        if song_id not in metadata_ids:
            discrepancies['INDEX_NO_META'].append({
                'songId': song_id,
                'filepaths': filepaths
            })
        if song_id not in fp_ids:
            discrepancies['INDEX_NO_FP'].append({
                'songId': song_id,
                'filepaths': filepaths
            })
    
    # Print discrepancies
    total_issues = sum(len(v) for v in discrepancies.values())
    
    if total_issues == 0:
        print(f"✅ All databases are in sync! No discrepancies found.")
    else:
        print(f"⚠️  DISCREPANCIES FOUND: {total_issues} total")
        
        # Show discrepancy table
        print_discrepancy_table(discrepancies, fp_ids, metadata_ids, index_ids)
        
        # Skip detailed output in brief mode
        if not brief:
            if discrepancies['FP_NO_META']:
                print(f"[FP_NO_META] Fingerprint exists, NO metadata ({len(discrepancies['FP_NO_META'])} songs):")
                for item in discrepancies['FP_NO_META'][:10]:  # Limit output
                    print(f"   - songId: \"{item['songId']}\"")
                if len(discrepancies['FP_NO_META']) > 10:
                    print(f"   ... and {len(discrepancies['FP_NO_META']) - 10} more")
                print()
            
            if discrepancies['FP_NO_INDEX']:
                print(f"[FP_NO_INDEX] Fingerprint exists, NOT in index ({len(discrepancies['FP_NO_INDEX'])} songs):")
                for item in discrepancies['FP_NO_INDEX'][:10]:
                    print(f"   - songId: \"{item['songId']}\"")
                if len(discrepancies['FP_NO_INDEX']) > 10:
                    print(f"   ... and {len(discrepancies['FP_NO_INDEX']) - 10} more")
                print()
            
            if discrepancies['META_NO_FP']:
                print(f"[META_NO_FP] Metadata exists, NO fingerprint ({len(discrepancies['META_NO_FP'])} songs):")
                for item in discrepancies['META_NO_FP'][:10]:
                    print(f"   - {item['artist']} - {item['title']}")
                    print(f"     File: {item['filepath']}")
                if len(discrepancies['META_NO_FP']) > 10:
                    print(f"   ... and {len(discrepancies['META_NO_FP']) - 10} more")
                print()
            
            if discrepancies['META_NO_INDEX']:
                print(f"[META_NO_INDEX] Metadata exists, NOT in index ({len(discrepancies['META_NO_INDEX'])} songs):")
                for item in discrepancies['META_NO_INDEX'][:10]:
                    print(f"   - songId: \"{item['songId']}\" | File: {Path(item['filepath']).name}")
                if len(discrepancies['META_NO_INDEX']) > 10:
                    print(f"   ... and {len(discrepancies['META_NO_INDEX']) - 10} more")
                print()
            
            if discrepancies['INDEX_NO_META']:
                print(f"[INDEX_NO_META] Index entry exists, NO metadata ({len(discrepancies['INDEX_NO_META'])} songs):")
                for item in discrepancies['INDEX_NO_META'][:10]:
                    print(f"   - songId: \"{item['songId']}\"")
                    for fp in item['filepaths'][:2]:
                        print(f"     File: {Path(fp).name}")
                if len(discrepancies['INDEX_NO_META']) > 10:
                    print(f"   ... and {len(discrepancies['INDEX_NO_META']) - 10} more")
                print()
            
            if discrepancies['INDEX_NO_FP']:
                print(f"[INDEX_NO_FP] Index entry exists, NO fingerprint ({len(discrepancies['INDEX_NO_FP'])} songs):")
                for item in discrepancies['INDEX_NO_FP'][:10]:
                    print(f"   - songId: \"{item['songId']}\"")
                    for fp in item['filepaths'][:2]:
                        print(f"     File: {Path(fp).name}")
                if len(discrepancies['INDEX_NO_FP']) > 10:
                    print(f"   ... and {len(discrepancies['INDEX_NO_FP']) - 10} more")
                print()
    
    if not brief:
        print(f"{'=' * 70}")
    
    # Low fingerprint count audit
    low_fp_songs = []
    if low_fp_threshold is not None:
        print(f"\n{'=' * 70}")
        print(f"LOW FINGERPRINT AUDIT (threshold: < {low_fp_threshold})")
        print(f"{'=' * 70}\n")
        
        for song_id, meta in metadata.items():
            fp_count = meta.get('fingerprintCount', 0)
            duration = meta.get('duration', 0)
            if fp_count < low_fp_threshold:
                low_fp_songs.append({
                    'songId': song_id,
                    'artist': meta.get('artist', '?'),
                    'title': meta.get('title', '?'),
                    'fpCount': fp_count,
                    'duration': duration
                })
        
        # Sort by fingerprint count (0 first, then ascending)
        low_fp_songs.sort(key=lambda x: x['fpCount'])
        
        if not low_fp_songs:
            print(f"✅ No songs with < {low_fp_threshold} fingerprints found.")
        else:
            # Separate zero FPs (broken) from low FPs
            zero_fp = [s for s in low_fp_songs if s['fpCount'] == 0]
            low_fp = [s for s in low_fp_songs if 0 < s['fpCount'] < low_fp_threshold]
            
            if zero_fp:
                print(f"❌ 0 fingerprints (BROKEN, need re-index): {len(zero_fp)} songs\n")
                for s in zero_fp[:20]:
                    print(f"  • {s['artist']} - {s['title']} [FPs: 0]")
                    print(f"    ID: {s['songId']}")
                if len(zero_fp) > 20:
                    print(f"  ... and {len(zero_fp) - 20} more\n")
                print()
            
            if low_fp:
                print(f"⚠️  < {low_fp_threshold} fingerprints: {len(low_fp)} songs\n")
                for s in low_fp[:20]:
                    dur_str = f" ({s['duration']:.0f}s)" if s['duration'] else ""
                    # Check if low FP might be OK (short song)
                    expected_fps = int(s['duration'] * 10.7) if s['duration'] else 0  # ~10.7 FPs per second
                    status = "✓ short" if expected_fps < low_fp_threshold else "⚠"
                    print(f"  • {s['artist']} - {s['title']} [FPs: {s['fpCount']}{dur_str}] {status}")
                    print(f"    ID: {s['songId']}")
                if len(low_fp) > 20:
                    print(f"  ... and {len(low_fp) - 20} more\n")
                print()
            
            print(f"Total: {len(low_fp_songs)} songs with low fingerprint counts")
            print(f"\nTip: Use 'reindex <folder>' to re-fingerprint problem songs")
        
        print(f"{'=' * 70}")
    
    return {
        'fp_count': len(fp_ids),
        'metadata_count': len(metadata_ids),
        'index_count': len(index_ids),
        'discrepancies': discrepancies,
        'total_issues': total_issues,
        'fp_ids': fp_ids,
        'metadata_ids': metadata_ids,
        'index_ids': index_ids,
        'low_fp_songs': low_fp_songs
    }


def repair_database(db_path: Path, batch: bool = False, auto: bool = False, daemon: 'IndexingDaemon' = None) -> Dict[str, Any]:
    """
    Repair database discrepancies.
    
    Args:
        db_path: Database directory path
        batch: If True, ask once for all fixes
        auto: If True, no confirmation needed
        daemon: Optional daemon instance to reuse
    """
    print(f"\n{'=' * 70}")
    print("DATABASE REPAIR")
    print(f"{'=' * 70}")
    
    # First run verify to get discrepancies (reuse daemon if provided)
    print("Running verification first...\n")
    verify_result = verify_database(db_path, daemon=daemon)
    
    discrepancies = verify_result['discrepancies']
    total_issues = verify_result['total_issues']
    
    if total_issues == 0:
        print("\n✅ Nothing to repair!")
        return {'repaired': 0, 'skipped': 0}
    
    # Build repair plan
    repair_plan = []
    
    # Load data for repairs
    metadata_path = db_path / "metadata.json"
    indexed_files_path = db_path / "indexed_files.json"
    metadata = load_json_file(metadata_path)
    indexed_files = load_json_file(indexed_files_path)
    
    # Build reverse lookup: songId -> filepath from index
    index_id_to_file = {}
    for filepath, entry in indexed_files.items():
        song_id = entry.get('songId')
        if song_id and song_id not in index_id_to_file:
            index_id_to_file[song_id] = filepath
    
    # Plan repairs for each type
    for item in discrepancies.get('FP_NO_META', []):
        song_id = item['songId']
        filepath = index_id_to_file.get(song_id)
        if filepath and Path(filepath).exists():
            repair_plan.append({
                'type': 'FP_NO_META',
                'action': 're-extract metadata',
                'songId': song_id,
                'filepath': filepath
            })
        else:
            repair_plan.append({
                'type': 'FP_NO_META',
                'action': 'WARN - no filepath, consider deleting fingerprint',
                'songId': song_id,
                'repairable': False
            })
    
    for item in discrepancies.get('FP_NO_INDEX', []):
        repair_plan.append({
            'type': 'FP_NO_INDEX',
            'action': 'WARN - no filepath available',
            'songId': item['songId'],
            'repairable': False
        })
    
    for item in discrepancies.get('META_NO_FP', []):
        filepath = item['filepath']
        if filepath and Path(filepath).exists():
            repair_plan.append({
                'type': 'META_NO_FP',
                'action': 're-fingerprint file',
                'songId': item['songId'],
                'filepath': filepath
            })
        else:
            repair_plan.append({
                'type': 'META_NO_FP',
                'action': 'delete orphan metadata (file not found)',
                'songId': item['songId'],
                'filepath': filepath
            })
    
    for item in discrepancies.get('META_NO_INDEX', []):
        repair_plan.append({
            'type': 'META_NO_INDEX',
            'action': 'add to index',
            'songId': item['songId'],
            'filepath': item['filepath']
        })
    
    for item in discrepancies.get('INDEX_NO_META', []):
        filepaths = item.get('filepaths', [])
        filepath = filepaths[0] if filepaths else None
        if filepath and Path(filepath).exists():
            repair_plan.append({
                'type': 'INDEX_NO_META',
                'action': 're-extract metadata',
                'songId': item['songId'],
                'filepath': filepath
            })
        else:
            repair_plan.append({
                'type': 'INDEX_NO_META',
                'action': 'WARN - file not found',
                'songId': item['songId'],
                'repairable': False
            })
    
    for item in discrepancies.get('INDEX_NO_FP', []):
        filepaths = item.get('filepaths', [])
        filepath = filepaths[0] if filepaths else None
        if filepath and Path(filepath).exists():
            repair_plan.append({
                'type': 'INDEX_NO_FP',
                'action': 're-fingerprint file',
                'songId': item['songId'],
                'filepath': filepath
            })
        else:
            repair_plan.append({
                'type': 'INDEX_NO_FP',
                'action': 'WARN - file not found',
                'songId': item['songId'],
                'repairable': False
            })
    
    # Show repair plan
    repairable = [r for r in repair_plan if r.get('repairable', True)]
    warnings = [r for r in repair_plan if not r.get('repairable', True)]
    
    print(f"\n{'=' * 70}")
    print("REPAIR PLAN (DRY RUN)")
    print(f"{'=' * 70}")
    print(f"\nRepairable issues: {len(repairable)}")
    print(f"Warnings (manual intervention needed): {len(warnings)}")
    
    if repairable:
        print(f"\n📋 Will perform these repairs:")
        for i, r in enumerate(repairable[:20], 1):
            print(f"   {i}. [{r['type']}] {r['action']}")
            print(f"      songId: {r['songId']}")
        if len(repairable) > 20:
            print(f"   ... and {len(repairable) - 20} more")
    
    if warnings:
        print(f"\n⚠️  These require manual intervention:")
        for w in warnings[:10]:
            print(f"   - [{w['type']}] {w['action']} | songId: {w['songId']}")
        if len(warnings) > 10:
            print(f"   ... and {len(warnings) - 10} more")
    
    if not repairable:
        print("\n❌ No automatic repairs possible. Please resolve warnings manually.")
        return {'repaired': 0, 'skipped': len(warnings)}
    
    # Get confirmation
    if not auto:
        print()
        if batch:
            response = input(f"Proceed with all {len(repairable)} repairs? (y/N): ").strip().lower()
            if response != 'y':
                print("Repair cancelled.")
                return {'repaired': 0, 'skipped': len(repairable)}
        else:
            response = input("Proceed with interactive repair? (y/N): ").strip().lower()
            if response != 'y':
                print("Repair cancelled.")
                return {'repaired': 0, 'skipped': len(repairable)}
    
    # Execute repairs
    print(f"\n{'=' * 70}")
    print("EXECUTING REPAIRS")
    print(f"{'=' * 70}\n")
    
    repaired = 0
    skipped = 0
    
    # Start daemon for fingerprinting operations (reuse if provided)
    own_daemon = daemon is None
    needs_daemon = any(r['action'] in ['re-fingerprint file'] for r in repairable)
    
    if needs_daemon and own_daemon:
        daemon = IndexingDaemon(db_path)
        if not daemon.start():
            print("❌ Could not start daemon for fingerprinting")
            return {'repaired': 0, 'error': 'daemon failed'}
    
    try:
        for i, repair in enumerate(repairable, 1):
            # Interactive mode: ask for each
            if not auto and not batch:
                print(f"\n[{i}/{len(repairable)}] {repair['action']}")
                print(f"   songId: {repair['songId']}")
                if repair.get('filepath'):
                    print(f"   file: {Path(repair['filepath']).name}")
                response = input("   Apply this fix? (y/n/q): ").strip().lower()
                if response == 'q':
                    print("Repair aborted.")
                    break
                if response != 'y':
                    skipped += 1
                    continue
            
            # Execute the repair
            try:
                if repair['action'] == 're-extract metadata':
                    filepath = repair['filepath']
                    if Path(filepath).exists():
                        new_meta = extract_full_metadata(Path(filepath))
                        new_meta['songId'] = repair['songId']
                        # Preserve existing fields that extraction doesn't provide
                        existing = metadata.get(repair['songId'], {})
                        for keep_field in ['fingerprintCount', 'indexedAt', 'contentHash']:
                            # Preserve if existing has value and new_meta doesn't (or is None)
                            if existing.get(keep_field) and not new_meta.get(keep_field):
                                new_meta[keep_field] = existing[keep_field]
                        metadata[repair['songId']] = new_meta
                        print(f"   ✅ Re-extracted metadata for {repair['songId']}")
                        repaired += 1
                    else:
                        print(f"   ❌ File not found: {filepath}")
                        skipped += 1
                
                elif repair['action'] == 're-fingerprint file':
                    filepath = repair['filepath']
                    if daemon and Path(filepath).exists():
                        meta = extract_full_metadata(Path(filepath))
                        meta['songId'] = repair['songId']
                        result = daemon.fingerprint(Path(filepath), meta, force=True)
                        if result.get('success'):
                            print(f"   ✅ Re-fingerprinted {repair['songId']}")
                            repaired += 1
                        else:
                            print(f"   ❌ Failed: {result.get('error', 'Unknown')}")
                            skipped += 1
                    else:
                        print(f"   ❌ Cannot fingerprint: {filepath}")
                        skipped += 1
                
                elif repair['action'] == 'add to index':
                    filepath = repair['filepath']
                    song_id = repair['songId']
                    indexed_files[filepath] = {
                        'songId': song_id,
                        'indexedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        'repairedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    }
                    print(f"   ✅ Added to index: {song_id}")
                    repaired += 1
                
                elif 'delete orphan' in repair['action']:
                    song_id = repair['songId']
                    if song_id in metadata:
                        del metadata[song_id]
                        print(f"   ✅ Deleted orphan metadata: {song_id}")
                        repaired += 1
                    else:
                        skipped += 1
                
                else:
                    print(f"   ⏭️  Skipped: {repair['action']}")
                    skipped += 1
                    
            except Exception as e:
                print(f"   ❌ Error: {e}")
                skipped += 1
    
    finally:
        if daemon and own_daemon:
            daemon.save()
            daemon.stop()
        elif daemon:
            daemon.save()  # Save but don't stop if we're reusing
    
    # Save updated files
    if repaired > 0:
        save_json_file(metadata_path, metadata)
        save_json_file(indexed_files_path, indexed_files)
        print(f"\n✅ Saved updated metadata.json and indexed_files.json")
    
    print(f"\n{'=' * 70}")
    print(f"REPAIR COMPLETE: {repaired} fixed, {skipped} skipped")
    print(f"{'=' * 70}")
    
    return {'repaired': repaired, 'skipped': skipped}


def reindex_folder(folder_path: Path, db_path: Path, force_include: bool = False,
                   dry_run: bool = False, daemon: 'IndexingDaemon' = None) -> Dict[str, Any]:
    """
    Force re-index all files in folder, overwriting existing entries.
    
    Unlike normal indexing, this:
    - Ignores indexed_files.json check (processes all files)
    - Overwrites existing fingerprints and metadata
    
    Args:
        folder_path: Path to folder containing audio files
        db_path: Path to database directory
        force_include: If True, bypasses exclusion list check (default: respects exclusions)
        dry_run: If True, only show what would be reindexed without making changes
        daemon: Optional existing IndexingDaemon to reuse (avoids creating duplicate)
    """
    print(f"\n{'=' * 70}")
    if dry_run:
        print("FORCE RE-INDEX (DRY-RUN PREVIEW)")
    else:
        print("FORCE RE-INDEX")
    print(f"{'=' * 70}")
    print(f"Folder: {folder_path}")
    print(f"Database: {db_path}")
    if force_include:
        print("Mode: --force (ignoring exclusion list)")
    else:
        print("Mode: normal (respecting exclusion list)")
    
    # Skip confirmation prompt in dry-run mode
    if not dry_run:
        print(f"\n⚠️  This will overwrite existing fingerprints and metadata for files in this folder.")
        response = input("Continue? (y/N): ").strip().lower()
        if response != 'y':
            print("Re-index cancelled.")
            return {'cancelled': True}
    
    # Find all audio files
    audio_files = []
    for ext in SUPPORTED_EXTENSIONS:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    print(f"\nFound {len(audio_files)} audio files.")
    
    # Load exclusion list (unless force_include)
    exclusions = {} if force_include else load_exclusion_list(db_path)
    
    # Pre-scan files to filter and collect metadata
    would_reindex = []
    excluded_count = 0
    skipped_count = 0
    
    for audio_file in audio_files:
        file_key = str(audio_file.absolute())
        
        # Extract metadata
        metadata = extract_full_metadata(audio_file)
        if not metadata['title'] or not metadata['artist']:
            skipped_count += 1
            continue
        
        song_id = normalize_song_id(metadata['artist'], metadata['title'])
        
        # Check exclusion list (unless force_include)
        if exclusions and is_excluded(song_id, metadata['artist'], metadata['title'], exclusions):
            excluded_count += 1
            continue
        
        would_reindex.append({
            'audio_file': audio_file,
            'file_key': file_key,
            'song_id': song_id,
            'metadata': metadata  # Store full metadata dict
        })
    
    # --- DRY-RUN: Exit with detailed summary ---
    if dry_run:
        print(f"\n{'=' * 70}")
        print("DRY-RUN MODE - No changes will be made")
        print(f"{'=' * 70}\n")
        
        if would_reindex:
            print(f"Would re-index {len(would_reindex)} files:\n")
            for item in would_reindex:
                m = item['metadata']
                dur = m.get('duration', 0)
                dur_str = f" ({dur:.0f}s)" if dur else ""
                print(f"  • {m['artist']} - {m['title']}{dur_str}")
                print(f"    ID: {item['song_id']}")
                print(f"    File: {item['file_key']}")
        else:
            print("No files to re-index.")
        
        print(f"\n{'=' * 40}")
        print("Summary")
        print(f"{'=' * 40}")
        print(f"  Total files found: {len(audio_files)}")
        print(f"  Would re-index: {len(would_reindex)}")
        if skipped_count > 0:
            print(f"  Skipped (missing tags): {skipped_count}")
        if excluded_count > 0:
            print(f"  Excluded: {excluded_count}")
        
        # Build export-friendly list (without Path objects)
        would_reindex_export = [
            {
                'songId': item['song_id'],
                'artist': item['metadata']['artist'],
                'title': item['metadata']['title'],
                'duration': item['metadata'].get('duration', 0),
                'filepath': item['file_key']
            }
            for item in would_reindex
        ]
        
        return {
            'dry_run': True,
            'total': len(audio_files),
            'would_reindex': would_reindex_export,
            'excluded': excluded_count,
            'skipped': skipped_count
        }
    
    # --- Normal mode: proceed with reindexing ---
    print(f"\n{len(would_reindex)} files to re-index.\n")
    
    # Load tracking files
    indexed_files_path = db_path / "indexed_files.json"
    indexed_files = load_json_file(indexed_files_path)
    
    # Start daemon (or reuse existing if running)
    own_daemon = daemon is None or not daemon.is_running
    if own_daemon:
        daemon = IndexingDaemon(db_path)
        if not daemon.start():
            print("❌ Failed to start indexing daemon")
            return {'error': 'Daemon startup failed'}
    else:
        print("✅ Using active CLI daemon")
    
    results = {
        'total': len(audio_files),
        'reindexed': 0,
        'excluded': excluded_count,  # Already counted in pre-scan
        'failed': 0,
        'files': []
    }
    
    try:
        for i, item in enumerate(would_reindex, 1):
            audio_file = item['audio_file']
            file_key = item['file_key']
            song_id = item['song_id']
            metadata = item['metadata']
            
            # Add required fields to metadata
            metadata['songId'] = song_id
            metadata['originalFilepath'] = file_key
            
            print(f"[{i}/{len(would_reindex)}] {audio_file.name}...")
            
            # Compute content hash (this is the only expensive operation we can't pre-scan)
            content_hash = compute_content_hash(audio_file)
            metadata['contentHash'] = content_hash
            
            # Fingerprint with force=True to overwrite existing
            result = daemon.fingerprint(Path(audio_file), metadata, force=True)
            
            if result.get('success'):
                print(f"   ✅ {metadata['artist']} - {metadata['title']} ({result.get('fingerprints', 0)} FPs)")
                results['reindexed'] += 1
                results['files'].append({
                    'songId': song_id,
                    'filepath': file_key,
                    'fingerprints': result.get('fingerprints', 0)
                })
                
                # Update indexed_files
                indexed_files[file_key] = {
                    'songId': song_id,
                    'contentHash': content_hash,
                    'indexedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'reindexed': True
                }
            elif result.get('skipped'):
                # Already indexed with same content - that's fine for reindex
                print(f"   ⏭️  Already up-to-date: {result.get('reason', '')}")
            else:
                print(f"   ❌ Failed: {result.get('error', 'Unknown')}")
                results['failed'] += 1
            
            # Save periodically
            if i % 10 == 0:
                daemon.save()
                save_json_file(indexed_files_path, indexed_files)
    
    finally:
        if own_daemon:
            daemon.save()
            daemon.stop()
        else:
            daemon.save()  # Just save, don't stop the CLI's daemon
        save_json_file(indexed_files_path, indexed_files)
    
    print(f"\n{'=' * 70}")
    print(f"RE-INDEX COMPLETE: {results['reindexed']} re-indexed, {results['failed']} failed")
    print(f"{'=' * 70}")
    
    # Save session log for undo
    if results['reindexed'] > 0:
        added_songs = [
            {'songId': f['songId'], 'filepath': f['filepath']}
            for f in results['files']
        ]
        flags = []
        if force_include:
            flags.append('--force')
        
        session_path = save_session_log(db_path, 'reindex', folder_path, added_songs, flags)
        print(f"\n📝 Session logged: {session_path.name}")
        print("   Use 'undo' to reverse this action if needed.")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="SoundFingerprinting Database Manager v2.1")
    
    # Interactive CLI mode (recommended)
    parser.add_argument("--cli", action="store_true",
                        help="Interactive CLI mode with daemon reuse (recommended)")
    
    # One-shot commands
    parser.add_argument("--index", type=str, help="Index all songs in folder")
    parser.add_argument("--test", type=str, help="Test recognition accuracy on folder")
    parser.add_argument("--live", action="store_true", help="Test live audio capture")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    parser.add_argument("--clear", action="store_true", help="Clear database (requires confirmation)")
    parser.add_argument("--delete", type=str, help="Delete a specific song by song_id")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--db-path", type=str, help="Override database path")
    parser.add_argument("--duration", type=int, default=10, help="Clip duration for testing (default: 10)")
    parser.add_argument("--positions", type=str, help="Comma-separated positions to test (default: 10,60,120)")
    parser.add_argument("--require-tags", type=str, 
                        help="Comma-separated list of additional required metadata fields (e.g., album,genre,year)")
    
    # Database verification and repair
    parser.add_argument("--verify", action="store_true", 
                        help="Verify database integrity - compare fingerprints, metadata, and index")
    parser.add_argument("--repair", action="store_true",
                        help="Repair database discrepancies (dry-run first, then interactive)")
    parser.add_argument("--batch", action="store_true",
                        help="With --repair: ask once for all fixes instead of each individually")
    parser.add_argument("--auto", action="store_true",
                        help="With --repair: no confirmation, just fix everything")
    parser.add_argument("--reindex", type=str, metavar="FOLDER",
                        help="Force re-index all files in folder (overwrites existing)")
    
    # Preview mode
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview what would be indexed/reindexed without making changes")
    parser.add_argument("--export", type=str, metavar="FILE",
                        help="Export dry-run results to JSON file")
    
    args = parser.parse_args()
    
    # Determine database path
    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = get_db_path()
    
    # Interactive CLI mode
    if args.cli:
        cli_mode(db_path)
    
    elif args.index:
        # Strip trailing slashes/backslashes (Windows CMD escapes closing quote with trailing \)
        folder = Path(args.index.rstrip('/\\'))
        if not folder.exists():
            print(f"Error: Folder not found: {folder}")
            return
        
        # Parse required tags
        required_tags = None
        if args.require_tags:
            required_tags = [t.strip() for t in args.require_tags.split(',') if t.strip()]
            print(f"Requiring additional tags: {', '.join(required_tags)}")
        
        result = index_folder(folder, db_path, required_tags=required_tags, dry_run=args.dry_run)
        
        # Handle export if dry-run
        if args.export and result.get('dry_run'):
            export_path = Path(args.export)
            save_json_file(export_path, result)
            print(f"\n✓ Dry-run results exported to: {export_path}")
    
    elif args.test:
        # Strip trailing slashes/backslashes (Windows CMD escapes closing quote with trailing \)
        folder = Path(args.test.rstrip('/\\'))
        if not folder.exists():
            print(f"Error: Folder not found: {folder}")
            return
        
        positions = [10, 60, 120]
        if args.positions:
            positions = [int(p) for p in args.positions.split(',')]
        
        test_recognition(folder, db_path, clip_duration=args.duration, positions=positions)
    
    elif args.live:
        test_live_capture(db_path, duration=args.duration)
    
    elif args.stats:
        show_stats(db_path)
    
    elif args.delete:
        song_id = args.delete
        print(f"\n=== Deleting Song ===")
        print(f"Song ID: {song_id}")
        result = run_sfp_command(db_path, "delete", song_id)
        if result.get('success'):
            print(f"✅ Deleted: {result.get('deleted')}")
            # Also remove from indexed_files.json
            indexed_files_path = db_path / "indexed_files.json"
            indexed_files = load_json_file(indexed_files_path)
            # Find and remove by song_id
            to_remove = [k for k, v in indexed_files.items() if v.get('songId') == song_id]
            for k in to_remove:
                del indexed_files[k]
                print(f"✅ Removed from indexed_files.json: {Path(k).name}")
            if to_remove:
                save_json_file(indexed_files_path, indexed_files)
        else:
            print(f"❌ Failed: {result.get('error', 'Unknown error')}")
    
    elif args.clear:
        clear_database(db_path, force=args.force)
    
    elif args.verify:
        verify_database(db_path)
    
    elif args.repair:
        repair_database(db_path, batch=args.batch, auto=args.auto)
    
    elif args.reindex:
        folder = Path(args.reindex.rstrip('/\\'))
        if not folder.exists():
            print(f"Error: Folder not found: {folder}")
            return
        result = reindex_folder(folder, db_path, dry_run=args.dry_run)
        
        # Handle export if dry-run
        if args.export and result.get('dry_run'):
            export_path = Path(args.export)
            save_json_file(export_path, result)
            print(f"\n✓ Dry-run results exported to: {export_path}")
    
    else:
        # Default to interactive CLI mode when no arguments provided
        cli_mode(db_path)


if __name__ == "__main__":
    main()
