"""
Audio Buffer Module

Rolling buffer that accumulates AudioChunk objects across multiple capture cycles
to provide longer audio samples for improved fingerprint recognition accuracy.

Features:
- Configurable buffer size (number of capture cycles)
- Automatic clearing on song change, silence, or low confidence
- Position tracking for multi-match verification
"""

import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from logging_config import get_logger

logger = get_logger(__name__)


# ============================================================================
# Tuning Constants (easily adjustable)
# ============================================================================

# Clear buffer if recognition confidence drops to this level
# 0.26 is the minimum observed in testing - indicates confused/mixed audio
BUFFER_CLEAR_MIN_CONFIDENCE = 0.26

# Tolerance in seconds for multi-match position verification
# If a match is within this range of expected position, prefer it
MULTI_MATCH_POSITION_TOLERANCE = 20.0

# If no match is within tolerance, fall back to highest confidence match
MULTI_MATCH_FALLBACK_TO_CONFIDENCE = True


@dataclass
class PositionTracker:
    """Tracks expected song position for multi-match verification."""
    
    last_position: Optional[float] = None
    last_time: Optional[float] = None
    song_id: Optional[str] = None
    
    def update(self, position: float, song_id: str) -> None:
        """Update tracker with new recognition result."""
        self.last_position = position
        self.last_time = time.time()
        self.song_id = song_id
    
    def get_expected_position(self) -> Optional[float]:
        """
        Calculate expected current position based on last known state.
        
        Returns:
            Expected position in seconds, or None if no tracking data.
        """
        if self.last_position is None or self.last_time is None:
            return None
        
        elapsed = time.time() - self.last_time
        return self.last_position + elapsed
    
    def clear(self) -> None:
        """Reset tracker (on song change)."""
        self.last_position = None
        self.last_time = None
        self.song_id = None
    
    def is_same_song(self, song_id: str) -> bool:
        """Check if we're still tracking the same song."""
        return self.song_id == song_id


class AudioBuffer:
    """
    Rolling buffer of AudioChunk objects for extended recognition.
    
    Accumulates audio across multiple capture cycles to provide longer
    samples for SFP, improving fingerprint density and confidence.
    
    Usage:
        buffer = AudioBuffer(max_cycles=3)  # 3 x 6s = 18s max
        
        # Each capture cycle:
        buffer.add(audio_chunk)
        combined = buffer.get_combined()  # Returns merged audio
        
        # On song change or silence:
        buffer.clear()
    """
    
    def __init__(self, max_cycles: int = 3):
        """
        Initialize audio buffer.
        
        Args:
            max_cycles: Maximum number of capture cycles to retain.
                       Buffer size = max_cycles × capture_duration
        """
        self._chunks: List = []  # List of AudioChunk objects
        self._max_cycles = max_cycles
        self._silence_count = 0
        self._last_confidence: Optional[float] = None
        
        # Position tracking for multi-match verification
        self.position_tracker = PositionTracker()
        
        logger.debug(f"AudioBuffer initialized: max_cycles={max_cycles}")
    
    def add(self, chunk) -> None:
        """
        Add a new audio chunk to the buffer.
        
        Args:
            chunk: AudioChunk to append
        
        The oldest chunk is removed if buffer exceeds max_cycles.
        """
        self._chunks.append(chunk)
        
        # Trim to max size (FIFO)
        while len(self._chunks) > self._max_cycles:
            self._chunks.pop(0)
        
        # Reset silence counter when we get audio
        self._silence_count = 0
        
        total_duration = sum(c.duration for c in self._chunks)
        logger.debug(
            f"AudioBuffer: Added chunk | "
            f"Cycles: {len(self._chunks)}/{self._max_cycles} | "
            f"Total: {total_duration:.1f}s"
        )
    
    def get_combined(self):
        """
        Combine all chunks into a single AudioChunk.
        
        Returns:
            Merged AudioChunk with concatenated audio data,
            or None if buffer is empty.
            
        The combined chunk uses:
        - Earliest capture_start_time (from first chunk)
        - Sum of all durations
        - Same sample_rate/channels (assumed consistent)
        """
        if not self._chunks:
            return None
        
        if len(self._chunks) == 1:
            return self._chunks[0]
        
        # Import here to avoid circular import
        from .capture import AudioChunk
        
        # Concatenate all audio data
        combined_data = np.concatenate([c.data for c in self._chunks])
        
        return AudioChunk(
            data=combined_data,
            sample_rate=self._chunks[0].sample_rate,
            channels=self._chunks[0].channels,
            duration=sum(c.duration for c in self._chunks),
            capture_start_time=self._chunks[0].capture_start_time
        )
    
    def clear(self, reason: str = "") -> None:
        """
        Clear the buffer.
        
        Args:
            reason: Optional reason for logging
        """
        if self._chunks:
            logger.debug(f"AudioBuffer cleared: {reason}" if reason else "AudioBuffer cleared")
        self._chunks = []
        self._silence_count = 0
        self._last_confidence = None
    
    def record_silence(self, silence_threshold: int = 1) -> bool:
        """
        Record a silence event and check if buffer should be cleared.
        
        Args:
            silence_threshold: Number of consecutive silences before clearing
            
        Returns:
            True if buffer was cleared, False otherwise
        """
        self._silence_count += 1
        
        if self._silence_count >= silence_threshold:
            self.clear(f"silence for {self._silence_count} cycle(s)")
            return True
        
        logger.debug(f"AudioBuffer: Silence detected ({self._silence_count}/{silence_threshold})")
        return False
    
    def check_confidence(self, confidence: float) -> bool:
        """
        Check if confidence drop indicates buffer corruption.
        
        Args:
            confidence: New recognition confidence
            
        Returns:
            True if buffer should be cleared, False otherwise
        """
        should_clear = confidence <= BUFFER_CLEAR_MIN_CONFIDENCE and len(self._chunks) > 1
        
        if should_clear:
            self.clear(f"confidence dropped to {confidence:.2f}")
            return True
        
        self._last_confidence = confidence
        return False
    
    def on_song_change(self, new_song_id: str) -> None:
        """
        Handle song change - clears buffer and resets position tracker.
        
        Args:
            new_song_id: ID of the new song
        """
        self.clear("song changed")
        self.position_tracker.clear()
    
    @property
    def cycle_count(self) -> int:
        """Number of chunks currently in buffer."""
        return len(self._chunks)
    
    @property
    def total_duration(self) -> float:
        """Total duration of buffered audio in seconds."""
        return sum(c.duration for c in self._chunks)
    
    @property
    def is_empty(self) -> bool:
        """Check if buffer has no chunks."""
        return len(self._chunks) == 0


