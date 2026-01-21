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
# Uses 5512 Hz mono (fingerprinting doesn't need full quality)
FFMPEG_ARGS = ["-ac", "1", "-ar", "5512"]

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
    
    Connects to sfp-cli daemon via stdin/stdout. The daemon loads FFmpeg
    and database once, then processes fingerprint commands without reloading.
    
    Features:
    - Retry logic (max 3 attempts)
    - Auto-restart on crash
    - Save every N files
    
    Usage:
        daemon = IndexingDaemon(db_path)
        daemon.start()
        for file in audio_files:
            result = daemon.fingerprint(file, metadata)
            if i % 5 == 0:  # Save every 5 files
                daemon.save()
        daemon.stop()
    """
    
    MAX_RESTART_ATTEMPTS = 3
    STARTUP_TIMEOUT = 120  # seconds
    COMMAND_TIMEOUT = 60   # seconds per file
    
    def __init__(self, db_path: Path):
        self.db_path = db_path.absolute()
        self.exe_path = get_sfp_exe()
        self.process: Optional[subprocess.Popen] = None
        self._ready = False
        self._song_count = 0
        self._restart_count = 0
    
    @property
    def is_running(self) -> bool:
        """Check if daemon process is still running."""
        return self.process is not None and self.process.poll() is None
    
    def start(self) -> bool:
        """Start the daemon process with retry logic. Returns True if successful."""
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
        """Single attempt to start daemon. Returns True if successful."""
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
        """Kill daemon process if running."""
        if self.process is not None:
            try:
                self.process.kill()
            except:
                pass
            self.process = None
            self._ready = False
    
    def fingerprint(self, audio_path: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send fingerprint command to daemon.
        
        Args:
            audio_path: Path to audio file (FLAC/MP3/WAV)
            metadata: Dictionary with songId, title, artist, etc.
        
        Returns:
            Result dict with success/error info
        """
        if not self._ready or self.process is None:
            return {"success": False, "error": "Daemon not ready"}
        
        cmd = {
            "cmd": "fingerprint",
            "path": str(audio_path.absolute()),
            "metadata": metadata
        }
        
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
    
    def fingerprint_batch(self, files: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Send batch fingerprint command for parallel processing (8 concurrent in C#).
        
        Args:
            files: List of dicts with 'path' (Path) and 'metadata' (dict) keys
        
        Returns:
            Result dict with processed count and individual results
        """
        if not self._ready or self.process is None:
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
        
        try:
            self.process.stdin.write(json.dumps(cmd) + "\n")
            self.process.stdin.flush()
            
            response = self.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
                self._song_count = result.get("successCount", 0) + self._song_count
                return result
            else:
                return {"success": False, "error": "No response from daemon"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def save(self) -> Dict[str, Any]:
        """Tell daemon to save database to disk."""
        if not self._ready or self.process is None:
            return {"status": "error", "error": "Daemon not ready"}
        
        try:
            self.process.stdin.write('{"cmd": "save"}\n')
            self.process.stdin.flush()
            
            response = self.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
                self._song_count = result.get("songCount", self._song_count)
                return result
            return {"status": "error", "error": "No response"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def stop(self):
        """Shutdown daemon gracefully (saves database automatically)."""
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


def extract_full_metadata(file_path: Path) -> Dict[str, Any]:
    """
    Extract all available metadata from audio file using mutagen.
    
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
            # Fallback: parse filename
            parsed = parse_filename(file_path)
            metadata['title'] = parsed.get('title')
            metadata['artist'] = parsed.get('artist')
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


def index_folder(folder_path: Path, db_path: Path, extensions: List[str] = None, 
                 required_tags: List[str] = None) -> Dict[str, Any]:
    """
    Index all audio files in a folder.
    
    Args:
        folder_path: Path to folder containing audio files
        db_path: Path to database directory
        extensions: List of extensions to include
        required_tags: Optional list of additional required metadata fields
                       (e.g., ['album', 'genre']). Artist and title are always required.
    
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
    
    # Find all audio files
    audio_files = []
    for ext in extensions:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    print(f"\n=== Indexing {len(audio_files)} files from {folder_path} ===\n")
    print(f"Database: {db_path}")
    print(f"Already indexed: {len(indexed_files)} files")
    print(f"Using batch mode (8 files parallel) for maximum speed...\n")
    
    results = {
        'total': len(audio_files),
        'indexed': 0,
        'skipped': 0,
        'failed': 0,
        'songs': [],
        'errors': []
    }
    
    # Start daemon for fast fingerprinting
    daemon = IndexingDaemon(db_path)
    if not daemon.start():
        print("❌ Failed to start indexing daemon")
        return {'error': 'Daemon startup failed', **results}
    
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
        
        # Extract metadata
        metadata = extract_full_metadata(audio_file)
        
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
        
        content_hash = compute_content_hash(audio_file)
        metadata['contentHash'] = content_hash
        metadata['originalFilepath'] = file_key
        
        prepared_files.append({
            'path': audio_file,
            'metadata': metadata,
            'file_key': file_key,
            'content_hash': content_hash
        })
    
    print(f"\nPhase 1 complete: {len(prepared_files)} files to index (skipped {results['skipped']})")
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
    
    # Shutdown daemon (auto-saves database)
    daemon.stop()
    
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
    
    return results


def test_recognition(folder_path: Path, db_path: Path, clip_duration: int = 10, positions: List[int] = None) -> Dict[str, Any]:
    """
    Test recognition accuracy on indexed songs.
    """
    if positions is None:
        positions = [10, 60, 120]
    
    # Get list of indexed songs
    stats = run_sfp_command(db_path, "list")
    if not stats.get('songs'):
        print("No songs indexed. Run --index first.")
        return {'error': 'No songs indexed'}
    
    indexed_songs = {s['songId']: s for s in stats['songs']}
    print(f"\n=== Testing {len(indexed_songs)} indexed songs ===\n")
    
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
            
            # Query
            start_time = time.time()
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
            
            # Convert to 5512Hz mono
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


def show_stats(db_path: Path):
    """Show database statistics."""
    print(f"\n=== Database Statistics ===")
    print(f"DB Path: {db_path}\n")
    
    stats = run_sfp_command(db_path, "stats")
    print(f"Songs indexed: {stats.get('songCount', 0)}")
    print(f"Total fingerprints: {stats.get('totalFingerprints', 0)}")
    print(f"Metadata exists: {stats.get('metadataExists', False)}")
    print(f"Fingerprint DB exists: {stats.get('fingerprintDbExists', False)}")
    
    # Show indexed files count
    indexed_files_path = db_path / "indexed_files.json"
    indexed_files = load_json_file(indexed_files_path)
    print(f"Tracked files: {len(indexed_files)}")
    
    # Show skip log count (dict keyed by filepath)
    skip_log_path = db_path / "skip_log.json"
    skip_log = load_json_file(skip_log_path)
    print(f"Skipped files: {len(skip_log)}")
    
    print("\n=== Indexed Songs ===\n")
    songs = run_sfp_command(db_path, "list")
    for song in songs.get('songs', []):
        duration = song.get('duration')
        dur_str = f" [{duration:.0f}s]" if duration else ""
        print(f"  • {song.get('artist')} - {song.get('title')}{dur_str} ({song.get('fingerprints', 0)} fp)")
    
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
        print(f"  Daemon: Running | Songs: {daemon.song_count}")
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
        ("repair", "Interactive repair wizard"),
        ("repair --batch", "Batch repair (confirm once for all)"),
        ("repair --auto", "Auto repair (no confirmation)"),
        ("index <folder>", "Index new files in folder"),
        ("reindex <folder>", "Force re-index all files in folder"),
        ("search <query>", "Search songs by artist/title"),
        ("info <songId>", "Show details for a song"),
        ("purge <songId>", "Delete song from all data sources"),
        ("list [page]", "List all songs (paginated)"),
        ("stats", "Show database statistics"),
        ("exit / quit", "Exit the CLI"),
    ]
    
    print("\nAvailable Commands:")
    print("-" * 50)
    for cmd, desc in commands:
        print(f"  {cmd:20} {desc}")
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
    
    # Check fingerprint DB (via daemon)
    if daemon and daemon.is_running:
        try:
            daemon.process.stdin.write('{\"cmd\": \"list-fp\"}\n')
            daemon.process.stdin.flush()
            response = daemon.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
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
    
    # Check fingerprint DB
    in_fp = False
    if daemon and daemon.is_running:
        try:
            daemon.process.stdin.write('{\"cmd\": \"list-fp\"}\n')
            daemon.process.stdin.flush()
            response = daemon.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
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
    
    # Delete from fingerprint DB
    if in_fp and daemon and daemon.is_running:
        try:
            cmd = json.dumps({"cmd": "delete", "songId": song_id})
            daemon.process.stdin.write(cmd + "\n")
            daemon.process.stdin.flush()
            response = daemon.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
                if result.get('success'):
                    deleted_from.append('fingerprint_db')
                    print(f"  ✓ Deleted from Fingerprint DB")
                else:
                    print(f"  ⚠ Could not delete from Fingerprint DB: {result.get('error')}")
        except Exception as e:
            print(f"  ⚠ Error deleting from Fingerprint DB: {e}")
    
    # Delete from metadata
    if in_metadata:
        del metadata[song_id]
        deleted_from.append('metadata')
        print(f"  ✓ Deleted from metadata.json")
    
    # Delete from indexed_files
    if in_index:
        for fp, _ in index_entries:
            del indexed_files[fp]
        deleted_from.append('indexed_files')
        print(f"  ✓ Deleted {len(index_entries)} entries from indexed_files.json")
    
    # Save changes
    if 'metadata' in deleted_from:
        save_json_file(metadata_path, metadata)
    if 'indexed_files' in deleted_from:
        save_json_file(indexed_files_path, indexed_files)
    
    if daemon and daemon.is_running:
        daemon.save()
    
    print(f"\n✓ Purge complete. Deleted from: {', '.join(deleted_from)}")
    return {'success': True, 'deleted_from': deleted_from}


def list_songs(db_path: Path, page: int = 1, page_size: int = 20):
    """List all songs with pagination."""
    metadata_path = db_path / "metadata.json"
    metadata = load_json_file(metadata_path)
    
    songs = list(metadata.items())
    total = len(songs)
    total_pages = (total + page_size - 1) // page_size
    
    start = (page - 1) * page_size
    end = start + page_size
    page_songs = songs[start:end]
    
    print(f"\n{'=' * 70}")
    print(f"Songs (Page {page}/{total_pages}, {total} total)")
    print(f"{'=' * 70}\n")
    
    for song_id, meta in page_songs:
        duration = meta.get('duration', 0)
        dur_str = f"[{duration:.0f}s]" if duration else ""
        fp_count = meta.get('fingerprintCount', '?')
        print(f"  {meta.get('artist', '?')} - {meta.get('title', '?')} {dur_str}")
        print(f"    ID: {song_id} | FPs: {fp_count}")
        print()
    
    if total_pages > 1:
        print(f"Use 'list {page + 1}' for next page")


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
                verify_database(db_path, daemon=daemon, brief=False)
            
            elif cmd == 'repair':
                batch = '--batch' in args
                auto = '--auto' in args
                repair_database(db_path, batch=batch, auto=auto, daemon=daemon)
            
            elif cmd == 'index':
                if not args:
                    print("Usage: index <folder>")
                    continue
                folder = Path(args.rstrip('/\\'))
                if not folder.exists():
                    print(f"Error: Folder not found: {folder}")
                    continue
                # Use the existing index_folder function but pass daemon
                index_folder(folder, db_path)
            
            elif cmd == 'reindex':
                if not args:
                    print("Usage: reindex <folder>")
                    continue
                folder = Path(args.rstrip('/\\'))
                if not folder.exists():
                    print(f"Error: Folder not found: {folder}")
                    continue
                reindex_folder(folder, db_path)
            
            elif cmd == 'search':
                if not args:
                    print("Usage: search <query>")
                    continue
                results = search_songs(args, db_path, daemon)
                if results:
                    print(f"\nFound {len(results)} matches:\n")
                    for r in results[:20]:
                        dur = f"[{r['duration']:.0f}s]" if r.get('duration') else ""
                        print(f"  {r['artist']} - {r['title']} {dur}")
                        print(f"    ID: {r['songId']}")
                    if len(results) > 20:
                        print(f"\n  ... and {len(results) - 20} more")
                else:
                    print(f"No matches found for '{args}'")
                print()
            
            elif cmd == 'info':
                if not args:
                    print("Usage: info <songId>")
                    continue
                show_song_info(args, db_path, daemon)
            
            elif cmd == 'purge':
                if not args:
                    print("Usage: purge <songId>")
                    continue
                purge_song(args, db_path, daemon)
            
            elif cmd == 'list':
                page = 1
                if args:
                    try:
                        page = int(args)
                    except ValueError:
                        print("Usage: list [page_number]")
                        continue
                list_songs(db_path, page)
            
            elif cmd == 'stats':
                show_stats(db_path)
            
            else:
                print(f"Unknown command: {cmd}. Type 'help' for available commands.")
    
    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    
    finally:
        if daemon:
            daemon.stop()
        print("Goodbye!")


def verify_database(db_path: Path) -> Dict[str, Any]:
    """
    Verify database integrity by comparing all 3 data sources:
    - Fingerprint DB (via daemon list-fp command)
    - metadata.json
    - indexed_files.json
    
    Reports ALL discrepancies with detailed logging.
    """
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
    print("Querying fingerprint database...")
    daemon = IndexingDaemon(db_path)
    fp_ids = set()
    
    if daemon.start():
        try:
            # Send list-fp command and get response
            daemon.process.stdin.write('{"cmd": "list-fp"}\n')
            daemon.process.stdin.flush()
            response = daemon.process.stdout.readline()
            if response:
                result = json.loads(response.strip())
                fp_ids = set(result.get('songIds', []))
        except Exception as e:
            print(f"⚠️  Warning: Could not query fingerprint DB: {e}")
        finally:
            daemon.stop()
    else:
        print("⚠️  Warning: Could not start daemon to query fingerprints")
        # Fall back to assuming metadata IDs are in fingerprints (best effort)
        print("   Using metadata songIds as proxy for fingerprint check")
        fp_ids = metadata_ids.copy()
    
    # Print counts
    print(f"\n📊 Data Source Counts:")
    print(f"   Fingerprint DB: {len(fp_ids)} songs")
    print(f"   Metadata:       {len(metadata_ids)} entries")
    print(f"   Index:          {len(index_ids)} unique songIds ({len(indexed_files)} files)")
    
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
        print(f"\n✅ All databases are in sync! No discrepancies found.")
    else:
        print(f"\n⚠️  DISCREPANCIES FOUND: {total_issues} total\n")
        
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
    
    print(f"{'=' * 70}")
    
    return {
        'fp_count': len(fp_ids),
        'metadata_count': len(metadata_ids),
        'index_count': len(index_ids),
        'discrepancies': discrepancies,
        'total_issues': total_issues
    }


def repair_database(db_path: Path, batch: bool = False, auto: bool = False) -> Dict[str, Any]:
    """
    Repair database discrepancies.
    
    Args:
        db_path: Database directory path
        batch: If True, ask once for all fixes
        auto: If True, no confirmation needed
    """
    print(f"\n{'=' * 70}")
    print("DATABASE REPAIR")
    print(f"{'=' * 70}")
    
    # First run verify to get discrepancies
    print("Running verification first...\n")
    verify_result = verify_database(db_path)
    
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
    
    # Start daemon for fingerprinting operations
    daemon = None
    needs_daemon = any(r['action'] in ['re-fingerprint file'] for r in repairable)
    if needs_daemon:
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
                        result = daemon.fingerprint(Path(filepath), meta)
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
        if daemon:
            daemon.save()
            daemon.stop()
    
    # Save updated files
    if repaired > 0:
        save_json_file(metadata_path, metadata)
        save_json_file(indexed_files_path, indexed_files)
        print(f"\n✅ Saved updated metadata.json and indexed_files.json")
    
    print(f"\n{'=' * 70}")
    print(f"REPAIR COMPLETE: {repaired} fixed, {skipped} skipped")
    print(f"{'=' * 70}")
    
    return {'repaired': repaired, 'skipped': skipped}


def reindex_folder(folder_path: Path, db_path: Path) -> Dict[str, Any]:
    """
    Force re-index all files in folder, overwriting existing entries.
    
    Unlike normal indexing, this:
    - Ignores indexed_files.json check (processes all files)
    - Overwrites existing fingerprints and metadata
    """
    print(f"\n{'=' * 70}")
    print("FORCE RE-INDEX")
    print(f"{'=' * 70}")
    print(f"Folder: {folder_path}")
    print(f"Database: {db_path}")
    print(f"\n⚠️  This will overwrite existing fingerprints and metadata for files in this folder.")
    
    response = input("Continue? (y/N): ").strip().lower()
    if response != 'y':
        print("Re-index cancelled.")
        return {'cancelled': True}
    
    # Find all audio files
    audio_files = []
    for ext in SUPPORTED_EXTENSIONS:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    print(f"\nFound {len(audio_files)} audio files to re-index.\n")
    
    # Load tracking files
    indexed_files_path = db_path / "indexed_files.json"
    indexed_files = load_json_file(indexed_files_path)
    
    # Start daemon
    daemon = IndexingDaemon(db_path)
    if not daemon.start():
        print("❌ Failed to start indexing daemon")
        return {'error': 'Daemon startup failed'}
    
    results = {
        'total': len(audio_files),
        'reindexed': 0,
        'failed': 0,
        'files': []
    }
    
    try:
        for i, audio_file in enumerate(audio_files, 1):
            file_key = str(audio_file.absolute())
            print(f"[{i}/{len(audio_files)}] {audio_file.name}...")
            
            # Extract metadata
            metadata = extract_full_metadata(audio_file)
            if not metadata['title'] or not metadata['artist']:
                print(f"   ⏭️  Skipped (missing tags)")
                continue
            
            song_id = normalize_song_id(metadata['artist'], metadata['title'])
            metadata['songId'] = song_id
            metadata['originalFilepath'] = file_key
            
            # Compute content hash
            content_hash = compute_content_hash(audio_file)
            metadata['contentHash'] = content_hash
            
            # Fingerprint (will overwrite if exists)
            result = daemon.fingerprint(Path(audio_file), metadata)
            
            if result.get('success'):
                print(f"   ✅ {metadata['artist']} - {metadata['title']} ({result.get('fingerprints', 0)} FPs)")
                results['reindexed'] += 1
                
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
        daemon.save()
        daemon.stop()
        save_json_file(indexed_files_path, indexed_files)
    
    print(f"\n{'=' * 70}")
    print(f"RE-INDEX COMPLETE: {results['reindexed']} re-indexed, {results['failed']} failed")
    print(f"{'=' * 70}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="SoundFingerprinting Test Suite v2.0")
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
    
    args = parser.parse_args()
    
    # Determine database path
    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = get_db_path()
    
    if args.index:
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
        
        index_folder(folder, db_path, required_tags=required_tags)
    
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
        reindex_folder(folder, db_path)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
