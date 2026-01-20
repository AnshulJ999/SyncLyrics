"""
Debug utilities for audio recognition.

Provides shared functionality for saving debug data like match history
and audio files for debugging purposes.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger

logger = get_logger(__name__)

# Maximum number of matches to keep in history
MAX_MATCH_HISTORY = 6


def _get_cache_dir() -> Path:
    """Get the cache directory path."""
    cache_dir = Path("cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def save_match_to_history(
    provider: str,
    result: dict,
    extra_data: Optional[Dict[str, Any]] = None
) -> None:
    """
    Save a match to the provider's match history (keeps last N matches).
    
    Args:
        provider: Provider name (e.g., 'local', 'shazam', 'acrcloud')
        result: The match result dict from the provider
        extra_data: Optional extra data to include (e.g., selection_reason)
    """
    try:
        cache_dir = _get_cache_dir()
        history_path = cache_dir / f"{provider}_match_history.json"
        
        # Load existing history
        history: List[Dict] = []
        if history_path.exists():
            try:
                with open(history_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    history = data.get("matches", [])
            except (json.JSONDecodeError, KeyError):
                history = []
        
        # Create new entry
        entry = {
            "timestamp": datetime.now().isoformat(),
            "unix_time": time.time(),
            "result": result,
        }
        if extra_data:
            entry.update(extra_data)
        
        # Add to history (newest first)
        history.insert(0, entry)
        
        # Trim to max size
        history = history[:MAX_MATCH_HISTORY]
        
        # Save updated history
        with open(history_path, 'w', encoding='utf-8') as f:
            json.dump({
                "provider": provider,
                "count": len(history),
                "matches": history
            }, f, indent=2, ensure_ascii=False)
        
    except Exception as e:
        logger.debug(f"Failed to save {provider} match to history: {e}")


def save_debug_audio(wav_bytes: bytes, is_buffered: bool = False) -> None:
    """
    Save audio to cache for debugging.
    
    Args:
        wav_bytes: WAV audio data to save
        is_buffered: If True, this is buffered audio (longer duration)
    """
    try:
        cache_dir = _get_cache_dir()
        
        # Use different filename for buffered vs single audio
        filename = "last_recognition_audio_buffer.wav" if is_buffered else "last_recognition_audio.wav"
        audio_path = cache_dir / filename
        
        with open(audio_path, 'wb') as f:
            f.write(wav_bytes)
        
        # Log the audio duration for debugging
        if len(wav_bytes) > 44:  # WAV header is 44 bytes
            # Calculate duration from file size
            # WAV format: 16-bit (2 bytes) × 44100 Hz × 1 channel = 88200 bytes/second
            data_size = len(wav_bytes) - 44  # Subtract header
            duration_s = data_size / 88200
            logger.debug(f"Saved debug audio: {filename} ({duration_s:.1f}s)")
    except Exception as e:
        logger.debug(f"Failed to save debug audio: {e}")