def select_best_match(
    matches: List[dict],
    expected_position: Optional[float],
    capture_start_time: float,
    recognition_time: float,
    tolerance: float = MULTI_MATCH_POSITION_TOLERANCE
) -> Tuple[dict, str]:
    """
    Select the best match from multiple SFP results using position verification.
    
    Args:
        matches: List of match dicts from SFP (each has trackMatchStartsAt, queryMatchStartsAt, confidence)
        expected_position: Expected song position based on tracking (or None if unknown)
        capture_start_time: When the audio capture started (for calculating current position)
        recognition_time: When recognition completed (for calculating current position)
        tolerance: Maximum acceptable deviation from expected position
        
    Returns:
        Tuple of (best_match_dict, selection_reason)
        
    Note:
        We calculate CURRENT POSITION for each match, not just use raw trackMatchStartsAt.
        Current position = trackMatchStartsAt + (recognition_time - adjusted_capture_start)
        where adjusted_capture_start = capture_start_time + queryMatchStartsAt
    """
    if not matches:
        return {}, "no matches"
    
    if len(matches) == 1:
        return matches[0], "single match"
    
    # Sort by confidence (descending) as fallback
    sorted_by_confidence = sorted(matches, key=lambda m: m.get("confidence", 0), reverse=True)
    
    # If no expected position, use highest confidence
    if expected_position is None:
        return sorted_by_confidence[0], "highest confidence (no position tracking)"
    
    # Find matches within tolerance of expected position
    # CRITICAL: Compare CURRENT POSITION (not raw offset) to expected position
    valid_matches = []
    for match in matches:
        track_offset = match.get("trackMatchStartsAt", 0)
        query_offset = match.get("queryMatchStartsAt", 0)
        
        # Calculate what current_position would be for THIS match
        adjusted_capture_start = capture_start_time + query_offset
        match_current_pos = track_offset + (recognition_time - adjusted_capture_start)
        
        deviation = abs(match_current_pos - expected_position)
        
        if deviation <= tolerance:
            valid_matches.append((match, deviation, match_current_pos))
    
    if valid_matches:
        # Sort by deviation (prefer closest to expected)
        valid_matches.sort(key=lambda x: x[1])
        best_match, deviation, match_pos = valid_matches[0]
        return best_match, f"position verified (deviation: {deviation:.1f}s)"
    
    # No matches within tolerance - calculate current positions for all matches for logging
    if MULTI_MATCH_FALLBACK_TO_CONFIDENCE:
        best = sorted_by_confidence[0]
        best_track_offset = best.get("trackMatchStartsAt", 0)
        best_query_offset = best.get("queryMatchStartsAt", 0)
        best_adjusted_start = capture_start_time + best_query_offset
        best_current_pos = best_track_offset + (recognition_time - best_adjusted_start)
        
        logger.warning(
            f"Multi-match: No position match within {tolerance}s | "
            f"Expected: {expected_position:.1f}s | "
            f"Using confidence fallback: {best_current_pos:.1f}s (offset: {best_track_offset:.1f}s)"
        )
        return best, "confidence fallback (no position match)"
    
    # Return closest by position even if outside tolerance
    all_with_current_pos = []
    for m in matches:
        track_offset = m.get("trackMatchStartsAt", 0)
        query_offset = m.get("queryMatchStartsAt", 0)
        adjusted_start = capture_start_time + query_offset
        current_pos = track_offset + (recognition_time - adjusted_start)
        deviation = abs(current_pos - expected_position)
        all_with_current_pos.append((m, deviation, current_pos))
    
    all_with_current_pos.sort(key=lambda x: x[1])
    best_match, deviation, match_pos = all_with_current_pos[0]
    return best_match, f"closest position (deviation: {deviation:.1f}s, outside tolerance)"
