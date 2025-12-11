"""
Audio Recognition Module for SyncLyrics

Provides audio fingerprinting capabilities using ShazamIO for song identification.
Supports Reaper DAW integration and manual audio recognition modes.
"""

from .capture import AudioCaptureManager, AudioChunk
from .shazam import ShazamRecognizer, RecognitionResult
from .engine import RecognitionEngine, EngineState

__all__ = [
    'AudioCaptureManager',
    'AudioChunk',
    'ShazamRecognizer',
    'RecognitionResult',
    'RecognitionEngine',
    'EngineState',
]
