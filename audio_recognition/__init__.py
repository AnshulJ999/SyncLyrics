"""
Audio Recognition Module for SyncLyrics

Provides audio fingerprinting capabilities using ShazamIO for song identification.
Supports Reaper DAW integration and manual audio recognition modes.

LAZY IMPORTS: shazam.py and engine.py are NOT imported at package load time
to avoid loading shazamio when audio recognition is disabled.
Only capture.py (which has no shazamio dependency) is loaded eagerly.
"""

# Eager imports - these have no shazamio/pydub dependencies
from .capture import AudioCaptureManager, AudioChunk

# Lazy imports - only loaded when actually accessed
# This prevents shazamio from loading when just listing audio devices
_lazy_imports = {
    'ShazamRecognizer': '.shazam',
    'RecognitionResult': '.shazam',
    'RecognitionEngine': '.engine',
    'EngineState': '.engine',
}

def __getattr__(name):
    """Lazy import handler for shazam and engine modules."""
    if name in _lazy_imports:
        module_name = _lazy_imports[name]
        import importlib
        # Use absolute module name (__name__ + '.submodule') instead of relative
        # import to ensure compatibility with PyInstaller's frozen import system,
        # where __package__ inside __getattr__ may not resolve correctly.
        # e.g. __name__='audio_recognition', module_name='.engine'
        #   -> 'audio_recognition.engine' (absolute, unambiguous)
        module = importlib.import_module(__name__ + module_name)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'AudioCaptureManager',
    'AudioChunk',
    'ShazamRecognizer',
    'RecognitionResult',
    'RecognitionEngine',
    'EngineState',
]
