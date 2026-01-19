"""
SoundFingerprinting Test Suite v2.0

Comprehensive test script for local audio fingerprinting using sfp-cli.
Now with:
- Full metadata extraction (all tags)
- Content hash deduplication (90-sec audio hash)
- indexed_files.json tracking
- skip_log.json for skipped files
- Configurable database path

Usage:
    python scripts/test_sfp_indexing.py --index <folder>   Index all songs in folder
    python scripts/test_sfp_indexing.py --test <folder>    Test recognition accuracy
    python scripts/test_sfp_indexing.py --live             Test live audio capture
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
from datetime import datetime
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
SFP_CLI_CMD = ["dotnet", "run", "--"]

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


def run_sfp_command(db_path: Path, command: str, *args) -> Dict[str, Any]:
    """Run sfp-cli command and return JSON result."""
    # Ensure db_path is absolute (sfp-cli runs from its own directory)
    abs_db_path = db_path.absolute()
    cmd = SFP_CLI_CMD + ["--db-path", str(abs_db_path), command] + list(args)

    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(SFP_CLI_DIR),
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
        
        # Fallback to filename if no tags
        if not metadata['title'] or not metadata['artist']:
            parsed = parse_filename(file_path)
            metadata['title'] = metadata['title'] or parsed.get('title')
            metadata['artist'] = metadata['artist'] or parsed.get('artist')
        
    except Exception as e:
        logger.warning(f"Could not read metadata from {file_path}: {e}")
        # Fallback: parse filename
        parsed = parse_filename(file_path)
        metadata['title'] = parsed.get('title')
        metadata['artist'] = parsed.get('artist')
    
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


def index_folder(folder_path: Path, db_path: Path, extensions: List[str] = None) -> Dict[str, Any]:
    """
    Index all audio files in a folder.
    
    Args:
        folder_path: Path to folder containing audio files
        db_path: Path to database directory
        extensions: List of extensions to include
    
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
    skip_log = load_json_file(skip_log_path)
    if 'skipped' not in skip_log:
        skip_log['skipped'] = []
    
    # Find all audio files
    audio_files = []
    for ext in extensions:
        audio_files.extend(folder_path.rglob(f"*{ext}"))
    
    print(f"\n=== Indexing {len(audio_files)} files from {folder_path} ===\n")
    print(f"Database: {db_path}")
    print(f"Already indexed: {len(indexed_files)} files\n")
    
    results = {
        'total': len(audio_files),
        'indexed': 0,
        'skipped': 0,
        'failed': 0,
        'songs': [],
        'errors': []
    }
    
    for i, audio_file in enumerate(audio_files, 1):
        file_key = str(audio_file.absolute())
        
        # Skip if already indexed by filepath
        if file_key in indexed_files:
            print(f"[{i}/{len(audio_files)}] ⏭️  Already indexed: {audio_file.name}")
            results['skipped'] += 1
            continue
        
        print(f"[{i}/{len(audio_files)}] {audio_file.name}...")
        
        # Extract metadata
        metadata = extract_full_metadata(audio_file)
        
        # Skip if missing required tags
        if not metadata['title'] or not metadata['artist']:
            print(f"  ⏭️  Skipped: Missing artist or title tags")
            skip_log['skipped'].append({
                'filepath': file_key,
                'reason': 'Missing required tags (artist or title)',
                'skippedAt': datetime.utcnow().isoformat() + 'Z'
            })
            results['skipped'] += 1
            continue
        
        # Skip if too long
        if metadata['duration'] and metadata['duration'] > MAX_DURATION_MINUTES * 60:
            duration_min = metadata['duration'] / 60
            print(f"  ⏭️  Skipped: Duration {duration_min:.1f} min exceeds {MAX_DURATION_MINUTES} min limit")
            skip_log['skipped'].append({
                'filepath': file_key,
                'reason': f'Duration exceeds limit ({duration_min:.1f} min > {MAX_DURATION_MINUTES} min)',
                'skippedAt': datetime.utcnow().isoformat() + 'Z'
            })
            results['skipped'] += 1
            continue
        
        # Generate song ID
        song_id = normalize_song_id(metadata['artist'], metadata['title'])
        metadata['songId'] = song_id
        
        # Compute content hash for deduplication
        content_hash = compute_content_hash(audio_file)
        metadata['contentHash'] = content_hash
        
        # Convert to WAV
        wav_path = temp_dir / f"{song_id}.wav"
        
        start_time = time.time()
        
        if not convert_to_wav(audio_file, wav_path):
            print(f"  ❌ FFmpeg conversion failed")
            results['failed'] += 1
            results['errors'].append({'file': file_key, 'error': 'FFmpeg failed'})
            continue
        
        convert_time = time.time() - start_time
        
        # Write metadata to temp JSON file
        meta_path = temp_dir / f"{song_id}_meta.json"
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        
        # Fingerprint using sfp-cli
        fp_start = time.time()
        result = run_sfp_command(
            db_path,
            "fingerprint",
            str(wav_path.absolute()),
            "--metadata",
            str(meta_path.absolute())
        )
        fp_time = time.time() - fp_start
        
        # Clean up temp files
        try:
            wav_path.unlink()
            meta_path.unlink()
        except:
            pass
        
        if result.get('success'):
            print(f"  ✅ {metadata['artist']} - {metadata['title']}")
            print(f"     FP: {result.get('fingerprints', 0)}, Convert: {convert_time:.1f}s, Index: {fp_time:.1f}s")
            results['indexed'] += 1
            results['songs'].append({
                'song_id': song_id,
                'title': metadata['title'],
                'artist': metadata['artist'],
                'source': file_key,
                'fingerprints': result.get('fingerprints', 0),
                'convert_time': convert_time,
                'fp_time': fp_time
            })
            
            # Track as indexed
            indexed_files[file_key] = {
                'songId': song_id,
                'contentHash': content_hash,
                'indexedAt': datetime.utcnow().isoformat() + 'Z'
            }
            
        elif result.get('skipped'):
            reason = result.get('reason', 'Unknown')
            print(f"  ⏭️  Skipped: {reason}")
            results['skipped'] += 1
            
            # Still track as indexed if it was a duplicate
            if 'already exists' in reason.lower() or 'duplicate' in reason.lower():
                indexed_files[file_key] = {
                    'songId': song_id,
                    'contentHash': content_hash,
                    'indexedAt': datetime.utcnow().isoformat() + 'Z',
                    'skipped': reason
                }
        else:
            error = result.get('error', 'Unknown error')
            print(f"  ❌ {error}")
            results['failed'] += 1
            results['errors'].append({'file': file_key, 'error': error})
        
        # Save tracking files periodically
        if i % 10 == 0:
            save_json_file(indexed_files_path, indexed_files)
            save_json_file(skip_log_path, skip_log)
    
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
        avg_fp_time = sum(s['fp_time'] for s in results['songs']) / len(results['songs'])
        print(f"Avg fingerprint time: {avg_fp_time:.1f}s")
    
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
            
            if result.get('matched') and result.get('songId') == song_id:
                offset = result.get('trackMatchStartsAt', 0)
                confidence = result.get('confidence', 0)
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
            print(f"\n✅ MATCH FOUND!")
            print(f"   Song: {result.get('artist')} - {result.get('title')}")
            print(f"   Album: {result.get('album')}")
            print(f"   Position: {result.get('trackMatchStartsAt', 0):.1f}s")
            print(f"   Confidence: {result.get('confidence', 0):.2f}")
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
    
    # Show skip log count
    skip_log_path = db_path / "skip_log.json"
    skip_log = load_json_file(skip_log_path)
    print(f"Skipped files: {len(skip_log.get('skipped', []))}")
    
    print("\n=== Indexed Songs ===\n")
    songs = run_sfp_command(db_path, "list")
    for song in songs.get('songs', []):
        duration = song.get('duration')
        dur_str = f" [{duration:.0f}s]" if duration else ""
        print(f"  • {song.get('artist')} - {song.get('title')}{dur_str} ({song.get('fingerprints', 0)} fp)")
    
    return stats


def clear_database(db_path: Path):
    """Clear the entire database."""
    print(f"\n=== Clearing Database ===")
    print(f"DB Path: {db_path}\n")
    
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


def main():
    parser = argparse.ArgumentParser(description="SoundFingerprinting Test Suite v2.0")
    parser.add_argument("--index", type=str, help="Index all songs in folder")
    parser.add_argument("--test", type=str, help="Test recognition accuracy on folder")
    parser.add_argument("--live", action="store_true", help="Test live audio capture")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    parser.add_argument("--clear", action="store_true", help="Clear database")
    parser.add_argument("--db-path", type=str, help="Override database path")
    parser.add_argument("--duration", type=int, default=10, help="Clip duration for testing (default: 10)")
    parser.add_argument("--positions", type=str, help="Comma-separated positions to test (default: 10,60,120)")
    
    args = parser.parse_args()
    
    # Determine database path
    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = get_db_path()
    
    if args.index:
        folder = Path(args.index)
        if not folder.exists():
            print(f"Error: Folder not found: {folder}")
            return
        index_folder(folder, db_path)
    
    elif args.test:
        folder = Path(args.test)
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
    
    elif args.clear:
        clear_database(db_path)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
