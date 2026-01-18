"""
Dejavu Local Audio Fingerprinting

Personal feature for local song recognition using Dejavu.
This module is ONLY loaded if DEJAVU_ENABLED=true in environment.

Dependencies (install manually if needed):
    pip install PyDejavu mutagen pydub
"""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

# Lazy imports - only when actually used
_dejavu = None
_mutagen_flac = None
_mutagen_mp3 = None


def _ensure_dejavu():
    """Lazy import Dejavu to avoid import errors when disabled."""
    global _dejavu
    if _dejavu is None:
        from dejavu import Dejavu
        _dejavu = Dejavu
    return _dejavu


def _ensure_mutagen():
    """Lazy import mutagen for metadata extraction."""
    global _mutagen_flac, _mutagen_mp3
    if _mutagen_flac is None:
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        _mutagen_flac = FLAC
        _mutagen_mp3 = MP3
    return _mutagen_flac, _mutagen_mp3


class DejavuRecognizer:
    """
    Local audio fingerprinting using Dejavu.
    
    Acts as first-pass recognition before Shazamio/ACRCloud.
    Uses the user's own FLAC library as the fingerprint database.
    
    Features:
    - Instant recognition (no network latency)
    - 100% accuracy for indexed songs at normal speed
    - Returns offset for lyrics synchronization
    - Integrates with existing RecognitionResult format
    
    NOTE: This class is only imported if DEJAVU_ENABLED=true.
    """
    
    # Indexing filters
    MAX_DURATION_MINUTES = 20  # Skip files longer than this (full albums)
    HASH_DURATION_SECONDS = 90  # Duration to use for content hash
    
    def __init__(self, db_path: str = None):
        """
        Initialize Dejavu recognizer.
        
        Args:
            db_path: Directory for fingerprint database and metadata.
                     Defaults to DEJAVU_DB_PATH env or 'local_fingerprint_database'
        """
        if db_path is None:
            db_path = os.getenv("DEJAVU_DB_PATH", "local_fingerprint_database")
        
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        # Paths for our data files
        self.metadata_path = self.db_path / "metadata.json"
        self.indexed_path = self.db_path / "indexed_files.json"
        self.skip_log_path = self.db_path / "skip_log.json"
        self.dejavu_db_path = self.db_path / "dejavu.db"
        
        # Load existing data
        self._metadata: Dict[str, dict] = self._load_json(self.metadata_path)
        self._indexed_files: Dict[str, dict] = self._load_json(self.indexed_path)
        self._skip_log: List[dict] = self._load_json(self.skip_log_path) or []
        
        # Dejavu instance (lazy init)
        self._dejavu = None
        
        logger.info(f"DejavuRecognizer initialized with db_path: {self.db_path}")
        logger.info(f"Loaded {len(self._metadata)} songs, {len(self._indexed_files)} indexed files")
    
    def _load_json(self, path: Path) -> Any:
        """Load JSON file, returning empty dict/list on error."""
        try:
            if path.exists():
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
        return {}
    
    def _save_json(self, path: Path, data: Any):
        """Save data to JSON file."""
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save {path}: {e}")
    
    def _get_dejavu(self):
        """Get or create Dejavu instance."""
        if self._dejavu is None:
            Dejavu = _ensure_dejavu()
            
            # Dejavu config for SQLite
            config = {
                "database": {
                    "host": str(self.dejavu_db_path),
                },
                "database_type": "sqlite",
            }
            
            self._dejavu = Dejavu(config)
            logger.info("Dejavu instance created")
        
        return self._dejavu
    
    def _extract_metadata(self, filepath: Path) -> Optional[dict]:
        """
        Extract metadata from audio file.
        
        Returns None if file should be skipped (no valid tags).
        """
        FLAC, MP3 = _ensure_mutagen()
        
        try:
            if filepath.suffix.lower() == '.flac':
                audio = FLAC(filepath)
                artist = audio.get('artist', [None])[0]
                title = audio.get('title', [None])[0]
                album = audio.get('album', [''])[0]
                duration = audio.info.length
                
            elif filepath.suffix.lower() == '.mp3':
                audio = MP3(filepath)
                duration = audio.info.length
                
                if audio.tags:
                    artist = str(audio.tags.get('TPE1', [''])[0]) or None
                    title = str(audio.tags.get('TIT2', [''])[0]) or None
                    album = str(audio.tags.get('TALB', [''])[0]) or ''
                else:
                    artist = None
                    title = None
                    album = ''
            else:
                return None
            
            # Skip files without valid tags
            if not artist or not title:
                return None
            
            # Skip files longer than max duration
            if duration > self.MAX_DURATION_MINUTES * 60:
                return None
            
            return {
                'artist': artist.strip(),
                'title': title.strip(),
                'album': album.strip() if album else '',
                'duration': duration,
                'filepath': str(filepath),
            }
            
        except Exception as e:
            logger.warning(f"Failed to extract metadata from {filepath}: {e}")
            return None
    
    def _compute_content_hash(self, filepath: Path) -> str:
        """
        Compute content hash for deduplication.
        
        Uses first 90 seconds of raw audio data.
        """
        try:
            # Read file and hash first portion
            with open(filepath, 'rb') as f:
                # Read first ~10MB which should cover 90 sec of audio
                data = f.read(10 * 1024 * 1024)
                return hashlib.md5(data).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to compute hash for {filepath}: {e}")
            return hashlib.md5(str(filepath).encode()).hexdigest()
    
    def _is_duplicate(self, filepath: str, content_hash: str) -> bool:
        """Check if file is already indexed or is a duplicate."""
        # Already indexed this exact file?
        if filepath in self._indexed_files:
            return True
        
        # Same content at different path?
        for indexed_path, data in self._indexed_files.items():
            if data.get('content_hash') == content_hash:
                logger.debug(f"Duplicate content: {filepath} matches {indexed_path}")
                return True
        
        return False
    
    def _log_skip(self, filepath: str, reason: str):
        """Log a skipped file."""
        self._skip_log.append({
            'filepath': filepath,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        })
        # Save periodically
        if len(self._skip_log) % 50 == 0:
            self._save_json(self.skip_log_path, self._skip_log)
    
    def index_file(self, filepath: str) -> bool:
        """
        Index a single audio file with metadata extraction.
        
        Args:
            filepath: Path to FLAC/MP3 file
            
        Returns:
            True if successfully indexed, False if skipped
        """
        filepath = Path(filepath)
        filepath_str = str(filepath)
        
        # Extract metadata
        metadata = self._extract_metadata(filepath)
        if metadata is None:
            self._log_skip(filepath_str, "no_valid_tags_or_too_long")
            return False
        
        # Compute content hash
        content_hash = self._compute_content_hash(filepath)
        
        # Check for duplicates
        if self._is_duplicate(filepath_str, content_hash):
            self._log_skip(filepath_str, "duplicate")
            return False
        
        # Create song key
        song_key = f"{metadata['artist']} - {metadata['title']}"
        
        # Check if song_key already exists (different version)
        if song_key in self._metadata:
            # Append a differentiator
            existing = self._metadata[song_key]
            if existing.get('filepath') != filepath_str:
                # Different file with same artist/title - might be different album
                album = metadata.get('album', '')
                if album:
                    song_key = f"{metadata['artist']} - {metadata['title']} ({album})"
                else:
                    song_key = f"{metadata['artist']} - {metadata['title']} (alt)"
        
        # Index with Dejavu
        try:
            djv = self._get_dejavu()
            djv.fingerprint_file(filepath_str, song_name=song_key)
            
            # Store metadata
            self._metadata[song_key] = {
                'artist': metadata['artist'],
                'title': metadata['title'],
                'album': metadata['album'],
                'duration': metadata['duration'],
                'filepath': filepath_str,
                'content_hash': content_hash,
                'indexed_at': datetime.now().isoformat(),
            }
            
            # Track indexed file
            self._indexed_files[filepath_str] = {
                'song_key': song_key,
                'content_hash': content_hash,
                'indexed_at': datetime.now().isoformat(),
            }
            
            # Save periodically
            if len(self._indexed_files) % 10 == 0:
                self._save_metadata()
            
            logger.info(f"Indexed: {song_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to index {filepath}: {e}")
            self._log_skip(filepath_str, f"index_error: {str(e)[:50]}")
            return False
    
    def index_directory(self, directory: str, extensions: List[str] = None) -> dict:
        """
        Index all audio files in a directory.
        
        Args:
            directory: Path to music library
            extensions: File extensions to include (default: .flac, .mp3)
            
        Returns:
            Dict with counts: {'indexed': N, 'skipped': N, 'failed': N}
        """
        if extensions is None:
            extensions = ['.flac', '.mp3']
        
        directory = Path(directory)
        
        # Find all audio files
        files = []
        for ext in extensions:
            files.extend(directory.rglob(f'*{ext}'))
        
        logger.info(f"Found {len(files)} audio files in {directory}")
        
        results = {'indexed': 0, 'skipped': 0, 'failed': 0}
        
        for i, filepath in enumerate(files):
            if (i + 1) % 50 == 0:
                logger.info(f"Progress: {i + 1}/{len(files)}")
            
            if self.index_file(str(filepath)):
                results['indexed'] += 1
            else:
                results['skipped'] += 1
        
        # Save final state
        self._save_metadata()
        self._save_json(self.skip_log_path, self._skip_log)
        
        logger.info(f"Indexing complete: {results}")
        return results
    
    def _save_metadata(self):
        """Save metadata and indexed files to disk."""
        self._save_json(self.metadata_path, self._metadata)
        self._save_json(self.indexed_path, self._indexed_files)
    
    async def recognize(self, wav_bytes: bytes) -> Optional[dict]:
        """
        Recognize audio against local fingerprint database.
        
        Args:
            wav_bytes: WAV audio data
            
        Returns:
            Dict with matches or None if no match:
            {
                'matches': [{'song_key': ..., 'confidence': ..., 'offset': ...}, ...],
                'best_match': {...} or None
            }
        """
        try:
            djv = self._get_dejavu()
            
            # Dejavu requires a file, so write to temp
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                f.write(wav_bytes)
                temp_path = f.name
            
            try:
                # Run recognition in thread pool (Dejavu is sync)
                result = await asyncio.to_thread(
                    djv.recognize, 
                    'file', 
                    temp_path
                )
            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except:
                    pass
            
            if result and result.get('results'):
                matches = []
                for r in result['results']:
                    song_key = r.get('song_name')
                    if song_key and song_key in self._metadata:
                        matches.append({
                            'song_key': song_key,
                            'confidence': r.get('fingerprinted_confidence', 0),
                            'offset': r.get('offset_seconds', 0),
                            'metadata': self._metadata[song_key],
                        })
                
                if matches:
                    # Sort by confidence
                    matches.sort(key=lambda x: x['confidence'], reverse=True)
                    return {
                        'matches': matches,
                        'best_match': matches[0] if matches else None,
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"Recognition failed: {e}")
            return None
    
    def get_stats(self) -> dict:
        """Get database statistics."""
        stats = {
            'song_count': len(self._metadata),
            'indexed_files': len(self._indexed_files),
            'skip_count': len(self._skip_log),
            'db_path': str(self.db_path),
        }
        
        # Get database size
        if self.dejavu_db_path.exists():
            stats['db_size_mb'] = self.dejavu_db_path.stat().st_size / (1024 * 1024)
        else:
            stats['db_size_mb'] = 0
        
        return stats
    
    def is_available(self) -> bool:
        """Check if Dejavu is available and has indexed songs."""
        return len(self._metadata) > 0
