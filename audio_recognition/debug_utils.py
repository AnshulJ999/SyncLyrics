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
MAX_MATCH_HISTORY = 8


def _get_cache_dir() -> Path:
    """Get the cache directory path."""
    cache_dir = Path("cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _generate_summary(result: dict, extra_data: Optional[Dict[str, Any]] = None) -> str:
    """Generate a one-line summary for quick scanning."""
    try:
        # Extract best match info
        best = result.get("bestMatch", result)
        artist = best.get("artist", "?")
        title = best.get("title", "?")
        offset = best.get("trackMatchStartsAt", 0)
        confidence = best.get("confidence", 0)
        match_count = result.get("matchCount", 1)
        
        # Get selection reason if available
        selection = ""
        if extra_data and extra_data.get("selection_reason"):
            sel = extra_data["selection_reason"]
            # Shorten common reasons
            if "position verified" in sel:
                selection = "pos-verified"
            elif "confidence fallback" in sel:
                selection = "conf-fallback"
            elif "highest confidence" in sel:
                selection = "highest-conf"
            else:
                selection = sel[:20]
        
        return f"{artist} - {title} @ {offset:.1f}s | Conf: {confidence:.2f} | {match_count} candidates | {selection}"
    except Exception:
        return "Error generating summary"


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
        
        # Create new entry with summary at top for readability
        entry = {
            "_summary": _generate_summary(result, extra_data),
            "timestamp": datetime.now().isoformat(),
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


def save_single_match(provider: str, result: dict, extra_data: Optional[Dict[str, Any]] = None) -> None:
    """
    Save the latest match to a single-match file (legacy format).
    
    This writes to last_{provider}_match.json for backward compatibility
    and simpler debugging when you just want to see the latest match.
    
    Args:
        provider: Provider name (e.g., 'local', 'shazam', 'acrcloud')
        result: The match result dict from the provider
        extra_data: Optional extra data to include (e.g., selection_reason)
    """
    try:
        cache_dir = _get_cache_dir()
        match_path = cache_dir / f"last_{provider}_match.json"
        
        debug_data = {
            "timestamp": datetime.now().isoformat(),
            "result": result,
        }
        if extra_data:
            debug_data.update(extra_data)
        
        with open(match_path, 'w', encoding='utf-8') as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
            
    except Exception as e:
        logger.debug(f"Failed to save {provider} single match: {e}")


def _parse_wav_header(wav_bytes: bytes) -> tuple:
    """
    Parse WAV header to get audio format info.
    
    Returns:
        Tuple of (sample_rate, channels, bits_per_sample) or (44100, 2, 16) as fallback
    """
    try:
        if len(wav_bytes) < 44:
            return (44100, 2, 16)
        
        import struct
        # WAV header format:
        # Bytes 22-23: Number of channels (2 bytes, little-endian)
        # Bytes 24-27: Sample rate (4 bytes, little-endian)
        # Bytes 34-35: Bits per sample (2 bytes, little-endian)
        channels = struct.unpack('<H', wav_bytes[22:24])[0]
        sample_rate = struct.unpack('<I', wav_bytes[24:28])[0]
        bits_per_sample = struct.unpack('<H', wav_bytes[34:36])[0]
        
        return (sample_rate, channels, bits_per_sample)
    except Exception:
        return (44100, 2, 16)  # Fallback to stereo 44.1kHz 16-bit


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
        
        # Log the audio duration by parsing WAV header
        if len(wav_bytes) > 44:
            sample_rate, channels, bits_per_sample = _parse_wav_header(wav_bytes)
            bytes_per_sample = bits_per_sample // 8
            bytes_per_second = sample_rate * channels * bytes_per_sample
            data_size = len(wav_bytes) - 44
            duration_s = data_size / bytes_per_second
            logger.debug(f"Saved debug audio: {filename} ({duration_s:.1f}s, {channels}ch)")
    except Exception as e:
        logger.debug(f"Failed to save debug audio: {e}")
